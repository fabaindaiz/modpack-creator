---
# bundle-proposal: a change this repository offers to the agent-guides bundle. Only the home repository integrates it; nothing here is guidance.
proposal: p-5e301aac5a
bundle: agent-guides
carrier: r-7985ee
base: 0.0.25
digest: 469d45b59078
kind: knowledge
action: new
target: data-location-is-part-of-the-deploy
lacks: a second occurrence
seen: "2026-10-01"
---

Where a stateful container keeps its data is part of the deploy contract, and changing it is a data migration rather than a configuration edit. A container image can declare its data directory as a volume. With nothing mounted there, the data lives in an anonymous volume: it survives a recreate, but not a teardown followed by a fresh start. Each such cycle silently orphans the previous data, and the service comes back empty with no error. Mounting a named volume at the same path fixes that, but the first start after the switch is also empty, while the old data sits in an unnamed volume. Every change to a stateful service's mounts therefore needs a dump-and-restore or copy step and a check of the record counts, before it is called done. Not when the service holds no state, or when its data is already on an explicitly named or bound location.

## Evidence

2026-10: a small web service's compose file ran its document store with no mount for the data directory, which the image declares as a volume. A commented-out bind mount pointed at a path the store does not use. The project's stop script ran a teardown, so every stop/start cycle had probably orphaned the data. Switching to a named volume, which the fix needed, would have made the next deploy start empty. The change shipped with a required version variable (the store cannot be downgraded and upgrades one major version at a time), a runbook (dump, deploy, restore without the admin namespace, count records) and a CI job that checks the compose file refuses to run without that version. Literature: the container runtime's documentation says anonymous volumes persist after the container is removed but are not reused automatically, and that `up` preserves them on recreate while `down` without `-v` leaves them behind (https://docs.docker.com/engine/storage/volumes/, https://docs.docker.com/reference/cli/docker/compose/down/).
