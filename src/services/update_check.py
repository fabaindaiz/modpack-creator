"""Check the recommended build of a modpack for newer versions (N5).

Modrinth's `POST /version_files/update` answers, for each file hash, the newest version that fits
a game version, loaders and channels. It ignores the modpack's pins, so a pinned mod comes back
marked `pinned` and is neither counted nor notified. A check is stored; a notification goes out
only when the (non-pinned) updates differ from the last ones announced for that modpack.
"""
import hashlib
import asyncio
import logging
import re
from collections import defaultdict
from datetime import datetime, time, timezone, tzinfo
from typing import Any, Optional
from zoneinfo import ZoneInfo
from beanie import PydanticObjectId
from src.database.jobs import JobDatabase
from src.database.modpack import ModpackDatabase
from src.database.modrinth import ModrinthDatabase
from src.database.update_check import UpdateCheckDatabase
from src.exceptions import HTTP_404_NOT_FOUND, HTTP_409_CONFLICT
from src.library.api.client.modrinth import ModrinthAPI
from src.library.resolution import Target
from src.library import utils
from src.library.utils import getenv, getenv_opt, utcnow
from src.model.audit import AuditTarget
from src.model.jobs import Job
from src.model.modpack import AvailableUpdate, UpdateCheck
from src.model.users import Actor
from src.services.audit import AuditSink
from src.services.jobs import Handler, JobContext, JobFailed, NotificationSink

DISCORD_LIMIT = 2000
DEFAULT_TZ = "America/Santiago"
CHECK_AT = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")
logger = logging.getLogger("modpack.update_check")

def parse_check_at(value: str) -> time:
    """`HH:MM`, 24 hours, two digits each; anything else is a ValueError that names the variable."""
    found = CHECK_AT.fullmatch(value.strip())
    if found is None:
        raise ValueError(f"UPDATE_CHECK_AT must be HH:MM, 00:00 to 23:59 (got {value!r})")
    return time(int(found.group(1)), int(found.group(2)))

def schedule_from_env() -> Optional[tuple[time, ZoneInfo]]:
    """The daily schedule from UPDATE_CHECK_AT and UPDATE_CHECK_TZ, None if UPDATE_CHECK_AT is not
    set. A bad time or an unknown zone raises: the app must not start with a schedule that never runs.
    So does a value load_env ignored because YAML did not read it as text (N-R5): an unquoted 10:30
    would otherwise leave the variable unset and the schedule off, without a word."""
    _refuse_unquoted("UPDATE_CHECK_AT")
    raw = getenv_opt("UPDATE_CHECK_AT")
    if raw is None or not raw.strip():
        return None
    at = parse_check_at(raw)
    _refuse_unquoted("UPDATE_CHECK_TZ")
    name = getenv("UPDATE_CHECK_TZ", DEFAULT_TZ).strip()
    try:
        return at, ZoneInfo(name)
    except (KeyError, ValueError, OSError) as error:  # ZoneInfoNotFoundError is a KeyError
        raise ValueError(f"UPDATE_CHECK_TZ is not a known time zone (got {name!r})") from error

UNQUOTED = {
    "UPDATE_CHECK_AT": 'UPDATE_CHECK_AT debe ir entre comillas en .env.yaml (p. ej. "04:30"): YAML lee 10:30 como un número',
    "UPDATE_CHECK_TZ": 'UPDATE_CHECK_TZ debe ir entre comillas en .env.yaml (p. ej. "America/Santiago"): YAML no lo leyó como texto',
}

def _refuse_unquoted(name: str) -> None:
    if name in utils.SKIPPED_NON_STRING:
        raise ValueError(UNQUOTED[name])

def due_today(now_utc: datetime, at: time, tz: tzinfo, last_checked_at: Optional[datetime]) -> bool:
    """True if the local time has passed `at` and the last check was on an earlier local date.
    Dates are local to `tz`, never UTC: around Chile's clock changes the two differ in the evening,
    and a check at 23:30 must not count as the next day's. A naive datetime is read as UTC."""
    local_now = _as_utc(now_utc).astimezone(tz)
    if local_now.time() < at:
        return False
    if last_checked_at is None:
        return True
    return _as_utc(last_checked_at).astimezone(tz).date() < local_now.date()

def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

def digest_of(updates: list[AvailableUpdate]) -> str:
    """What was announced: the non-pinned updates, as 'project:from>to', sorted, hashed."""
    lines = sorted(f"{u.project_id}:{u.from_version}>{u.to_version}" for u in updates if not u.pinned)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()

