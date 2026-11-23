# Photobooth Booth — Functional Specification Document (FSD)

**Scope of this FSD:** truthful print status on the booth's print screen (SELPHY CP1300 via CUPS, plus an ESP32 paper-path sensor unit).
**Fork:** starkerflamingo32/photobooth, branch `booth` (based on upstream `dev` fdb58c0, 4.99.0).
**Status:** DRAFT — requirements only. Implementation and code architecture are deliberately not decided yet (§2.3).

| Lifecycle | |
|---|---|
| Created | 2026-11-23 |
| Last updated | 2026-11-23 |
| Load defined | **No** — open timing parameters (Appendix A), open questions (§4.4), Method/UserDocumentation planes not created yet |

**Provenance tags:** `[user]` decided by the booth owner (interview 2026-11-23 or earlier notes) · `[code]` observed in code, logs or on the real printer · `[derived]` follows from a `[user]`/`[code]` item · `(assumed)` inferred, must be confirmed.
**Status values:** `approved` (user decided) · `proposed` (written by the assistant, awaiting acceptance).

---

## 1. System Overview

### 1.1 Purpose / problem

Today the booth reports a print as successful as soon as CUPS has **received** the job `[code]`:

- `api/print.php` returns `{"status":"ok"}` once the job is queued, not when paper comes out.
- CUPS marks the SELPHY job *completed* ~17 s in, long before the printer is finished.
- On paper/ink errors the Gutenprint backend **stops the queue**; the guest still saw "success". At the 2026-11-22 event the queue stopped 4× (`Paper Feed (02)` ×5, `Ink Cassette Empty (06)`, `Unknown Error (08)`), see photobooth-setup `event-2026-11-22-findings.md`.

The goal: the booth's print screen says **what is really happening**, using (a) the CUPS printer and job status and (b) events from an ESP32 sensor unit watching the paper path. `[user]`

### 1.2 Users

| Who | Needs |
|---|---|
| Guest | Knows whether their photo is printing, how far along it is, whether it is waiting, and when to take it. German only. `[user]` |
| Staff / booth operator | Sees what is wrong (paper, ink, jam, sensor) and that printing resumes by itself once fixed. |

### 1.3 Goals

- G1 Print screen shows the real job stage from "Druckauftrag gesendet" to "Fertig – bitte Foto entnehmen". `[user]`
- G2 Printer problems are shown with a specific cause (paper, ink cassette, jam, …) or, if unknown, the raw CUPS message. `[user]`
- G3 While the printer has a problem, the booth takes no new print requests; photos and QR codes keep working. `[user]`
- G4 After the problem is fixed, waiting prints resume without staff action on the booth. `[user]`
- G5 (later phase) Paper/ink counters warn before supplies run out. `[user]`

### 1.4 Non-goals

- Choosing implementation, code structure, ESP32 transport protocol details, sensor hardware or mounting — decided later. `[user]`
- Languages other than German. `[user]`
- Supporting printers other than the SELPHY CP1300. (assumed)
- Changing how photos are taken, uploaded or shared.

### 1.5 High-level flow

```text
Guest presses "Drucken"
  → job sent to CUPS            → "Druckauftrag gesendet"   (or "Warten – noch N Drucke vor dir")
  → ESP32: 1st rear pass        → "Druck gestartet" / "Gelb wird gedruckt (1/4)"
  → ESP32: 2nd rear pass        → "Magenta wird gedruckt (2/4)"
  → ESP32: 3rd rear pass        → "Cyan wird gedruckt (3/4)"
  → ESP32: 4th rear pass        → "Schutzschicht wird aufgetragen (4/4)"
  → ESP32: front eject          → "Fertig – bitte Foto entnehmen"  → screen closes
At any point: CUPS printer error / ESP32 jam / sensor error → error screen, printing blocked
```

---

## 2. System Architecture

### 2.1 Logical architecture

