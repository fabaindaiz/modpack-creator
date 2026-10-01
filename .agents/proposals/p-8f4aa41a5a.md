---
# bundle-proposal: a change this repository offers to the agent-guides bundle. Only the home repository integrates it; nothing here is guidance.
proposal: p-8f4aa41a5a
bundle: agent-guides
carrier: r-7985ee
base: 0.0.25
digest: 469d45b59078
kind: method
action: new
target: roadmap-heading-id-position
lacks: "nothing: a reproducible defect between a template and the tool"
seen: "2026-10-01"
---

The roadmap template in the method's shared reference (artifact 8) writes an item heading with the id first, `### i-<repo6>-<content6> · <Idea>`. The record-id check recognises a heading definition only when the id comes after the middle dot. A carrier that follows the template therefore gets a green id check that never sees its roadmap definitions: duplicates and malformed ids there pass silently. One of the two has to change: the template puts the id after the dot, or the tool also accepts it before. The same applies to any other heading template with the id first.

## Evidence

2026-10, in a carrier bootstrapped that day. The roadmap followed the template, with 26 items. `bundle.py ids` reported 0 errors and 21 definitions for 42 written ids; the missing 21 were all roadmap headings. With the ids moved after the dot, the tool reported 42 definitions and turned red on five real double definitions, which it had not been able to see before. The carrier recorded the substitution in `adapted`. In the tool, the definition pattern is `RECORD_DEFINITION` in `tools/bundle.py`: a table row whose first cell is the id, or a heading with `·` before the id.
