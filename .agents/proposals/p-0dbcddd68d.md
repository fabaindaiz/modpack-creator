---
# bundle-proposal: a change this repository offers to the agent-guides bundle. Only the home repository integrates it; nothing here is guidance.
proposal: p-0dbcddd68d
bundle: agent-guides
carrier: r-7985ee
base: 0.0.25
digest: 469d45b59078
kind: knowledge
action: new
target: hidden-is-not-deferred
lacks: "refused: one front-end library's trigger semantics; the general form is ordinary practice an agent already knows"
seen: "2026-10-04"
---

A user-interface fragment that loads itself when rendered is fetched as soon as the page renders, even when it sits inside a closed dialog, so hiding a costly preview does not defer it: every visit pays for it, and its errors surface to users who never asked for it. Deferring means triggering the load on the reveal, and an external call slow enough to need a deadline does not belong in the page render at all. A handler that closes a dialog when any request started inside it completes closes it on requests that are not its submission.

## Evidence

2026-10, in a small server-rendered web panel. A change preview that called a third-party API for changelogs (bounded at several seconds) was first built during page render, then moved to a fragment with a load trigger inside a closed dialog, which still fired on every page load; a final review found it, and the trigger became *when the dialog's content first becomes visible*. The change exposed an older defect: the dialog closed on the completion of any request from an element inside it, which a lazily loaded preview would trigger at once; it now closes only on its own form's response. A headless-browser sweep now opens the dialog and fails if it closes or if the preview loads with the page. Refused: the specific mechanism is one front-end library's trigger semantics, and the general form (hidden is not deferred; keep slow third-party calls out of the render; scope a handler to its own event) is ordinary front-end practice that an agent already knows; what made it costly here is covered by the review-cadence proposal of this harvest.
