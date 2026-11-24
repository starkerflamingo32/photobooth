# Photobooth Booth — Functional Specification Document (FSD)

**Scope of this FSD:** a custom print stack for the booth: every photo printed exactly once, one print at a time, and a truthful print screen (SELPHY CP1300 via CUPS, plus an ESP32 unit with IR barriers on the paper path and an operator panel inside the booth).
**Fork:** starkerflamingo32/photobooth, branch `booth` (based on upstream `dev` fdb58c0, 4.99.0).
**Status:** DRAFT — requirements only. Implementation and code architecture are deliberately not decided yet (§2.3), except the decisions listed there.

| Lifecycle | |
|---|---|
| Created | 2026-11-23 |
| Last updated | 2026-11-24 |
| Load defined | **No** — bench session B-1 not run (§17.3), timing parameters open (Appendix A), open decisions (§4.4), Method/UserDocumentation planes not created yet |

Full lifecycle metadata: Appendix D.

**Provenance tags:** `[user]` decided by the booth owner (interviews 2026-11-23/24 or earlier notes) · `[code]` observed in code, logs or on the real printer · `[derived]` follows from a `[user]`/`[code]` item · `(assumed)` inferred, must be confirmed.
**Status values:** `approved` (user decided) · `proposed` (written by the assistant, awaiting acceptance) · `superseded` (listed in Appendix D only).

---

## 1. System Overview

### 1.1 Purpose / problem

Today the booth reports a print as successful as soon as CUPS has **received** the job `[code]`:

- `api/print.php` returns `{"status":"ok"}` once the job is queued, not when paper comes out.
- CUPS marks the SELPHY job *completed* ~17 s in, long before the printer is finished. Cause: the Gutenprint backend (`gutenprint53+usb`, Gutenprint 5.3.4) runs in *fast return* mode by default under CUPS and exits as soon as the printer starts printing. After that, CUPS sees no error of that print at all. `[code]` (§9, R2)
- On paper/ink errors the backend **stops the queue** and leaves the job pending; the guest still saw "success". At the 2026-11-22 event the queue stopped 4× (`Paper Feed (02)` ×5, `Ink Cassette Empty (06)`, `Unknown Error (08)`), see photobooth-setup `event-2026-11-22-findings.md`.
- Re-enabling a stopped queue sends the pending job again; if the printer had already printed it, the guest gets a duplicate. (R3)
- Print buttons on pages loaded earlier (result screen, gallery) can still send print requests while the printer is busy. `[user]`

The goal: each photo is printed exactly once, only one print runs at a time, and the print screen says what is really happening, using (a) CUPS printer and job status and (b) events from the ESP32 unit watching the paper path. `[user]`

### 1.2 Users

| Who | Where | Needs |
|---|---|---|
| Guest | In front of the booth screen | Knows whether their photo is printing, how far along it is, and when to take it. German only. `[user]` |
| Operator (staff) | Inside the booth, with the back opened to check or fix the printer, using the operator panel (§12) `[user]` | Sees what is wrong, fixes the printer, tells the booth the problem is fixed and whether the photo came out. |

### 1.3 Goals

- G0 **Every requested copy is printed exactly once**, whatever happens (printer error, jam, delay, booth restart). `[user]`
- G1 Only one print request runs at a time, on every page that can print (result screen, gallery), including pages loaded before the print started. `[user]`
- G2 The print screen shows the real stage, from "Druckauftrag gesendet" to "Fertig – bitte Foto entnehmen". `[user]`
- G3 Printer errors come from CUPS and are shown with a specific cause or the raw CUPS message; IR-barrier observations are shown as information only. `[user]`
- G4 After the operator confirms a fix on the operator panel, printing continues without staff action on the booth screen. `[user]`
- G5 (later phase) Paper/ink counters and print limit, after the built-in print limit has been tested in the virtual test setup. `[user]`

### 1.4 Non-goals

- Choosing implementation, code structure, ESP32 wire protocol, sensor hardware or mounting — decided later. `[user]`
- Languages other than German. `[user]`
- Supporting printers other than the SELPHY CP1300. (assumed)
- Changing how photos are taken, uploaded or shared.
- The built-in print limit and auto-print (`print.auto_delay`): out of scope until their current behaviour has been tested in the virtual test setup (§17.1); they do not work correctly today. `[user]`

### 1.5 High-level flow

```text
Guest presses "Drucken" (N copies)
  → booth busy?  yes → "Drucker beschäftigt" (no job created)
  → copy k sent to CUPS          → "Druckauftrag gesendet (Kopie k von N)"
  → rear pass 1…4 (IR barrier)   → Gelb / Magenta / Cyan / Schutzschicht (1/4…4/4)
  → front eject (IR barrier)     → copy k done; next copy sent, or "Fertig – bitte Foto entnehmen"
CUPS printer error at any point  → error screen + operator panel; queue stopped by the backend
  → operator fixes the printer and presses the printer's own print button (prints the waiting photo)
  → operator confirms on the panel: "Foto ist da" / "Foto fehlt"
  → booth reconciles the CUPS job (no duplicate), re-enables the queue, continues with copy k+1
IR barrier reports jam / misread → information on the print screen and the panel, not an error
```

---

## 2. System Architecture

### 2.1 Logical architecture

```text
 ┌──────────────── Booth (Photobooth web app on the event laptop) ─────────────────┐
 │  Print screen + print buttons (UI) ◄── Print Status Tracker ──► Busy Gate        │
 │                                          ▲            ▲                          │
 │                           CUPS status interface   Unit interface                 │
 └──────────────────────────────────────────┼────────────┼──────────────────────────┘
                                            │            │ USB
                                  CUPS + Gutenprint   ESP32 unit ── IR barriers (rear exit, front eject)
                                            │            └──────── operator panel: rotary encoder + I²C OLED
                                     SELPHY CP1300 (USB)            (inside the booth, at the back)
```

### 2.2 Hardware / platform