```text
 ┌──────────── Booth (Photobooth web app on the event laptop) ────────────┐
 │  Print screen (UI)  ◄──  Print Status Tracker  ──►  Print Gate         │
 │                             ▲            ▲                             │
 │                 CUPS status interface   Sensor unit interface          │
 └─────────────────────────────┼────────────┼─────────────────────────────┘
                               │            │
                     CUPS + Gutenprint   ESP32 sensor unit (USB) ── IR sensors
                               │                     (rear exit, front eject)
                        SELPHY CP1300 (USB)
```

### 2.2 Hardware / platform

| Item | Fact | Source |
|---|---|---|
| Printer | Canon SELPHY CP1300, USB `04a9:32db`, dye-sublimation, 4 passes per print (Y, M, C, overcoat); paper leaves the **back** of the printer between passes and exits the **front** when finished | `[code]` / `[derived]` |
| Driver | CUPS queue `selphy`, `gutenprint.5.3://canon-cp1300`, `PageSize=Postcard`, `StpBorderless=True` | `[code]` photobooth-setup |
| Booth host | Acer Switch 3, Debian 12, native Photobooth install | `[code]` |
| Sensor unit | ESP32 connected to the booth host over USB, with at least one IR sensor at the rear paper exit and one at the front output | `[user]` |
| Shared USB hub | Printer and camera share one USB hub on the laptop; adding the ESP32 there is a risk (§4.1 R6) | `[code]` |

### 2.3 Software architecture

**Deliberately open.** Which process reads CUPS, where the tracker runs, how the UI gets updates, and the ESP32 wire protocol are HOW decisions, to be made after this FSD is accepted. `[user]` This FSD only fixes the observable behaviour and the information each interface must deliver.

### 2.4 Component layering

```text
L2 Application  │ Print Status Tracker (§5) │ Print Gate (§6) │ Print Screen (§7) │ Supply Counters (§8, Phase 3)
────────────────┼──────────────────────────────────────────────────────────────────────────────
L1 Interfaces   │ CUPS Status Interface (§9) │ Sensor Unit Interface (§10) │ ESP32 Sensor Unit (§11)
────────────────┼──────────────────────────────────────────────────────────────────────────────
L0 Foundation   │ CUPS + Gutenprint (§12) │ USB host link (§12)
```

Dependencies point downward only. The ESP32 firmware (§11) is listed under L1 because the booth sees it as an external black box; its internal requirements are the externally observable ones only.

---

## 3. Implementation Phases

| Phase | Scope | Exit criteria | Depends on |
|---|---|---|---|
| **1 — True CUPS status** | §5 (CUPS-only stages), §6, §7, §9 | A guest-visible print never shows "Fertig" based only on CUPS queue acceptance; every error in Appendix B produces its German message and blocks printing; printing resumes automatically after the fix; queued jobs show their position. Verified on the real SELPHY. | existing CUPS setup |
| **2 — Sensor unit** | §5 (layer stages), §10, §11 | All stages of §1.5 shown from real sensor events; jam and sensor-error handling per FR-11.4–11.6; CUPS-only fallback when the unit is disconnected. Timing parameters in Appendix A measured and filled in. | Phase 1 (screen, gate, error handling), ESP32 hardware built |
| **3 — Supply counters** | §8 | Paper/ink low warnings and reset after refill. | Phase 1 (gate) |

Phase 1 is usable on its own (the booth stays honest without the ESP32); Phase 2 only adds detail to stages Phase 1 already displays.

---

## 4. Risks, Assumptions & Dependencies

### 4.1 Risks

