# Photobooth Booth — Documentation

Documentation for the booth fork (`starkerflamingo32/photobooth`, branch `booth`): the custom print stack for the
SELPHY CP1300 and the operator web UI. Three planes, three questions, three readers. Every sentence belongs to
exactly one of them.

| Plane | Question | Directory | Reader |
|---|---|---|---|
| **WHAT** | What must be true of the system? | [`Functionality/`](Functionality/) | Anyone judging whether it is correct: reviewer, tester, future maintainer |
| **HOW** | How is it built and changed? | [`Method/`](Method/) | Whoever writes the next change, human or agent |
| **OPERATE** | How do I install and run it? | [`UserDocumentation/`](UserDocumentation/) | Whoever sets up, runs or recovers the booth at an event |

The rest of `docs/` (`install/`, `faq/`, `update/`, …) is upstream Photobooth's own documentation. It is not part
of these planes and is left as upstream ships it.

**Authority order: the FSD defines the target; the Method defines the way there.**
On conflict the FSD wins on *what must be true*, the Method wins on *how to get there*. The user manual describes
the system as built; if it disagrees with either, it is stale, which means reality or the spec moved.

None of the three carries history or rationale narrative. Those live in `git log`. (The FSD's lifecycle block,
Appendix D, is the one sanctioned exception.)

## Where to start

| If you are… | Read |
|---|---|
| Making a change | [`Method/AI-Workflow.md`](Method/AI-Workflow.md): the loop every change follows |
| Judging correctness | [`Functionality/fsd.md`](Functionality/fsd.md): requirements, state model, verification contracts |
| Running the booth | [`UserDocumentation/User-Manual.md`](UserDocumentation/User-Manual.md) |
| Wondering why | `git log`: a decision leaves its effect in a plane and its reason in the commit |

`Functionality/fsd-esp32-v0.15.md` is the frozen predecessor of the FSD (the setup with an ESP32 unit) and is not
maintained.

## Routing a new sentence

Ask in order; the first yes wins:

1. Externally observable and must be true → **Functionality**
2. Constrains how code is written or verified → **Method**
3. Tells a human how to run or recover the system → **UserDocumentation**
4. About collaborating with an AI assistant → `CLAUDE.md`, which is not a plane
5. Why a past decision was made → the commit message

Two questions settle the hard cases. *Could a black-box tester verify it?* Yes means WHAT. *Would it survive a
rewrite in another language?* No means HOW.

Examples from this booth: *"a printer error reaches the tracker within 2 s"* is Functionality. *"the simulated
SELPHY replaces the USB device of the `selphy` queue"* is Method. *"switch the printer off and on, then confirm on
the phone"* is UserDocumentation.

## There is no decisions file

A settled decision leaves its **effect** as present-state content in the plane it governs (a load-bearing "must
not" becomes a stated negative requirement) and its reason in the commit message.

## Writing rules

**Present-state.** Write what is true now, in the present tense. No "now uses", "previously", "as of v2", "we
decided to". Version history belongs in `git log` and the FSD's Appendix D.

**One canonical home.** Every fact is stated once. Anywhere else that needs it, link.