| Item | Fact | Source |
|---|---|---|
| Printer | Canon SELPHY CP1300, USB `04a9:32db`, dye-sublimation, 4 passes per print (Y, M, C, overcoat); paper leaves the **back** of the printer between passes and exits the **front** when finished | `[code]` / `[derived]` |
| Driver | CUPS queue `selphy`, device `gutenprint53+usb://canon-cp1300/…`, `gutenprint.5.3://canon-cp1300`, `PageSize=Postcard`, `StpBorderless=True` | `[code]` photobooth-setup, laptop |
| Backend | Gutenprint 5.3.4 (Debian 12) `backend_canonselphyneo`: under CUPS *fast return* is on unless `FAST_RETURN` is set; on most printer errors it stops the queue and leaves the job pending (`CUPS_BACKEND_STOP`), on status `0x0A` it cancels the job (`CUPS_BACKEND_CANCEL`) | `[code]` Gutenprint source (master; same switch in the installed binary) |
| Booth host | Acer Switch 3, Debian 12, native Photobooth install | `[code]` |
| ESP32 unit | Connected to the booth host over USB; IR barriers at the rear paper exit and the front output; operator panel with a rotary encoder and an I²C OLED, mounted inside the booth for use with the back opened | `[user]` |
| Shared USB hub | Printer and camera share one USB hub on the laptop; adding the ESP32 there is a risk (R6) | `[code]` |

### 2.3 Software architecture

**Mostly open.** Which process reads CUPS, where the tracker runs, how the UI gets updates, and the ESP32 wire protocol are HOW decisions, to be made after this FSD is accepted. `[user]` This FSD fixes the observable behaviour and the information each interface must deliver.

Decided:

- Each copy of a photo is its own CUPS job; copies are sent one after another. `[user]`
- The guest chooses the number of copies in upstream's existing copies dialog (FR-7.16); only its UI is reused, the backend path is open. `[user]`
- The Gutenprint backend runs with fast return **off** (`FAST_RETURN=0`), so printer errors during a print reach CUPS (NFR-13.2). `[derived]` R2 — to be confirmed by bench session B-1 (§17.3).
- The print stack is custom; upstream print-limit functions may be reused once tested (§1.4). `[user]`

### 2.4 Component layering

```text
L2 Application  │ Print Status Tracker (§5) │ Busy Gate (§6) │ Print Screen (§7) │ Supply Counters (§8, later)
────────────────┼──────────────────────────────────────────────────────────────────────────────
L1 Interfaces   │ CUPS Status Interface (§9) │ Unit Interface (§10) │ ESP32 Sensor Unit (§11) │ Operator Panel (§12)
────────────────┼──────────────────────────────────────────────────────────────────────────────
L0 Foundation   │ CUPS + Gutenprint (§13) │ USB host link (§13)
```

Dependencies point downward only. The ESP32 firmware (§11, §12) is listed under L1 because the booth sees it as an external black box; only its externally observable behaviour is specified.

---

## 3. Implementation Phases

| Phase | Scope | Exit criteria | Depends on |
|---|---|---|---|
| **1 — Virtual test setup** | §17.1: simulated CUPS backend that can inject SELPHY errors and pending/stopped-queue states; simulated ESP32 unit incl. operator panel events | Every unit and integration test in this FSD can be run without hardware | — |
| **2 — Booth logic** | §5, §6, §7, §9, §10 against the virtual test setup | All unit and integration tests pass; exactly-once contracts (§5.4) pass in simulation | Phase 1 |
| **3 — ESP32 unit + bench session** | §11, §12; bench session B-1 (§17.3) | ESP32 unit built; B-1 run; B-Q1…B-Q6 answered; Appendix A filled; OD1 and OD6 decided | Phase 2, ESP32 hardware |
| **4 — Acceptance** | §17.4 on the real booth | Acceptance list passes | Phase 3 |
| **5 — Supply counters / print limit** | §8, print limit | Behaviour of the built-in print limit tested in the virtual test setup, then specified | Phase 1, 4 |

---

## 4. Risks, Assumptions & Dependencies

### 4.1 Risks

| ID | Risk | Mitigation |
|---|---|---|
| R1 | `Paper Feed (02)` is reported both for an empty tray and a misfeed `[code]` — the message cannot be more specific than "Papier leer oder Papiereinzug gestört". | Message text covers both (Appendix B). |
| R2 | With fast return on, CUPS marks the job *completed* ~17 s in and never sees an error during the rest of the print `[code]`. | `FAST_RETURN=0` (NFR-13.2); CUPS *completed* never shown as "Fertig" (FR-5.6); status read failures are not errors (FR-9.4). |
| R3 | After an error the backend leaves the job pending in a stopped queue. Pressing the printer's own print button prints the buffered photo; re-enabling the queue then sends the job again → duplicate. `[code]`/`[user]` | Reconcile before re-enabling (FR-6.4, FR-5.14); only the booth re-enables the queue (FR-6.11). Behaviour verified in B-1. |
| R4 | IR barrier misreads (dust, a hand, a cable). `[user]` | Barrier observations are information only, never errors (FR-5.10). |
| R5 | Pass timing differs per printer/temperature; wrong limits cause false stall information or late detection. | Limits measured in B-1 (Appendix A). |
| R6 | ESP32 on the same USB hub as printer and camera; unexplained freezes at the 2026-11-22 event. | Out of this FSD's control; see event findings. Unit dropout handled (FR-10.2, OD1). |
| R7 | With `FAST_RETURN=0` the backend polls the printer for the whole print; this might confuse the printer. | Five consecutive prints in B-1 without printer misbehaviour before the setting is kept (B-Q1). |
| R8 | A page loaded before a print started (result screen, gallery) still shows an active print button. `[user]` | Server-side gate on every print entry point (FR-6.8). |
| R9 | Backend status `0x0A` cancels the job instead of stopping the queue `[code]`; its meaning on the CP1300 is unknown, and the photo may be lost. | Record in B-1 if it occurs (B-Q6); OD6. |

### 4.2 Assumptions

- A1 The SELPHY always makes exactly 4 rear passes per print, in the order yellow, magenta, cyan, overcoat. (assumed — B-Q2)
- A2 Only one printer (`selphy`) is used. (assumed)
- A3 The booth host's clock is the time reference for all timeouts. (assumed)
- A4 After a printer error the printer has already received the job; fixing the problem and pressing the printer's own print button prints that photo. There is no way to clear a stuck printer without printing it. `[user]` (assumed — B-Q4)
- A5 Only one job is ever inside the printer at a time, because the booth sends the next copy only after the previous one is out (FR-5.11). `[derived]`
- A6 The fast-return behaviour read in the Gutenprint master source matches the installed 5.3.4. (assumed — B-Q1)

### 4.3 Dependencies

- photobooth-setup `native/setup-printer.sh` (queue options) and the CUPS `SetEnv FAST_RETURN 0` setting (NFR-13.2; which config file is not yet determined).
- `selphy-autoresume` must stay disabled: it re-enables the queue without reconciliation and would cause duplicates (FR-6.11).

### 4.4 Open decisions

