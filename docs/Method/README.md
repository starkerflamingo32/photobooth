# Photobooth Booth — Method (HOW)

The **build contract**: the rules for how the booth fork is built and changed.
Entry point for any task: **[`AI-Workflow.md`](AI-Workflow.md)**.

| Path | Contents |
|------|----------|
| `AI-Workflow.md` | The loop every change follows. Read it before touching code. |
| `standards/` | Portable rules: engineering and testing. |
| `project/booth.md` | This fork's bindings: repositories, upstream relation, layers, commands, conventions. |
| `project/test-infrastructure.md` | Where tests live, the virtual test setup, the browser setup, the CI workflows. |

A rule belongs here when it constrains **how the code is written, structured, or verified** without being
observable from outside the running system. If a black-box tester could verify it, it is a requirement and belongs
in the FSD.

The plane map, authority order, and routing rule are in [`../00-Overview.md`](../00-Overview.md).
