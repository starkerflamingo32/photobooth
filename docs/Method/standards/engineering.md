# Standard — Engineering conventions

Project-agnostic build rules, each a single present-state rule. Project-specific commands and bindings are in
[`../project/booth.md`](../project/booth.md).

- **Reuse before adding**: search for an existing helper, module or skill (upstream's first) before writing new code.
- **Smallest change that satisfies the rule**: no speculative scope, no drive-by refactors bundled with a fix.
- **One module per component**: never fold an interface into its only consumer. A component that cannot be named
  cannot be tested.
- **Dependencies point one way**: lower layers never import higher ones. Where the need seems to invert, invert it
  at the composition root rather than reaching upward.
- **Extract pure cores**: separate the decision from the I/O. A state transition, a message parser or a text
  mapping is a free function with no CUPS, USB, network or filesystem dependency, so the unit tier can reach it.
- **Verification before commit**: the build, lint and test commands in `project/booth.md` all pass.
- **Errors fail loudly**: actionable messages, no silent catches, no swallowed return codes. An ignored error is a
  defect even when nothing breaks yet.
- **Secrets are never in code or documentation**: only their *location* is documented. Validate all external input.
  Least privilege by default.
- **Commits**: never commit generated output, build artifacts, temporary directories or secrets.