| ID | Decision | Gates | Proposal |
|---|---|---|---|
| OD1 | ESP32 unit drops out (no liveness): block new prints, or continue on CUPS status alone? | FR-10.3, FR-6.1 | Decide after B-Q1 (is CUPS *completed* with `FAST_RETURN=0` close to the real eject?) |
| OD2 | IR events with no job in progress, a front eject before `LAYER_4`, a 5th rear pass: how shown? | FR-10.5, §5.2 | Log; show as sensor information on the panel; no state change. `proposed` |
| OD3 | Paper tray capacity and prints per ink cassette | §8 | paper-ink-counter.md says ~18 sheets and 56 prints; unconfirmed |
| OD4 | Booth page reload during a print (e.g. session reload): what does the screen show? | FR-7.12 | The print screen is restored from the tracker's state. `proposed` |
| OD5 | Guest presses a print button while busy: what is shown? | FR-6.10 | Short notice "Drucker beschäftigt – bitte warten, bis der aktuelle Druck fertig ist". `proposed` |
| OD6 | Job cancelled by the backend (status `0x0A`): resend the copy, or treat as failed? And after a cancelled print, does the booth go to the start screen (like FR-7.15) or back to the result view? | §5.2 (`CANCELLED`), FR-7.15 | Decide after B-Q6 has shown what `0x0A` means |
| OD8 | What the panel shows and allows while the booth is not reachable | FR-12.6 | "Booth nicht verbunden", no decisions possible. `proposed` |

---

# Part A — Application Logic (L2)

## 5. Print Status Tracker

### 5.1 Purpose

Determines, for every print request (one photo, N copies) and each copy's CUPS job, the stage shown to the guest and the operator, from CUPS job/printer status (§9), IR-barrier events and operator decisions (§10).

### 5.2 Job state model

States of the copy currently being printed. The request additionally carries "copy k of N".

| State | Shown as (Appendix B) | Entered on |
|---|---|---|
| `SENT` | Druckauftrag gesendet | CUPS accepted the copy's job |
| `LAYER_1`…`LAYER_4` | Gelb / Magenta / Cyan / Schutzschicht | 1st…4th rear pass |
| `DONE` | Fertig – bitte Foto entnehmen (last copy) | front eject after `LAYER_4`, or operator "Foto ist da" |
| `ERROR` | CUPS error text | CUPS printer error / queue stopped while this job is active; CUPS refused the job |
| `AWAIT_OPERATOR` | Druck wird geprüft – bitte Personal rufen | stall (FR-5.5); booth restart with an unfinished job (FR-5.13) |
| `CANCELLED` | Druck abgebrochen | job cancelled or aborted in CUPS |

Events: **E1** CUPS accepted · **E2** CUPS refused · **E3** rear pass · **E4** front eject · **E5** CUPS printer error / queue stopped · **E6** CUPS job completed · **E7** CUPS job cancelled/aborted · **E8** IR jam or misread report · **E9** stage limit exceeded (stall) · **E10** operator "Foto ist da" · **E11** operator "Foto fehlt" · **E12** booth restart.

Transitions (normative). "info" = shown as information on the print screen and panel, no state change. "log" = logged only. "evidence" = recorded for the operator panel (FR-5.14).

| State \ Event | E3 rear pass | E4 front eject | E5 CUPS error | E6 completed | E7 cancelled | E8 IR report | E9 stall | E10 Foto ist da | E11 Foto fehlt |
|---|---|---|---|---|---|---|---|---|---|
| `SENT` | → `LAYER_1` | log, info (OD2) | → `ERROR` | log | → `CANCELLED` | info | → `AWAIT_OPERATOR` | ignored (not offered) | ignored (not offered) |
| `LAYER_n` (n<4) | → `LAYER_n+1` | log, info (OD2) | → `ERROR` | log | → `CANCELLED` | info | → `AWAIT_OPERATOR` | ignored | ignored |
| `LAYER_4` | log, info (OD2, A1) | → `DONE` | → `ERROR` | log | → `CANCELLED` | info | → `AWAIT_OPERATOR` | ignored | ignored |
| `ERROR` | evidence | evidence | log | evidence | → `CANCELLED` | info | — | → `DONE` (reconcile, FR-6.4) | → `SENT` (reconcile, FR-6.4) |
| `AWAIT_OPERATOR` | evidence | evidence | → `ERROR` | evidence | → `CANCELLED` | info | — | → `DONE` (reconcile) | → `SENT` (reconcile) |
| `DONE` | log | log | (next copy or gate) | log | log | log | — | ignored | ignored |
| `CANCELLED` | log | log | (gate) | log | log | log | — | ignored | ignored |

Entry events: E1 creates a copy in `SENT`; E2 creates it in `ERROR`; E12 puts every unfinished copy found in CUPS into `AWAIT_OPERATOR` (FR-5.13). After `DONE` of copy k < N the tracker sends copy k+1 (FR-5.11). In `DONE`/`CANCELLED` an E5 belongs to no job and only affects the Busy Gate (§6).