| ID | Risk | Mitigation |
|---|---|---|
| R1 | `Paper Feed (02)` is reported both for an empty tray and a misfeed `[code]` — the message cannot be more specific than "Papier leer oder Papiereinzug gestört". | Message text covers both (Appendix B). |
| R2 | CUPS reports *completed* ~17 s in and the printer status query can fail while the printer is busy `[code]`. | CUPS *completed* is never shown as "Fertig" (FR-5.6); status read failures are not errors (FR-9.4). |
| R3 | If staff press the printer's own button after fixing paper, the buffered sheet prints while CUPS still holds the job → duplicate after resume `[code]`. | Error screen tells staff not to press the printer button (FR-7.7). |
| R4 | IR sensor misreads (dust, a hand, a cable blocking the sensor). `[user]` | Jam is only declared after the front sensor confirms nothing came out (FR-11.4–11.6). |
| R5 | Pass timing differs per printer/temperature; wrong timeouts cause false jams or late detection. | Timeouts measured on the real printer before Phase 2 exits (Appendix A). |
| R6 | ESP32 on the same USB hub as printer and camera; unexplained freezes at the 2026-11-22 event. | Out of this FSD's control; see event findings. Sensor unit disconnect is handled (FR-10.3). |

### 4.2 Assumptions

- A1 The SELPHY always makes exactly 4 rear passes per print, in the order yellow, magenta, cyan, overcoat. (assumed — to verify with the sensor unit)
- A2 Only one printer (`selphy`) is used. (assumed)
- A3 The booth host's clock is the time reference for all timeouts. (assumed)

### 4.3 Dependencies

- photobooth-setup `native/setup-printer.sh` (queue options) and the queue-resume mechanism (`selphy-autoresume`, currently disabled by default). FR-6.4 needs *some* automatic resume; which mechanism is HOW.

### 4.4 Open questions

| ID | Question |
|---|---|
| Q1 | How is a "Sensorfehler" block (FR-11.6) cleared — automatically on the next good print, by staff on the booth, or by power-cycling the sensor unit? |
| Q2 | Sensor events while no job is printing (e.g. someone waves at a sensor): ignore and log, or treat as sensor error? (assumed: ignore + log) |
| Q3 | Fallback mode (no ESP32): what does the screen show after CUPS *completed*, and when does it close? Proposed in FR-5.7. |
| Q4 | Paper tray capacity and prints per ink cassette for Phase 3 (paper-ink-counter.md says ~18 sheets and 56 prints; both unconfirmed). |
| Q5 | Should the guest's own print screen be dismissable while waiting in the queue? (assumed: yes, the job stays queued) |

---

# Part A — Application Logic (L2)

## 5. Print Status Tracker

### 5.1 Purpose

Determines, for every print job, the stage shown to the guest, from CUPS job/printer status (§9) and sensor events (§10).

### 5.2 Job state model

| State | Shown as (Appendix B) | Entered on |
|---|---|---|
| `QUEUED` | Warten – noch N Drucke vor dir | job accepted by CUPS while another job is ahead |
| `SENT` | Druckauftrag gesendet | job accepted by CUPS and first in line |
| `LAYER_1`…`LAYER_4` | Druck gestartet / Gelb / Magenta / Cyan / Schutzschicht | 1st…4th rear pass (sensor) |
| `PRINTING_CUPS` | Wird gedruckt … | CUPS *processing* while the sensor unit is offline (fallback) |
| `HANDED_OVER` | Druck wird fertiggestellt … | CUPS *completed* while the sensor unit is offline (fallback) |
| `DONE` | Fertig – bitte Foto entnehmen | front eject (sensor) |
| `ERROR` | error text (Appendix B) | printer error, jam, sensor error, stall |
| `CANCELLED` | Druck abgebrochen | job cancelled in CUPS |

Transitions (normative):

