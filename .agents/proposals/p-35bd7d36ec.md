---
# bundle-proposal: a change this repository offers to the agent-guides bundle. Only the home repository integrates it; nothing here is guidance.
proposal: p-35bd7d36ec
bundle: agent-guides
carrier: r-7985ee
base: 0.0.25
digest: 469d45b59078
kind: knowledge
action: extends
target: tolerant-of-absent-is-not-tolerant-of-malformed
lacks: a second repository
seen: "2026-10-04"
---

A configuration loader that skips values of an unexpected type turns a malformed setting into an absent one, and for an optional feature *absent* is the legitimate way to say *off*: the typo ships as a deliberately disabled feature, with no error and nothing in the log. The typed configuration format makes this common rather than rare, because it retypes values on its own: a YAML 1.1 loader reads an unquoted clock time with a two-digit hour from 10 upward as a base-60 integer (`10:30` becomes 630), while one with a leading zero (`09:30`) stays text, so a test suite whose example times happen to be early-morning ones never meets the failing class. The rule this extends: tolerance for absence is not tolerance for malformation. Absent may mean off; present-but-wrong-type must refuse at boot, naming the key and never the value (it may be a secret); an explicit empty or null is a third case the loader has to give a meaning on purpose. Test the parser with values drawn from every lexical class the format retypes, not only with the ones the developer would type. Not when the format has no implicit typing (YAML 1.2 core schema, JSON, environment strings), or when the loader validates against a schema that names each key's type. Cost: one schema or one explicit type check per optional key, and an operator who quoted nothing before now meets a refusal at boot.

## Evidence

2026-10, in a small web service configured from a YAML file read with a YAML 1.1 loader. A daily job was scheduled by an optional clock-time key; the loader dropped any value that was not a string. Unquoted, every time with an hour from 10 to 23 (more than half of the day) became an integer, was skipped, and the schedule silently never ran, against a decision that a malformed value must stop the boot. The unit tests used early-morning times, which the format leaves as text, and passed. A final whole-branch review found it by reading; measured afterwards with the repository's loader: `10:30` → 630, `1:30` → 90, `04:30` and `09:30` → text, quoted → text, empty → null. The fix refuses a non-string value of that key at boot with a message naming only the key; an empty value still fails with the *quote it* message, which is now a recorded open item (empty should probably mean off).

### Literature

The YAML 1.1 integer type defines base 60 as `[-+]?[1-9][0-9_]*(:[0-5]?[0-9])+`, which is why a leading zero escapes it (https://yaml.org/type/int.html); YAML 1.2 "removed many of the problematic implicit typing recommendations" (https://yaml.org/spec/1.2.2/, §1.2); the loader in use documents itself as a YAML 1.1 implementation (https://pyyaml.org/wiki/PyYAMLDocumentation). This extends the queued candidate on absent versus malformed from the other side: there a lenient parser still raised on the wrong shape; here the leniency swallowed it. It also bounds `fail-closed-defaults` (*absence genuinely the common case*): the optional key may default to off, but only when it is really absent.