### 5.3 Requirements

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-5.1 | Must | The tracker shall assign `SENT` only after CUPS has accepted the copy's job. `[user]` | approved |
| FR-5.9 | Must | A job CUPS refuses shall go to `ERROR` with the CUPS reason. `[user]` | approved |
| FR-5.2 | Must | The n-th rear pass of the current copy shall set `LAYER_n` (n = 1…4). `[user]` | approved |
| FR-5.3 | Must | A front eject after `LAYER_4` shall set `DONE`. `[user]` | approved |
| FR-5.4 | Must | No CUPS event shall set `DONE`. `[derived]` R2 | approved |
| FR-5.6 | Must | CUPS job state *completed* shall never by itself produce the text "Fertig". `[derived]` R2 | approved |
| FR-5.5 | Must | If a stage lasts longer than its limit in Appendix A, the tracker shall set `AWAIT_OPERATOR` with cause *stall*. `[user]` (stall is not a CUPS error, G3) | approved (limits open) |
| FR-5.10 | Must | An IR jam or misread report shall be shown as information and shall not set `ERROR`. `[user]` | approved |
| FR-5.11 | Must | Each copy shall be sent as its own CUPS job, and copy k+1 only after copy k is `DONE`. `[user]` | approved |
| FR-5.12 | Must | After an error on copy k is resolved, the remaining copies shall be sent without guest or staff action. `[user]` | approved |
| FR-5.13 | Must | After a booth restart, an unfinished job found in CUPS shall be held, set to `AWAIT_OPERATOR`, and not sent to the printer until the operator decides on the panel. `[user]` | approved |
| FR-5.14 | Must | Rear passes and front ejects received in `ERROR` or `AWAIT_OPERATOR` shall be recorded as evidence and shown on the operator panel. `[derived]` A4 | approved |
| FR-5.15 | Must | Every copy shall reach the printer's output exactly once: no requested copy missing, no copy printed twice. `[user]` G0 | approved |

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| FR-5.1 | Simulated CUPS accepts · guest presses Drucken | `SENT` after acceptance | `SENT` before acceptance | unit |
| FR-5.9 | Queue rejecting (`cupsreject`) · guest presses Drucken | `ERROR` with CUPS reason | "Druckauftrag gesendet" | integration |
| FR-5.2 | `SENT` · inject 1,2,3,4 rear passes | `LAYER_1`…`LAYER_4` in order | stage skipped or repeated | unit |
| FR-5.3 | `LAYER_4` · inject front eject | `DONE` | `DONE` before `LAYER_4` | unit |
| FR-5.4 | `LAYER_2` · CUPS completed | state stays `LAYER_2` | `DONE` | unit |
| FR-5.6 | any state · CUPS completed | no "Fertig" text | "Fertig" | unit |
| FR-5.5 | `SENT` · no rear pass for > limit | `AWAIT_OPERATOR`/stall within limit +2 s | stall before the limit; `ERROR` | unit |
| FR-5.10 | `LAYER_2` · inject IR jam report | info shown, state `LAYER_2` | `ERROR`; gate blocks for an error | unit |
| FR-5.11 | 3 copies · run copy 1 | only one CUPS job exists until copy 1 `DONE` | two jobs in CUPS at once | integration |
| FR-5.12 | 3 copies, `ERROR` on copy 2 · operator "Foto ist da" | copy 3 sent automatically | copy 3 lost or waits for staff | integration |
| FR-5.13 | Job pending in simulated CUPS · restart booth | `AWAIT_OPERATOR`, queue stays disabled | job sent to printer | integration |
| FR-5.14 | `ERROR` · inject front eject | panel shows "Foto erkannt" | eject ignored | unit |
| FR-5.15 | see §5.4 | | | integration + bench |

### 5.4 Exactly-once contracts (full form)

```yaml
id: FR-5.15/FR-6.4 photo printed after the printer's own button press
verification:
  preconditions:
    - ESP32 unit online. FAST_RETURN=0. One copy requested.
  stimulus:
    - The copy hits a CUPS printer error (e.g. "Paper Feed (02)"); the backend stops the queue, the job stays pending.
    - Operator fixes the printer and presses the printer's own print button; the photo comes out.
    - Operator selects "Foto ist da" on the panel.
  expected_observations:
    - The job goes SENT → ERROR → DONE.
    - The booth cancels the pending CUPS job before it re-enables the queue.
    - The queue is enabled again afterwards; the next print request is accepted.
    - Exactly one photo comes out in total.
  timing: queue enabled within {{T_resume}} of the operator's confirmation
  prohibited_outcomes:
    - A second (duplicate) photo is printed.
    - The queue is re-enabled while the printed job is still pending.
    - Staff must act on the booth screen.
  tier: integration (simulated CUPS) + bench (B-1)
  evidence: CUPS job log, tracker log, operator panel log, photo of the output tray
  cleanup: cancel remaining jobs, refill paper
```

```yaml
id: FR-5.12/FR-5.15 three copies with an error on copy 2
verification:
  preconditions:
    - ESP32 unit online. Three copies requested.
  stimulus:
    - Copy 1 prints normally. Copy 2 hits a CUPS printer error.
    - Operator fixes the printer, presses the printer's print button (copy 2 comes out), selects "Foto ist da".
  expected_observations:
    - Copy 2 → DONE without being resent; copy 3 is sent and prints.
    - The screen shows "Kopie 3 von 3" and finally "Fertig".
    - Exactly three photos come out.
  prohibited_outcomes:
    - Four photos, or two.
    - Copy 3 waits for staff action.
  tier: integration (simulated CUPS)
  evidence: CUPS job log, tracker log
  cleanup: cancel remaining jobs
```

## 6. Busy Gate

### 6.1 Purpose

Decides whether the booth accepts a new print request. There is no print queue: one request at a time. `[user]`

### 6.2 Requirements

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-6.1 | Must | While a print request is unfinished (its last copy not `DONE` or `CANCELLED`), the booth shall reject new print requests. `[user]` | approved |
| FR-6.12 | Must | While a CUPS printer error or `AWAIT_OPERATOR` is active, the booth shall reject new print requests. `[user]` | approved |
| FR-6.8 | Must | The rejection shall be enforced on the server for every print entry point (result screen, gallery, any API call), regardless of when the page was loaded. `[user]` | approved |
| FR-6.9 | Must | Print buttons on pages already open (result screen, gallery) shall show as disabled within {{T_ui}} of the booth becoming busy, and enabled again within {{T_ui}} of it becoming free. `[user]` | approved (value open) |
| FR-6.10 | Must | A rejected request shall create no CUPS job and shall show the guest the busy notice (OD5). `[user]` | approved (wording open) |
| FR-6.2 | Must | While printing is blocked by a CUPS printer error and no print request is unfinished, taking photos, showing the result and the QR code shall keep working. `[user]` (While a request is unfinished the print screen stays up, FR-7.14.) | approved |
| FR-6.4 | Must | On an operator decision for a job in `ERROR` or `AWAIT_OPERATOR`, the booth shall first make CUPS match it ("Foto ist da": cancel the job if still pending; "Foto fehlt": keep it), then re-enable the queue. `[user]` | approved |
| FR-6.11 | Must | Only the booth's reconciliation (FR-6.4) shall re-enable the CUPS queue; no other automatic `cupsenable` shall run. `[derived]` R3 | approved |
| FR-6.13 | Must | IR-barrier information alone shall not block printing. `[user]` | approved |

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| FR-6.1 | Print running · 2nd print request via API | rejected | 2nd CUPS job | integration |
| FR-6.12 | Simulated `Ink Cassette Empty` · print request | rejected | CUPS job created | integration |
| FR-6.8 | Gallery page loaded before the print started · press its print button while busy | rejected, notice shown | CUPS job created | integration |
| FR-6.9 | Gallery open · start a print elsewhere | gallery print buttons disabled within T_ui | button stays active | integration |
| FR-6.10 | Busy · press print | busy notice | CUPS job; silent nothing | integration |
| FR-6.2 | Printer error, no request unfinished · guest takes a photo | photo + QR shown | photo flow blocked | integration |
| FR-6.4 | see §5.4 | | | integration + bench |
| FR-6.11 | Queue stopped by simulated backend · wait 5 min | queue stays stopped until operator decision | queue re-enabled by anything else | integration |
| FR-6.13 | IR jam info active, no CUPS error, request finished · new print request | accepted | rejected | unit |