| From | Event | Guard | To |
|---|---|---|---|
| — | CUPS accepted job | other jobs ahead | `QUEUED` |
| — | CUPS accepted job | none ahead | `SENT` |
| `QUEUED` | job ahead finished/cancelled | now first | `SENT` |
| `SENT` | rear pass | sensor online | `LAYER_1` |
| `LAYER_n` (n<4) | rear pass | — | `LAYER_n+1` |
| `LAYER_4` | front eject | — | `DONE` |
| `SENT` | CUPS processing | sensor offline | `PRINTING_CUPS` |
| `PRINTING_CUPS` | CUPS completed | — | `HANDED_OVER` |
| any non-final | printer error / jam / sensor error / stall | — | `ERROR` |
| `ERROR` | printer ready again, job still in CUPS | — | `SENT` |
| any non-final | CUPS job cancelled/aborted | — | `CANCELLED` |
| `LAYER_n` | CUPS completed | — | unchanged (ignored, R2) |
| `DONE`, `CANCELLED` | any | — | ignored (final) |

### 5.3 Requirements

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-5.1 | Must | The tracker shall assign `SENT` (or `QUEUED`) only after CUPS has accepted the job; a job CUPS refused shall go to `ERROR`. `[user]` | approved |
| FR-5.2 | Must | With the sensor unit online, the n-th rear pass of the current job shall set `LAYER_n` (n = 1…4). `[user]` | approved |
| FR-5.3 | Must | With the sensor unit online, a front-eject event after `LAYER_4` shall set `DONE`. `[user]` | approved |
| FR-5.4 | Must | With the sensor unit online, the tracker shall not set `DONE` from any CUPS event. `[derived]` R2 | approved |
| FR-5.5 | Must | If a stage lasts longer than its limit in Appendix A, the tracker shall set `ERROR` with cause *stall*. `[user]` | approved (limits open) |
| FR-5.6 | Must | CUPS job state *completed* shall never by itself produce the text "Fertig". `[derived]` R2 | approved |
| FR-5.7 | Should | With the sensor unit offline, CUPS *completed* shall set `HANDED_OVER` and the screen shall close {{T_handover_close}} later. | proposed (Q3) |
| FR-5.8 | Must | The queue position N shown in `QUEUED` shall equal the number of booth print jobs in CUPS ahead of this job. `[user]` | approved |

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| FR-5.1 | Queue `selphy` rejecting (`cupsreject`) · guest presses Drucken | `ERROR` with CUPS reason | "Druckauftrag gesendet" shown | integration |
| FR-5.2 | Sensor online, job `SENT` · inject 1,2,3,4 rear passes | `LAYER_1`…`LAYER_4` in order | stage skipped or repeated | unit |
| FR-5.3 | `LAYER_4` · inject front eject | `DONE` | `DONE` before `LAYER_4` | unit |
| FR-5.4 | Sensor online, `LAYER_2` · CUPS reports completed | state stays `LAYER_2` | `DONE` | unit |
| FR-5.5 | `SENT` · no rear pass for > limit | `ERROR`/stall within limit +2 s | stall before the limit | unit |
| FR-5.6 | any mode · CUPS completed | no "Fertig" text | "Fertig" | unit |
| FR-5.8 | 2 jobs in CUPS · 3rd guest prints | 3rd guest sees "noch 2 Drucke vor dir" | wrong count | integration |

### 5.4 Error-resume contract (full form)

```yaml
id: FR-5.2/6.4 resume after paper error
verification:
  preconditions:
    - Sensor unit online. Paper tray holds 1 sheet. Two prints requested.
  stimulus:
    - First print completes; second print hits "Paper Feed (02)".
    - Staff refill paper (without pressing the printer's own button).
  expected_observations:
    - Second job goes SENT → ERROR (Papier leer …) → SENT → LAYER_1…4 → DONE.
    - Exactly two photos come out of the printer in total.
  timing: resume starts within {{T_resume}} of the printer reporting ready
  prohibited_outcomes:
    - A third (duplicate) photo is printed.
    - The second job is lost or must be re-requested by the guest.
    - Staff must act on the booth screen to resume.
  tier: bench (real SELPHY)
  evidence: CUPS job log, tracker log, photo of the output tray
  cleanup: cancel remaining jobs, refill paper
```

## 6. Print Gate

### 6.1 Purpose

Decides whether the booth accepts new print requests.

