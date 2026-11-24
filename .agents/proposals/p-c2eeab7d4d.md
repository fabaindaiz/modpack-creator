---
# bundle-proposal: a change this repository offers to the agent-guides bundle. Only the home repository integrates it; nothing here is guidance.
proposal: p-c2eeab7d4d
bundle: agent-guides
carrier: r-7985ee
base: 0.0.32
digest: 68717f030a4c
kind: method
action: extends
target: steps-readable-by-a-permission-layer
lacks: the host's documentation of where its shell escape runs; a carrier under a second owner
seen: "2026-11-24"
---

# Proposal: steps-readable-by-a-permission-layer

A step handed to the human because the sandbox denies it says where it runs. The host's in-session shell escape is not a reliable way out: it may run under the agent's own sandbox, it has no standard input, and mistyped it arrives as plain text. A step that needs what the sandbox denies names a terminal outside the host, an interactive confirmation is answered on the command line by the human deliberately, and the agent reads the resulting state back before reporting the step done.

## Evidence

One carrier, one owner. For about a week, pushes run through the escape worked. In a later session the escape ran under the agent's sandbox: a branch switch handed to the human failed exactly as the agent's own attempt had, leaving the working tree half-written a second time (most of a hundred files removed, restored by hand), and a push handed the same way hit the same network deny. The cause of the change is unknown (ASSUMPTION: a host setting or version change). A command with a yes/no prompt read end-of-input through the escape and cancelled itself. Twice, the escape typed without a space after its marker reached the agent as text and ran nothing.
