---
# bundle-proposal: a change this repository offers to the agent-guides bundle. Only the home repository integrates it; nothing here is guidance.
proposal: p-42fea0af36
bundle: agent-guides
carrier: r-7985ee
base: 0.0.25
digest: 469d45b59078
kind: knowledge
action: extends
target: a-check-must-be-seen-to-fail
lacks: a second repository
seen: "2026-10-02"
---

A check that takes its list of subjects from the code it is checking cannot see the defect it exists for, because that defect removes the subject from the list: a sweep that asks *does every guarded route refuse the unprivileged caller?* and finds the guarded routes by reading the registry the guard decorator fills passes when the decorator is deleted, since the route is no longer guarded and so no longer swept. The same blindness has a composition form: a guard tested on a host the test builds for itself passes when the line that installs the guard in the real host is deleted. Anchor the expected set outside the code under test (a table in a document the test parses, compared both ways with the registry), test the guard on the real composed host, and make the planted violation the guard's removal from the real artefact, not a change to the guard's behaviour. This extends `a-check-must-be-seen-to-fail` with a source of the constant green that a plant only reveals if the plant is the right one: removing the guard, the defect the check is for. Not when the registry is itself the specification (nothing else could say what should be guarded), in which case the check verifies wiring only and says so.

## Evidence

2026-10, in a small web service with two roles, where a decorator on service methods both enforced the privileged role and registered the method in a set. The first plan's role sweep found the privileged routes by matching route handlers against that set, and its own see-it-fail step was to delete the decorator from one method — the one plant that this design cannot turn red. A per-task review flagged the derived set as a minor; it was replaced by an explicit expected set, and after an owner decision by a table of every writing method and its role in the decisions document, which the test parses: privileged rows must equal the registry, every writing route must reach a row, and every method that writes an audit event must have one. In the same branch, the cross-origin refusal and the security-header middleware were tested on an application the tests built; a final whole-branch review noted that deleting either installation line in the real application failed no test, and a test on the real application now covers both.

### Literature

Mutation testing is a fault-based technique — a suite is judged by whether it detects deliberately planted faults (Jia and Harman, "An Analysis and Survey of the Development of Mutation Testing", IEEE TSE 37(5), 2011, https://doi.org/10.1109/TSE.2010.62) — so it is only as strong as the faults planted; this occurrence is a case where the obvious plant (delete the guard) was written down in the plan and would have exposed the design had it been run against the derived list. Not checked: whether common mutation tools include decorator or registration-call deletion among their operators.