### 6.2 Requirements

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-6.1 | Must | While any blocking condition holds (printer error, jam, sensor error, stall), the booth shall reject new print requests and show the Print button disabled with the error text. `[user]` | approved |
| FR-6.2 | Must | While printing is blocked, taking photos, showing the result and the QR code shall keep working. `[user]` | approved |
| FR-6.3 | Must | Jobs already queued when the block starts shall stay queued, not be cancelled. `[user]` | approved |
| FR-6.4 | Must | When CUPS reports the printer ready again and no sensor error is active, the gate shall lift the block and queued jobs shall resume without staff action on the booth. `[user]` | approved |
| FR-6.5 | Must | A sensor-unit disconnect alone shall not block printing (fallback, FR-10.3). `[user]` | approved |
| FR-6.6 | Must | A sensor error (FR-11.6) shall block printing until cleared per Q1. `[user]` | approved (clearing open) |
| FR-6.7 | Must | While a print is in progress, new print requests shall be accepted and queued. `[user]` | approved |

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| FR-6.1 | Printer reports `Ink Cassette Empty` · guest opens result screen | Print button disabled, "Farbkassette leer" | job reaches CUPS | integration |
| FR-6.2 | Printing blocked · guest takes a photo | photo + QR shown | photo flow blocked | integration |
| FR-6.3 | 2 jobs queued · printer error | both still in CUPS | jobs cancelled | integration |
| FR-6.4 | see §5.4 | | | bench |
| FR-6.5 | Unplug sensor unit · guest prints | print accepted, fallback stages | Print button disabled | bench |
| FR-6.7 | Job printing · 2nd guest presses Drucken | accepted, `QUEUED` | "Drucker beschäftigt" rejection | integration |

## 7. Print Screen

### 7.1 Requirements

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-7.1 | Must | After the guest presses Drucken, the print screen shall stay up and show the current stage of that guest's job until `DONE`, `CANCELLED`, or the guest dismisses an error. `[user]` | approved |
| FR-7.2 | Must | Stage changes shall appear on screen within {{T_ui}} of the event reaching the booth. | approved (value open) |
| FR-7.3 | Must | All texts shall be German. `[user]` | approved |
| FR-7.4 | Must | Layer stages shall be named yellow, magenta, cyan and **Schutzschicht** (clear coat); the 4th stage shall never be called black/"Schwarz"/"K". `[user]` | approved |
| FR-7.5 | Must | For a known error (Appendix B) the screen shall show its German cause and what staff should do. `[user]` | approved |
| FR-7.6 | Must | For an unrecognised CUPS error the screen shall show "Druckerproblem – bitte Personal rufen" large, and the exact CUPS message small underneath. `[user]` | approved |
| FR-7.7 | Must | Error screens for paper/ink errors shall tell staff not to press the printer's own button. `[derived]` R3 | approved |
| FR-7.8 | Must | With the sensor unit offline, the print screen shall show a small staff notice "Sensor offline". `[user]` | approved |
| FR-7.9 | Should | Exact German wording per Appendix B. | proposed |

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| FR-7.1 | Sensor online · full print | screen shows every stage, closes after "Fertig" | screen closes before `DONE` | bench |
| FR-7.4 | `LAYER_4` | "Schutzschicht" | "Schwarz", "K" | unit |
| FR-7.6 | CUPS reason `Unknown Error (08)` | generic text + "Unknown Error (08)" | blank or only "Fehler" | unit |
| FR-7.8 | Unplug sensor unit · print | notice "Sensor offline" visible | no notice | bench |

## 8. Supply Counters (Phase 3)

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-8.1 | Should | The booth shall count completed prints against a paper count and an ink count. `[user]` | approved |
| FR-8.2 | Should | When either count reaches {{LOW_THRESHOLD}} the print screen shall show a staff warning ("Papier fast leer" / "Farbkassette fast leer"). | proposed |
| FR-8.3 | Should | Staff shall be able to reset each count separately after refilling, entering the number of sheets loaded. `[derived]` paper-ink-counter.md | proposed |
| FR-8.4 | May | At zero, printing shall be blocked as in FR-6.1. | proposed |

