# Photobooth Booth — AI Workflow (the build contract)

Read this before any change. It is how new functionality is built and how the documentation stays in sync with it.

## The loop

**1. Locate the contract.** Find the FSD requirement the work serves in
[`../Functionality/fsd.md`](../Functionality/fsd.md). If none exists, the work starts by defining the **WHAT** (a new,
atomic, falsifiable requirement with its verification contract, via `/define update`), not by writing code. A change
with no requirement behind it is either scope creep or an undocumented requirement; both are resolved before
implementation.

**2. Build per the Method.** Follow `standards/` and `project/`. Reuse an existing upstream module, helper or skill
before adding new code. Make the smallest change that satisfies the requirement: no speculative scope, no drive-by
refactors of upstream code.

**3. Test: the gate, not an afterthought.** A change is **not done** until its test exists and passes. The
requirement's verification contract in the FSD says what the test must observe and what must not happen; the test
is declared in `testing/test-plan.yaml` (owned by `/build`) and written beside the code. A bug fix writes its
**regression test first**, and that test fails before the fix and passes after.

**4. Reconcile the documentation.**
- The **FSD** absorbs new or changed behaviour: *verify, don't transcribe*. If the code deviates from the intended
  spec, fix the code; do not enshrine the defect as a requirement.
- The **user manual** absorbs anything an operator would notice.
- The **Method stays put** unless the change taught a rule that is true for the whole project, not just this change.
- All present-state. No "now uses", no "previously".

**5. Verify both directions.** Confirm the implementation matches the FSD, and that no FSD requirement is silently
unimplemented. Deviations fix the code; genuine gaps are recorded as gaps; contradictions are escalated to the
booth owner rather than guessed at.

## Requirement quality gate

Before a new requirement enters the FSD it is:

- **Atomic**: one obligation. Anything joining two verbs, a behaviour and a deadline, or a success and a failure
  path is split.
- **Falsifiable**: precondition, stimulus, observable response, deadline, tolerance, failure behaviour,
  verification tier; a Must/Should carries its contract row.
- **Free of weasel words**: *appropriate, graceful, user-friendly, reasonable, sufficient, robust, seamless,
  acceptable, normal operation, best effort*.
- **Provenance-tagged**: `[user]`, `[derived]` or `[code]`.

## Roles

- **The booth owner** decides every requirement, threshold and wording. Only the owner moves a requirement or value
  from `proposed` to `approved`.
- **The assistant** writes and edits all three planes, proposes values as `proposed`, and never approves its own
  proposals.
- **Contradictions** between planes, or between a plane and the running booth, go to the booth owner. Until decided,
  the FSD wins on what must be true.
