---
# bundle-proposal: a change this repository offers to the agent-guides bundle. Only the home repository integrates it; nothing here is guidance.
proposal: p-de60e53a1e
bundle: agent-guides
carrier: r-7985ee
base: 0.0.32
digest: 68717f030a4c
kind: method
action: extends
target: a-prose-rule-loses-to-a-tool-default
lacks: nothing new beyond the count; a carrier under a second owner
seen: "2026-11-24"
---

# Proposal: a-prose-rule-loses-to-a-tool-default

Another count for a written rule that a tool default beats: a rule against commands that wait on standard input (heredocs, a bare interpreter reading its input), written in the root instruction file and repeated in every delegate's prompt, was broken in every session of the period, by the main agent and by delegates alike; only a mechanism that runs at the tool call would make it hold.

## Evidence

One carrier, one owner. Four sessions in ten days, each with at least one incident: in one, about a dozen scripts sent through heredocs by the main agent before it noticed, and three empty heredocs by delegates whose prompts forbade them; in another, three by the main agent and four by delegates, one of which hung and was stopped by hand. Earlier harvests counted five of five sessions. The proposed pre-tool hook that rejects such commands still waits on the owner's decision, which reopens a declined entry under the owner's own rule.