## 7. Print Screen

### 7.1 Requirements

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-7.1 | Must | After the guest presses Drucken, the print screen shall show the current stage of that request until the last copy is `DONE` or `CANCELLED`. `[user]` | approved |
| FR-7.14 | Must | While a print request is unfinished, the print screen shall offer no way to leave it, including during `ERROR` and `AWAIT_OPERATOR`; no photo can be started until it closes. `[user]` | approved |
| FR-7.15 | Must | When the print screen closes after the last copy is `DONE`, the booth shall show the guest start screen immediately; the result view of that photo, with its print button, shall not be shown again. `[user]` | approved |
| FR-7.16 | Must | With `print.max_multi` > 1, the guest shall choose the number of copies in upstream's existing copies dialog (minus/plus, 1…`print.max_multi`, "Drucken" / "Abbrechen"; `assets/js/tools.js` `askCopies`). How the chosen number reaches the print stack is not yet decided. `print.max_multi` stays an admin-panel setting, unchanged. `[user]` | approved |
| FR-7.17 | Must | Printing a photo again from the gallery shall stay possible, subject to the Busy Gate (§6); staff use it for reprints and can hide the gallery button in the admin panel. `[user]` | approved |
| FR-7.2 | Must | Stage changes shall appear on screen within {{T_ui}} of the event reaching the booth. | approved (value open) |
| FR-7.3 | Must | All texts shall be German. `[user]` | approved |
| FR-7.4 | Must | Layer stages shall be named yellow, magenta, cyan and **Schutzschicht** (clear coat); the 4th stage shall never be called black/"Schwarz"/"K". `[user]` | approved |
| FR-7.5 | Must | For a known CUPS error (Appendix B) the screen shall show its German cause. `[user]` | approved |
| FR-7.13 | Must | For a known CUPS error the screen shall show the staff hint from Appendix B. `[user]` | approved |
| FR-7.6 | Must | For an unrecognised CUPS error the screen shall show "Druckerproblem – bitte Personal rufen" large, and the exact CUPS message small underneath. `[user]` | approved |
| FR-7.7 | Must | Staff hints for printer errors shall say: fix the printer, press the printer's own print button, then confirm on the panel inside the booth. `[user]` A4 | approved |
| FR-7.10 | Must | With N > 1 copies the screen shall show "Kopie k von N". `[derived]` FR-5.11 | approved |
| FR-7.11 | Must | IR-barrier information (jam, misread) shall be shown as information, visually distinct from a printer error. `[user]` | approved |
| FR-7.8 | Must | With the ESP32 unit offline, the print screen shall show a small staff notice "Sensor offline". `[user]` | approved |
| FR-7.12 | Should | After a page reload during a print, the print screen shall show the request's current stage again. | proposed (OD4) |
| FR-7.9 | Should | Exact German wording per Appendix B. | proposed |

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| FR-7.1 | Full print | every stage shown, closes after "Fertig" | screen closes before `DONE` | integration |
| FR-7.14 | `ERROR` on copy 1 · tap everywhere on the screen, swipe, press keys | print screen stays; no photo starts | screen closes; start screen or photo countdown reachable | integration |
| FR-7.15 | Full print · "Fertig" screen closes | start screen shown at once | result view of that photo shown; its print button pressable | integration |
| FR-7.16 | `print.max_multi` = 3 · press Drucken, choose 2, confirm | 2 copies printed in sequence | more than 3 selectable; dialog skipped | integration |
| FR-7.16 | Copies dialog open · press Abbrechen | no print request | CUPS job created | integration |
| FR-7.17 | Photo printed before, booth idle · print it from the gallery | printed once more | print refused because printed before | integration |
| FR-7.4 | `LAYER_4` | "Schutzschicht" | "Schwarz", "K" | unit |
| FR-7.5 | CUPS reason `Ink Cassette Empty (06)` | "Farbkassette leer" | raw message only | unit |
| FR-7.6 | CUPS reason `Unknown Error (08)` | generic text + "Unknown Error (08)" | blank or only "Fehler" | unit |
| FR-7.7 | Any Appendix B printer error | hint mentions printer button and panel | "Nicht die Taste am Drucker drücken" | unit |
| FR-7.10 | 3 copies, copy 2 printing | "Kopie 2 von 3" | missing or wrong count | unit |
| FR-7.11 | IR jam info | info styling | error styling | unit |
| FR-7.8 | Simulated unit stops liveness | "Sensor offline" within T_alive | no notice | integration |

## 8. Supply Counters (later phase)

Deferred together with the print limit until the built-in print limit has been tested in the virtual test setup (§1.4, Phase 5). Earlier sketch: photobooth-setup `paper-ink-counter.md`; capacities OD3.

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-8.1 | Should | The booth shall count completed prints against a paper count and an ink count. `[user]` | approved |
| FR-8.2 | Should | When either count reaches {{LOW_THRESHOLD}} the print screen shall show a staff warning ("Papier fast leer" / "Farbkassette fast leer"). | proposed |
| FR-8.3 | Should | Staff shall be able to reset each count separately after refilling, entering the number of sheets loaded. `[derived]` paper-ink-counter.md | proposed |
| FR-8.4 | May | At zero, printing shall be blocked as in FR-6.12. | proposed |

---

# Part B — Interfaces (L1)

## 9. CUPS Status Interface

### 9.1 Purpose

Delivers, for the `selphy` queue: whether a job was accepted, each job's state (pending / processing / completed / cancelled / aborted), whether the queue is stopped, and the printer's state and error message.

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-9.1 | Must | Shall report job acceptance or refusal for each print request. | approved |
| FR-9.2 | Must | Shall report printer errors with the CUPS/Gutenprint message text (e.g. `Paper Feed (02)`), including when the backend has stopped the queue. `[code]` | approved |
| FR-9.3 | Must | Shall distinguish completed, cancelled and aborted jobs (not `lpstat -W completed`, which lists cancelled jobs too). `[code]` | approved |
| FR-9.4 | Must | A failed printer status query while a job is processing shall not be reported as a printer error. `[code]` R2 | approved |
| FR-9.5 | Must | A printer error shall reach the tracker within {{T_cups_poll}}. | approved (value open) |
| FR-9.6 | Must | Shall report a job left pending in a stopped queue as pending, so reconciliation (FR-6.4) can find it. `[code]` R3 | approved |

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| FR-9.1 | Simulated queue accepting / rejecting | acceptance / refusal reported | refusal reported as accepted | integration |
| FR-9.2 | Simulated backend stops queue with `Paper Feed (02)` | error with that text | generic error only | integration |
| FR-9.3 | One job each completed, cancelled, aborted | three distinct states | cancelled reported as completed | integration |
| FR-9.4 | Status query fails while processing | no error reported | `ERROR` | unit |
| FR-9.5 | Inject printer error | tracker sees it within T_cups_poll | later | integration |
| FR-9.6 | Queue stopped, job pending | job reported pending | job missing | integration |

