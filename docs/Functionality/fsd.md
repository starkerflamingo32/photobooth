# Photobooth Booth — Functional Specification Document (FSD)

**Scope of this FSD:** a custom print stack for the booth: every photo printed exactly once, one print at a time, and a truthful print screen (SELPHY CP1300 via CUPS, plus an ESP32 unit with IR barriers on the paper path, an operator web UI on a phone on the booth's own network, and a minimal fallback operator panel inside the booth).
**Fork:** starkerflamingo32/photobooth, branch `booth` (upstream `dev` 49b09a94 merged in 1d49f007 on 2026-11-17; code otherwise unchanged).
**Status:** DRAFT — requirements only. Implementation and code architecture are deliberately not decided yet (§2.3), except the decisions listed there.

| Lifecycle | |
|---|---|
| Created | 2026-11-23 |
| Last updated | 2026-11-25 |
| Load defined | **No** — bench session B-1 not run (§17.3), print-limit characterisation not run (§17.5), timing parameters open (Appendix A), open decisions (§4.4), Method/UserDocumentation planes not created yet |

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
| Operator (staff) | Inside the booth, with the back opened to check or fix the printer. Uses the operator web UI on a phone (§12b) as the main interface, and the minimal operator panel (§12) as a fallback. `[user]` | Sees what is wrong, fixes the printer, tells the booth the problem is fixed and whether the photo came out. |

### 1.3 Goals

- G0 **Every requested copy is printed exactly once**, whatever happens (printer error, jam, delay, booth restart). `[user]`
- G1 Only one print request runs at a time, on every page that can print (result screen, gallery), including pages loaded before the print started. `[user]`
- G2 The print screen shows the real stage, from "Druckauftrag gesendet" to "Fertig – bitte Foto entnehmen". `[user]`
- G3 Printer errors come from CUPS and are shown with a specific cause or the raw CUPS message; IR-barrier observations are shown as information only. `[user]`
- G4 After the operator confirms a fix on the phone web UI or the operator panel, printing continues without staff action on the booth screen. `[user]`
- G5 Paper and ink counters warn before supplies run out and tell an empty tray from a misfeed (§8); there is no fixed print limit. `[user]`

### 1.4 Non-goals

- Choosing implementation, code structure, ESP32 wire protocol, sensor hardware or mounting — decided later. `[user]`
- Languages other than German. `[user]`
- Supporting printers other than the SELPHY CP1300. (assumed)
- Changing how photos are taken, uploaded or shared.
- A fixed print limit ("N prints, then stop"): not used and not built; the booth is not rented out. Upstream's limit stays off (FR-8.8). `[user]`
- Auto-print (`print.auto`, `print.auto_delay`): out of scope until characterised (§17.5). `[user]`

### 1.5 High-level flow

```text
Guest presses "Drucken" (N copies)
  → booth busy?  yes → "Drucker beschäftigt" (no job created)
  → printer output still holds a photo? → "Bitte zuerst das Foto aus dem Drucker nehmen", wait until taken
  → copy k sent to CUPS          → "Druckauftrag gesendet (Kopie k von N)"
  → rear pass 1…4 (IR barrier, front sensor sees the sheet between passes)
                                 → Gelb / Magenta / Cyan / Schutzschicht (1/4…4/4)
  → front eject (front sensor stays covered) → copy k done
      k < N: "Kopie k von N fertig – bitte Foto entnehmen" until the photo is taken, then copy k+1
      k = N: "Fertig – bitte Foto entnehmen" for T_take_hint, then start screen"
CUPS printer error at any point  → error screen + phone UI + operator panel; queue stopped by the backend
  → operator fixes the printer and presses the printer's own print button (prints the waiting photo)
  → operator confirms on the phone (or the panel): "Foto ist da" / "Foto fehlt"
  → booth reconciles the CUPS job (no duplicate), re-enables the queue, continues with copy k+1
IR barrier reports jam / misread → information on the print screen and the panel, not an error
```

---

## 2. System Architecture

### 2.1 Logical architecture

```text
 ┌──────────────── Booth (Photobooth web app on the event laptop) ─────────────────┐
 │  Print screen + print buttons (UI) ◄── Print Status Tracker ──► Busy Gate        │
 │                                          ▲            ▲        ▲                 │
 │                           CUPS status interface   Unit interface  Operator web UI│
 └──────────────────────────────────────────┼────────────┼────────┼─────────────────┘
                                            │            │ USB    │ Wi-Fi (booth router, inside the booth)
                                  CUPS + Gutenprint   ESP32 unit  └── operator phone (browser)
                                            │            ├── IR barriers (rear exit, front eject)
                                            │            └── minimal operator panel: rotary encoder + I²C OLED
                                     SELPHY CP1300 (USB)            (inside the booth, at the back)
```

The phone is the operator's main interface; the panel is the fallback when the phone or Wi-Fi is not available. Printing never depends on Wi-Fi (NFR-13.3).

### 2.2 Hardware / platform

| Item | Fact | Source |
|---|---|---|
| Printer | Canon SELPHY CP1300, USB `04a9:32db`, dye-sublimation, 4 passes per print (Y, M, C, overcoat); paper leaves the **back** of the printer between passes and exits the **front** when finished | `[code]` / `[derived]` |
| Driver | CUPS queue `selphy`, device `gutenprint53+usb://canon-cp1300/…`, `gutenprint.5.3://canon-cp1300`, `PageSize=Postcard`, `StpBorderless=True` | `[code]` photobooth-setup, laptop |
| Backend | Gutenprint 5.3.4 (Debian 12) `backend_canonselphyneo`: under CUPS *fast return* is on unless `FAST_RETURN` is set; on most printer errors it stops the queue and leaves the job pending (`CUPS_BACKEND_STOP`), on status `0x0A` it cancels the job (`CUPS_BACKEND_CANCEL`) | `[code]` Gutenprint source (master; same switch in the installed binary) |
| Booth host | Acer Switch 3, Debian 12, native Photobooth install | `[code]` |
| ESP32 unit | ESP32-C6 mini board `[user]`, connected to the booth host over USB. Two HW-201 IR modules (§11.2), each with an added analog wire to an ADC input: the rear one rebuilt as a through-beam barrier at the rear paper exit, the front one as a reflective sensor over the output tray. Minimal operator panel (fallback, §12) with a rotary encoder and an I²C OLED, mounted inside the booth for use with the back opened | `[user]` |
| Paper path | During each pass the sheet comes out at the back, then at the front, and goes fully back inside at both sides; the finished print is ejected at the front onto the output tray | `[user]` |
| Shared USB hub | Printer and camera share one USB hub on the laptop; adding the ESP32 there is a risk (R6) | `[code]` |
| Booth router | Router inside the booth, always on during an event; its Wi-Fi is joined only by the booth laptop and the operator's phone, never by guests. Whether it is the same LTE router that carries the photo uploads: (assumed) yes | `[user]` |
| Operator phone | Any current smartphone browser; no app installed | `[user]` / (assumed) |

### 2.3 Software architecture

**Mostly open.** Which process reads CUPS, where the tracker runs, how the UI gets updates, and the ESP32 wire protocol are HOW decisions, to be made after this FSD is accepted. `[user]` This FSD fixes the observable behaviour and the information each interface must deliver.

Decided:

- Each copy of a photo is its own CUPS job; copies are sent one after another. `[user]`
- The guest chooses the number of copies in upstream's existing copies dialog (FR-7.16); only its UI is reused, the backend path is open. `[user]`
- The Gutenprint backend runs with fast return **off** (`FAST_RETURN=0`), so printer errors during a print reach CUPS (NFR-13.2). `[derived]` R2 — to be confirmed by bench session B-1 (§17.3).
- The print stack is custom; upstream print-limit functions may be reused once tested (§1.4). `[user]`
- The operator has two interfaces: a web UI on a phone on the booth network (§12b, main) and a minimal panel on the ESP32 unit (§12, fallback). `[user]`
- The operator web UI is served by the booth laptop. `[derived]` (it must work while the ESP32 unit is offline, FR-12b.5)
- No fixed print limit; paper and ink counters are part of the custom print stack and count at `DONE` (§8). `[user]`
- A copy is only sent while the printer output is empty: with a finished photo still lying on the front sensor, the next copy (or the next request's first copy) waits until it is taken (§5.2 `WAIT_OUTPUT`). `[user]`

### 2.4 Component layering

```text
L2 Application  │ Print Status Tracker (§5) │ Busy Gate (§6) │ Print Screen (§7) │ Supply Counters (§8, later)
────────────────┼──────────────────────────────────────────────────────────────────────────────
L1 Interfaces   │ CUPS Status Interface (§9) │ Unit Interface (§10) │ ESP32 Sensor Unit (§11) │ Operator Panel (§12) │ Operator Web UI (§12b)
────────────────┼──────────────────────────────────────────────────────────────────────────────
L0 Foundation   │ CUPS + Gutenprint (§13) │ USB host link (§13)
```

Dependencies point downward only. The ESP32 firmware (§11, §12) is listed under L1 because the booth sees it as an external black box; only its externally observable behaviour is specified.

---

## 3. Implementation Phases

| Phase | Scope | Exit criteria | Depends on |
|---|---|---|---|
| **1 — Virtual test setup** | §17.1: simulated SELPHY backend that can inject SELPHY errors and pending/stopped-queue states; simulated ESP32 unit incl. panel events; browser test setup (§17.1.1); workflows (§17.2) | Every unit, integration and e2e test in this FSD can be run without hardware, in GitHub Actions; §17.5 run against upstream | — |
| **2 — Booth logic + operator web UI + supply counters** | §5, §6, §7, §8, §9, §10, §12b against the virtual test setup | All unit and integration tests pass; exactly-once contracts (§5.4) pass in simulation, with decisions made on the web UI | Phase 1 |
| **3 — ESP32 unit + bench session** | §11, §12; bench session B-1 (§17.3) | ESP32 unit built; B-1 run; B-Q1…B-Q6 answered; Appendix A filled; OD1 and OD6 decided | Phase 2, ESP32 hardware |
| **4 — Acceptance** | §17.4 on the real booth | Acceptance list passes | Phase 3 |

---

## 4. Risks, Assumptions & Dependencies

### 4.1 Risks

| ID | Risk | Mitigation |
|---|---|---|
| R1 | `Paper Feed (02)` is reported both for an empty tray and a misfeed `[code]` — the message cannot be more specific than "Papier leer oder Papiereinzug gestört". With a known paper count the booth tells them apart (FR-8.6); otherwise the text covers both (Appendix B). |
| R2 | With fast return on, CUPS marks the job *completed* ~17 s in and never sees an error during the rest of the print `[code]`. | `FAST_RETURN=0` (NFR-13.2); CUPS *completed* never shown as "Fertig" (FR-5.6); status read failures are not errors (FR-9.4). |
| R3 | After an error the backend leaves the job pending in a stopped queue. Pressing the printer's own print button prints the buffered photo; re-enabling the queue then sends the job again → duplicate. `[code]`/`[user]` | Reconcile before re-enabling (FR-6.4, FR-5.14); only the booth re-enables the queue (FR-6.11). Behaviour verified in B-1. |
| R4 | IR barrier misreads (dust, a hand, a cable). `[user]` | Barrier observations are information only, never errors (FR-5.10). |
| R5 | Pass timing differs per printer/temperature; wrong limits cause false stall information or late detection. | Limits measured in B-1 (Appendix A). |
| R6 | ESP32 on the same USB hub as printer and camera; unexplained freezes at the 2026-11-22 event. | Out of this FSD's control; see event findings. Unit dropout handled (FR-10.2, OD1). |
| R7 | With `FAST_RETURN=0` the backend polls the printer for the whole print; this might confuse the printer. | Five consecutive prints in B-1 without printer misbehaviour before the setting is kept (B-Q1). |
| R8 | A page loaded before a print started (result screen, gallery) still shows an active print button. `[user]` | Server-side gate on every print entry point (FR-6.8). |
| R9 | Backend status `0x0A` cancels the job instead of stopping the queue `[code]`; its meaning on the CP1300 is unknown, and the photo may be lost. | Record in B-1 if it occurs (B-Q6); OD6. |
| R10 | Phone not at hand, battery empty, or booth Wi-Fi down when an error needs a decision. | Minimal panel on the ESP32 unit offers the same decisions (§12); printing never depends on Wi-Fi (NFR-13.3). |
| R11 | The operator web UI adds network exposure: anyone on the booth Wi-Fi could make operator decisions. | WPA2 Wi-Fi joined only by laptop and operator phone; upstream admin login on the web UI (FR-12b.2); §15. |
| R12 | Phone and panel used at the same time give conflicting decisions. | First confirmed decision wins; later ones are ignored and both interfaces show the decision made (FR-10.8). |
| R13 | The fork is public and photobooth-setup is private: the fork's CI needs a token to build the stack, and test artifacts are visible to others. | Read-only fine-grained token on photobooth-setup only, as an Actions secret; synthetic test data only (§17.1.1, §17.2). |

### 4.2 Assumptions

- A1 The SELPHY always makes exactly 4 rear passes per print, in the order yellow, magenta, cyan, overcoat. (assumed — B-Q2)
- A2 Only one printer (`selphy`) is used. (assumed)
- A3 The booth host's clock is the time reference for all timeouts. (assumed)
- A4 After a printer error the printer has already received the job; fixing the problem and pressing the printer's own print button prints that photo. There is no way to clear a stuck printer without printing it. `[user]` (assumed — B-Q4)
- A5 Only one job is ever inside the printer at a time, because the booth sends the next copy only after the previous one is out (FR-5.11). `[derived]`
- A6 The fast-return behaviour read in the Gutenprint master source matches the installed 5.3.4. (assumed — B-Q1)
- A8 The sheet comes out at the back and at the front during each pass and fully retracts at both sides in between; only the final eject leaves it lying on the output tray. `[user]` (pattern and timing — B-Q7)
- A7 Once the first rear pass has happened, the copy's ink panel set is used up even if the sheet is then lost; before it, no ink is used. (assumed — B-Q4)

### 4.3 Dependencies

- photobooth-setup `native/setup-printer.sh` (queue options) and the CUPS `SetEnv FAST_RETURN 0` setting (NFR-13.2; which config file is not yet determined).
- `selphy-autoresume` must stay disabled: it re-enables the queue without reconciliation and would cause duplicates (FR-6.11).

### 4.4 Open decisions

| ID | Decision | Gates | Proposal |
|---|---|---|---|
| OD1 | ESP32 unit drops out (no liveness), or the front sensor reports a fault: block new prints, or continue on CUPS status alone? Without the front sensor the output state is unknown: skip the wait for removal (FR-5.16), or ask the operator on the phone? | FR-10.3, FR-6.1, FR-5.16 | Decide after B-Q1 (is CUPS *completed* with `FAST_RETURN=0` close to the real eject?). New option since v0.4: the operator web UI keeps working without the unit, so printing could continue with the operator confirming each eject on the phone. |
| OD2 | IR events with no job in progress, a front eject before `LAYER_4` (something placed on the tray, or a misread — front protrusions during passes are normal, A8), a 5th rear pass: how shown? | FR-10.5, §5.2 | Log; show as sensor information on the web UI (and panel, FR-12.8); no state change. `proposed` |
| OD3 | Paper tray capacity and prints per ink cassette (defaults offered on the phone; the operator can always enter the real number) | FR-8.3 | paper-ink-counter.md says ~18 sheets and 56 prints; unconfirmed |
| OD4 | Booth page reload during a print (e.g. session reload): what does the screen show? | FR-7.12 | The print screen is restored from the tracker's state. `proposed` |
| OD5 | Guest presses a print button while busy: what is shown? | FR-6.10 | Short notice "Drucker beschäftigt – bitte warten, bis der aktuelle Druck fertig ist". `proposed` |
| OD6 | Job cancelled by the backend (status `0x0A`): resend the copy, or treat as failed? And after a cancelled print, does the booth go to the start screen (like FR-7.15) or back to the result view? | §5.2 (`CANCELLED`), FR-7.15 | Decide after B-Q6 has shown what `0x0A` means |
| OD8 | What the panel shows and allows while the booth is not reachable (panel only; the phone covers the case where the unit is offline) | FR-12.6 | "Booth nicht verbunden", no decisions possible. `proposed` |

---

# Part A — Application Logic (L2)

## 5. Print Status Tracker

### 5.1 Purpose

Determines, for every print request (one photo, N copies) and each copy's CUPS job, the stage shown to the guest and the operator, from CUPS job/printer status (§9), IR-barrier events (§10) and operator decisions (from the web UI §12b or the panel via §10).

### 5.2 Job state model

States of the copy currently being printed. The request additionally carries "copy k of N". The front sensor also reports whether the printer output is *occupied* (a finished photo lies on it) or *clear*.

| State | Shown as (Appendix B) | Entered on |
|---|---|---|
| `WAIT_OUTPUT` | k = 1: Bitte zuerst das Foto aus dem Drucker nehmen · k > 1: Kopie k−1 von N fertig – bitte Foto entnehmen | copy k is due to be sent but the output is occupied (FR-5.16); no CUPS job exists for copy k yet |
| `SENT` | Druckauftrag gesendet | CUPS accepted the copy's job |
| `LAYER_1`…`LAYER_4` | Gelb / Magenta / Cyan / Schutzschicht | 1st…4th rear pass |
| `DONE` | last copy: Fertig – bitte Foto entnehmen (for {{T_take_hint}}) | front eject after `LAYER_4`, or operator "Foto ist da" |
| `ERROR` | CUPS error text | CUPS printer error / queue stopped while this job is active; CUPS refused the job |
| `AWAIT_OPERATOR` | Druck wird geprüft – bitte Personal rufen | stall (FR-5.5); booth restart with an unfinished job (FR-5.13) |
| `CANCELLED` | Druck abgebrochen | job cancelled or aborted in CUPS |

Events: **E1** CUPS accepted · **E2** CUPS refused · **E3** rear pass · **E4** front eject (front sensor covered and staying covered, FR-11.2) · **E4a** front protrusion (covered, then clear again, FR-11.7) · **E13** output cleared (photo taken, FR-11.8) · **E5** CUPS printer error / queue stopped · **E6** CUPS job completed · **E7** CUPS job cancelled/aborted · **E8** IR jam or misread report · **E9** stage limit exceeded (stall) · **E10** operator "Foto ist da" · **E11** operator "Foto fehlt" · **E12** booth restart.

Transitions (normative). "info" = shown as information on the print screen and the operator interfaces, no state change. "log" = logged only. "evidence" = recorded for the operator interfaces (FR-5.14). E10/E11 come from either operator interface; only the first confirmed decision counts (FR-10.8).

| State \ Event | E3 rear pass | E4a front protrusion | E4 front eject | E13 output cleared | E5 CUPS error | E6 completed | E7 cancelled | E8 IR report | E9 stall | E10 Foto ist da | E11 Foto fehlt |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `WAIT_OUTPUT` | log, info (OD2) | log, info (OD2) | log | send copy k (→ `SENT` on E1) | (gate) | log | log | log | — | ignored (not offered) | ignored (not offered) |
| `SENT` | → `LAYER_1` | log (pass evidence) | log, info (OD2) | log | → `ERROR` | log | → `CANCELLED` | info | → `AWAIT_OPERATOR` | ignored (not offered) | ignored (not offered) |
| `LAYER_n` (n<4) | → `LAYER_n+1` | log (pass evidence) | log, info (OD2) | log | → `ERROR` | log | → `CANCELLED` | info | → `AWAIT_OPERATOR` | ignored | ignored |
| `LAYER_4` | log, info (OD2, A1) | log (pass evidence) | → `DONE` | log | → `ERROR` | log | → `CANCELLED` | info | → `AWAIT_OPERATOR` | ignored | ignored |
| `ERROR` | evidence | evidence | evidence | log | log | evidence | → `CANCELLED` | info | — | → `DONE` (reconcile, FR-6.4) | → `SENT` (reconcile, FR-6.4) |
| `AWAIT_OPERATOR` | evidence | evidence | evidence | log | → `ERROR` | evidence | → `CANCELLED` | info | — | → `DONE` (reconcile) | → `SENT` (reconcile) |
| `DONE` | log | log | log | log | (next copy or gate) | log | log | log | — | ignored | ignored |
| `CANCELLED` | log | log | log | log | (gate) | log | log | log | — | ignored | ignored |

Entry events: E1 creates a copy in `SENT`; E2 creates it in `ERROR`; E12 puts every unfinished copy found in CUPS into `AWAIT_OPERATOR` (FR-5.13). Before any copy is sent — the first copy of a request, or copy k+1 after `DONE` of copy k < N — the tracker checks the output: clear → send; occupied → `WAIT_OUTPUT` until E13 (FR-5.16). In `DONE`/`CANCELLED`/`WAIT_OUTPUT` an E5 belongs to no job and only affects the Busy Gate (§6); a `WAIT_OUTPUT` copy is sent only once the gate is open again.

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
| FR-5.16 | Must | A copy (the first of a request, or the next of a multi-copy request) shall be sent only while the front sensor reports the output *clear*; while it is *occupied*, the copy shall wait in `WAIT_OUTPUT` and be sent automatically when the output is cleared (E13). `[user]` OD11 | approved |
| FR-5.17 | Must | Front protrusions (E4a) shall never set `DONE`; only a front eject (E4) after `LAYER_4`, or the operator, shall. `[derived]` A8 | approved |
| FR-5.12 | Must | After an error on copy k is resolved, the remaining copies shall be sent without staff action; the only guest action is taking each finished photo out (FR-5.16). `[user]` | approved |
| FR-5.13 | Must | After a booth restart, an unfinished job found in CUPS shall be held, set to `AWAIT_OPERATOR`, and not sent to the printer until the operator decides (phone or panel). `[user]` | approved |
| FR-5.14 | Must | Rear passes and front ejects received in `ERROR` or `AWAIT_OPERATOR` shall be recorded as evidence and shown on both operator interfaces. `[derived]` A4 | approved |
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
| FR-5.16 | 3 copies · copy 1 `DONE`, photo left on the tray | `WAIT_OUTPUT`, no CUPS job for copy 2; take the photo → copy 2 sent | copy 2 sent with the output occupied | integration |
| FR-5.16 | Previous photo still on the tray · new request | accepted, `WAIT_OUTPUT` with "Bitte zuerst das Foto aus dem Drucker nehmen"; take it → copy 1 sent | request rejected; job sent at once | e2e |
| FR-5.17 | `LAYER_2` · inject front protrusion | stays `LAYER_2` | `DONE` | unit |
| FR-5.12 | 3 copies, `ERROR` on copy 2 · operator "Foto ist da", photo 2 taken | copy 3 sent automatically | copy 3 lost or waits for staff | integration |
| FR-5.13 | Job pending in simulated CUPS · restart booth | `AWAIT_OPERATOR`, queue stays disabled | job sent to printer | integration |
| FR-5.14 | `ERROR` · inject front eject | web UI and panel show "Foto erkannt" | eject ignored | unit |
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
    - Operator selects and confirms "Foto ist da" on the phone web UI (repeat once with the panel).
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
  evidence: CUPS job log, tracker log, operator decision log (source phone/panel), photo of the output tray
  cleanup: cancel remaining jobs, refill paper
```

```yaml
id: FR-5.12/FR-5.15 three copies with an error on copy 2
verification:
  preconditions:
    - ESP32 unit online. Three copies requested.
  stimulus:
    - Copy 1 prints normally. Copy 2 hits a CUPS printer error.
    - Operator fixes the printer, presses the printer's print button (copy 2 comes out), confirms "Foto ist da" on the phone.
    - The guest takes each photo out of the printer as it is finished.
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
| FR-6.12 | Must | While a CUPS printer error or `AWAIT_OPERATOR` is active, or a supply count is 0 (FR-8.4), the booth shall reject new print requests. `[user]` | approved |
| FR-6.8 | Must | The rejection shall be enforced on the server for every print entry point (result screen, gallery, any API call), regardless of when the page was loaded. `[user]` | approved |
| FR-6.9 | Must | Print buttons on pages already open (result screen, gallery) shall show as disabled within {{T_ui}} of the booth becoming busy, and enabled again within {{T_ui}} of it becoming free. `[user]` | approved (value open) |
| FR-6.10 | Must | A rejected request shall create no CUPS job and shall show the guest the busy notice (OD5). `[user]` | approved (wording open) |
| FR-6.2 | Must | While printing is blocked by a CUPS printer error and no print request is unfinished, taking photos, showing the result and the QR code shall keep working. `[user]` (While a request is unfinished the print screen stays up, FR-7.14.) | approved |
| FR-6.4 | Must | On an operator decision for a job in `ERROR` or `AWAIT_OPERATOR`, the booth shall first make CUPS match it ("Foto ist da": cancel the job if still pending; "Foto fehlt": keep it), then re-enable the queue. `[user]` | approved |
| FR-6.11 | Must | Only the booth's reconciliation (FR-6.4) shall re-enable the CUPS queue; no other automatic `cupsenable` shall run. `[derived]` R3 | approved |
| FR-6.13 | Must | IR-barrier information alone shall not block printing. `[user]` | approved |
| FR-6.14 | Must | An occupied printer output shall not reject a new print request; the request is accepted and its first copy waits (FR-5.16). The request counts as unfinished (FR-6.1) while it waits. `[user]` | approved |

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
| FR-7.18 | Must | After the last copy of a request is `DONE` (single or multi-copy), the print screen shall show "Fertig – bitte Foto entnehmen" for {{T_take_hint}} and then close, whether or not the photo has been taken. `[user]` | approved (value open) |
| FR-7.19 | Must | Between copies of a multi-copy request, the print screen shall show "Kopie k von N fertig – bitte Foto entnehmen" for as long as the photo stays in the output, and continue with copy k+1 when it is taken. `[user]` | approved |
| FR-7.20 | Must | When a new request's first copy waits because the output still holds an earlier photo, the print screen shall show "Bitte zuerst das Foto aus dem Drucker nehmen" until it is taken. `[user]` | approved |
| FR-7.16 | Must | With `print.max_multi` > 1, the guest shall choose the number of copies in upstream's existing copies dialog (minus/plus, 1…`print.max_multi`, "Drucken" / "Abbrechen"; `assets/js/tools.js` `askCopies`). How the chosen number reaches the print stack is not yet decided. `print.max_multi` stays an admin-panel setting, unchanged. `[user]` | approved |
| FR-7.17 | Must | Printing a photo again from the gallery shall stay possible, subject to the Busy Gate (§6); staff use it for reprints and can hide the gallery button in the admin panel. `[user]` | approved |
| FR-7.2 | Must | Stage changes shall appear on screen within {{T_ui}} of the event reaching the booth. | approved (value open) |
| FR-7.3 | Must | All texts shall be German. `[user]` | approved |
| FR-7.4 | Must | Layer stages shall be named yellow, magenta, cyan and **Schutzschicht** (clear coat); the 4th stage shall never be called black/"Schwarz"/"K". `[user]` | approved |
| FR-7.5 | Must | For a known CUPS error (Appendix B) the screen shall show its German cause. `[user]` | approved |
| FR-7.13 | Must | For a known CUPS error the screen shall show the staff hint from Appendix B. `[user]` | approved |
| FR-7.6 | Must | For an unrecognised CUPS error the screen shall show "Druckerproblem – bitte Personal rufen" large, and the exact CUPS message small underneath. `[user]` | approved |
| FR-7.7 | Must | Staff hints for printer errors shall say: fix the printer, press the printer's own print button, then confirm on the phone or the panel inside the booth. `[user]` A4 | approved |
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
| FR-7.18 | Single print · photo not taken | "Fertig – bitte Foto entnehmen" for T_take_hint, then start screen | screen stays until taken; closes early | e2e |
| FR-7.19 | 2 copies · copy 1 out, not taken for 60 s | "Kopie 1 von 2 fertig – bitte Foto entnehmen" the whole time; take it → copy 2 printing | screen closes; copy 2 sent | e2e |
| FR-7.20 | Earlier photo on the tray · press Drucken | "Bitte zuerst das Foto aus dem Drucker nehmen"; take it → "Druckauftrag gesendet" | job sent; busy rejection shown | e2e |
| FR-7.16 | `print.max_multi` = 3 · press Drucken, choose 2, confirm | 2 copies printed in sequence | more than 3 selectable; dialog skipped | integration |
| FR-7.16 | Copies dialog open · press Abbrechen | no print request | CUPS job created | integration |
| FR-7.17 | Photo printed before, booth idle · print it from the gallery | printed once more | print refused because printed before | integration |
| FR-7.4 | `LAYER_4` | "Schutzschicht" | "Schwarz", "K" | unit |
| FR-7.5 | CUPS reason `Ink Cassette Empty (06)` | "Farbkassette leer" | raw message only | unit |
| FR-7.6 | CUPS reason `Unknown Error (08)` | generic text + "Unknown Error (08)" | blank or only "Fehler" | unit |
| FR-7.7 | Any Appendix B printer error | hint mentions printer button and confirming on phone or panel | "Nicht die Taste am Drucker drücken" | unit |
| FR-7.10 | 3 copies, copy 2 printing | "Kopie 2 von 3" | missing or wrong count | unit |
| FR-7.11 | IR jam info | info styling | error styling | unit |
| FR-7.8 | Simulated unit stops liveness | "Sensor offline" within T_alive | no notice | integration |

## 8. Supply Counters (Phase 2)

### 8.1 Purpose

Tracks how much paper is left in the tray and how many prints are left on the ink cassette, so the operator refills before a guest's print fails, and so `Paper Feed (02)` can be told apart as empty tray or misfeed (R1). Part of the custom print stack; replaces upstream's print limit for this booth. `[user]`

**No fixed print limit.** The booth is not rented out and has no print budget per event, so there is no "N prints, then stop" cap: upstream's print limit stays off and none is built. `[user]` Reasons: upstream counts at CUPS acceptance, not at paper out; its lock/unlock does not match refilling paper; and it left the booth locked at the 2026-11-22 event (`Print locked`, then `Unable to print 1 copies` after unlocking; likely the threshold bug fixed upstream in #1667, to be confirmed by §17.5). Earlier sketch: photobooth-setup `paper-ink-counter.md`; capacities OD3.

### 8.2 Requirements

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-8.1 | Must | The booth shall keep two counts, *paper left* and *prints left on the ink cassette*, and lower both by one when a copy reaches `DONE` (front eject or "Foto ist da"). Apart from FR-8.10 and operator corrections (FR-8.3), no other event shall lower them. `[user]` | approved |
| FR-8.2 | Must | When either count reaches {{LOW_THRESHOLD}}, the operator web UI and the print screen (as a staff notice) shall show a warning ("Papier fast leer – noch n Blatt" / "Farbkassette fast leer – noch n Drucke"). `[user]` | approved (value open) |
| FR-8.3 | Must | On the operator web UI the operator shall be able to: set *paper left* after refilling by entering the number of sheets now in the tray; set *prints left* after changing the cassette (default from OD3); and correct either count at any time. Each change is logged (FR-14.1). `[user]` | approved |
| FR-8.4 | Must | While *paper left* or *prints left* is 0, new print requests shall be rejected as in FR-6.12, with the guest notice "Drucker wird nachgefüllt – bitte Personal rufen", and the operator web UI shall show what to refill. A request already running is not cancelled: its next copy waits, with the print screen showing the refill notice, until the operator has set the count above 0. | proposed |
| FR-8.5 | Must | The counts shall survive a booth restart and a page reload unchanged. `[derived]` | approved |
| FR-8.6 | Must | On `Paper Feed (02)`, the booth shall show "Papier leer" when *paper left* is 0 and "Papiereinzug gestört" when it is greater than 0 (Appendix B); with an unknown count, the combined text. `[derived]` R1 | approved |
| FR-8.7 | Must | While a count is unknown (never set since installation), it shall be shown as "unbekannt", with no warning and no block. `[derived]` | approved |
| FR-8.8 | Must | Upstream's print limit shall be off (`print.limit` = 0) on the booth; photobooth-setup shall set it so. `[user]` | approved |
| FR-8.9 | Should | The operator web UI shall show both counts at all times, also while idle. | proposed |
| FR-8.10 | Must | After an operator decision "Foto fehlt" (on the phone or the panel), the operator web UI shall ask "Blatt verbraucht?" with "Ja" / "Nein". "Ja" lowers *paper left* by one, and *prints left* by one if at least one rear pass was seen for that copy (the ink panels are then assumed used, A7); "Nein" changes nothing. The resend of the copy shall not wait for the answer (FR-5.12); an unanswered question changes nothing and is dropped when the request finishes. `[user]` | approved |

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| FR-8.1 | Paper 10 · one print reaches `DONE` | paper 9, prints 9 | count lowered at `SENT` or on CUPS completed | integration |
| FR-8.1 | Paper 10 · print stops with `Paper Feed (02)`, operator "Foto fehlt", resent, `DONE` | paper 9 | paper 8 | integration |
| FR-8.2 | LOW_THRESHOLD 3, paper 4 · one print | warning on phone and print screen | no warning; guest-style error | e2e |
| FR-8.3 | Phone · "Papier nachgefüllt: 18" | paper 18, change logged | count unchanged; not logged | e2e |
| FR-8.4 | Paper 0 · guest presses Drucken | refill notice, no CUPS job | CUPS job created | e2e |
| FR-8.4 | Paper 1, 2 copies requested | copy 1 printed, copy 2 waits blocked until refill; operator sets 18 · copy 2 printed | copy 2 sent with paper 0; copy 2 lost | integration |
| FR-8.5 | Paper 7 · restart booth | paper 7 | reset to 0 or unknown | integration |
| FR-8.6 | Paper 0 / paper 5 · `Paper Feed (02)` | "Papier leer" / "Papiereinzug gestört" | combined text with a known count | unit |
| FR-8.7 | Fresh install · print | prints; counts "unbekannt" | blocked | integration |
| FR-8.8 | Booth after photobooth-setup run | `print.limit` = 0 | any other value | integration |
| FR-8.10 | Paper 10, prints 10, `Paper jam (0b)` in `LAYER_2` · operator "Foto fehlt", then "Blatt verbraucht?" → Ja | paper 9, prints 9 before the resent copy; 8/8 after it | question blocks the resend; counts lowered without "Ja" | e2e |
| FR-8.10 | Paper 10, `Paper Feed (02)` in `SENT` (no rear pass) · "Foto fehlt" → Ja | paper 9, prints 10 | prints lowered | unit |
| FR-8.10 | "Foto fehlt" on the panel | question appears on the phone | no question; question on the panel | e2e |
| FR-8.10 | "Foto fehlt" → Nein, or no answer until the request is done | counts unchanged; question gone | counts lowered | e2e |

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
| FR-10.1 | Must | Shall deliver from the ESP32 unit: rear pass, front protrusion, front eject, output occupied / cleared, IR jam report, IR misread report, sensor fault, panel operator decision ("Foto ist da" / "Foto fehlt"), and a liveness signal. `[user]` | approved |
| FR-10.9 | Must | After the unit connects or reconnects, the booth shall obtain the current output state (occupied / clear) before sending any copy. `[derived]` FR-5.16 | approved |
| FR-10.2 | Must | Shall detect the unit as offline when no liveness signal arrives for {{T_alive}}. | approved (value open) |
| FR-10.3 | Must | Behaviour while the unit is offline: OD1. | open (OD1) |
| FR-10.5 | Should | IR events with no copy in progress shall be logged and otherwise ignored. | proposed (OD2) |
| FR-10.6 | Must | Shall send the panel what §12 shows: current stage, copy k of N, the short error text (Appendix C), the evidence of FR-5.14, and a decision already made on the phone. `[derived]` §12 | approved |
| FR-10.7 | Must | The booth shall accept an operator decision, from the panel or the web UI, only while a copy is in `ERROR` or `AWAIT_OPERATOR`. `[derived]` | approved |
| FR-10.8 | Must | For each copy in `ERROR` or `AWAIT_OPERATOR`, only the first confirmed decision shall be applied, whichever interface it came from; a later decision for the same copy shall be ignored, logged, and both interfaces shall show the decision that was applied. `[derived]` R12 | approved |

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| FR-10.1 | Simulated unit sends each event type | each event reaches the tracker | event lost or mistyped | integration |
| FR-10.9 | Output occupied · unit reconnects · new request | `WAIT_OUTPUT` | copy sent with unknown output state | integration |
| FR-10.2 | Simulated unit stops liveness | offline after T_alive (±1 s) | offline before T_alive | unit |
| FR-10.5 | No copy in progress · inject rear pass | logged, no state change | new state | unit |
| FR-10.6 | `ERROR` on copy 2/3 | panel receives stage, 2/3, error text | missing field | integration |
| FR-10.7 | `LAYER_2` · inject "Foto ist da" from panel, then from web UI | both ignored, logged | `DONE` | unit |
| FR-10.8 | `ERROR` · web UI "Foto ist da", then panel "Foto fehlt" 1 s later | `DONE`, job not resent; panel shows "Am Handy entschieden: Foto ist da" | copy resent; duplicate photo | integration |

## 11. ESP32 Sensor Unit

### 11.1 Purpose

Watches the paper path with two IR sensors at the rear exit and front output of the SELPHY and reports events to the booth. Firmware structure and wire protocol: later. `[user]`

### 11.2 Sensor hardware

Both sensors are **HW-201** IR obstacle modules `[user]`: an IR LED and a photodiode side by side, an LM393 comparator with a potentiometer, a digital output (OUT) and an indicator LED; supply 3.3–5 V. Each module gets one added wire from the comparator input that carries the photodiode signal, giving an analog reading `[user]`. (Module facts: common listing values, assumed.)

| Sensor | Build | Placement |
|---|---|---|
| Rear | HW-201 with its IR LED moved onto wires `[user]`: LED below the paper path, the module with its photodiode above it, beam vertical across the sheet (through-beam barrier) | About 1–2 cm behind the rear exit slot, where every pass reaches and the sheet is still flat; the sheet retracts fully between passes (A8), so the beam is clear between passes. Photodiode in a short black tube aimed at the LED. (proposed) |
| Front | HW-201 unchanged, above the output tray, LED and photodiode angled at a reflector glued to the tray; a sheet between them and the reflector lowers the received light `[user]` | Where both the pass protrusions and the finished print cover it `[user]`. Reflector: retroreflective (microprism) tape preferred, as it tolerates small angle changes when the paper cassette is pulled out and reinserted; mirror foil works if alignment holds. Tilt about 5–10° so a glossy print does not reflect straight back. (proposed) |

Wiring and electrical (proposed unless marked):

- **Supply 3.3 V** from the ESP32 board. OUT is pulled up to the module supply, so at 5 V it would put 5 V on an ESP32 pin, which is not 5 V tolerant. `[derived]`
- **Analog wire:** from the LM393 input connected to the photodiode (find it with a multimeter: the voltage at that pin changes when the sensor is covered and uncovered; the other input is the potentiometer and does not change) to an ADC pin of the ESP32-C6. A 10–100 nF capacitor from that pin to GND steadies the reading of this high-impedance node; it slows the signal by about a millisecond at most, irrelevant for paper moving over seconds.
- **Digital OUT** stays unconnected; the potentiometer setting does not matter (the indicator LED still helps when aiming).
- **ADC (ESP32-C6):** one 12-bit SAR ADC (ADC1, raw 0–4095) on GPIO0–GPIO6; the C6 has no ADC2, so Wi-Fi does not restrict the choice. Check which of GPIO0–6 the board brings out and leaves free. Effective resolution is about 9–10 bits; use the highest attenuation (12 dB), average several samples per reading and apply ESP-IDF calibration (`adc_cali`). The usable input range at 12 dB (roughly up to about 3 V) is to be confirmed from the C6 datasheet; the B-1 raw logs (FR-11.14) show whether the HW-201 signal stays inside it. (proposed; range assumed)
- **Broken analog wire:** a weak pull-down (about 1 MΩ) from the ADC pin to GND makes a broken or loose wire read near 0, which FR-11.12 reports as a sensor fault. Its load on the photodiode signal is small next to the module's own resistor. (proposed)
- **Signal sense:** for both sensors, much light at the photodiode means *clear* (rear: beam unbroken; front: reflector visible) and little light means *covered*. The module's indicator LED is therefore lit when clear, the opposite of its "obstacle" meaning — useful for aiming.
- **LED drive:** the module powers its IR LED permanently, so no LED-off reading is possible on the front sensor. The rear LED, already on its own wires, may instead be driven from a GPIO through a resistor, which allows LED-off readings for the rear sensor (FR-11.9).
- Short analog leads; the booth enclosure keeps room light off the sensors.

### 11.3 Requirements

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-11.1 | Must | Shall report one *rear pass* event each time paper leaves the back of the printer during a print pass. `[user]` | approved |
| FR-11.2 | Must | Shall report one *front eject* event when the front sensor becomes covered and stays covered for at least {{T_eject_settle}}; from then on the output is *occupied*. `[user]` | approved (value open) |
| FR-11.7 | Must | Shall report a *front protrusion* event when the front sensor is covered and becomes clear again within {{T_eject_settle}} (the sheet coming out at the front between passes, A8). `[user]` | approved |
| FR-11.8 | Must | Shall report *output cleared* when the front sensor goes from occupied to clear, and shall answer a request for the current output state (occupied / clear). `[user]` | approved |
| FR-11.9 | May | For a sensor whose LED is driven from a GPIO (possible for the rear sensor only, §11.2), the firmware may measure with the LED off and on and use the difference. | proposed |
| FR-11.17 | Must | The camera flash and changes in booth lighting shall not produce sensor events; with the module LEDs always on, this rests on the enclosure and the debounce of FR-11.10, and is verified in B-1. `[derived]` | approved |
| FR-11.10 | Must | A sensor's state shall come from its analog reading: it shall change between *clear* and *covered* only after the reading has crossed the matching firmware threshold for {{T_debounce}} without interruption, with separate thresholds for each direction (hysteresis). The module's potentiometer and digital OUT are not used. | approved (values open) |
| FR-11.11 | Should | While idle and clear, shall slowly follow the clear reading (dust, temperature, reflector wear) and keep the thresholds relative to it. | proposed |
| FR-11.12 | Must | When a sensor's clear reading falls below a minimum signal for {{T_fault}} (LED dead, misaligned, dirty or worn reflector), shall report a *sensor fault* for that sensor; the booth shows it as information ("Sensorfehler"), never as a printer error (FR-5.10). | approved |
| FR-11.13 | Must | The firmware thresholds, debounce time and {{T_eject_settle}} shall be adjustable over USB without reflashing and kept across power cycles. | approved |
| FR-11.14 | Must | Shall offer a raw logging mode: both sensors' analog readings (plus LED-off readings where FR-11.9 is used), with timestamps at {{F_raw}} or faster, alongside the events (for B-1). | approved |
| FR-11.15 | Should | Shall check that rear passes and front protrusions alternate as measured in B-1 (B-Q7), and send an *IR misread report* when they do not. | proposed |
| NFR-11.16 | Must | The front reflector shall lie flush on the tray with no raised or loose edge in the paper path, so it cannot catch a sheet; it shall be replaceable. `[derived]` | approved |
| FR-11.3 | Must | Shall send a liveness signal at least every {{T_alive}}/2. | proposed |
| FR-11.4 | Must | When the rear barrier stays blocked longer than {{T_rear_block}}, or no front eject follows the 4th rear pass within {{T_eject}}, the unit shall treat it as a *suspected jam* and wait {{T_confirm}} for a front eject. `[user]` | approved (values open) |
| FR-11.5 | Must | If no front eject occurs within {{T_confirm}} after a suspected jam, the unit shall send an *IR jam report* (information, FR-5.10). `[user]` | approved |
| FR-11.6 | Must | If a front eject does occur within {{T_confirm}} after a suspected jam, the unit shall send an *IR misread report* (information, FR-5.10). `[user]` | approved |

Not yet specified (needed before Phase 3): USB identification among camera and printer, boot time and behaviour after a reset mid-print (incl. the output state at boot), threshold values (from B-1 raw logs), event buffering while the booth is not listening, watchdog, firmware version reporting (Appendix D).

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
| FR-11.2 | One normal print | exactly 1 front-eject event, after the 4th rear pass | eject reported for a pass protrusion | bench (B-1) |
| FR-11.7 | One normal print | front-protrusion events as counted in B-Q7, none reported as eject | protrusion missing or reported as eject | bench (B-1) |
| FR-11.8 | Photo on the tray · take it | output cleared once | no event; several events | target |
| FR-11.17 | Sensor clear, booth closed · fire the booth flash and switch room lights on/off | no state change, no event | covered reported; event sent | bench (B-1) |
| FR-11.10 | Raw log of one print | state changes only where the analog reading crossed the thresholds for T_debounce | change without a threshold crossing | target |
| FR-11.10 | Hand waved through the rear beam for less than T_debounce | no event | rear pass reported | target |
| FR-11.12 | Disconnect the rear LED wires; separately, disconnect a sensor's analog wire | sensor fault within T_fault; booth shows "Sensorfehler" as information | printer error shown; printing blocked by it | target |
| FR-11.13 | Change a threshold over USB · power-cycle | new value kept | value lost; reflash needed | target |
| FR-11.3 | Unit running, booth idle | liveness at least every T_alive/2 | gap > T_alive/2 | target |
| FR-11.5 | Hold paper back during pass 2 (provoked jam) | IR jam report after T_confirm | IR misread report | bench (B-1) |

## 12. Operator Panel (minimal fallback)

### 12.1 Purpose

Fallback for when the phone or the booth Wi-Fi is not available (R10). Lets the operator, standing at the opened back of the booth, see the essentials of the current print and make the "Foto ist da" / "Foto fehlt" decision. Rotary encoder and small I²C OLED on the ESP32 unit, mounted inside the booth. Everything beyond that (full error text, history, counters, settings) is on the web UI only (§12b). `[user]`

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-12.1 | Must | Shall show the current stage, copy k of N, and the short error text (Appendix C) when there is one. `[user]` | approved |
| FR-12.2 | Must | Shall show the evidence of FR-5.14 ("Foto erkannt" after the error). `[derived]` | approved |
| FR-12.3 | Must | While a copy is in `ERROR` or `AWAIT_OPERATOR`, shall offer the decisions "Foto ist da" and "Foto fehlt". `[user]` | approved |
| FR-12.4 | Must | A decision shall be sent only after it is selected with the encoder and confirmed with a press. | proposed |
| FR-12.5 | Must | Panel texts shall be German (Appendix C). `[user]` | approved |
| FR-12.6 | Must | While the booth is not reachable, the panel shall say so and offer no decisions. | proposed (OD8) |
| FR-12.7 | Must | When the decision for the current copy was made on the phone, the panel shall show it and offer no decisions for that copy. `[derived]` FR-10.8 | approved |
| FR-12.8 | May | Shall show IR information (jam, misread) as one short line. | proposed |

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| FR-12.1 | `ERROR` on copy 2/3, `Paper Feed (02)` | stage, 2/3 and the short error shown | missing field | target |
| FR-12.3 | `ERROR` · open panel | both decisions offered | no decisions; decisions offered in `LAYER_n` | target |
| FR-12.4 | Turn encoder to "Foto ist da", no press | nothing sent | decision sent on turn | target |
| FR-12.6 | Booth link down | "Booth nicht verbunden", no decisions | decision accepted locally | target |
| FR-12.7 | `ERROR` · decide on the phone | panel shows "Am Handy entschieden: …", no decisions | decisions still offered | target |

## 12b. Operator Web UI (phone, main interface)

### 12b.1 Purpose

The operator's main interface: a printer status page served by the booth laptop and opened on a phone on the booth Wi-Fi. Shows everything the booth knows about the current print in full and takes the operator's decisions. Works while the ESP32 unit is offline. `[user]`

Access reuses upstream's admin login `[user]`: the operator opens the login page (`login/`), logs in with the admin login, and reaches upstream's menu (`login/menu.php`), where gallery, admin panel, slideshow etc. are already listed `[code]`. That menu gets a new **"Drucker"** button that opens the printer status page. `[user]` Upstream sessions last 48 h (`lib/boot.php`) `[code]`, so one login covers an event.

### 12b.2 Requirements

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-12b.1 | Must | Shall be reachable from a phone browser on the booth Wi-Fi through the upstream login page, whose address is printed on a label inside the booth; no app installed. `[user]` | approved |
| FR-12b.2 | Must | Shall use the upstream admin login (`login/`, username/password or keypad PIN as configured, with upstream's login throttling); without a valid admin session the printer status page shall show nothing and accept no decision, and shall send the browser to the login page. `[user]` R11 | approved |
| FR-12b.13 | Must | The upstream menu after login (`login/menu.php`) shall show a "Drucker" button that opens the printer status page, under the same condition as the admin panel button. `[user]` | approved |
| FR-12b.14 | Must | A login on the phone shall stay valid for at least 24 h (upstream: 48 h, `lib/boot.php` `[code]`); the printer status page shall not log the operator out by itself. (Upstream's menu page logs out after 60 s `[code]`; that stays on the menu only.) `[user]` | approved |
| FR-12b.15 | Should | The printer status page shall have a link back to the menu. | proposed |
| FR-12b.16 | Must | Shall show the output state (occupied / clear / unknown) and each sensor's fault state; while a copy has waited in `WAIT_OUTPUT` longer than {{T_output_wait}}, shall show "Foto liegt noch im Drucker – bitte entnehmen". `[derived]` FR-5.16 | approved (value open) |
| FR-12b.3 | Must | Shall show: current stage, copy k of N, the full CUPS error text with the Appendix B guest text and staff hint, IR information, the evidence of FR-5.14 with timestamps, ESP32 unit online/offline, and whether the CUPS queue is stopped. `[user]` | approved |
| FR-12b.4 | Must | While a copy is in `ERROR` or `AWAIT_OPERATOR`, shall offer "Foto ist da" and "Foto fehlt"; a decision shall be sent only after a second confirmation step naming the copy ("Kopie 2 von 3: Foto ist da – bestätigen?"). `[user]` | approved |
| FR-12b.5 | Must | Shall show status and accept decisions while the ESP32 unit is offline. `[derived]` R10, OD1 | approved |
| FR-12b.6 | Must | Shall update within {{T_ui}} of a state change without a manual reload. | approved (value open) |
| FR-12b.7 | Must | When the page loses its connection to the booth, it shall say so ("Keine Verbindung zur Booth") and offer no decisions until it is connected again and shows current data. | proposed |
| FR-12b.8 | Must | When the decision for the current copy was made on the panel, shall show it and offer no decisions for that copy. `[derived]` FR-10.8 | approved |
| FR-12b.9 | Must | Texts German; layout made for phone width with touch targets large enough to use with one hand. `[user]` | approved |
| FR-12b.10 | Should | Shall list the print requests of the current event: requested, printed and failed copies, and each operator decision with its source (FR-14.2). | proposed |
| FR-12b.11 | Must | Shall show the supply counts, let the operator set and correct them, and ask "Blatt verbraucht?" after "Foto fehlt" (FR-8.2, FR-8.3, FR-8.9, FR-8.10). `[user]` | approved |
| FR-12b.12 | Must | Shall not offer actions beyond those in this section (no cancelling jobs, no re-enabling the queue directly, FR-6.11). `[derived]` R3 | proposed |

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| FR-12b.2 | Not logged in · open the printer status page directly | sent to login page | status or decisions shown | integration |
| FR-12b.2 | Not logged in · POST a decision to the booth | refused, logged | decision applied | integration |
| FR-12b.13 | Log in on the phone | menu shows "Drucker" next to gallery and admin panel; tapping it opens the printer status page | button missing; button shown without login (phone) | integration |
| FR-12b.14 | Logged in, printer status page open for 30 min | still logged in, page still updating | logout; login page shown | integration |
| FR-12b.3 | `ERROR` on copy 2/3, `Paper Feed (02)`, rear pass recorded after the error | all listed fields shown, evidence with time | missing field | integration |
| FR-12b.4 | `ERROR` · tap "Foto ist da", no confirmation | nothing sent | decision sent on first tap | integration |
| FR-12b.4 | `LAYER_2` · open page | no decisions offered | decisions offered | integration |
| FR-12b.5 | Simulated unit offline, `ERROR` · decide on the web UI | decision applied; "Sensor offline" shown | decision refused | integration |
| FR-12b.6 | Page open · inject rear pass | new stage within T_ui, no reload | stale stage | integration |
| FR-12b.7 | Page open, `ERROR` · cut the connection | "Keine Verbindung zur Booth", decision buttons gone | decision sent from stale page | integration |
| FR-12b.12 | Any state | no cancel / enable buttons | queue re-enabled from the web UI | integration |

---

# Part C — Foundation (L0)

## 13. CUPS + Gutenprint, USB host link

Configured, not owned: the `selphy` queue (photobooth-setup `native/setup-printer.sh`), the Gutenprint `canon-cp1300` backend, USB enumeration of printer and ESP32 unit. Tested transitively through §9 and §10.

| ID | Pri | Requirement | Status |
|---|---|---|---|
| NFR-13.1 | Must | Queue options stay identical between Docker (`cups/entrypoint.sh`) and native (`setup-printer.sh`). `[code]` | approved |
| NFR-13.2 | Must | The Gutenprint backend shall run with `FAST_RETURN=0` (CUPS `SetEnv`), so it stays attached for the whole print and reports errors during it. `[derived]` R2 | approved (pending B-Q1) |
| NFR-13.3 | Must | Printing, the print screen and the panel shall keep working when the booth router or its Wi-Fi is down; only the operator web UI is lost. `[derived]` R10 | approved |

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| NFR-13.3 | Print running · switch the router off | print completes; panel decisions work after a provoked error | print or tracker stops | bench |

---

# Part D — Cross-cutting

## 14. Logging

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-14.1 | Must | Every stage change, error, IR report and operator decision shall be logged with timestamp, job ID, copy k of N and source (CUPS/IR/operator phone/operator panel/timeout); ignored decisions (FR-10.7, FR-10.8) included. | approved |
| FR-14.2 | Should | Logs shall let staff reconstruct after an event how many copies were requested, printed, and failed. `[derived]` event findings | proposed |

## 15. Security

Threat model: physical booth at a private event; ESP32 unit on local USB; the operator web UI on the booth Wi-Fi, which only the laptop and the operator phone join `[user]`. Accepted risk: someone with physical access can fake IR events or panel decisions. (assumed)

| ID | Pri | Requirement | Status |
|---|---|---|---|
| NFR-15.1 | Must | The booth Wi-Fi shall be WPA2 (or better) with a password known only to staff; guests are never given it. `[user]` | approved |
| NFR-15.2 | Must | The operator web UI shall require the upstream admin login (FR-12b.2), also from the booth Wi-Fi; at events `login.enabled` and admin protection (`protect.admin`) shall be on. A localhost exception for the booth laptop itself (`protect.localhost_admin` off) is allowed, as for the admin panel. `[derived]` R11 | approved |
| NFR-15.3 | Must | The operator web UI shall not be reachable from the internet (no port forwarding, not exposed through the public photo/QR host). `[derived]` | approved |

---

# Part E — Operations & Verification

## 16. Operational procedures (reading path)

Set up printer with `FAST_RETURN=0` (§13) → plug in ESP32 unit (§11) → switch on the booth router, log in on the phone with the admin login and open "Drucker" in the menu (§12b) → guest prints (§5, §7) → CUPS error appears (§6, Appendix B), shown on the phone → operator opens the back, fixes the printer, presses the printer's print button → confirms on the phone (§12b), or on the panel if the phone is not available (§12) → booth reconciles and continues (FR-6.4, FR-5.12).

## 17. Verification & Validation

### 17.0 Test architecture

| Tier | Where | Covers |
|---|---|---|
| unit | CI, no hardware | §5 state model, §7 text mapping, gate logic as pure functions |
| host | CI, ESP32 firmware logic built for the host | §11 jam/misread logic, §12 menu logic |
| integration | Virtual test setup (§17.1) on the Docker stack, driven through the booth's HTTP API and CUPS | §5–§7, §9, §10, §12b, exactly-once contracts in simulation |
| e2e | Virtual test setup (§17.1) with real browsers (Playwright) acting as guest, admin and operator, the way people use the booth at an event | §6–§7, §12b, §17.5, exactly-once contracts as seen on screen |
| target | The ESP32 unit on the desk, booth simulated | §11 liveness, §12 panel |
| bench | Event laptop + real SELPHY + real ESP32 unit | B-1 (§17.3), §5.4 contracts on hardware, Appendix A |

Traceability is generated from the requirement IDs in this document and the test plan (`testing/test-plan.yaml`, owned by `/build`) — not maintained by hand here.

**Where tests live:** all test code, scenarios, simulators and test workflows live in this fork (`starkerflamingo32/photobooth`, public), under `testing/` and `.github/workflows/`. `[user]` photobooth-setup (private) stays the deployment repo; the fork's CI checks it out read-only at a pinned commit to build the `cups` and `dummy-dslr` images, so the queue options under test are the deployed ones (NFR-13.1). `[derived]`

### 17.1 Virtual test setup

Required before booth logic is built (Phase 1). `[user]`

| Part | What it does |
|---|---|
| Docker stack | photobooth-setup's `compose.yaml` services (photobooth built from the commit under test, `cups`, `dummy-dslr`), plus the two simulators below, from a test compose file in `testing/`. The fake DSLR already lets the booth take photos without a camera `[code]`. |
| Simulated SELPHY | A CUPS backend `sim-selphy://` replacing the USB device of the `selphy` queue; same PPD and queue options as `cups/entrypoint.sh`. It reads a scenario (per job: accept and finish after *t* s; exit with `CUPS_BACKEND_STOP` and an Appendix B message, leaving the job pending; `CUPS_BACKEND_CANCEL` for the `0x0A` case; fast return on/off) and logs every job it receives, so a duplicate is visible. Real CUPS stays in the loop, so stopped queues, pending jobs and `cupsenable` behave as on the laptop. |
| Simulated ESP32 unit | Sends every §10 event (rear pass, front protrusion, front eject, output occupied / cleared, IR reports, sensor fault, panel decisions, liveness) on a script, timed against the simulated SELPHY's jobs; holds an output state that tests set (photo left on the tray) and clear (photo taken); can go silent (offline). Speaks the real unit protocol once it is decided (§2.3). |
| Scenario files | One YAML file per test: printer behaviour per job, unit events, and the browser steps (§17.1.1). Reused unchanged against upstream and against `booth`. |
| Simulated clock | Print timings scaled down (e.g. 1 s per pass) so a run takes minutes, not hours; Appendix A limits scaled the same way. |

#### 17.1.1 Browser test setup (e2e)

Real browsers do what people do at an event, against the virtual stack. `[user]`

| Browser | Viewport | Acts as | Typical steps |
|---|---|---|---|
| Booth | Acer Switch 3 screen, kiosk page | Guest | Start a photo, wait for the countdown, press "Drucken", choose copies, watch the print screen, open the gallery and print again |
| Admin | Desktop | Staff setting up | Log in, change `print.limit`, `print.max_multi`, `print.auto`; unlock printing, raise the limit, reset the print database |
| Operator phone | Phone size, touch | Operator | Log in, open "Drucker" in the menu (FR-12b.13), read the error, confirm "Foto ist da" / "Foto fehlt" |
| Stale page | Booth viewport | Guest on an old page | Gallery or result page opened before a print started, used later (FR-6.8, FR-6.9) |

- Several browsers run at the same time in one test, so cross-page effects (busy gate, stale pages, phone vs. panel) are tested as they happen.
- Each test checks what is on screen (texts from Appendix B/C, enabled/disabled buttons) and what reached the simulated SELPHY (number of jobs, duplicates).
- Each run keeps, per browser: video, screenshots at every step, a Playwright trace; plus the CUPS `error_log`, the simulated SELPHY job log, the tracker log and the booth's `data/` print files (`printed.csv`, `print.count`, `print.lock`).
- Test data is synthetic only (fake-DSLR images, test passwords); no event photos or real credentials, since artifacts of the public fork can be downloaded by others. `[derived]`

### 17.2 GitHub Actions plan

All workflows run in the fork. `[user]`

| Workflow | Trigger | Does | Time |
|---|---|---|---|
| `ci.yml` | every push / PR to `booth` | lint (upstream `linters.yaml` rules), PHPUnit, JS unit tests, ESP32 host tests (once firmware exists) | ~3 min |
| `e2e.yml` | every push to `booth`, by hand | build the virtual stack (§17.1), run integration + e2e suites; upload Playwright report, videos, traces and logs as artifacts; failed tests listed in the run summary | ~15 min (assumed) |
| `upstream-characterisation.yml` | by hand, input: refs (default upstream `dev` 49b09a94, `76d76e36^`, and `booth`) | run the §17.5 suite against each ref; publish the L-Q answers as the run summary and an artifact | ~15 min (assumed) |
| `upstream-drift.yml` | weekly | merge upstream `dev` into a scratch branch, run `ci.yml` + `e2e.yml`; open an issue if anything breaks | |
| `package.yml` (existing, adapted) | tag | prebuilt release package so the laptop needs no npm/gulp build | |
| shellcheck | in photobooth-setup, on push | `native/*.sh` | |

- Upstream workflows not needed by the fork (crowdin ×3, `publish_docs.yml`, `build-faq.yml`, `release.yml`, `installer.yaml`) are disabled.
- Access to the private photobooth-setup repo: a fine-grained token, read-only on that one repo, stored as an Actions secret. Secrets are not given to pull requests from other forks, so those runs skip `e2e.yml` and run `ci.yml` only. (R13)
- **Not possible in Actions:** USB hardware — the real SELPHY and ESP32 unit (target, bench, acceptance); deploying to the laptop. No self-hosted runner on the booth laptop: on a public repo it would run code from strangers' pull requests. `[derived]`
- Public repo: standard GitHub-hosted runners cost nothing. `[code]` (repository visibility)

### 17.3 Bench session B-1 — real printer, five prints

Run once, when the ESP32 unit with IR barriers is built, so timings are measured in the same session. Five prints answer the `FAST_RETURN=0` safety question and all real-printer questions without wasting paper. `[user]`

**Preconditions:** `FAST_RETURN=0` set for CUPS; ESP32 unit logging every IR event with a timestamp, plus raw sensor readings (FR-11.14); booth lighting and flash as at an event; CUPS `LogLevel debug`; a script logging `lpstat -l -p selphy` and `lpstat -W all -o` every second; phone video of the printer front and back.

| # | Print | Questions answered |
|---|---|---|
| 1 | Normal print | B-Q1, B-Q2 |
| 2 | Normal print (second timing sample); fire the booth flash several times during it; leave the photo on the tray, then request print 3 | B-Q1, B-Q2, B-Q7, FR-5.16 |
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
| B-Q4 | Does pressing the printer's print button after the fix print the photo? Do IR events show it? After the provoked jam (print 5), was an ink panel set used up (check the cassette's ribbon), and how many sheets were lost? (A4, A7, FR-5.14, FR-8.10) |
| B-Q5 | With the job still pending, does `cupsenable` send it again (duplicate)? What does CUPS report afterwards? (R3, FR-6.4) |
| B-Q6 | Does backend status `0x0A` (job cancelled instead of queue stopped) occur, and for what? Recorded only if it happens. (R9, OD6) |
| B-Q7 | Front sensor: how many front protrusions per print and in which order with the rear passes; longest protrusion vs. time from eject to steady (sets `T_eject_settle`); analog levels clear / sheet for both sensors under booth light and flash (sets the firmware thresholds); any false event from the flash? (A8, FR-11.2, FR-11.7, FR-11.10, FR-11.15, FR-11.17) |

**Exit:** B-Q1…B-Q5 and B-Q7 answered (B-Q6 if seen); Appendix A filled; results added to this FSD's lifecycle (Appendix D, `related_test_baseline`).

### 17.4 Acceptance (before the next event)

1. 10 consecutive single-copy prints: every stage shown, 10 photos out, no false IR information.
2. One request with 3 copies: "Kopie k von 3" shown, 3 photos out.
3. Each error in Appendix B provoked once on the real printer: correct text on the booth screen and the phone, new prints rejected, photo/QR still work, operator fixes the printer and confirms on the phone (at least one error confirmed on the panel instead), exactly one photo per requested copy.
4. A gallery page loaded before a print started: its print button is refused while the print runs.
5. ESP32 unit unplugged mid-print: behaviour per OD1; the phone still shows status and takes decisions.
6. Booth router switched off mid-print: the print completes; a following error is resolved on the panel (NFR-13.3).
7. Phone and panel decide the same error within a few seconds: only the first decision is applied, no duplicate (FR-10.8).
8. Paper counter: load 3 sheets and enter 3 on the phone; print 3 photos — low warning shown, then the 4th request is refused with the refill notice; refill, enter 18, printing continues; empty the tray with the count at 5 → "Papiereinzug gestört" shown, as expected for a wrong count (FR-8.6).
9. Printer output: a request with 2 copies, photo 1 left on the tray for a minute — "Kopie 1 von 2 fertig – bitte Foto entnehmen" stays, copy 2 starts when it is taken; a single print with the previous photo still on the tray — "Bitte zuerst das Foto aus dem Drucker nehmen", printing starts when it is taken; a single print not taken — "Fertig" disappears after T_take_hint.

### 17.5 Upstream characterisation (print limit, auto-print, rate limit)

**Purpose:** since v0.7 the print limit is not used (§8, FR-8.8), so this no longer decides whether to reuse it. It (a) confirms why the booth stayed locked at the 2026-11-22 event, (b) checks that upstream's limit is really inert with `print.limit` = 0, and (c) records how auto-print and upstream's per-session print rate limit behave, which still matter (§1.4, FR-7.17). Characterisation only: it records what happens. `[user]`

**Runs:** in the virtual test setup (§17.1) with the e2e browsers (§17.1.1), via `upstream-characterisation.yml` (§17.2), against:

- upstream `dev` 49b09a94 (current base of `booth`, includes fix #1667 to the limit threshold);
- upstream `76d76e36^` (just before #1667), for the event failure only (L-Q1);
- `booth`, once Phase 2 is done.

**What the code suggests** (read in `api/print.php`, `src/Service/PrintManagerService.php`, `api/printLimit.php`, `api/admin.php`, `lib/configsetup.inc.php`, `assets/js/core.js`, `assets/js/tools.js`; not yet observed) `[code]`:

- With `print.limit` > 0, a print is counted when CUPS accepts it; at an exact multiple the booth locks (`print.lock`). Before #1667, the threshold after unlocking was `ceil(count/limit)*limit` = the current count, so every further print was refused with "Unable to print 1 copies" — matching the event log.
- The unlock button deletes only the lock; the count stays. The admin reset only touches the count when its "print database" checkbox (expert view, off by default) is ticked.
- With `print.limit` = 0 the limit code is skipped; the lock check (`print.lock` exists) still runs on every print.
- Auto-print (`print.auto`, only with filters disabled) prints 1 copy `print.auto_delay` ms after the result is rendered.
- `api/print.php` refuses prints above a per-session rate (`Rate limit exceeded…`, English).

**Questions:**

| ID | Question | Feeds |
|---|---|---|
| L-Q1 | Event failure: with `print.limit` = 18, after the 18th print, unlock with the booth's unlock button — are further prints refused on `76d76e36^`, and allowed on 49b09a94? Same after an admin reset without and with the "print database" checkbox. | §8.1 (cause), upstream bug report |
| L-Q2 | With `print.limit` = 0: is printing ever locked or refused by limit code? Does a `print.lock` left over from earlier use still block printing? | FR-8.8 |
| L-Q3 | Auto-print: does it respect a busy printer and a stopped queue? Does reloading the result page print the same photo again? | §1.4, G0 |
| L-Q4 | Does the per-session rate limit refuse legitimate prints (gallery reprints in a row, several copies)? What does the guest see? | FR-7.17, FR-7.3 |

**Scenarios** (each a scenario file, §17.1):

| # | Scenario | Answers |
|---|---|---|
| S1 | Limit 18 · 18 prints · unlock button · print again (both upstream refs) | L-Q1 |
| S2 | Limit 18 · 18 prints · admin reset without, then with "print database" · print again | L-Q1 |
| S3 | Limit 0 · 20 prints; then place a stale `print.lock` · print | L-Q2 |
| S4 | `print.auto` on, `auto_delay` 2 s · take a photo, reload the result page during and after the delay; repeat with the queue stopped and with a print running | L-Q3 |
| S5 | Limit 0 · 10 gallery reprints in quick succession; one request with 3 copies | L-Q4 |

**Evidence per scenario:** browser videos and traces, the simulated SELPHY job log (jobs received, duplicates), CUPS `error_log`, and `printed.csv` / `print.count` / `print.lock` before and after each step.

**Exit:** L-Q1…L-Q4 answered; results recorded in Appendix D (`related_test_baseline`). If L-Q1 shows the failure still on 49b09a94, the owner decides whether to report it upstream. L-Q2 must confirm the limit is inert before the next event (else FR-8.8 needs more than `print.limit` = 0). L-Q3 is input for whether auto-print is ever used.

---

## Appendix A — Timing parameters (to be measured in B-1)

| Parameter | Meaning | Value |
|---|---|---|
| `T_sent_to_pass1` | max time from SENT to first rear pass (stall limit) | TBD (B-Q2) |
| `T_pass` | max time between rear passes (stall limit) | TBD (B-Q2) |
| `T_rear_block` | rear barrier blocked longer than this → suspected jam | TBD (B-Q2) |
| `T_eject` | max time from 4th rear pass to front eject | TBD (B-Q2) |
| `T_eject_settle` | front sensor covered at least this long → front eject (shorter → front protrusion) | TBD (B-Q7) |
| `T_debounce` | a sensor reading must stay past its threshold this long to change state | TBD (proposed: 20 ms) |
| `F_raw` | sample rate of the raw logging mode (not a timing limit) | proposed: 50 Hz |
| `T_fault` | clear reading below minimum this long → sensor fault | TBD (proposed: 10 s) |
| `T_take_hint` | how long "Fertig – bitte Foto entnehmen" stays after the last copy | TBD (proposed: 10 s) |
| `T_output_wait` | `WAIT_OUTPUT` longer than this → notice on the phone | TBD (proposed: 30 s) |
| `T_confirm` | wait for a front eject after a suspected jam | TBD |
| `T_alive` | ESP32 unit considered offline after this silence | TBD |
| `T_cups_poll` | max delay for a CUPS error to reach the tracker | TBD |
| `T_ui` | max delay from event to screen (booth screen and operator web UI), and for print buttons to change state | TBD |
| `T_resume` | max delay from operator confirmation to queue enabled | TBD |
| `LOW_THRESHOLD` | supply count at which the low warning starts (not a timing value; FR-8.2) | TBD (proposed: 3) |
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
| `DONE` (last copy, for T_take_hint) | Fertig – bitte Foto entnehmen |
| `DONE` of copy k < N, photo not yet taken (FR-7.19) | Kopie k von N fertig – bitte Foto entnehmen |
| `WAIT_OUTPUT`, first copy (FR-7.20) | Bitte zuerst das Foto aus dem Drucker nehmen |
| `AWAIT_OPERATOR` | Druck wird geprüft – bitte Personal rufen |
| `CANCELLED` | Druck abgebrochen |
| copies (N > 1) | Kopie k von N |
| busy rejection (OD5) | Drucker beschäftigt – bitte warten, bis der aktuelle Druck fertig ist |

**Printer errors (from CUPS)** — staff hint for all: fix the problem, press the printer's print button, then confirm on the phone or the panel in the booth (FR-7.7).

| Trigger | Guest text | Staff hint |
|---|---|---|
| `Paper Feed (02)`, paper left 0 (FR-8.6) | Papier leer | Papier auffüllen, Anzahl am Handy eingeben, Drucktaste am Drucker drücken, dann am Handy oder im Bedienfeld bestätigen. |
| `Paper Feed (02)`, paper left > 0 | Papiereinzug gestört | Papier prüfen und neu einlegen, Drucktaste am Drucker drücken, dann am Handy oder im Bedienfeld bestätigen. |
| `Paper Feed (02)`, count unknown `[code]` | Papier leer oder Papiereinzug gestört | Papier prüfen/auffüllen, Drucktaste am Drucker drücken, dann am Handy oder im Bedienfeld bestätigen. |
| `Ink Cassette Empty (06)` `[code]` | Farbkassette leer | Farbkassette tauschen, Drucktaste am Drucker drücken, dann am Handy oder im Bedienfeld bestätigen. |
| `No Paper and Ink (09)` `[code]` | Papier- oder Farbkassette fehlt | Beide Kassetten prüfen, Drucktaste am Drucker drücken, dann am Handy oder im Bedienfeld bestätigen. |
| `Paper jam (0b)` `[code]` | Papierstau | Papier vorsichtig entfernen, Drucktaste am Drucker drücken, dann am Handy oder im Bedienfeld bestätigen. |
| printer not connected / not found (assumed) | Drucker nicht verbunden | USB-Kabel und Strom prüfen. |
| any other, e.g. `Unknown Error (08)` `[code]` | Druckerproblem – bitte Personal rufen | *(raw CUPS message)* |

**Information (not errors)**

| Source | Trigger | Text |
|---|---|---|
| IR | jam report (FR-11.5) | Hinweis: Sensor meldet möglichen Papierstau |
| IR | misread report (FR-11.6) | Hinweis: Sensor war kurz verdeckt |
| Tracker | stall (FR-5.5) | Drucker reagiert nicht – bitte Personal rufen |
| Unit | offline (FR-7.8) | Sensor offline *(staff notice)* |
| Unit | sensor fault (FR-11.12) | Sensorfehler hinten / vorne *(staff notice)* |
| Counter | low (FR-8.2) | Papier fast leer – noch n Blatt / Farbkassette fast leer – noch n Drucke *(staff notice)* |
| Counter | zero, request refused (FR-8.4) | Drucker wird nachgefüllt – bitte Personal rufen |

## Appendix C — Operator texts (German; wording `proposed`)

**Panel (minimal, §12)**

| Situation | Text |
|---|---|
| Idle | Bereit |
| Printing | Kopie k/N – Gelb (1/4) … Schutzschicht (4/4) |
| Printer error | Fehler: *(Appendix B guest text, shortened to fit)* |
| Evidence after error | Foto erkannt |
| Decision prompt | Foto ist da · Foto fehlt |
| Confirmation | Gedrückt halten zum Bestätigen *(or: Drücken zum Bestätigen, FR-12.4)* |
| Decided on the phone | Am Handy entschieden: Foto ist da / Foto fehlt |
| Booth not reachable | Booth nicht verbunden |

**Web UI (phone, §12b)** — additionally shows the full Appendix B texts, the raw CUPS message and the evidence list.

| Situation | Text |
|---|---|
| Decision confirmation | Kopie k von N: Foto ist da – bestätigen? / Kopie k von N: Foto fehlt – nochmal drucken? |
| Decided on the panel | Im Bedienfeld entschieden: Foto ist da / Foto fehlt |
| Connection lost | Keine Verbindung zur Booth |
| Output state (FR-12b.16) | Ausgabe: frei / Foto liegt im Drucker / unbekannt |
| Waiting for removal > T_output_wait | Foto liegt noch im Drucker – bitte entnehmen |
| Unit offline | Sensor offline |
| Queue stopped | Druckwarteschlange angehalten |
| After "Foto fehlt" (FR-8.10) | Blatt verbraucht? · Ja / Nein *(Ja: Papier −1, bei begonnenem Druck auch Farbe −1)* |

## Appendix D — Lifecycle metadata

```yaml
document_status: draft
fsd_version: 0.12
repository: starkerflamingo32/photobooth (branch booth)
baseline_commit: e8c2cbd3
applicable_firmware_version: none yet (ESP32 unit not built; board ESP32-C6 mini)
author: booth owner, with Claude
reviewers: []
approval_status: not approved
created: 2026-11-23
last_updated: 2026-11-25
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
  - 2026-11-25 v0.4: operator web UI on a phone on the booth Wi-Fi as the main operator interface (§12b);
      operator panel reduced to a minimal fallback (§12); first-decision-wins between phone and panel (FR-10.8);
      printing independent of Wi-Fi (NFR-13.3); security requirements for the booth Wi-Fi and web UI (§15);
      OD9 (web UI login) added; OD1 gains the web UI option; R10–R12 added; acceptance items 6–7 added
  - 2026-11-25 v0.5: OD9 decided — the web UI reuses the upstream admin login (48 h sessions); a "Drucker" button
      in the upstream login menu opens the printer status page (FR-12b.13); no self-logout on that page (FR-12b.14)
  - 2026-11-25 v0.6: tests live in the fork (§17.0); virtual test setup detailed (simulated SELPHY backend, simulated
      ESP32, scenario files) and browser test setup with guest/admin/operator browsers (§17.1, new e2e tier);
      GitHub Actions plan rewritten (§17.2); upstream print-limit characterisation added (§17.5, L-Q1…L-Q10, S1…S9); R13
  - 2026-11-25 v0.7: no fixed print limit (booth not rented out); upstream limit off (FR-8.8); paper/ink counters
      rewritten as Must and moved into Phase 2 (§8), counting at DONE, block at zero, empty tray vs misfeed (FR-8.6);
      Phase 5 removed; §17.5 reduced to the event failure, limit=0 check, auto-print and rate limit (L-Q1…L-Q4,
      S1…S5); OD10 added; baseline corrected to upstream dev 49b09a94
  - 2026-11-25 v0.8: OD10 decided — after "Foto fehlt" the phone asks "Blatt verbraucht?" (FR-8.10); A7 added;
      B-Q4 extended (ink use after a jam)
  - 2026-11-25 v0.9: sensor hardware (§11.2: rear through-beam ~1–2 cm behind the slot, front reflective sensor over a
      reflector on the tray, analog receivers on ESP32 ADC1); A8 paper path (out back and front each pass, fully
      retracting); front protrusion / front eject / output cleared events; firmware requirements FR-11.7…11.15,
      NFR-11.16; OD11 decided as A: a copy is sent only with the output clear (`WAIT_OUTPUT`, FR-5.16), print screen
      waits between copies (FR-7.19), "Fertig" for T_take_hint after the last copy (FR-7.18), "Bitte zuerst das Foto
      aus dem Drucker nehmen" before a new request (FR-7.20); B-Q7; new Appendix A values
  - 2026-11-25 v0.10: sensors are HW-201 modules at 3.3 V with an added analog wire; rear module rebuilt as a
      through-beam barrier (IR LED on wires below the paper); §11.2 rewritten; FR-11.9 LED-off subtraction now May
      (rear only); FR-11.17 flash immunity, FR-11.18 digital/analog cross-check added; state from the analog reading
  - 2026-11-25 v0.11: FR-11.18 removed (digital OUT not used, little value); weak pull-down on each ADC pin so a
      broken analog wire shows as a sensor fault (FR-11.12)
  - 2026-11-25 v0.12: ESP32 board is an ESP32-C6 mini: ADC1 on GPIO0–6, 12-bit, 12 dB attenuation, calibration;
      classic-ESP32 pin references (GPIO 32–39, ADC2 / Wi-Fi) removed
superseded_requirements:
  - FR-11.18 v0.10 (digital OUT / analog cross-check) — removed; broken wire caught by a pull-down and FR-11.12
  - FR-11.9 v0.9 (Must: LED-off/LED-on subtraction for both sensors) — May, rear only; HW-201 LEDs are always on (FR-11.17)
  - FR-8.1–8.4 v0.6 (Should/May, later phase) — rewritten as §8.2 v0.7
  - Phase 5 (supply counters / print limit) — counters in Phase 2, no print limit
  - §17.5 v0.6 L-Q1…L-Q10, S1…S9 — reduced to L-Q1…L-Q4, S1…S5
  - FR-12.1 v0.3 (panel shows the full CUPS error text) — short text on the panel, full text on the web UI (FR-12b.3)
  - FR-12.2 v0.3 (panel must show IR information) — now May (FR-12.8); IR information shown on the web UI
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
related_test_baseline: none yet (B-1 and §17.5 not run)
```

## Related

- photobooth-setup: `event-2026-11-22-findings.md`, `paper-ink-counter.md`, `native/setup-printer.sh`, `native/selphy-autoresume.sh`, `cups/entrypoint.sh`
- This fork: `api/print.php`, `src/Service/PrintManagerService.php` (current print path, for reference only); `api/printLimit.php`, `api/printDB.php`, `api/admin.php`, `assets/js/core.js`, `assets/js/tools.js` (print limit, auto-print; §17.5); `login/menu.php`, `lib/boot.php` (§12b); `testing/`, `.github/workflows/` (tests, §17)
- Gutenprint source: `src/cups/backend_common.c` (`FAST_RETURN`), `src/cups/backend_canonselphyneo.c` (CP1300 status loop)