Capacities: Q4. Contracts to be written when Phase 3 starts.

---

# Part B — Interfaces (L1)

## 9. CUPS Status Interface

### 9.1 Purpose

Delivers, for the `selphy` queue: whether a job was accepted, each job's state (pending / processing / completed / cancelled / aborted), the queue order, and the printer's state and error message.

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-9.1 | Must | Shall report job acceptance or refusal for each print request. | approved |
| FR-9.2 | Must | Shall report printer errors with the CUPS/Gutenprint message text (e.g. `Paper Feed (02)`), including when the backend has stopped the queue. `[code]` | approved |
| FR-9.3 | Must | Shall distinguish completed, cancelled and aborted jobs (not `lpstat -W completed`, which lists cancelled jobs too). `[code]` | approved |
| FR-9.4 | Must | A failed printer status query while a job is processing shall not be reported as a printer error. `[code]` R2 | approved |
| FR-9.5 | Must | A printer error shall reach the tracker within {{T_cups_poll}}. | approved (value open) |

## 10. Sensor Unit Interface (booth side)

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-10.1 | Must | Shall deliver the events: rear pass, front eject, jam, sensor error, and a liveness signal. `[user]` | approved |
| FR-10.2 | Must | Shall detect the sensor unit as offline when no liveness signal arrives for {{T_alive}}. | approved (value open) |
| FR-10.3 | Must | When offline, the tracker shall use CUPS-only stages (`PRINTING_CUPS`, `HANDED_OVER`) for the current and following jobs. `[user]` | approved |
| FR-10.4 | Must | When the unit comes back online, the next job shall use sensor stages again. `[derived]` | approved |
| FR-10.5 | Should | Sensor events with no job in progress shall be logged and otherwise ignored. | proposed (Q2) |

```yaml
id: FR-10.3 fallback on disconnect mid-print
verification:
  preconditions: [Sensor unit online, job in LAYER_2]
  stimulus: [Unplug the sensor unit USB cable]
  expected_observations:
    - Within T_alive the screen shows "Sensor offline" and CUPS-only stages.
    - The print still comes out; the next print is accepted.
  prohibited_outcomes:
    - Printing becomes blocked.
    - The screen shows "Fertig" based on CUPS completed.
    - The booth hangs on LAYER_2 forever.
  tier: bench
  evidence: tracker log, screen recording
  cleanup: re-plug sensor unit
```

## 11. ESP32 Sensor Unit

### 11.1 Purpose

Watches the paper path with IR sensors at the rear exit and front output of the SELPHY and reports events to the booth. Hardware, firmware structure and wire protocol: later. `[user]`

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-11.1 | Must | Shall report one *rear pass* event each time paper leaves the back of the printer during a print pass. `[user]` | approved |
| FR-11.2 | Must | Shall report one *front eject* event when the finished photo exits the front. `[user]` | approved |
| FR-11.3 | Must | Shall send a liveness signal at least every {{T_alive}}/2. | proposed |
| FR-11.4 | Must | When the rear sensor stays blocked longer than {{T_rear_block}}, or the front eject does not follow the 4th rear pass within {{T_eject}}, the unit shall treat it as a *suspected jam* and wait {{T_confirm}} for a front eject. `[user]` | approved (values open) |
| FR-11.5 | Must | If no front eject occurs within {{T_confirm}} after a suspected jam, the unit shall report *jam*. `[user]` | approved |
| FR-11.6 | Must | If a front eject does occur within {{T_confirm}} after a suspected jam, the unit shall report *sensor error* (a misread: something blocked the sensor). The booth shall then block printing with "Sensorfehler". `[user]` | approved |

