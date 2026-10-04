"""Check the recommended build of a modpack for newer versions (N5).

Modrinth's `POST /version_files/update` answers, for each file hash, the newest version that fits
a game version, loaders and channels. It ignores the modpack's pins, so a pinned mod comes back
marked `pinned` and is neither counted nor notified. A check is stored; a notification goes out
only when the (non-pinned) updates differ from the last ones announced for that modpack.
"""
import hashlib
from collections import defaultdict
from typing import Any, Optional
from beanie import PydanticObjectId
from src.database.jobs import JobDatabase
from src.database.modpack import ModpackDatabase
from src.database.update_check import UpdateCheckDatabase
from src.exceptions import HTTP_409_CONFLICT
from src.library.api.client.modrinth import ModrinthAPI
from src.library.resolution import Target
from src.library.utils import utcnow
from src.model.audit import AuditTarget
from src.model.jobs import Job
from src.model.modpack import AvailableUpdate, UpdateCheck
from src.model.users import Actor
from src.services.audit import AuditSink
from src.services.jobs import Handler, JobContext, JobFailed, NotificationSink

DISCORD_LIMIT = 2000

def digest_of(updates: list[AvailableUpdate]) -> str:
    """What was announced: the non-pinned updates, as 'project:from>to', sorted, hashed."""
    lines = sorted(f"{u.project_id}:{u.from_version}>{u.to_version}" for u in updates if not u.pinned)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()

class UpdateCheckService():
    def __init__(self,
            modpackDB: ModpackDatabase,
            modrinthAPI: ModrinthAPI,
            updateCheckDB: UpdateCheckDatabase,
            jobDB: JobDatabase,
            audit: AuditSink,
            notifier: Optional[NotificationSink] = None,
            ) -> None:
        self.modpackDB = modpackDB
        self.api = modrinthAPI
        self.checkDB = updateCheckDB
        self.jobDB = jobDB
        self.audit = audit
        self.notifier = notifier

    async def compute(self, bundle_id: str) -> UpdateCheck:
        """Ask Modrinth about the files of the recommended build (else the latest). Not stored.
        Raises if Modrinth fails; 409 if the modpack has no build."""
        bundle = await self.modpackDB.bundle_get(bundle_id)
        number = bundle.recommended_build or bundle.latest_build
        if number is None:
            raise HTTP_409_CONFLICT(f"Bundle '{bundle_id}' has no build")
        build = await self.modpackDB.build_get(bundle_id, number)
        target = Target(game_version=bundle.version, loader=bundle.loader.value)
        # One call per set of loaders: mods, shaders and resource packs are not loaded by the same.
        groups: dict[tuple[str, ...], list[Any]] = defaultdict(list)
        for entry in build.lock.entries:
            if entry.file is not None:
                groups[tuple(sorted(target.loaders(entry.project_type)))].append(entry)
        updates: list[AvailableUpdate] = []
        for loaders, entries in sorted(groups.items()):
            found = await self.api.files_update_hash_post_many(
                [e.file.sha512 for e in entries], "sha512", loaders, [bundle.version], bundle.channels)
            for entry in entries:
                latest = found.get(entry.file.sha512)
                if latest is not None and latest.version_id != entry.version_id:
                    updates.append(AvailableUpdate(
                        project_id=entry.project_id, from_version=entry.version_number,
                        to_version=latest.version_number, pinned=entry.project_id in bundle.pins))
        updates.sort(key=lambda u: u.project_id)
        return UpdateCheck(bundle_id=PydanticObjectId(bundle_id), build_number=number, checked_at=utcnow(), updates=updates)

    async def enqueue(self, bundle_id: str, actor: Optional[Actor]) -> Job:
        """Queue a check of a modpack; 409 if it already has an active job (the key is shared with
        every job of the bundle). `actor` is None for the scheduler, which is not recorded."""
        await self.modpackDB.bundle_get(bundle_id)
        job = await self.jobDB.enqueue("update_check", key=f"bundle:{bundle_id}",
                                       payload={"bundle_id": bundle_id},
                                       requested_by=actor.ref() if actor is not None else None)
        if actor is not None:
            await self.audit.record(actor, "bundle.update_check", AuditTarget(kind="bundle", id=bundle_id), {})
        return job

    async def latest(self, bundle_id: str) -> Optional[UpdateCheck]:
        return await self.checkDB.latest(PydanticObjectId(bundle_id))

    async def latest_many(self, bundle_ids: list[str]) -> dict[PydanticObjectId, UpdateCheck]:
        return await self.checkDB.latest_many([PydanticObjectId(b) for b in bundle_ids])

    def handlers(self) -> dict[str, Handler]:
        return {"update_check": self._run}

    async def _run(self, job: Job, context: JobContext) -> None:
        bundle_id = job.payload["bundle_id"]

        async def check() -> dict[str, Any]:
            try:
                result = await self.compute(bundle_id)
            except Exception as error:
                # Modrinth down (or no build): kept as a check with its error, and no notification.
                bundle = await self.modpackDB.bundle_get(bundle_id)
                await self.checkDB.insert(UpdateCheck(
                    bundle_id=PydanticObjectId(bundle_id), build_number=bundle.recommended_build or bundle.latest_build or 0,
                    checked_at=utcnow(), error=f"{type(error).__name__}: {error}"[:500]))
                return {"error": f"{type(error).__name__}: {error}"[:500]}
            if await self._should_notify(result):
                result.notified_digest = digest_of(result.updates)
                if self.notifier is not None:
                    await self.notifier.notify(await self._message(bundle_id, result))
            await self.checkDB.insert(result)
            return {"updates": len(result.updates), "notified": result.notified_digest is not None}

        outcome = await context.step("check", check)
        if "error" in outcome:
            raise JobFailed(outcome["error"])

    async def _should_notify(self, check: UpdateCheck) -> bool:
        if not any(not u.pinned for u in check.updates):
            return False
        last = await self.checkDB.last_notified(check.bundle_id)
        return last is None or last.notified_digest != digest_of(check.updates)

    async def _message(self, bundle_id: str, check: UpdateCheck) -> str:
        bundle = await self.modpackDB.bundle_get(bundle_id)
        shown = [u for u in check.updates if not u.pinned]
        listing = ", ".join(f"{u.project_id} {u.from_version}→{u.to_version}" for u in shown)
        text = f"🔎 {bundle.name} (build {check.build_number}): {len(shown)} mods con actualización — {listing}"
        return text[:DISCORD_LIMIT]
