---
# bundle-proposal: a change this repository offers to the agent-guides bundle. Only the home repository integrates it; nothing here is guidance.
proposal: p-11ffa9ae39
bundle: agent-guides
carrier: r-7985ee
base: 0.0.32
digest: 68717f030a4c
kind: knowledge
action: extends
target: verification-load-competes-with-the-product
lacks: a second carrier
seen: "2026-10-14"
---

# Proposal: verification-load-competes-with-the-product

Two heavy verifications run at once on one machine produce failures that read as the product's but come from the load: a browser timeout, a container VM killed. A heavy verification runs alone, and a timeout is re-run alone before it is believed.

## Evidence

One carrier, three occurrences in two sessions. 2026-10-12: the container VM running the test database died twice, once when an old background task was killed, and one gate failed because of it; the same day another project's tests on the machine lengthened the gates. 2026-10-14: a browser sweep timed out on a page load while the local test suite ran in parallel; the server log showed the page answered in under a tenth of a second, and the sweep run alone was clean. The home may join this with the answered shared-pool-pressure-reads-as-test-failure. Literature: the candidate's own; no new source consulted.