## 10. Unit Interface (booth side)

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-10.1 | Must | Shall deliver from the ESP32 unit: rear pass, front eject, IR jam report, IR misread report, operator decision ("Foto ist da" / "Foto fehlt"), and a liveness signal. `[user]` | approved |
| FR-10.2 | Must | Shall detect the unit as offline when no liveness signal arrives for {{T_alive}}. | approved (value open) |
| FR-10.3 | Must | Behaviour while the unit is offline: OD1. | open (OD1) |
| FR-10.5 | Should | IR events with no copy in progress shall be logged and otherwise ignored. | proposed (OD2) |
| FR-10.6 | Must | Shall send the panel the current stage, copy k of N, the CUPS error text, IR information, and the evidence of FR-5.14. `[derived]` §12 | approved |
| FR-10.7 | Must | Shall accept an operator decision only while a copy is in `ERROR` or `AWAIT_OPERATOR`. `[derived]` | approved |

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| FR-10.1 | Simulated unit sends each event type | each event reaches the tracker | event lost or mistyped | integration |
| FR-10.2 | Simulated unit stops liveness | offline after T_alive (±1 s) | offline before T_alive | unit |
| FR-10.5 | No copy in progress · inject rear pass | logged, no state change | new state | unit |
| FR-10.6 | `ERROR` on copy 2/3 | panel receives stage, 2/3, error text | missing field | integration |
| FR-10.7 | `LAYER_2` · inject "Foto ist da" | ignored, logged | `DONE` | unit |

## 11. ESP32 Sensor Unit

### 11.1 Purpose

Watches the paper path with IR barriers at the rear exit and front output of the SELPHY and reports events to the booth. Hardware, firmware structure and wire protocol: later. `[user]`

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-11.1 | Must | Shall report one *rear pass* event each time paper leaves the back of the printer during a print pass. `[user]` | approved |
| FR-11.2 | Must | Shall report one *front eject* event when the finished photo exits the front. `[user]` | approved |
| FR-11.3 | Must | Shall send a liveness signal at least every {{T_alive}}/2. | proposed |
| FR-11.4 | Must | When the rear barrier stays blocked longer than {{T_rear_block}}, or no front eject follows the 4th rear pass within {{T_eject}}, the unit shall treat it as a *suspected jam* and wait {{T_confirm}} for a front eject. `[user]` | approved (values open) |
| FR-11.5 | Must | If no front eject occurs within {{T_confirm}} after a suspected jam, the unit shall send an *IR jam report* (information, FR-5.10). `[user]` | approved |
| FR-11.6 | Must | If a front eject does occur within {{T_confirm}} after a suspected jam, the unit shall send an *IR misread report* (information, FR-5.10). `[user]` | approved |

Not yet specified (needed before Phase 3): USB identification among camera and printer, boot time and behaviour after a reset mid-print, barrier debouncing and thresholds, event buffering while the booth is not listening, watchdog, firmware version reporting (Appendix D).

```yaml
id: FR-11.6 misread is reported as information
verification:
  preconditions: [ESP32 unit online, copy printing LAYER_2]
  stimulus: [Hold an object in front of the rear barrier for longer than T_rear_block while the print continues normally]
  expected_observations:
    - The photo exits the front within T_confirm.
    - The unit sends an IR misread report; the print screen and panel show it as information.
    - The copy reaches DONE.
  prohibited_outcomes:
    - "Papierstau" shown as an error.
    - New prints blocked because of the misread.
    - The current job is cancelled or resent.
  tier: bench
  evidence: unit serial log, tracker log, screen recording
  cleanup: none
```

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| FR-11.1 | One normal print | exactly 4 rear-pass events | 3 or 5 events | bench (B-1) |
| FR-11.2 | One normal print | exactly 1 front-eject event | eject reported during a pass | bench (B-1) |
| FR-11.3 | Unit running, booth idle | liveness at least every T_alive/2 | gap > T_alive/2 | target |
| FR-11.5 | Hold paper back during pass 2 (provoked jam) | IR jam report after T_confirm | IR misread report | bench (B-1) |

## 12. Operator Panel

### 12.1 Purpose

Lets the operator, standing at the opened back of the booth, see what the booth knows about the current print and tell it that the printer is fixed and whether the photo came out. Rotary encoder and small I²C OLED on the ESP32 unit, mounted inside the booth. `[user]`

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-12.1 | Must | Shall show the current stage, copy k of N, and the CUPS error text when there is one. `[user]` | approved |
| FR-12.2 | Must | Shall show IR information (jam, misread) and the evidence of FR-5.14 ("Foto erkannt" after the error). `[derived]` | approved |
| FR-12.3 | Must | While a copy is in `ERROR` or `AWAIT_OPERATOR`, shall offer the decisions "Foto ist da" and "Foto fehlt". `[user]` | approved |
| FR-12.4 | Must | A decision shall be sent only after it is selected with the encoder and confirmed with a press. | proposed |
| FR-12.5 | Must | Panel texts shall be German (Appendix C). `[user]` | approved |
| FR-12.6 | Must | While the booth is not reachable, the panel shall say so and offer no decisions. | proposed (OD8) |

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| FR-12.1 | `ERROR` on copy 2/3, `Paper Feed (02)` | stage, 2/3 and the error shown | missing field | target |
| FR-12.3 | `ERROR` · open panel | both decisions offered | no decisions; decisions offered in `LAYER_n` | target |
| FR-12.4 | Turn encoder to "Foto ist da", no press | nothing sent | decision sent on turn | target |
| FR-12.6 | Booth link down | "Booth nicht verbunden", no decisions | decision accepted locally | target |

---

# Part C — Foundation (L0)

## 13. CUPS + Gutenprint, USB host link

Configured, not owned: the `selphy` queue (photobooth-setup `native/setup-printer.sh`), the Gutenprint `canon-cp1300` backend, USB enumeration of printer and ESP32 unit. Tested transitively through §9 and §10.

| ID | Pri | Requirement | Status |
|---|---|---|---|
| NFR-13.1 | Must | Queue options stay identical between Docker (`cups/entrypoint.sh`) and native (`setup-printer.sh`). `[code]` | approved |
| NFR-13.2 | Must | The Gutenprint backend shall run with `FAST_RETURN=0` (CUPS `SetEnv`), so it stays attached for the whole print and reports errors during it. `[derived]` R2 | approved (pending B-Q1) |