```yaml
id: FR-11.6 misread is not reported as jam
verification:
  preconditions: [Sensor unit online, job printing LAYER_2]
  stimulus: [Hold an object in front of the rear sensor for longer than T_rear_block while the print continues normally]
  expected_observations:
    - The photo exits the front within T_confirm.
    - The unit reports "sensor error"; the booth shows "Sensorfehler" and blocks new prints.
  prohibited_outcomes:
    - "Papierstau" is shown.
    - The current job is cancelled.
  tier: bench
  evidence: unit serial log, tracker log, screen recording
  cleanup: clear the sensor error (Q1)
```

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| FR-11.1 | One normal print | exactly 4 rear-pass events | 3 or 5 events | bench |
| FR-11.2 | One normal print | exactly 1 front-eject event | eject reported during a pass | bench |
| FR-11.5 | Hold paper back during pass 2 (provoked jam) | "jam" after T_confirm; "Papierstau" on screen | "Sensorfehler" | bench |

---

# Part C — Foundation (L0)

## 12. CUPS + Gutenprint, USB host link

Configured, not owned: the `selphy` queue (photobooth-setup `native/setup-printer.sh`), the Gutenprint `canon-cp1300` backend, USB enumeration of printer and sensor unit. Tested transitively through §9 and §10.

| ID | Pri | Requirement | Status |
|---|---|---|---|
| NFR-12.1 | Must | Queue options stay identical between Docker (`cups/entrypoint.sh`) and native (`setup-printer.sh`). `[code]` | approved |
| NFR-12.2 | Must | Some mechanism shall re-enable the queue after the backend stops it (needed by FR-6.4). `[code]` | approved |

---

# Part D — Cross-cutting

## 13. Logging

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-13.1 | Must | Every stage change and error shall be logged with timestamp, job ID and source (CUPS/sensor/timeout). | approved |
| FR-13.2 | Should | Logs shall let staff reconstruct after an event how many photos were requested, printed, and failed. `[derived]` event findings | proposed |

## 14. Security

Threat model: physical booth at a private event; sensor unit on local USB; no network exposure added. Accepted risk: someone with physical access can fake sensor events. No requirements derived. (assumed)

---

# Part E — Operations & Verification

## 15. Operational procedures (reading path)

Setup printer (§12) → plug in sensor unit (§11) → guest prints (§5, §7) → error appears (§6, Appendix B) → staff fix paper/ink/jam without pressing the printer button (FR-7.7) → automatic resume (FR-6.4).

## 16. Verification & Validation

### 16.0 Test architecture

| Tier | Where | Covers |
|---|---|---|
| unit | CI, no hardware | §5 state model, §7 text mapping, §11 jam logic as pure functions |
| integration | Docker stack (photobooth-setup) with dummy camera, CUPS queue controllable by `cupsreject`/`cupsdisable`, simulated sensor events | §6, §9, §10 |
| bench | Event laptop + real SELPHY + real sensor unit | §5.4, FR-10.3, FR-11.x, Appendix A measurements |

Traceability is generated from the requirement IDs in this document and the test plan (`testing/test-plan.yaml`, owned by `/build`) — not maintained by hand here.

### 16.1 GitHub Actions plan

- Fork CI: build + lint on push to `booth`; disable irrelevant upstream workflows (crowdin, docs, release).
- Unit tests for the tracker, gate, text mapping and jam logic on every push.
- Prebuilt release package so the laptop needs no npm/gulp build.
- Weekly upstream-merge drift check that opens an issue.
- photobooth-setup: end-to-end Docker stack test with dummy camera and dummy printer, including a simulated printer error and simulated sensor events; shellcheck for `native/*.sh`.
- **Not possible in Actions:** deploy to the (usually off) laptop; anything with the real SELPHY or sensor unit — those are bench tests, run manually before an event.

### 16.2 Acceptance (before the next event)

