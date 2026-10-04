---
# bundle-proposal: a change this repository offers to the agent-guides bundle. Only the home repository integrates it; nothing here is guidance.
proposal: p-e0ce4d142d
bundle: agent-guides
carrier: r-7985ee
base: 0.0.25
digest: 469d45b59078
kind: experiment
target: nested-partial-update-replaces
verdict: confirms
where: a web service over a document store, written through an object-document mapper and its driver
seen: "2026-10-04"
---

For the object-document mapper and the driver in use over a document store, one run per update path against a disposable store: seed a record whose two nested maps each hold two keys, then (1) send a partial patch model holding one key of one map through the mapper's set call, as the service's generic patch endpoint does; (2) set one leaf through the driver with a dotted path, as the per-key endpoints do; (3) pass a dotted key to the same mapper set call. A command listener captured the update mask each call emitted.

## Result

2026-10-04. (1) The mask was the parent map's key alone, and the sibling key was gone afterwards: the patch replaced the whole map. (2) and (3) emitted the leaf path and kept every sibling. The repository had already moved its per-key writes to dotted leaves after a review found a read-modify-write losing concurrent edits, but its generic patch endpoint still accepts the nested maps and replaces them whole; no test sends a nested map through it (the existing tests patch scalar fields only), which is the note's *the test only checks the values that were sent*. Whether replacing the map is the intended meaning of that endpoint is not written anywhere. Cost: about ten minutes, most of it starting the container. Verdict confirms: the semantics are per call (the same mapper call replaces with a nested value and touches a leaf with a dotted key), and only the emitted keys show which.
