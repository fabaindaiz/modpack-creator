---
# bundle-proposal: a change this repository offers to the agent-guides bundle. Only the home repository integrates it; nothing here is guidance.
proposal: p-d939b1aadc
bundle: agent-guides
carrier: r-7985ee
base: 0.0.25
digest: 469d45b59078
kind: knowledge
action: new
target: paginate-by-timestamp-skips-ties
lacks: "refused: textbook (keyset pagination needs a unique tiebreaker); the store-precision corollary belongs to stored-timestamp-key-never-matches"
seen: "2026-10-02"
---

Paging a list by a cursor on a timestamp alone skips every item that shares the boundary timestamp with the last item shown; the cursor has to be a unique, totally ordered key — the timestamp plus a unique id as a tiebreaker in both the sort and the filter, or the id alone when it is monotonic. A store that truncates timestamps (to milliseconds, say) makes ties more frequent than the source's precision suggests.

## Evidence

2026-10, in a small web service: the first implementation of an audit-log page paged by the event time; a task review noticed that events written in the same millisecond would be skipped. The cursor became the last event's id, with the sort on (time, id) and the filter *earlier time, or equal time and smaller id*.

### Literature

"Paging requires a deterministic sort order", and the seek method extends the sort with a unique column for exactly this case (Winand, Use The Index, Luke, https://use-the-index-luke.com/sql/partial-results/fetch-next-page). Refused: keyset pagination with a unique tiebreaker is textbook and well documented; the only addition, that a store's timestamp truncation raises the tie rate, is a corollary of the store-precision candidate this repository already proposed (`stored-timestamp-key-never-matches`).