1. 10 consecutive prints with the sensor unit: every stage shown, 10 photos out, no false jam/sensor error.
2. Each error in Appendix B provoked once on the real printer (empty tray, ink cassette removed, provoked jam): correct text, printing blocked, photo/QR still work, automatic resume, no duplicate.
3. Sensor unit unplugged mid-print: fallback per FR-10.3.

---

## Appendix A — Timing parameters (all open, to be measured on the real SELPHY)

| Parameter | Meaning | Value |
|---|---|---|
| `T_sent_to_pass1` | max time from SENT to first rear pass (stall limit) | TBD |
| `T_pass` | max time between rear passes (stall limit) | TBD |
| `T_rear_block` | rear sensor blocked longer than this → suspected jam | TBD |
| `T_eject` | max time from 4th rear pass to front eject | TBD |
| `T_confirm` | wait for a front eject after a suspected jam | TBD |
| `T_alive` | sensor unit considered offline after this silence | TBD |
| `T_cups_poll` | max delay for a CUPS error to reach the tracker | TBD |
| `T_ui` | max delay from event to screen | TBD |
| `T_resume` | max delay from printer ready to job resuming | TBD |
| `T_handover_close` | fallback: screen close after CUPS completed | TBD |
| `T_cups_completed` | observed: CUPS marks job completed ~17 s in `[code]` | ~17 s (observed) |

## Appendix B — Messages (German; wording `proposed`)

**Stages**

| State | Text |
|---|---|
| `QUEUED` | Warten – noch N Druck(e) vor dir |
| `SENT` | Druckauftrag gesendet |
| `LAYER_1` | Druck gestartet – Gelb wird gedruckt (1/4) |
| `LAYER_2` | Magenta wird gedruckt (2/4) |
| `LAYER_3` | Cyan wird gedruckt (3/4) |
| `LAYER_4` | Schutzschicht wird aufgetragen (4/4) |
| `PRINTING_CUPS` | Wird gedruckt … |
| `HANDED_OVER` | Druck wird fertiggestellt – dein Foto kommt gleich |
| `DONE` | Fertig – bitte Foto entnehmen |
| `CANCELLED` | Druck abgebrochen |

**Errors**

| Source | Trigger | Guest text | Staff hint |
|---|---|---|---|
| CUPS | `Paper Feed (02)` `[code]` | Papier leer oder Papiereinzug gestört | Papierkassette prüfen/auffüllen. Nicht die Taste am Drucker drücken. |
| CUPS | `Ink Cassette Empty (06)` `[code]` | Farbkassette leer | Farbkassette tauschen. Nicht die Taste am Drucker drücken. |
| CUPS | `No Paper and Ink (09)` `[code]` | Papier- oder Farbkassette fehlt | Beide Kassetten prüfen. Nicht die Taste am Drucker drücken. |
| CUPS | `Paper jam (0b)` `[code]` | Papierstau | Papier vorsichtig entfernen. |
| CUPS | printer not connected / not found (assumed) | Drucker nicht verbunden | USB-Kabel und Strom prüfen. |
| CUPS | any other, e.g. `Unknown Error (08)` `[code]` | Druckerproblem – bitte Personal rufen | *(raw CUPS message)* |
| Sensor | jam (FR-11.5) | Papierstau | Papier vorsichtig entfernen. |
| Sensor | sensor error (FR-11.6) | Sensorfehler – bitte Personal rufen | Sensor auf Verdeckung prüfen. |
| Tracker | stall (FR-5.5) | Drucker reagiert nicht – bitte Personal rufen | Drucker und Kabel prüfen. |
| Sensor | offline (FR-7.8) | *(no guest text)* | Sensor offline |

## Related

- photobooth-setup: `event-2026-11-22-findings.md`, `paper-ink-counter.md`, `native/setup-printer.sh`, `native/selphy-autoresume.sh`, `cups/entrypoint.sh`
- This fork: `api/print.php`, `src/Service/PrintManagerService.php` (current print path, for reference only)
