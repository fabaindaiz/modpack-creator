---
# bundle-proposal: a change this repository offers to the agent-guides bundle. Only the home repository integrates it; nothing here is guidance.
proposal: p-93bccf4e09
bundle: agent-guides
carrier: r-7985ee
base: 0.0.32
digest: 68717f030a4c
kind: knowledge
action: new
target: automation-applies-only-the-safe-class
lacks: a second carrier; the citations were read through a summarising tool and are marked as assumptions until checked against the live pages
seen: "2026-10-13"
---

# Proposal: automation-applies-only-the-safe-class

An automatic reconciler applies on its own only the changes it can show are safe: a newer version, through the same path, within the allowed channels, never below what is installed. A change of path, an addition or a removal, and a downgrade are proposed and wait for a person's confirmation, and each action has a test that pins which class it is in.

## Evidence

One carrier, 2026-10-13: the owner made it a rule for a resolver that picks versions of plugins, some of them through a compatibility bridge. With the rule written, an isolated review still found three paths where the automatic side crossed into the dangerous class: turning a bridge off moved items to their native versions and could go back to an older one; a move to native could take an older version; and a pin changed an item's path without asking. Each is now a test.

Literature (read through a summarising tool; assumption-grade until checked): Renovate's automerge guidance automerges only where one would merge anyway, typically non-major semver updates, and waits for status checks; GitHub's Dependabot guidance auto-merges by update type (patch in its example) behind required checks; apt_preferences(5) never downgrades unless a pin's priority exceeds 1000; Argo CD's automated sync does not delete resources without prune and does not roll back while enabled; Flux makes prune a required choice; the Google SRE chapter on automation supports scoping automation to well-defined domains. No source states the rule as one, and none classifies by path or channel.
