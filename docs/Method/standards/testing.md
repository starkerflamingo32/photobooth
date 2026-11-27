# Standard — Testing

Testing is part of the build contract. Every Must/Should in the FSD carries a verification contract, and every change
ships the test that proves it.

## Tiers

Order tests by cost. Use the cheapest tier that can catch the failure, and reserve the expensive ones for behaviour
no cheaper tier can reach. This project's tiers are **unit · integration · e2e · bench**; what each runs on and
covers is stated once, in the FSD ([§17.0](../../Functionality/fsd.md#170-test-architecture)). How they are run is
in [`../project/test-infrastructure.md`](../project/test-infrastructure.md).

## Rules

- **Every Must/Should has a test.** A requirement with no test is an untested contract, surfaced as a gap rather
  than left silent.
- **Every change ships its test.** Not done until the test is written and passes. Bug fixes write the regression
  test **first**, and it fails before the fix.
- **Tests trace to requirement IDs.** Each test names the requirement(s) it verifies, so coverage is computed rather
  than asserted.
- **Coverage is generated, never hand-maintained** (from `testing/test-plan.yaml`).
- **State machines are covered per transition, not per state.** Reaching a state proves nothing about the edges
  into it.
- **No silent gaps.** A specified-but-unbuilt test is marked with its prerequisite.
- **The suite is green before commit.**

## Security testing

Security tests are grounded in published standards (OWASP ASVS, OWASP Top 10 / WSTG, CWE, CVSS, NIST SP 800-115)
and written for the threat model the FSD declares (§15), not imported wholesale.

- Every security-relevant requirement (authentication, authorization, input validation, secrets, root rights) has
  a test tagged with its standard reference.
- A security criterion must be able to **fail**.
- Where the threat model does not justify a protection, it is an accepted risk in the FSD, not a requirement nobody
  implements.

---

# Running a test

## Rules that hold for every run

- **Capture evidence before resetting anything**: a reset destroys the reason for the reset. On the bench that
  means the CUPS `error_log`, the backend messages and the printer query log before a power cycle or `cupsenable`.
- **A timeout is a failure, not an absent result.** Require a completion marker.
- **A null observation accuses the instrument before it accuses the subject.** Before concluding anything from an
  absence (no backend message, no printer query answer, no job in the simulator log), show a **positive control in
  the same session**: the same instrument producing a known signal.
- **An experiment whose control produces nothing is void, not negative.** Say so and stop.
- **Prefer the instrument's own API to raw access**: CUPS tools (`lpstat`, `cupsenable`) and the Gutenprint backend
  rather than raw USB.
- **Verify the precondition; if it fails record `not done`, never `failed`.**
- **Leave the rig as you found it**: queue enabled, no pending jobs, printer idle, paper and ink counts noted.
