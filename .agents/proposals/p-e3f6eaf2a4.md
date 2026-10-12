---
# bundle-proposal: a change this repository offers to the agent-guides bundle. Only the home repository integrates it; nothing here is guidance.
proposal: p-e3f6eaf2a4
bundle: agent-guides
carrier: r-7985ee
base: 0.0.25
digest: 469d45b59078
kind: method
action: extends
target: a-deny-on-secrets-misses-indirect-reads
lacks: a second repository; the host's sandbox availability per platform
seen: "2026-10-12"
---

A path deny on a secrets file stops only the commands that name the file; a recursive search, a script or any subprocess that discovers the file by itself still reads it and prints its contents into the session. Where the effect must not happen, the protection is an operating-system sandbox on every shell command (a deny-read list), proven with a canary file, not a permission rule.

## Evidence

An agent running a recursive search over a repository printed a line of the configuration template that the user-level read deny covered; the host's documentation states that read denies do not apply to commands that read files without naming them. A canary secret in a scratch folder: a recursive search without the sandbox printed it, with the sandbox's deny-read it did not (exit status of the search showed the refused read). Enabling the sandbox then needed two adjustments: the test collector skips the denied file by name (the sandbox refuses even its stat), and local port binding stays allowed for tests that start servers.