---

# Part D — Cross-cutting

## 14. Logging

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-14.1 | Must | Every stage change, error, IR report and operator decision shall be logged with timestamp, job ID, copy k of N and source (CUPS/IR/operator/timeout). | approved |
| FR-14.2 | Should | Logs shall let staff reconstruct after an event how many copies were requested, printed, and failed. `[derived]` event findings | proposed |

## 15. Security

Threat model: physical booth at a private event; ESP32 unit on local USB; no network exposure added. Accepted risk: someone with physical access can fake IR events or operator decisions. No requirements derived. (assumed)

---

# Part E — Operations & Verification

## 16. Operational procedures (reading path)

Set up printer with `FAST_RETURN=0` (§13) → plug in ESP32 unit (§11) → guest prints (§5, §7) → CUPS error appears (§6, Appendix B) → operator opens the back, fixes the printer, presses the printer's print button → confirms on the panel (§12) → booth reconciles and continues (FR-6.4, FR-5.12).

## 17. Verification & Validation

### 17.0 Test architecture

| Tier | Where | Covers |
|---|---|---|
| unit | CI, no hardware | §5 state model, §7 text mapping, gate logic as pure functions |
| host | CI, ESP32 firmware logic built for the host | §11 jam/misread logic, §12 menu logic |
| integration | Virtual test setup (§17.1) on the Docker stack | §5–§7, §9, §10, exactly-once contracts in simulation |
| target | The ESP32 unit on the desk, booth simulated | §11 liveness, §12 panel |
| bench | Event laptop + real SELPHY + real ESP32 unit | B-1 (§17.3), §5.4 contracts on hardware, Appendix A |

Traceability is generated from the requirement IDs in this document and the test plan (`testing/test-plan.yaml`, owned by `/build`) — not maintained by hand here.

### 17.1 Virtual test setup

Required before booth logic is built (Phase 1). `[user]`

- A simulated CUPS backend for the `selphy` queue that can: accept and complete jobs with configurable timing; stop the queue with each Appendix B error and leave the job pending; cancel a job (`0x0A` case); emulate fast return on and off.
- A simulated ESP32 unit that can send every §10 event (rear pass, front eject, IR reports, operator decisions, liveness) on a script.
- Scenarios for the print limit and its edge cases, to measure how the built-in print limit behaves today (Phase 5 input).

### 17.2 GitHub Actions plan

- Fork CI: build + lint on push to `booth`; disable irrelevant upstream workflows (crowdin, docs, release).
- Unit and host tests on every push.
- Prebuilt release package so the laptop needs no npm/gulp build.
- Weekly upstream-merge drift check that opens an issue.
- photobooth-setup: end-to-end Docker stack test with the virtual test setup (§17.1); shellcheck for `native/*.sh`.
- **Not possible in Actions:** deploy to the (usually off) laptop; anything with the real SELPHY or ESP32 unit — target and bench tests, run manually.

### 17.3 Bench session B-1 — real printer, five prints

Run once, when the ESP32 unit with IR barriers is built, so timings are measured in the same session. Five prints answer the `FAST_RETURN=0` safety question and all real-printer questions without wasting paper. `[user]`

**Preconditions:** `FAST_RETURN=0` set for CUPS; ESP32 unit logging every IR event with a timestamp; CUPS `LogLevel debug`; a script logging `lpstat -l -p selphy` and `lpstat -W all -o` every second; phone video of the printer front and back.

| # | Print | Questions answered |
|---|---|---|
| 1 | Normal print | B-Q1, B-Q2 |
| 2 | Normal print (second timing sample) | B-Q1, B-Q2 |
| 3 | Paper tray empty at start; after the error: refill, press the printer's print button | B-Q3, B-Q4, B-Q5 |
| 4 | Ink cassette removed at start; after the error: reinsert, press the printer's print button | B-Q3, B-Q4, B-Q5 |
| 5 | Provoked jam: hold the paper back during pass 2; then clear it, press the printer's print button | B-Q3, B-Q4, B-Q5, FR-11.5 |

**Duplicate check without paper (prints 3–5):** after the photo has come out, remove the paper tray, then run `cupsenable selphy`. If CUPS sends the job again, the printer reports a paper error instead of printing, which shows the duplicate without using a sheet. Then cancel the job.

**Questions:**

| ID | Question |
|---|---|
| B-Q1 | Over all five prints, does `FAST_RETURN=0` work without confusing the printer (no hangs, no extra errors, no lost prints)? How long from send to CUPS *completed*, compared with the front eject? (R7, A6, NFR-13.2, OD1) |
| B-Q2 | Exactly 4 rear passes per print? Times: send → pass 1, between passes, pass 4 → eject. (A1, Appendix A) |
| B-Q3 | For each error: what do CUPS job state, printer state and message show; is the queue stopped; is the job pending? (FR-9.2, FR-9.6) |
| B-Q4 | Does pressing the printer's print button after the fix print the photo? Do IR events show it? (A4, FR-5.14) |
| B-Q5 | With the job still pending, does `cupsenable` send it again (duplicate)? What does CUPS report afterwards? (R3, FR-6.4) |
| B-Q6 | Does backend status `0x0A` (job cancelled instead of queue stopped) occur, and for what? Recorded only if it happens. (R9, OD6) |

**Exit:** B-Q1…B-Q5 answered (B-Q6 if seen); Appendix A filled; results added to this FSD's lifecycle (Appendix D, `related_test_baseline`).

### 17.4 Acceptance (before the next event)

1. 10 consecutive single-copy prints: every stage shown, 10 photos out, no false IR information.
2. One request with 3 copies: "Kopie k von 3" shown, 3 photos out.
3. Each error in Appendix B provoked once on the real printer: correct text, new prints rejected, photo/QR still work, operator fixes the printer and confirms on the panel, exactly one photo per requested copy.
4. A gallery page loaded before a print started: its print button is refused while the print runs.
5. ESP32 unit unplugged mid-print: behaviour per OD1.

---

## Appendix A — Timing parameters (to be measured in B-1)

