---
# bundle-proposal: a change this repository offers to the agent-guides bundle. Only the home repository integrates it; nothing here is guidance.
proposal: p-f026b1bc9e
bundle: agent-guides
carrier: r-7985ee
base: 0.0.25
digest: 469d45b59078
kind: knowledge
action: new
target: a-created-time-may-be-a-change-time
lacks: literature on change-time semantics; a second occurrence
seen: "2026-10-12"
---

A file listing's "created" timestamp may be the inode change time, which a routine ownership or permission reset moves on every file without changing its content; a cache keyed on it is invalidated by that reset. Detect the events that really bring old content back (a restore, an archive extraction, a reinstall) from the system that performs them, and treat the change time as information only.

## Evidence

A scan cache keyed on (path, size, modification time, the listing's creation time) to detect backup restores, which keep old modification times. The platform's file daemon recursively changes ownership of every file at each server start, by default, and on Linux that updates the change time even when the owner is unchanged (verified with a same-owner chown). Every restart would have made the next scan download every file again. Replaced by one read of the server's activity log before each scan, failing closed (a full scan) when it cannot be read.
