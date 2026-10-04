---
# bundle-proposal: a change this repository offers to the agent-guides bundle. Only the home repository integrates it; nothing here is guidance.
proposal: p-85a55e5dbf
bundle: agent-guides
carrier: r-7985ee
base: 0.0.25
digest: 469d45b59078
kind: knowledge
action: extends
target: absence-is-a-third-value
lacks: nothing
seen: "2026-10-04"
---

A missing boolean that guards an irreversible effect is read by every component that touches it, and each one collapses *missing* in its own default direction: a query with a not-equal filter counts the missing record as the dangerous value, a worker reading it with a safe default treats it as the safe one, and a template reading it with the opposite default shows it as the dangerous one. The record is then simultaneously *did nothing* for the component that runs it and *wrote something* for the component that decides what may be undone. Name the safe meaning of absence once, in one predicate that only an explicit value can pass (*real only when the flag is explicitly false*), and route every reader — query, worker, service, view — through it. This adds to `absence-is-a-third-value` the case where the disagreement is between three readers of one flag, not two, and where the query engine's not-equal operator is what matched the missing documents.

## Evidence

2026-10, in a service that queues jobs which may modify a remote host or only simulate doing so, decided by a boolean in the job's free-form payload. A whole-branch review noted that a payload without the flag was treated inconsistently; reading the code found four readers: the lookup of the last job that wrote used a not-equal-true filter, which matches documents without the field (so a flagless job counted as having written and could be offered for rollback); the worker defaulted the flag to true (it would simulate); the rollback rules and two templates defaulted it to false (real). Jobs created through the application always carry the flag, so only hand-made or older records could hit this. The fix introduced one predicate (real only when the flag is explicitly false), used it in the query (equality to false), the worker, the service and the views, and added a test that a flagless job is a simulation everywhere; the deploy runbook gained a count of stored jobs without the flag, to run before deploying.

### Literature

The store's documentation states that a not-equal query also selects documents that do not contain the field (https://www.mongodb.com/docs/manual/reference/operator/query/ne/).