| Parameter | Meaning | Value |
|---|---|---|
| `T_sent_to_pass1` | max time from SENT to first rear pass (stall limit) | TBD (B-Q2) |
| `T_pass` | max time between rear passes (stall limit) | TBD (B-Q2) |
| `T_rear_block` | rear barrier blocked longer than this → suspected jam | TBD (B-Q2) |
| `T_eject` | max time from 4th rear pass to front eject | TBD (B-Q2) |
| `T_confirm` | wait for a front eject after a suspected jam | TBD |
| `T_alive` | ESP32 unit considered offline after this silence | TBD |
| `T_cups_poll` | max delay for a CUPS error to reach the tracker | TBD |
| `T_ui` | max delay from event to screen, and for print buttons to change state | TBD |
| `T_resume` | max delay from operator confirmation to queue enabled | TBD |
| `T_cups_completed` | send → CUPS *completed*: ~17 s observed with fast return on `[code]`; with `FAST_RETURN=0` | TBD (B-Q1) |

## Appendix B — Messages (German; wording `proposed`)

**Stages**

| State | Text |
|---|---|
| `SENT` | Druckauftrag gesendet |
| `LAYER_1` | Druck gestartet – Gelb wird gedruckt (1/4) |
| `LAYER_2` | Magenta wird gedruckt (2/4) |
| `LAYER_3` | Cyan wird gedruckt (3/4) |
| `LAYER_4` | Schutzschicht wird aufgetragen (4/4) |
| `DONE` | Fertig – bitte Foto entnehmen |
| `AWAIT_OPERATOR` | Druck wird geprüft – bitte Personal rufen |
| `CANCELLED` | Druck abgebrochen |
| copies (N > 1) | Kopie k von N |
| busy rejection (OD5) | Drucker beschäftigt – bitte warten, bis der aktuelle Druck fertig ist |

**Printer errors (from CUPS)** — staff hint for all: fix the problem, press the printer's print button, then confirm on the panel in the booth (FR-7.7).

| Trigger | Guest text | Staff hint |
|---|---|---|
| `Paper Feed (02)` `[code]` | Papier leer oder Papiereinzug gestört | Papier prüfen/auffüllen, Drucktaste am Drucker drücken, dann im Bedienfeld bestätigen. |
| `Ink Cassette Empty (06)` `[code]` | Farbkassette leer | Farbkassette tauschen, Drucktaste am Drucker drücken, dann im Bedienfeld bestätigen. |
| `No Paper and Ink (09)` `[code]` | Papier- oder Farbkassette fehlt | Beide Kassetten prüfen, Drucktaste am Drucker drücken, dann im Bedienfeld bestätigen. |
| `Paper jam (0b)` `[code]` | Papierstau | Papier vorsichtig entfernen, Drucktaste am Drucker drücken, dann im Bedienfeld bestätigen. |
| printer not connected / not found (assumed) | Drucker nicht verbunden | USB-Kabel und Strom prüfen. |
| any other, e.g. `Unknown Error (08)` `[code]` | Druckerproblem – bitte Personal rufen | *(raw CUPS message)* |

**Information (not errors)**

| Source | Trigger | Text |
|---|---|---|
| IR | jam report (FR-11.5) | Hinweis: Sensor meldet möglichen Papierstau |
| IR | misread report (FR-11.6) | Hinweis: Sensor war kurz verdeckt |
| Tracker | stall (FR-5.5) | Drucker reagiert nicht – bitte Personal rufen |
| Unit | offline (FR-7.8) | Sensor offline *(staff notice)* |

## Appendix C — Operator panel texts (German; wording `proposed`)

| Situation | Text |
|---|---|
| Idle | Bereit |
| Printing | Kopie k/N – Gelb (1/4) … Schutzschicht (4/4) |
| Printer error | Fehler: *(Appendix B guest text)* |
| Evidence after error | Foto erkannt |
| Decision prompt | Foto ist da · Foto fehlt |
| Confirmation | Gedrückt halten zum Bestätigen *(or: Drücken zum Bestätigen, FR-12.4)* |
| Booth not reachable | Booth nicht verbunden |

## Appendix D — Lifecycle metadata

```yaml
document_status: draft
fsd_version: 0.3
repository: starkerflamingo32/photobooth (branch booth)
baseline_commit: e8c2cbd3
applicable_firmware_version: none yet (ESP32 unit not built)
author: booth owner, with Claude
reviewers: []
approval_status: not approved
created: 2026-11-23
last_updated: 2026-11-24
change_history:
  - 2026-11-23 v0.1: first draft (truthful print status, CUPS + ESP32 sensor unit)
  - 2026-11-24 v0.2: custom print stack; exactly-once goal; one print at a time incl. gallery (server-enforced);
      CUPS errors only, IR observations as information; operator panel (rotary encoder + I²C OLED);
      one CUPS job per copy; FAST_RETURN=0; no sensor-less fallback booth; bench session B-1;
      print limit and auto-print out of scope until tested in the virtual test setup
  - 2026-11-24 v0.3: OD7 decided — the print screen stays up until the request is finished, no early exit (FR-7.14);
      FR-6.2 narrowed to printer errors with no request running; start screen right after the print screen closes
      (FR-7.15); upstream copies dialog reused for choosing copies (FR-7.16); gallery reprints stay allowed (FR-7.17);
      screen after a cancelled print added to OD6
superseded_requirements:
  - FR-6.2 v0.2 (photos keep working while a print runs) — print screen stays up (FR-7.14)
  - FR-5.7 (fallback HANDED_OVER) — no booth without ESP32 unit
  - FR-5.8 (queue position) — no print queue (FR-6.1)
  - FR-6.3 (keep queued jobs) — no print queue
  - FR-6.5 (unit disconnect never blocks) — replaced by OD1
  - FR-6.6 (sensor error blocks) — IR reports are information (FR-5.10, FR-6.13)
  - FR-6.7 (accept prints while printing) — one print at a time (FR-6.1)
  - FR-7.7 v0.1 ("do not press the printer button") — pressing it is the only fix (A4)
  - FR-10.3 v0.1, FR-10.4 (CUPS-only fallback stages) — replaced by OD1
  - NFR-12.2 (some mechanism re-enables the queue) — booth reconciliation (FR-6.4, FR-6.11)
  - FR-13.x → FR-14.x (renumbered with the new §12)
open_decisions: see §4.4 (OD1–OD6, OD8), each naming the requirement it gates
related_test_baseline: none yet (B-1 not run)
```

## Related

- photobooth-setup: `event-2026-11-22-findings.md`, `paper-ink-counter.md`, `native/setup-printer.sh`, `native/selphy-autoresume.sh`, `cups/entrypoint.sh`
- This fork: `api/print.php`, `src/Service/PrintManagerService.php` (current print path, for reference only)
- Gutenprint source: `src/cups/backend_common.c` (`FAST_RETURN`), `src/cups/backend_canonselphyneo.c` (CP1300 status loop)