class UpdateCheckService():
    def __init__(self,
            modpackDB: ModpackDatabase,
            modrinthAPI: ModrinthAPI,
            modrinthDB: ModrinthDatabase,
            updateCheckDB: UpdateCheckDatabase,
            jobDB: JobDatabase,
            audit: AuditSink,
            notifier: Optional[NotificationSink] = None,
            ) -> None:
        self.modpackDB = modpackDB
        self.api = modrinthAPI
        self.modrinthDB = modrinthDB
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
                if (latest is not None and latest.project_id == entry.project_id
                        and latest.version_id != entry.version_id):
                    updates.append(AvailableUpdate(
                        project_id=entry.project_id, from_version=entry.version_number,
                        to_version=latest.version_number, pinned=entry.project_id in bundle.pins,
                        from_version_id=entry.version_id, to_version_id=latest.version_id))
        updates.sort(key=lambda u: u.project_id)
        titles = {p.project_id: p.title for p in await self.modrinthDB.project_find_by_modrinth_id([u.project_id for u in updates])}
        for update in updates:
            update.title = titles.get(update.project_id) or update.project_id
        return UpdateCheck(bundle_id=PydanticObjectId(bundle_id), build_number=number, checked_at=utcnow(), updates=updates)

    async def enqueue(self, bundle_id: str, actor: Optional[Actor]) -> Job:
        """Queue a check of a modpack; 409 if it already has an active job (the key is shared with
        every job of the bundle). `actor` is None for the scheduler, which is not recorded."""
        await self.modpackDB.bundle_get(bundle_id)
        try:
            job = await self.jobDB.enqueue("update_check", key=f"bundle:{bundle_id}",
                                           payload={"bundle_id": bundle_id},
                                           requested_by=actor.ref() if actor is not None else None)
        except HTTP_409_CONFLICT as error:
            raise HTTP_409_CONFLICT("Ya hay una revisión en curso para este modpack") from error
        if actor is not None:
            await self.audit.record(actor, "bundle.update_check", AuditTarget(kind="bundle", id=bundle_id), {})
        return job

    async def schedule(self, now: datetime, at: time, tz: tzinfo) -> list[Job]:
        """Queue a check of every modpack with a build that has not been checked today (local
        date). One without any build is skipped: it would fail every day. A modpack with an active
        job is skipped too (409), and so is one another process queued first."""
        bundles = [b for b in await self.modpackDB.bundle_find().to_list()
                   if b.id is not None and (b.recommended_build or b.latest_build) is not None]
        last = await self.checkDB.latest_many([b.id for b in bundles if b.id is not None])
        queued = []
        for bundle in bundles:
            if bundle.id is None:
                continue
            checked = last.get(bundle.id)
            if not due_today(now, at, tz, checked.checked_at if checked else None):
                continue
            try:
                queued.append(await self.enqueue(str(bundle.id), actor=None))
            except HTTP_409_CONFLICT:
                continue
        return queued

    async def run_scheduler(self, stop: asyncio.Event, at: time, tz: tzinfo, check: float = 300.0) -> None:
        """Call `schedule` every `check` seconds until `stop` is set."""
        while not stop.is_set():
            try:
                await self.schedule(utcnow(), at, tz)
            except Exception:
                logger.exception("update check scheduler error")
            try:
                await asyncio.wait_for(stop.wait(), check)
            except asyncio.TimeoutError:
                pass

    async def latest(self, bundle_id: str) -> Optional[UpdateCheck]:
        if not PydanticObjectId.is_valid(bundle_id):
            raise HTTP_404_NOT_FOUND(f"Bundle '{bundle_id}' not found")
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
                # Catches everything (Modrinth down, no build, a bug): the error is recorded, no notice. Kept as a check with its error, and no notification.
                bundle = await self.modpackDB.bundle_get(bundle_id)
                await self.checkDB.insert(UpdateCheck(
                    bundle_id=PydanticObjectId(bundle_id), build_number=bundle.recommended_build or bundle.latest_build or 0,
                    checked_at=utcnow(), error=f"{type(error).__name__}: {error}"[:500]))
                return {"error": f"{type(error).__name__}: {error}"[:500]}
            # Notify, then insert: deliberate. A crash in between repeats one notice; the other
            # order would lose it for good.
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
        listing = ", ".join(f"{u.title} {u.from_version}→{u.to_version}" for u in shown)
        text = f"🔎 {bundle.name} (build {check.build_number}): {len(shown)} mods con actualización — {listing}"
        return text[:DISCORD_LIMIT]
