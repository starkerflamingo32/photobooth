# Photobooth Booth — Functional Specification Document (FSD)

**Scope of this FSD:** a custom print stack for the booth: every photo printed exactly once, one print at a time, and a truthful print screen (SELPHY CP1300 via CUPS, print status taken from CUPS and from the printer itself by the booth laptop, and an operator web UI on a phone on the booth's own network). **No ESP32 unit** since v0.16 `[user]`; the version with the ESP32 unit (IR barriers, operator panel) is frozen as [`fsd-esp32-v0.15.md`](fsd-esp32-v0.15.md).
**Fork:** starkerflamingo32/photobooth, branch `booth` (upstream `dev` 49b09a94 merged in 1d49f007 on 2026-11-17; code otherwise unchanged).
**Status:** DRAFT — requirements only. Implementation and code architecture are deliberately not decided yet (§2.3), except the decisions listed there.

| Lifecycle | |
|---|---|
| Created | 2026-11-23 |
| Last updated | 2026-11-27 |
| Load defined | **No** — B-1 done 2026-11-27 (§17.3.2), Appendix A complete, all owner decisions made (R18 decided in v0.29). Still missing: Method/UserDocumentation planes (`/define --planes`); then a final run of the full gate (`/define` §12.5). Every Must/Should has a contract since v0.33. Not blocking: print-limit characterisation (§17.5) runs in Phase 1, since it needs the virtual test setup and FR-8.8 already states the requirement; auto-resume (FR-6.15) is checked at the first event (E-1, §17.6). |

Full lifecycle metadata: Appendix D.

**Provenance tags:** `[user]` decided by the booth owner (interviews 2026-11-23/24/27 or earlier notes) · `[code]` observed in code, logs or on the real printer · `[derived]` follows from a `[user]`/`[code]` item · `(assumed)` inferred, must be confirmed.
**Status values:** `approved` (user decided) · `proposed` (written by the assistant, awaiting acceptance) · `superseded` (listed in Appendix D only).

---

## 1. System Overview

### 1.1 Purpose / problem

Today the booth reports a print as successful as soon as CUPS has **received** the job `[code]`:

- `api/print.php` returns `{"status":"ok"}` once the job is queued, not when paper comes out.
- CUPS marks the SELPHY job *completed* ~10–17 s in (10.4 s in P-1, §17.3.1, photo out at ~58 s), long before the printer is finished. Cause: the Gutenprint backend (`gutenprint53+usb`, Gutenprint 5.3.4) runs in *fast return* mode by default under CUPS and exits as soon as the printer starts printing. After that, CUPS sees no error of that print at all. `[code]` (§9, R2)
- On paper/ink errors the backend **stops the queue** and leaves the job pending; the guest still saw "success". At the 2026-11-22 event the queue stopped 4× (`Paper Feed (02)` ×5, `Ink Cassette Empty (06)`, `Unknown Error (08)`), see photobooth-setup `event-2026-11-22-findings.md`.
- Re-enabling a stopped queue sends the pending job again; if the printer had already printed it, the guest gets a duplicate. (R3)
- Print buttons on pages loaded earlier (result screen, gallery) can still send print requests while the printer is busy. `[user]`

The goal: each photo is printed exactly once, only one print runs at a time, and the print screen says what is really happening, using (a) CUPS printer and job status, including the printer state the Gutenprint backend reports while it stays attached (`FAST_RETURN=0`), and (b) the printer's own status, queried by the booth laptop while the queue is stopped. `[user]` (P-1, §17.3.1, showed both are available; the ESP32 unit is no longer needed for status.)

### 1.2 Users

| Who | Where | Needs |
|---|---|---|
| Guest | In front of the booth screen | Knows whether their photo is printing, how far along it is, and when to take it. German only. `[user]` |
| Operator (staff) | Inside the booth, with the back opened to check or fix the printer. Uses the operator web UI on a phone (§12b); when the phone is not available, the same page on the booth laptop (FR-12b.20). `[user]` | Sees what is wrong, fixes the printer, tells the booth the problem is fixed and whether the photo came out. |

### 1.3 Goals

- G0 **Every requested copy is printed exactly once**, whatever happens (printer error, jam, delay, booth restart). `[user]`
- G1 Only one print request runs at a time, on every page that can print (result screen, gallery), including pages loaded before the print started. `[user]`
- G2 The print screen shows the real stage, from "Druckauftrag gesendet" to "Fertig – bitte Foto entnehmen". `[user]`
- G3 Printer errors come from CUPS and are shown with a specific cause or the raw CUPS message. `[user]`
- G4 After the operator confirms a fix on the phone web UI, printing continues without staff action on the booth screen. `[user]`
- G5 Paper and ink counters warn before supplies run out and tell an empty tray from a misfeed (§8); there is no fixed print limit. `[user]`

### 1.4 Non-goals

- Choosing implementation or code structure — decided later. `[user]`
- Sensors on the paper path (ESP32 unit, IR barriers) and a hardware operator panel: removed in v0.16. `[user]`
- Languages other than German. `[user]`
- Supporting printers other than the SELPHY CP1300. `[user]` (A2)
- Changing how photos are taken, uploaded or shared.
- A fixed print limit ("N prints, then stop"): not used and not built; the booth is not rented out. Upstream's limit stays off (FR-8.8). `[user]`
- Auto-print (`print.auto`, `print.auto_delay`): out of scope until characterised (§17.5). `[user]`

### 1.5 High-level flow

```text
Guest presses "Drucken" (N copies)
  → booth busy?  yes → "Drucker beschäftigt" (no job created)
  → copy k sent to CUPS          → "Druckauftrag gesendet (Kopie k von N)"
  → backend reports Feeding Paper  → "Papier wird eingezogen"
  → backend reports Printing YELLOW / MAGENTA / CYAN / LAMINATE
                                 → Gelb / Magenta / Cyan / Schutzschicht (1/4…4/4)
  → backend reports "Print complete", CUPS job completed → copy k done
      k < N: "Kopie k von N fertig – bitte Foto entnehmen" for T_take_hint, then copy k+1 (a photo left on the tray does no harm)
      k = N: "Fertig – bitte Foto entnehmen" for T_take_hint, then start screen
CUPS printer error at any point  → error screen + phone UI; queue stopped by the backend
  → booth queries the printer itself while the queue is stopped (printing → idle = photo printed)
  → operator fixes the printer; the printer usually prints the held photo by itself (P-1)
  → auto-resume on: booth decides from clean printer query evidence (printed → "Foto ist da"; lost → "Foto fehlt")
  → otherwise (switch off, or evidence not clean): operator confirms on the phone: "Foto ist da" / "Foto fehlt"
  → booth reconciles the CUPS job (no duplicate), re-enables the queue, continues with copy k+1
```

---

## 2. System Architecture

### 2.1 Logical architecture

```text
 ┌──────────────── Booth (Photobooth web app on the event laptop) ─────────────────┐
 │  Print screen + print buttons (UI) ◄── Print Status Tracker ──► Busy Gate        │
 │                                          ▲                     ▲                 │
 │                               CUPS status interface (§9)   Operator web UI      │
 │                               incl. printer status query                         │
 └──────────────────────────────────────────┼─────────────────────┼─────────────────┘
                                            │                     │ Wi-Fi (booth router, inside the booth)
                     CUPS + Gutenprint backend ─┐                 └── operator phone (browser)
                     (FAST_RETURN=0)            │ USB
                     printer status query ──────┤ (only while the queue is disabled)
                                                ▼
                                         SELPHY CP1300
```

The phone is the operator's main interface; the same page on the booth laptop is the fallback (FR-12b.20). Printing never depends on Wi-Fi (NFR-13.3).

### 2.2 Hardware / platform

| Item | Fact | Source |
|---|---|---|
| Printer | Canon SELPHY CP1300, USB `04a9:32db`, dye-sublimation, 4 passes per print (Y, M, C, overcoat); paper leaves the **back** of the printer between passes and exits the **front** when finished | `[code]` / `[derived]` |
| Driver | CUPS queue `selphy`, device `gutenprint53+usb://canon-cp1300/…`, `gutenprint.5.3://canon-cp1300`, `PageSize=Postcard`, `StpBorderless=True` | `[code]` photobooth-setup, laptop |
| Backend | Gutenprint 5.3.4 (Debian 12) `backend_canonselphyneo`: under CUPS *fast return* is on unless `FAST_RETURN` is set; on most printer errors it stops the queue and leaves the job pending (`CUPS_BACKEND_STOP`), on status `0x0A` ("Incorrect media for job", job and loaded media do not match) it cancels the job (`CUPS_BACKEND_CANCEL`) and the queue keeps running. With `FAST_RETURN=0` it reports the printer state about once a second (§9.1). Run standalone (`BACKEND=canonselphyneo gutenprint53+usb -s`, as root) it queries the printer's status | `[code]` Gutenprint source (master; same switch in the installed binary); P-1 |
| Booth host | Acer Switch 3, Debian 12, native Photobooth install | `[code]` |
| Paper path | During each pass the sheet comes out at the back, then at the front, and goes fully back inside at both sides; the finished print is ejected at the front onto the output tray | `[user]` |
| Shared USB hub | Printer and camera share one USB hub on the laptop | `[code]` |
| Booth router | Router inside the booth, always on during an event; its Wi-Fi is joined only by the booth laptop and the operator's phone, never by guests. It is the same LTE router that carries the photo uploads | `[user]` |
| Operator phone | Any current smartphone browser; no app installed | `[user]` / (assumed) |

### 2.3 Software architecture

**Mostly open.** Which process reads CUPS and queries the printer, where the tracker runs, and how the UI gets updates are HOW decisions, to be made after this FSD is accepted. `[user]` This FSD fixes the observable behaviour and the information each interface must deliver.

Decided:

- No ESP32 unit, no IR barriers, no hardware operator panel. Print status comes from CUPS (job state and the backend's printer-state messages) and, while the queue is stopped, from the booth laptop querying the printer itself. `[user]` 2026-11-27
- Each copy of a photo is its own CUPS job; copies are sent one after another. `[user]`
- The guest chooses the number of copies in upstream's existing copies dialog (FR-7.16); only its UI is reused, the backend path is open. `[user]`
- The Gutenprint backend runs with fast return **off** (`FAST_RETURN=0`), so it stays attached for the whole print, reports each stage, and printer errors during a print reach CUPS (NFR-13.2). `[derived]` R2 — first print confirmed in P-1 (§17.3.1); B-1 (§17.3.2) added three more clean prints; 4 of the 5 R7 asks for, the rest covered by acceptance (§17.4 item 1) `[user]`. Without it the booth has no stages and no reliable "done" at all.
- The print stack is custom; upstream print-limit functions may be reused once tested (§1.4). `[user]`
- The operator interface is a web UI on a phone on the booth network (§12b), served by the booth laptop. The fallback is the same page opened on the booth laptop through the admin login and its menu; no hidden gesture. `[user]` (OD12)
- A finished photo left on the output tray does not disturb the next print, so copies are not held back for it: the booth shows the take-photo message for a fixed time and moves on. `[user]` (OD11)
- No fixed print limit; paper and ink counters are part of the custom print stack and count at `DONE` (§8). `[user]`

### 2.4 Component layering

```text
L2 Application  │ Print Status Tracker (§5) │ Busy Gate (§6) │ Print Screen (§7) │ Supply Counters (§8) │ Print Image (§8b)
────────────────┼──────────────────────────────────────────────────────────────────────────────
L1 Interfaces   │ CUPS Status Interface incl. printer status query (§9) │ Operator Web UI (§12b)
────────────────┼──────────────────────────────────────────────────────────────────────────────
L0 Foundation   │ CUPS + Gutenprint (§13) │ USB host link (§13)
```

Dependencies point downward only. §10–§12 (unit interface, ESP32 sensor unit, operator panel) were removed in v0.16; their numbers are not reused.

---

## 3. Implementation Phases

| Phase | Scope | Exit criteria | Depends on |
|---|---|---|---|
| **1 — Virtual test setup** | §17.1: simulated SELPHY backend that emits the backend's stage messages, can inject SELPHY errors and pending/stopped-queue states, and answers printer status queries; browser test setup (§17.1.1); workflows (§17.2) | Every unit, integration and e2e test in this FSD can be run without hardware, in GitHub Actions; §17.5 run against upstream | — |
| **2 — Booth logic + operator web UI + supply counters** | §5, §6, §7, §8, §8b, §9, §12b against the virtual test setup | All unit and integration tests pass; exactly-once contracts (§5.4) pass in simulation, with decisions made on the web UI | Phase 1 |
| **3 — Bench session** | Bench session B-1 (§17.3) on the real SELPHY; **done 2026-11-27**, ahead of Phase 1 (§17.3.2) `[user]` | B-1 run; B-Q1…B-Q5 answered (B-Q6 if seen); Appendix A filled | Phase 2 |
| **4 — Acceptance** | §17.4 on the real booth | Acceptance list passes | Phase 3 |
| **5 — First event** | Event observation E-1 (§17.6): printer query logged next to every operator decision | E-Q1 answered; auto-resume default confirmed or changed | Phase 4 |

---

## 4. Risks, Assumptions & Dependencies

### 4.1 Risks

| ID | Risk | Mitigation |
|---|---|---|
| R1 | `Paper Feed (02)` is reported both for an empty tray and a misfeed `[code]` (P-1: empty tray inserted → `Paper Feed (02)`; tray removed → `No Paper (03)`, §17.3.1) — the message cannot be more specific than "Papier leer oder Papiereinzug gestört". With a known paper count the booth tells them apart (FR-8.6); otherwise the text covers both (Appendix B). |
| R2 | With fast return on, CUPS marks the job *completed* ~10–17 s in and never sees an error during the rest of the print `[code]`. P-1: the backend exits at "Printing YELLOW", 48 s before the eject `[code]`. With `FAST_RETURN=0` an error during printing (ink sheet, `Unknown Error (0c)`, 35 s in) reached CUPS within ~1 s (P-1 job 123). | `FAST_RETURN=0` (NFR-13.2); *completed* without a reported `LAMINATE` stage never shown as "Fertig" (FR-5.4); startup check of the setting (NFR-13.4); status read failures are not errors (FR-9.4). |
| R3 | After an error the backend leaves the job pending in a stopped queue. The printer prints the held photo (by itself after paper/ink fixes, P-1); re-enabling the queue then sends the job again → duplicate. `[code]`/`[user]` P-1: duplicate confirmed (job 120 resent by `cupsenable`, §17.3.1). | Reconcile before re-enabling (FR-6.4, FR-5.14); only the booth re-enables the queue (FR-6.11). Behaviour verified in B-1 (job 126, §17.3.2). |
| R5 | Stage timing differs per printer/temperature; wrong limits cause false stalls or late detection. | Limits from B-1 with margin (Appendix A): 20 s per stage, plus 20 s before the first layer for the printer's paper-feed retries. `[user]` |
| R7 | With `FAST_RETURN=0` the backend polls the printer for the whole print; this might confuse the printer. | Five prints with the backend attached throughout, without printer misbehaviour, before the setting is kept (B-Q1). 4 clean so far (P-1 job 119; B-1 jobs 125, 128, 129, §17.3.2); the owner accepted this, the 10 prints of acceptance item 1 (§17.4) complete it. `[user]` |
| R8 | A page loaded before a print started (result screen, gallery) still shows an active print button. `[user]` | Server-side gate on every print entry point (FR-6.8). |
| R9 | Backend status `0x0A` cancels the job instead of stopping the queue. It means "Incorrect media for job" (Debian 12 text; upstream: "Incorrect paper tray and ink for job", protocol note "Media/Job mismatch"): the job's print size does not match the loaded paper tray and ink cassette, e.g. a wrong-size cassette after a swap; the booth always prints postcard size. It is only reported while the backend waits for the printer, before printing starts, so nothing should be printed (inferred from the code, not yet observed). `[code]` Gutenprint `backend_canonselphyneo.c`, Debian 12 source package `5.3.4.20220624T01008808d602-1` (backend 0.22) and upstream master (0.24) | Treated as a printer error and the copy resent after the fix (FR-5.18); confirm "nothing printed" when first seen (B-Q6). |
| R10 | Phone not at hand, battery empty, or booth Wi-Fi down when an error needs a decision. Since v0.16 there is no hardware panel as fallback. | The printer status page on the booth laptop (FR-12b.20); printing itself never depends on Wi-Fi (NFR-13.3). |
| R11 | The operator web UI adds network exposure: anyone on the booth Wi-Fi could make operator decisions. | WPA2 Wi-Fi joined only by laptop and operator phone; upstream admin login on the web UI (FR-12b.2); §15. |
| R12 | Two browsers (two staff phones, or two tabs) give conflicting decisions. | First confirmed decision wins; later ones are ignored and every operator page shows the decision made (FR-12b.18). |
| R13 | The fork is public and photobooth-setup is private: the fork's CI needs a token to build the stack, and test artifacts are visible to others. | Read-only fine-grained token on photobooth-setup only, as an Actions secret; synthetic test data only (§17.1.1, §17.2). |
| R14 | While the queue is stopped, only a standalone status query (`gutenprint53+usb -s`) can see the printer print the held photo `[code]` P-1. It reconnects to the printer each time and twice per print hung ~17 s (`libusb error -7`) at a layer change; layer names around a hang were wrong. A query on the USB device while a CUPS backend prints could disturb the print. It needs root. | Use only *printing → Idle* from it, never layer names (FR-9.8); query only while the queue is disabled, never while a backend is attached or during `cupsenable` (FR-9.7); a hang is not an error (FR-9.9); root limited to this one command (NFR-15.4). It also shows a lost photo: after the ink-sheet error and a power cycle the printer went straight to *Idle* without printing (P-1 job 123). Decides on its own only on clean evidence and with the switch on (FR-6.15). |
| R15 | Provoking a jam by hand does not give a clean jam: holding the sheet made the gears slip and **tore the ink sheet** (P-1 job 123), costing a cassette. `[code]` | No provoked jam of any kind in B-1 (no hand-held sheet, no spent cassette); a real jam is recorded if one happens during any print (§17.3). `[user]` approved |
| R18 | **A job the printer already holds cannot be cancelled from CUPS.** `cancel` removes the job only from CUPS; the printer keeps it and prints it as soon as the cause is fixed. B-1 job 126: resent by `cupsenable` into a missing tray (`No Paper (03)`), cancelled in CUPS, tray back in → the printer printed it a second time. Only a power cycle clears a held job (A4, P-1 job 123, owner report on jams). `[code]` | Never resend a copy while the printer may still hold it (FR-6.4). A manual "Foto fehlt" while the printer query does not show *Idle* without an error is at risk: the printer may still print the held photo after the fix, and the resend duplicates it. Before a manual "Foto fehlt" is applied while the printer is not *Idle* without an error, the operator is asked to switch the printer off and on first (FR-12b.22). `[user]` approved |
| R17 | Stages and "done" now rest on the backend's message texts (`Printing YELLOW`, `Print complete`, …); a Gutenprint update could change them. `[derived]` | Gutenprint version pinned on the booth (§4.3); texts recorded in P-1/B-1 are test fixtures (§17.1); unknown messages are logged and never move a copy to `DONE` (FR-5.4). |

R4 (IR misreads) and R6 (ESP32 on the shared USB hub) were removed with the ESP32 unit in v0.16; R16 (photo left on the tray) in v0.17, since it does no harm (A8).

### 4.2 Assumptions

- A1 For each print the backend reports exactly 4 layer stages, in the order `Printing YELLOW`, `MAGENTA`, `CYAN`, `LAMINATE`, then `Print complete`. `[code]` P-1 job 119 and B-1 jobs 125, 128, 129: 4 of 4 (§17.3.2)
- A2 Only one printer (`selphy`, a SELPHY CP1300) is used. `[user]`
- A3 The booth host's clock is the time reference for all timeouts. `[user]`
- A4 After a printer error the printer has already received the job. **P-1 (§17.3.1) `[code]`:** for an empty paper tray and a missing ink cassette the printer printed the held photo **by itself** as soon as the cause was fixed (it senses the tray and the cassette); no button press, 4 times. **Ink-sheet error** (`Unknown Error (0c)`, ink sheet torn): the printer needs a power cycle, after which it is *Idle* and **the held photo is lost**, so resending is correct. So a printer can be cleared without printing (power cycle). FR-7.7 and the Appendix B staff hints were revised accordingly in v0.20. **Real paper jam** `[user]`: the printer's display asks for a power cycle to clear it, and the printer cancels the held job during the restart, so the photo is lost and resending is correct (as for the ink-sheet error; FR-6.15 case (b)). Not yet logged on the bench. B-1 (§17.3.2) confirmed the paper and ink cases again: printed by itself after the fix, no button. Other errors (`Ink Cassette Empty (06)`): B-Q4.
- A5 Only one job is ever inside the printer at a time, because the booth sends the next copy only after the previous one is `DONE` (FR-5.11). `[derived]`
- A6 The fast-return behaviour read in the Gutenprint master source matches the installed 5.3.4. `[code]` confirmed in P-1 (§17.3.1): fast return on → backend exits when printing starts; `FAST_RETURN=0` → backend stays until "Print complete".
- A7 Once `Printing YELLOW` has been reported for a copy, its ink panel set is used up even if the sheet is then lost; before it, no ink is used. (assumed — B-Q4) (P-1: the first rear pass comes ~3 s before `Printing YELLOW`.)
- A8 The sheet comes out at the back and at the front during each pass and fully retracts at both sides in between; only the final eject leaves it lying on the output tray. A finished photo left on the tray does not disturb the next print. `[user]`
- A9 "Print complete" / CUPS *completed* with `FAST_RETURN=0` comes shortly after the photo is out of the printer. `[code]` P-1 and B-1: 1.2–1.3 s after the eject mark in 3 of 3 B-1 prints (§17.3.2)

### 4.3 Dependencies

- photobooth-setup `native/setup-printer.sh` (queue options) and the CUPS `SetEnv FAST_RETURN 0` setting (NFR-13.2) in `/etc/cups/cups-files.conf`. CUPS 2.4 ignores `SetEnv` in `cupsd.conf` (only an info line "Please move … to cups-files.conf" in `error_log`), although the installed `cupsd.conf(5)` still lists it. `[code]` P-1
- Gutenprint 5.3.4 as installed (Debian 12); an upgrade needs the backend messages re-checked (R17).
- Root rights without a password for the few fixed commands that need them, i.e. the printer status query (NFR-15.4); set up by photobooth-setup.
- `selphy-autoresume` must stay disabled: it re-enables the queue without reconciliation and would cause duplicates (FR-6.11).

### 4.4 Open decisions

None open (v0.29). Decided and removed decisions, for traceability:

Decided in v0.29: R18 mitigation — power cycle before a manual "Foto fehlt" unless the printer is *Idle* without an error (FR-12b.22). `[user]`

OD1 (ESP32 unit offline), OD2 (IR events without a job) and OD8 (panel while the booth is unreachable) were removed with the ESP32 unit in v0.16.

Decided in v0.26: OD13 — auto-resume (the former "middle option") is in force now: on clean printer query evidence the booth decides by itself, otherwise the operator decides; a switch on the printer page turns it off (manual resume) (FR-6.15, FR-12b.21). E-1 (§17.6) checks it at the first event. `[user]`

Decided in v0.25: OD6 — a backend cancel with `0x0A` is a printer error, not a finished cancel; after the operator's fix the copy is resent as a new job (FR-5.18), so the print screen stays up and the cancelled-print question is moot. `[user]`

Decided in v0.21: OD3 (defaults 18 sheets / 56 prints, FR-8.3), OD4 (print screen restored after a reload, FR-7.12), OD5 (busy notice, FR-6.10). `[user]`

Decided in v0.17: OD11 (a photo left on the tray does no harm; take-photo message for {{T_take_hint}}, FR-7.19) and OD12 (fallback is the printer status page on the booth laptop, FR-12b.20). `[user]`

---

# Part A — Application Logic (L2)

## 5. Print Status Tracker

### 5.1 Purpose

Determines, for every print request (one photo, N copies) and each copy's CUPS job, the stage shown to the guest and the operator, from CUPS job/printer status and the backend's printer-state messages (§9), the printer status query while the queue is stopped (§9), and operator decisions (from the web UI, §12b).

### 5.2 Job state model

States of the copy currently being printed. The request additionally carries "copy k of N".

| State | Shown as (Appendix B) | Entered on |
|---|---|---|
| `SENT` | Druckauftrag gesendet | CUPS accepted the copy's job (covers *Waiting for printer idle / acknowledgement*) |
| `FEEDING` | Papier wird eingezogen | backend reports `Feeding Paper` |
| `LAYER_1`…`LAYER_4` | Gelb / Magenta / Cyan / Schutzschicht | backend reports `Printing YELLOW` / `MAGENTA` / `CYAN` / `LAMINATE` |
| `DONE` | last copy: Fertig – bitte Foto entnehmen (for {{T_take_hint}}) | `Print complete` or job *completed* after `LAYER_4`, or operator "Foto ist da" |
| `ERROR` | CUPS error text | CUPS printer error / queue stopped while this job is active; CUPS refused the job |
| `AWAIT_OPERATOR` | Druck wird geprüft – bitte Personal rufen | stall (FR-5.5); job *completed* before `LAYER_4` (FR-5.4); booth restart with an unfinished job (FR-5.13) |
| `CANCELLED` | Druck abgebrochen | job cancelled or aborted in CUPS, except a backend cancel with `0x0A` (→ `ERROR`, FR-5.18) |

Events: **E1** CUPS accepted · **E2** CUPS refused · **E3** backend stage message (`Feeding Paper`, `Printing YELLOW/MAGENTA/CYAN/LAMINATE`) · **E4** backend `Print complete` or CUPS job *completed* (whichever first) · **E5** CUPS printer error / queue stopped · **E7** CUPS job cancelled/aborted · **E9** stage limit exceeded (stall) · **E10** operator "Foto ist da" · **E11** operator "Foto fehlt" · **E12** booth restart · **E14** printer status query result (only while the queue is stopped: *printed* = printing seen, then Idle; *idle, nothing printed*; *error*; *no answer*).

Transitions (normative). "log" = logged only. "evidence" = recorded and shown on the operator web UI (FR-5.14). E10/E11 come from the operator web UI; only the first confirmed decision counts (FR-12b.18).

| State \ Event | E3 layer message | E4 complete | E5 CUPS error | E7 cancelled | E9 stall | E14 printer query | E10 Foto ist da | E11 Foto fehlt |
|---|---|---|---|---|---|---|---|---|
| `SENT` | → `FEEDING` or `LAYER_m` for the stage named | → `AWAIT_OPERATOR` (FR-5.4) | → `ERROR` | → `CANCELLED` | → `AWAIT_OPERATOR` | log | ignored (not offered) | ignored (not offered) |
| `FEEDING`, `LAYER_n` (n<4) | later stage → that stage; same or earlier → log | → `AWAIT_OPERATOR` (FR-5.4) | → `ERROR` | → `CANCELLED` | → `AWAIT_OPERATOR` | log | ignored | ignored |
| `LAYER_4` | log | → `DONE` | → `ERROR` | → `CANCELLED` | → `AWAIT_OPERATOR` | log | ignored | ignored |
| `ERROR` | evidence | evidence | log | → `CANCELLED` | — | evidence | → `DONE` (reconcile, FR-6.4) | → `SENT` (reconcile, FR-6.4) |
| `AWAIT_OPERATOR` | evidence | evidence | → `ERROR` | → `CANCELLED` | — | evidence | → `DONE` (reconcile) | → `SENT` (reconcile) |
| `DONE` | log | log | (next copy or gate) | log | — | log | ignored | ignored |
| `CANCELLED` | log | log | (gate) | log | — | log | ignored | ignored |

Entry events: E1 creates a copy in `SENT`; E2 creates it in `ERROR`; E12 puts every unfinished copy found in CUPS into `AWAIT_OPERATOR` (FR-5.13). E7 with the job's last message `Printer error: Incorrect media for job (0a)` is not a cancel: the copy goes to `ERROR` (FR-5.18), as for E5. Copy k+1 of a request is sent {{T_take_hint}} after `DONE` of copy k < N, whether or not the photo has been taken (FR-7.19). In `DONE`/`CANCELLED` an E5 belongs to no job and only affects the Busy Gate (§6).

Stage order: `SENT` < `FEEDING` < `LAYER_1` … `LAYER_4`. A skipped stage (e.g. `Feeding Paper` or `MAGENTA` never seen because a poll missed it) is logged; the stage shown jumps to the stage reported. E4 is the only event that moves a copy to `DONE` on its own, and only from `LAYER_4`.

### 5.3 Requirements

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-5.1 | Must | The tracker shall assign `SENT` only after CUPS has accepted the copy's job. `[user]` | approved |
| FR-5.9 | Must | A job CUPS refuses shall go to `ERROR` with the CUPS reason. `[user]` | approved |
| FR-5.2 | Must | The backend messages for the current copy's job shall set the stage: `Feeding Paper` → `FEEDING`, `Printing YELLOW`, `MAGENTA`, `CYAN`, `LAMINATE` → `LAYER_1`…`LAYER_4`; the stage shall never move backwards. `[user]` (v0.16: from the backend, not from a rear sensor; `FEEDING` added v0.19) | approved |
| FR-5.3 | Must | `Print complete` or CUPS job *completed* (whichever comes first) after `LAYER_4` shall set `DONE`. `[user]` A9 | approved |
| FR-5.4 | Must | CUPS job *completed* or `Print complete` before `LAYER_4` was reported shall not set `DONE`; the copy goes to `AWAIT_OPERATOR` with cause *status unclear* (e.g. fast return active again, R2). `[derived]` R2, R17 | approved |
| FR-5.6 | Must | The text "Fertig" shall only be shown for a copy in `DONE`. `[derived]` R2 | approved |
| FR-5.5 | Must | If a stage lasts longer than its limit in Appendix A, the tracker shall set `AWAIT_OPERATOR` with cause *stall*. `[user]` (stall is not a CUPS error, G3) | approved |
| FR-5.11 | Must | Each copy shall be sent as its own CUPS job, and copy k+1 only after copy k is `DONE`. `[user]` | approved |
| FR-5.12 | Must | After an error on copy k is resolved, the remaining copies shall be sent without staff or guest action. `[user]` | approved |
| FR-5.13 | Must | After a booth restart, an unfinished job found in CUPS shall be held, set to `AWAIT_OPERATOR`, and not sent to the printer until the operator decides on the phone. `[user]` | approved |
| FR-5.14 | Must | Backend messages and printer status query results (E14) received in `ERROR` or `AWAIT_OPERATOR` shall be recorded as evidence with timestamps and shown on the operator web UI, e.g. "Drucker hat gedruckt (14:32:05)" or "Drucker meldet keinen Druck – Foto vermutlich verloren". `[derived]` A4 | approved |
| FR-5.15 | Must | Every copy shall reach the printer's output exactly once: no requested copy missing, no copy printed twice. `[user]` G0 | approved |
| FR-5.18 | Must | When the backend cancels a copy's job with `0x0A` (`Incorrect media for job`), the copy shall go to `ERROR` (not `CANCELLED`) with the Appendix B text; the operator web UI shall offer only "Behoben – nochmal drucken" (with confirmation), and on it the booth shall send the copy again as a new CUPS job. No reconciliation is needed, since CUPS has already removed the job. `[user]` OD6, R9 | approved |

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| FR-5.1 | Simulated CUPS accepts · guest presses Drucken | `SENT` after acceptance | `SENT` before acceptance | unit |
| FR-5.9 | Queue rejecting (`cupsreject`) · guest presses Drucken | `ERROR` with CUPS reason | "Druckauftrag gesendet" | integration |
| FR-5.2 | `SENT` · inject Feeding Paper, YELLOW, MAGENTA, CYAN, LAMINATE | `FEEDING`, `LAYER_1`…`LAYER_4` in order | stage skipped or repeated | unit |
| FR-5.2 | `LAYER_3` · inject `Printing MAGENTA` (as in a query hang, R14) | stays `LAYER_3` | `LAYER_2` | unit |
| FR-5.3 | `LAYER_4` · inject `Print complete` | `DONE` | `DONE` before `LAYER_4` | unit |
| FR-5.4 | `LAYER_1` · CUPS completed (simulated fast return on) | `AWAIT_OPERATOR`, cause *status unclear* | `DONE`; "Fertig" | unit |
| FR-5.6 | any state but `DONE` · CUPS completed | no "Fertig" text | "Fertig" | unit |
| FR-5.5 | `SENT` · no layer message for > limit | `AWAIT_OPERATOR`/stall within limit +2 s | stall before the limit; `ERROR` | unit |
| FR-5.11 | 3 copies · run copy 1 | only one CUPS job exists until copy 1 `DONE` | two jobs in CUPS at once | integration |
| FR-5.12 | 3 copies, `ERROR` on copy 2 · operator "Foto ist da" | copy 3 sent without staff action | copy 3 lost or waits for staff | integration |
| FR-5.13 | Job pending in simulated CUPS · restart booth | `AWAIT_OPERATOR`, queue stays disabled | job sent to printer | integration |
| FR-5.14 | `ERROR` · simulated query reports printing → Idle | web UI shows "Drucker hat gedruckt" with time | evidence missing | unit |
| FR-5.15 | see §5.4 | | | integration + bench |
| FR-5.18 | Simulated backend cancels with `Incorrect media for job (0a)` · operator "Behoben – nochmal drucken" | `ERROR` with "Falsche Papier- oder Farbkassette"; then one new job, copy printed once | `CANCELLED`; print screen closes; copy lost; two new jobs | integration |

### 5.4 Exactly-once contracts (full form)

```yaml
id: FR-5.15/FR-6.4 held photo printed by the printer after the fix
verification:
  preconditions:
    - FAST_RETURN=0. One copy requested.
  stimulus:
    - The copy hits a CUPS printer error (e.g. "Paper Feed (02)", empty tray); the backend stops the queue, the job stays pending.
    - Operator refills the paper; the printer prints the held photo by itself (P-1) — or after its print button, if it does not.
    - The printer status query reports printing, then Idle.
    - Operator selects and confirms "Foto ist da" on the phone web UI.
  expected_observations:
    - The job goes SENT → ERROR → DONE.
    - The web UI showed "Drucker hat gedruckt" before the decision.
    - The booth cancels the pending CUPS job before it re-enables the queue.
    - The queue is enabled again afterwards; the next print request is accepted.
    - Exactly one photo comes out in total.
  timing: queue enabled within {{T_resume}} of the operator's confirmation
  prohibited_outcomes:
    - A second (duplicate) photo is printed.
    - The queue is re-enabled while the printed job is still pending.
    - A printer status query runs while the queue is enabled.
    - Staff must act on the booth screen.
  tier: integration (simulated CUPS + simulated printer query) + bench (B-1)
  evidence: CUPS job log, tracker log, printer query log, operator decision log, photo of the output tray
  cleanup: cancel remaining jobs, refill paper
```

```yaml
id: FR-5.15/FR-6.4 held photo lost by a power cycle
verification:
  preconditions:
    - FAST_RETURN=0. One copy requested.
  stimulus:
    - The copy hits an ink-sheet error ("Unknown Error (0c)") during printing; the queue stops, the job stays pending.
    - Operator switches the printer off and on, fixes or replaces the cassette.
    - The printer status query reports Idle without printing.
    - Operator confirms "Foto fehlt" on the phone.
  expected_observations:
    - The web UI showed "Drucker meldet keinen Druck – Foto vermutlich verloren" before the decision.
    - The pending job is kept and the queue re-enabled; the copy is printed once.
    - Exactly one good photo comes out in total.
  prohibited_outcomes:
    - No photo (copy lost).
    - The copy printed twice.
  tier: integration (simulated) + bench (B-1 print 5 if an ink-sheet error happens)
  evidence: CUPS job log, tracker log, printer query log
  cleanup: cancel remaining jobs
```

```yaml
id: FR-5.12/FR-5.15 three copies with an error on copy 2
verification:
  preconditions:
    - Three copies requested.
  stimulus:
    - Copy 1 prints normally. Copy 2 hits a CUPS printer error.
    - Operator fixes the printer (copy 2 comes out), confirms "Foto ist da" on the phone.
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
| FR-6.12 | Must | While a CUPS printer error or `AWAIT_OPERATOR` is active, the booth shall reject new print requests. A supply count of 0 does not reject (FR-8.4). `[user]` | approved |
| FR-6.8 | Must | The rejection shall be enforced on the server for every print entry point (result screen, gallery, any API call), regardless of when the page was loaded. `[user]` | approved |
| FR-6.9 | Must | Print buttons on pages already open (result screen, gallery) shall show as disabled within {{T_ui}} of the booth becoming busy, and enabled again within {{T_ui}} of it becoming free. `[user]` | approved |
| FR-6.10 | Must | A rejected request shall create no CUPS job and shall briefly show the guest the busy notice "Drucker beschäftigt – bitte warten, bis der aktuelle Druck fertig ist" (Appendix B). `[user]` OD5 | approved |
| FR-6.2 | Must | While printing is blocked by a CUPS printer error and no print request is unfinished, taking photos, showing the result and the QR code shall keep working. `[user]` (While a request is unfinished the print screen stays up, FR-7.14.) | approved |
| FR-6.4 | Must | On an operator decision for a job in `ERROR` or `AWAIT_OPERATOR`, the booth shall first stop any printer status query, then make CUPS match the decision ("Foto ist da": cancel the job if still pending; "Foto fehlt": keep it), then re-enable the queue. `[user]` The same applies to an automatic decision (FR-6.15). | approved |
| FR-6.11 | Must | Only the booth's reconciliation (FR-6.4) shall re-enable the CUPS queue; no other automatic `cupsenable` shall run. `[derived]` R3 | approved |
| FR-6.15 | Must | **Auto-resume.** While the auto-resume switch is on (FR-12b.21) and a copy is in `ERROR` after a CUPS printer error, the booth shall decide by itself from the printer status query, only on clean evidence: (a) *printing* seen, then *Idle*, with no *no answer* period in between → "Foto ist da"; (b) the printer unreachable (power cycle), then *Idle*, *printing* never seen and no *no answer* period since the error → "Foto fehlt"; (c) anything else — a hang or *no answer* in the window, an error still active, *printing* without *Idle*, an unknown state, `AWAIT_OPERATOR` (stall, status unclear, booth restart), `0x0A` (FR-5.18) — the operator decides. An automatic decision goes through FR-6.4, counts as the first decision (FR-12b.18; an operator decision made earlier wins), is logged with source *printer query* (FR-14.1), and is shown on the phone with its reason. With the switch off (manual resume) the query is evidence only and the operator always decides. `[user]` OD13 | approved |

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| FR-6.1 | Print running · 2nd print request via API | rejected | 2nd CUPS job | integration |
| FR-6.12 | Simulated `Ink Cassette Empty` · print request | rejected | CUPS job created | integration |
| FR-6.8 | Gallery page loaded before the print started · press its print button while busy | rejected, notice shown | CUPS job created | integration |
| FR-6.9 | Gallery open · start a print elsewhere | gallery print buttons disabled within T_ui | button stays active | integration |
| FR-6.10 | Busy · press print | busy notice | CUPS job; silent nothing | integration |
| FR-6.2 | Printer error, no request unfinished · guest takes a photo | photo + QR shown | photo flow blocked | integration |
| FR-6.4 | see §5.4; query running · operator decides | query stopped before `cupsenable` | query and backend on the USB device at once | integration + bench |
| FR-6.11 | Queue stopped by simulated backend · wait 5 min | queue stays stopped until operator decision | queue re-enabled by anything else | integration |
| FR-6.15 | Auto-resume on, `Paper Feed (02)` · simulated query: printing, Idle, no hang | "Foto ist da" applied automatically, job cancelled, queue re-enabled; phone shows "Automatisch entschieden: Foto ist da – Drucker hat gedruckt" | job resent; operator asked | integration |
| FR-6.15 | Auto-resume on, `Unknown Error (0c)` · query: no answer (power cycle), then Idle, no printing | "Foto fehlt" applied automatically, copy printed once | copy lost; printed twice | integration |
| FR-6.15 | Auto-resume on · query: printing, 17 s hang, Idle | operator asked | automatic decision | unit |
| FR-6.15 | Auto-resume off · query: printing, Idle | evidence shown, operator asked | automatic decision | unit |

## 7. Print Screen

### 7.1 Requirements

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-7.1 | Must | After the guest presses Drucken, the print screen shall show the current stage of that request until the last copy is `DONE` or `CANCELLED`. `[user]` | approved |
| FR-7.14 | Must | While a print request is unfinished, the print screen shall offer no way to leave it, including during `ERROR` and `AWAIT_OPERATOR`; no photo can be started until it closes. `[user]` (Staff opening the admin login on the laptop, FR-12b.20, is not a guest way out.) | approved |
| FR-7.15 | Must | When the print screen closes after the last copy is `DONE`, the booth shall show the guest start screen immediately; the result view of that photo, with its print button, shall not be shown again. `[user]` | approved |
| FR-7.18 | Must | After the last copy of a request is `DONE` (single or multi-copy), the print screen shall show "Fertig – bitte Foto entnehmen" for {{T_take_hint}} and then close. `[user]` | approved |
| FR-7.19 | Must | Between copies of a multi-copy request, the print screen shall show "Kopie k von N fertig – bitte Foto entnehmen" for {{T_take_hint}} and then send copy k+1, whether or not the photo has been taken. `[user]` A8 | approved |
| FR-7.16 | Must | With `print.max_multi` > 1, the guest shall choose the number of copies in upstream's existing copies dialog (minus/plus, 1…`print.max_multi`, "Drucken" / "Abbrechen"; `assets/js/tools.js` `askCopies`). How the chosen number reaches the print stack is not yet decided. `print.max_multi` stays an admin-panel setting, unchanged. `[user]` | approved |
| FR-7.17 | Must | Printing a photo again from the gallery shall stay possible, subject to the Busy Gate (§6); staff use it for reprints and can hide the gallery button in the admin panel. `[user]` | approved |
| FR-7.21 | Must | From the press of Drucken (or the start of copy k+1, FR-7.19) until the copy's job is accepted by CUPS (`SENT`), the print screen shall show the `SENT` text "Druckauftrag gesendet (Kopie k von N)"; the image preparation (§8b) in that time is not a tracker state and no stall limit runs before `SENT`. `[user]` | approved |
| FR-7.2 | Must | Stage changes shall appear on screen within {{T_ui}} of the event reaching the booth. | approved |
| FR-7.3 | Must | All texts shall be German. `[user]` | approved |
| FR-7.4 | Must | Layer stages shall be named yellow, magenta, cyan and **Schutzschicht** (clear coat; backend `LAMINATE`); the 4th stage shall never be called black/"Schwarz"/"K". `[user]` | approved |
| FR-7.5 | Must | For a known CUPS error (Appendix B) the screen shall show its German cause. `[user]` | approved |
| FR-7.13 | Must | For a known CUPS error the screen shall show the staff hint from Appendix B. `[user]` | approved |
| FR-7.6 | Must | For an unrecognised CUPS error the screen shall show "Druckerproblem – bitte Personal rufen" large, and the exact CUPS message small underneath. `[user]` | approved |
| FR-7.7 | Must | Each printer error in Appendix B shall have its own staff hint, telling the operator how to fix that error and to confirm on the phone whether the photo came out; for errors after which the printer prints the held photo by itself (A4), the hint says so and names the print button only as a fallback. `[user]` A4 (v0.19, replaces the single "press the print button" rule) | approved |
| FR-7.10 | Must | With N > 1 copies the screen shall show "Kopie k von N". `[derived]` FR-5.11 | approved |
| FR-7.12 | Should | After a page reload or browser restart during a print, the booth shall show the print screen again with the request's current stage, rebuilt from the tracker's state. `[user]` OD4 | approved |
| FR-7.9 | Should | Exact German wording per Appendix B. | approved |

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| FR-7.1 | Full print | every stage shown, closes after "Fertig" | screen closes before `DONE` | integration |
| FR-7.21 | Press Drucken, image preparation delayed 3 s in simulation | "Druckauftrag gesendet" within {{T_ui}} of the press, unchanged until `SENT` | empty or start screen during preparation; stall raised before `SENT` | integration |
| FR-7.14 | `ERROR` on copy 1 · tap everywhere on the screen, swipe, press keys | print screen stays; no photo starts | screen closes; start screen or photo countdown reachable | integration |
| FR-7.15 | Full print · "Fertig" screen closes | start screen shown at once | result view of that photo shown; its print button pressable | integration |
| FR-7.18 | Single print | "Fertig – bitte Foto entnehmen" for T_take_hint, then start screen | screen stays; closes early | e2e |
| FR-7.19 | 2 copies · copy 1 `DONE`, photo not taken | "Kopie 1 von 2 fertig – bitte Foto entnehmen" for T_take_hint, then copy 2 sent | screen closes after copy 1; copy 2 waits for the photo to be taken | e2e |
| FR-7.16 | `print.max_multi` = 3 · press Drucken, choose 2, confirm | 2 copies printed in sequence | more than 3 selectable; dialog skipped | integration |
| FR-7.16 | Copies dialog open · press Abbrechen | no print request | CUPS job created | integration |
| FR-7.17 | Photo printed before, booth idle · print it from the gallery | printed once more | print refused because printed before | integration |
| FR-7.4 | `LAYER_4` | "Schutzschicht" | "Schwarz", "K", "Laminate" | unit |
| FR-7.5 | CUPS reason `Ink Cassette Empty (06)` | "Farbkassette leer" | raw message only | unit |
| FR-7.6 | CUPS reason `Unknown Error (08)` | generic text + "Unknown Error (08)" | blank or only "Fehler" | unit |
| FR-7.7 | Each Appendix B printer error | its own hint as in Appendix B, ending with confirming on the phone | the same generic hint for every error; "Drucktaste" as the first step for paper/ink errors | unit |
| FR-7.10 | 3 copies, copy 2 printing | "Kopie 2 von 3" | missing or wrong count | unit |
| FR-7.2 | Print screen open, `LAYER_1` · simulated backend emits `Printing MAGENTA` | "Magenta wird gedruckt (2/4)" within T_ui of the message reaching the tracker, without a reload | previous stage still shown after T_ui; a stage skipped | integration |
| FR-7.3 | Walk every print screen state (each Appendix B stage, error, information text and the busy rejection) · collect all visible text | every string is German (Appendix B); only the raw CUPS line of FR-7.6 may be English | English or untranslated upstream text anywhere else | e2e |
| FR-7.9 | Each Appendix B row · render its state (k, N, n filled in) | text equals the Appendix B text character for character | changed, shortened or merged wording | unit |
| FR-7.12 | 2 copies, copy 1 in `LAYER_3` · reload the page; separately, close and restart the browser | print screen back within T_ui of the page loading, showing "Cyan wird gedruckt (3/4)" and "Kopie 1 von 2" | start screen or result view shown; stage reset to `SENT`; copy sent again | e2e |
| FR-7.12 | `ERROR` `No Paper (03)` · reload the page | guest text and staff hint shown again; still no way out (FR-7.14) | error lost; start screen reachable | e2e |
| FR-7.13 | Each known error in Appendix B · show it on the print screen | its own staff hint from Appendix B shown with the guest text | guest text without hint; another error's hint | unit |

## 8. Supply Counters (Phase 2)

### 8.1 Purpose

Tracks how much paper is left in the tray and how many prints are left on the ink cassette, so the operator refills before a guest's print fails, and so `Paper Feed (02)` can be told apart as empty tray or misfeed (R1). Part of the custom print stack; replaces upstream's print limit for this booth. `[user]`

**No fixed print limit.** The booth is not rented out and has no print budget per event, so there is no "N prints, then stop" cap: upstream's print limit stays off and none is built. `[user]` Reasons: upstream counts at CUPS acceptance, not at paper out; its lock/unlock does not match refilling paper; and it left the booth locked at the 2026-11-22 event (`Print locked`, then `Unable to print 1 copies` after unlocking; likely the threshold bug fixed upstream in #1667, to be confirmed by §17.5). Earlier sketch: photobooth-setup `paper-ink-counter.md`; defaults 18 sheets / 56 prints (FR-8.3).

### 8.2 Requirements

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-8.1 | Must | The booth shall keep two counts, *paper left* and *prints left on the ink cassette*, and lower both by one when a copy reaches `DONE` (`Print complete` or "Foto ist da"). Apart from FR-8.10 and operator corrections (FR-8.3), no other event shall lower them. `[user]` | approved |
| FR-8.2 | Must | When either count reaches {{LOW_THRESHOLD}}, the operator web UI and the print screen (as a staff notice) shall show a warning ("Papier fast leer – noch n Blatt" / "Farbkassette fast leer – noch n Drucke"). `[user]` | approved |
| FR-8.3 | Must | On the operator web UI the operator shall be able to: set *paper left* after refilling by entering the number of sheets now in the tray; set *prints left* after changing the cassette (defaults offered: 18 sheets per paper tray, 56 prints per ink cassette `[user]` OD3, from photobooth-setup `paper-ink-counter.md`; the operator can always enter the real number); and correct either count at any time. Each change is logged (FR-14.1). `[user]` | approved |
| FR-8.4 | Must | When *paper left* or *prints left* reaches 0, the operator web UI shall show a refill warning and the print screen a staff notice ("Papier laut Zähler leer" / "Farbkassette laut Zähler leer"); printing shall **not** be blocked: if the tray or cassette is really empty, the printer's own error stops the print (Appendix B). Counts shall not go below 0. `[user]` | approved |
| FR-8.5 | Must | The counts shall survive a booth restart and a page reload unchanged. `[derived]` | approved |
| FR-8.6 | Must | On `Paper Feed (02)`, the booth shall show "Papier leer" when *paper left* is 0 and "Papiereinzug gestört" when it is greater than 0 (Appendix B); with an unknown count, the combined text. `[derived]` R1 | approved |
| FR-8.7 | Must | While a count is unknown (never set since installation), it shall be shown as "unbekannt", with no warning and no block. `[derived]` | approved |
| FR-8.8 | Must | Upstream's print limit shall be off (`print.limit` = 0) on the booth; photobooth-setup shall set it so. `[user]` | approved |
| FR-8.9 | Should | The operator web UI shall show both counts at all times, also while idle. | approved |
| FR-8.10 | Must | After an operator decision "Foto fehlt", the operator web UI shall ask "Blatt verbraucht?" with "Ja" / "Nein". "Ja" lowers *paper left* by one, and *prints left* by one if `LAYER_1` (`Printing YELLOW`) was reported for that copy (the ink panels are then assumed used, A7); "Nein" changes nothing. The resend of the copy shall not wait for the answer (FR-5.12); an unanswered question changes nothing and is dropped when the request finishes. `[user]` | approved |

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| FR-8.1 | Paper 10 · one print reaches `DONE` | paper 9, prints 9 | count lowered at `SENT` or on CUPS completed before `LAYER_4` | integration |
| FR-8.1 | Paper 10 · print stops with `Paper Feed (02)`, operator "Foto fehlt", resent, `DONE` | paper 9 | paper 8 | integration |
| FR-8.2 | LOW_THRESHOLD 3, paper 4 · one print | warning on phone and print screen | no warning; guest-style error | e2e |
| FR-8.3 | Phone · "Papier nachgefüllt: 18" | paper 18, change logged | count unchanged; not logged | e2e |
| FR-8.4 | Paper 0 · guest presses Drucken | print sent; refill warning on the phone, staff notice on the print screen | print refused; count −1 | e2e |
| FR-8.4 | Paper 1, 2 copies requested, 3 sheets really in the tray | both copies printed; paper 0 after copy 1, stays 0 | copy 2 held back; count negative | integration |
| FR-8.5 | Paper 7 · restart booth | paper 7 | reset to 0 or unknown | integration |
| FR-8.6 | Paper 0 / paper 5 · `Paper Feed (02)` | "Papier leer" / "Papiereinzug gestört" | combined text with a known count | unit |
| FR-8.7 | Fresh install · print | prints; counts "unbekannt" | blocked | integration |
| FR-8.8 | Booth after photobooth-setup run | `print.limit` = 0 | any other value | integration |
| FR-8.10 | Paper 10, prints 10, `Paper jam (0b)` in `LAYER_2` · operator "Foto fehlt", then "Blatt verbraucht?" → Ja | paper 9, prints 9 before the resent copy; 8/8 after it | question blocks the resend; counts lowered without "Ja" | e2e |
| FR-8.10 | Paper 10, `Paper Feed (02)` in `SENT` (no layer reported) · "Foto fehlt" → Ja | paper 9, prints 10 | prints lowered | unit |
| FR-8.10 | "Foto fehlt" → Nein, or no answer until the request is done | counts unchanged; question gone | counts lowered | e2e |
| FR-8.9 | Paper 12, prints 40 · open the printer status page while idle, then during a print and in `ERROR` | both counts visible in every state without opening a menu, tab or dialog | counts hidden while idle; shown only after a print | integration |

## 8b. Print Image

### 8b.1 Purpose

Prepares the image of each copy for the printer, so that CUPS does not scale it. Given a camera JPEG (e.g. 4509 × 3052) with `fit-to-page`, CUPS' `imagetoraster` shrinks it ~2.4× with plain interpolation and no anti-alias filter, which aliases fine detail; a photo prepared as below prints visibly better (B-1, §17.3.2). `[user]` The job options that make CUPS place the image 1:1 (`ppi=300`, no `fit-to-page` or `landscape`) are the means, not the requirement; FR-8b.1 states the observable result.

Target from the `selphy` PPD `[code]`: Postcard 299.52 × 449.28 pt, 300 dpi, borderless → 1248 × 1872 px; `imagetoraster` rounds the page to 449 pt and builds a **1248 × 1871** raster, and passes an image of exactly that size at `ppi=300` through bit for bit (1872 rows are resampled again). Borderless: the paper (100 × 148 mm ≈ 1181 × 1748 px) is smaller than the raster, so ~30 px per edge are printed past the paper edge and not seen.

Reference implementation: `testing/bench/prescale-for-selphy.py` (B-1). Preparing a photo takes ~2.6 s on the booth laptop; it happens before the copy is sent (`SENT`), so it is outside the stall limits.

### 8b.2 Requirements

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-8b.1 | Must | The CUPS raster of each copy shall be pixel-identical to its prepared image (1248 × 1871 px at 300 dpi, FR-8b.3), i.e. CUPS shall not scale or resample it. `[user]` | approved |
| FR-8b.2 | Must | The prepared image shall fill the whole raster with the photo, centred, with no letterbox bars and no change of the photo's aspect ratio. `[user]` | approved |
| FR-8b.4 | Must | When preparing a copy's image fails, or takes longer than {{T_prepare}}, the booth shall send that copy as upstream does (its print image with `fit-to-page` and `landscape`) and log a warning with the cause (FR-14.1); FR-8b.1 does not apply to that copy. `[user]` | approved |
| FR-8b.3 | Must | The prepared image shall be made from the image upstream's print path would send to CUPS (the photo with any print frame, text or QR code already applied) with this algorithm, as tested in B-1: (1) apply the EXIF orientation; (2) if an ICC profile is embedded, convert to sRGB; (3) rotate landscape to portrait (90°); (4) centre crop to 1248 : 1871 in source pixels; (5) resample to 1248 × 1871 with Lanczos (Pillow `LANCZOS`, kernel widened by the reduction factor); (6) unsharp mask radius 0.8 px, amount 60 %, threshold 2; (7) save as PNG, 300 dpi. `[user]` | approved |

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| FR-8b.1 | 4509 × 3052 booth JPEG · prepare, then `cupsfilter` with the `selphy` PPD and the booth's `lp` options | raster 1248 × 1871, maximum pixel difference to the image 0 | any raster pixel differs; raster of another size | integration |
| FR-8b.2 | Landscape 3:2, landscape 4:3 and portrait photos · prepare | 1248 × 1871, no uniform border row or column, aspect of the crop box within 0.1 % of 1248 : 1871 `[user]` | white or black bars; stretched image | unit |
| FR-8b.4 | Unreadable print image, and separately a preparation stopped after {{T_prepare}} · print | copy sent once by the upstream path, printed, warning with cause logged | copy not printed; printed twice; silent fallback | integration |
| FR-8b.3 | Fixed reference photo set incl. EXIF orientation 6 and an embedded Adobe RGB profile · prepare | output equals the reference implementation's output (same Pillow version), maximum difference ≤ 1 per channel `[user]` | other resampling filter; orientation or colour profile ignored | unit |

---

# Part B — Interfaces (L1)

## 9. CUPS Status Interface

### 9.1 Purpose

Delivers, for the `selphy` queue: whether a job was accepted, each job's state (pending / processing / completed / cancelled / aborted), whether the queue is stopped, the printer's state and error message, the backend's printer-state messages for the active job, and — while the queue is stopped — the printer's own status from a standalone query.

**Observed source data** `[code]` (P-1, §17.3.1; job 28 from the 2026-11-22 event): with `FAST_RETURN=0` the backend reports the printer state about once a second as `printer-state-message` and `job-printer-state-message`: `Waiting for printer idle`, `Waiting for printer acknowledgement`, `Printer state: Feeding Paper`, `Printer state: Printing YELLOW` / `MAGENTA` / `CYAN` / `LAMINATE`, `Print complete (0 copies remaining)`. Errors appear as `Printer error: <text>`, e.g. `Printer error: Paper Feed (02)`, and stay in `job-printer-state-message` after the job ends. IPP `Get-Job-Attributes` keeps completed / cancelled / aborted apart (FR-9.3). Errors seen in P-1: `Paper Feed (02)` (empty tray inserted), `No Paper (03)` (tray removed), `No Ink (07)` (cassette removed), `Unknown Error (0c)` (ink sheet torn, during printing); each stopped the queue with the job pending (FR-9.6), `printer-state-reasons` was `media-empty,paused` for the paper and ink errors, so only the message text tells them apart.

**Printer status query** `[code]` (P-1): `BACKEND=canonselphyneo gutenprint53+usb -s`, as root, about once a second while the queue is disabled. It showed the printer print the held photo (*Feeding Paper → Printing … → Idle*) while CUPS still reported the job pending, and *Idle* without printing after a power cycle (photo lost). It hung twice per print for ~17 s (`libusb error -7`, `Unable to attach to printer`) at layer changes, with wrong layer names around the hangs (R14).

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-9.1 | Must | Shall report job acceptance or refusal for each print request. | approved |
| FR-9.2 | Must | Shall report printer errors with the CUPS/Gutenprint message text (e.g. `Paper Feed (02)`), including when the backend has stopped the queue. `[code]` | approved |
| FR-9.3 | Must | Shall distinguish completed, cancelled and aborted jobs (not `lpstat -W completed`, which lists cancelled jobs too). `[code]` | approved |
| FR-9.4 | Must | A failed printer status read while a job is processing shall not be reported as a printer error. `[code]` R2 | approved |
| FR-9.5 | Must | A printer error shall reach the tracker within {{T_cups_poll}}. | approved |
| FR-9.6 | Must | Shall report a job left pending in a stopped queue as pending, so reconciliation (FR-6.4) can find it. `[code]` R3 | approved |
| FR-9.10 | Must | Shall deliver the backend's printer-state messages for the active job (`Feeding Paper`, `Printing YELLOW/MAGENTA/CYAN/LAMINATE`, `Print complete`) to the tracker within {{T_cups_poll}}, tagged with the job ID they belong to; unknown messages are delivered as such and logged. `[user]` §2.3 | approved |
| FR-9.7 | Must | While the queue is stopped and a copy is in `ERROR` or `AWAIT_OPERATOR`, shall query the printer's own status about every {{T_printer_poll}}; never while the queue is enabled, a CUPS backend is attached to the printer, or `cupsenable` runs. `[user]` R14 | approved |
| FR-9.8 | Must | From the printer status query, shall report only: *printing* (any `Feeding Paper` / `Printing …` state), *idle*, *error* (with text), *no answer*; and per held copy whether printing was seen and then *idle* followed (= *printed*), or *idle* followed without printing (= *nothing printed*). Layer names from the query shall not be used. `[derived]` R14 | approved |
| FR-9.9 | Must | A query that hangs or fails (e.g. `libusb error -7`) shall be reported as *no answer*, never as a printer error; a query running longer than {{T_query_hang}} shall be stopped and retried. `[code]` R14 | approved |

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| FR-9.1 | Simulated queue accepting / rejecting | acceptance / refusal reported | refusal reported as accepted | integration |
| FR-9.2 | Simulated backend stops queue with `Paper Feed (02)` | error with that text | generic error only | integration |
| FR-9.3 | One job each completed, cancelled, aborted | three distinct states | cancelled reported as completed | integration |
| FR-9.4 | Status read fails while processing | no error reported | `ERROR` | unit |
| FR-9.5 | Inject printer error | tracker sees it within T_cups_poll | later | integration |
| FR-9.6 | Queue stopped, job pending | job reported pending | job missing | integration |
| FR-9.10 | Simulated backend emits the P-1 job 119 message sequence | each message reaches the tracker with job ID within T_cups_poll | message lost; wrong job | integration |
| FR-9.7 | Queue enabled, print running | no printer status query runs | query while the backend is attached | integration |
| FR-9.7 | Queue stopped by an error | queries about every T_printer_poll; stop when the operator decides | no query; query after `cupsenable` | integration |
| FR-9.8 | Simulated query: Feeding, Printing, Idle | *printed* reported | layer stage shown from the query | unit |
| FR-9.8 | Simulated query: Idle only (power cycle) | *nothing printed* | *printed* | unit |
| FR-9.9 | Simulated query hangs 17 s | *no answer*, retried after T_query_hang | printer error shown | unit |

§10 (Unit Interface), §11 (ESP32 Sensor Unit) and §12 (Operator Panel) were removed in v0.16; see `fsd-esp32-v0.15.md`. FR-10.7 and FR-10.8 continue as FR-12b.17 and FR-12b.18.

## 12b. Operator Web UI (phone, main interface)

### 12b.1 Purpose

The operator's interface: a printer status page served by the booth laptop and opened on a phone on the booth Wi-Fi. Shows everything the booth knows about the current print in full and takes the operator's decisions. `[user]` When the phone or Wi-Fi is not available, the same page is opened on the booth laptop (FR-12b.20).

Access reuses upstream's admin login `[user]`: the operator opens the login page (`login/`), logs in with the admin login, and reaches upstream's menu (`login/menu.php`), where gallery, admin panel, slideshow etc. are already listed `[code]`. That menu gets a new **"Drucker"** button that opens the printer status page. `[user]` Upstream sessions last 48 h (`lib/boot.php`) `[code]`, so one login covers an event.

### 12b.2 Requirements

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-12b.1 | Must | Shall be reachable from a phone browser on the booth Wi-Fi through the upstream login page, whose address is printed on a label inside the booth; no app installed. `[user]` | approved |
| FR-12b.2 | Must | Shall use the upstream admin login (`login/`, username/password or keypad PIN as configured, with upstream's login throttling); without a valid admin session the printer status page shall show nothing and accept no decision, and shall send the browser to the login page. `[user]` R11 | approved |
| FR-12b.13 | Must | The upstream menu after login (`login/menu.php`) shall show a "Drucker" button that opens the printer status page, under the same condition as the admin panel button. `[user]` | approved |
| FR-12b.14 | Must | A login on the phone shall stay valid for at least 24 h (upstream: 48 h, `lib/boot.php` `[code]`); the printer status page shall not log the operator out by itself. (Upstream's menu page logs out after 60 s `[code]`; that stays on the menu only.) `[user]` | approved |
| FR-12b.15 | Should | The printer status page shall have a link back to the menu. | approved |
| FR-12b.3 | Must | Shall show: current stage, copy k of N, the full CUPS error text with the Appendix B guest text and staff hint, the last backend message, the evidence of FR-5.14 with timestamps (incl. the printer status query result and *no answer* periods), and whether the CUPS queue is stopped. `[user]` | approved |
| FR-12b.4 | Must | While a copy is in `ERROR` or `AWAIT_OPERATOR`, shall offer "Foto ist da" and "Foto fehlt" (for `0x0A` only "Behoben – nochmal drucken", FR-5.18); a decision shall be sent only after a second confirmation step naming the copy ("Kopie 2 von 3: Foto ist da – bestätigen?"). `[user]` | approved |
| FR-12b.17 | Must | The booth shall accept an operator decision only while a copy is in `ERROR` or `AWAIT_OPERATOR`. `[derived]` (was FR-10.7) | approved |
| FR-12b.18 | Must | For each copy in `ERROR` or `AWAIT_OPERATOR`, only the first confirmed decision shall be applied, from whichever browser it came; a later decision for the same copy shall be ignored and logged, and every open printer status page shall show the decision that was applied. `[derived]` R12 (was FR-10.8) | approved |
| FR-12b.6 | Must | Shall update within {{T_ui}} of a state change without a manual reload. | approved |
| FR-12b.7 | Must | When the page loses its connection to the booth, it shall say so ("Keine Verbindung zur Booth") and offer no decisions until it is connected again and shows current data. | approved |
| FR-12b.9 | Must | Texts German; layout for phone width (360 CSS px, no horizontal scrolling); every button, link and switch at least 48×48 CSS px. `[user]` (sizes v0.33) | approved |
| FR-12b.10 | Should | Shall list the print requests since the booth service last started: requested, printed and failed copies, and each operator decision; older requests are found in the logs (FR-14.2). `[user]` (boundary v0.33) | approved |
| FR-12b.11 | Must | Shall show the supply counts, let the operator set and correct them, and ask "Blatt verbraucht?" after "Foto fehlt" (FR-8.2, FR-8.3, FR-8.9, FR-8.10). `[user]` | approved |
| FR-12b.12 | Must | Shall not offer actions beyond those in this section (no cancelling jobs, no re-enabling the queue directly, FR-6.11). `[derived]` R3 | approved |
| FR-12b.20 | Must | The printer status page shall also be usable on the booth laptop itself, reached the same way as on the phone (admin login → menu → "Drucker"), with the same content, decisions and confirmation step; no hidden gesture or separate staff screen. `[user]` OD12 | approved |
| FR-12b.19 | Should | Shall show a warning when the startup check finds `FAST_RETURN=0` missing (NFR-13.4): "Druckstatus unzuverlässig – FAST_RETURN nicht gesetzt". `[derived]` R2 | approved |
| FR-12b.21 | Must | The printer status page shall have a switch "Automatisch fortsetzen" (auto-resume, FR-6.15) on / off (manual resume). Default on; the setting survives a booth restart, applies from the next decision (a copy already waiting is evaluated again when it is switched on), is logged with time and browser, and is shown on every open printer status page. `[user]` OD13 | approved |
| FR-12b.22 | Must | When the operator chooses "Foto fehlt" and the latest printer status query result is anything other than *Idle* with no printer error (error still active, *printing*, *no answer*, or no result yet), the printer status page shall show the power-cycle prompt (Appendix C) instead of the normal confirmation. `[user]` R18 | approved |
| FR-12b.23 | Must | After the power-cycle prompt (FR-12b.22), the booth shall apply "Foto fehlt" only when the operator confirms that the printer was switched off and on; closing the prompt leaves the copy in its state. `[user]` R18 | approved |
| FR-12b.24 | Must | A "Foto fehlt" applied through the power-cycle prompt shall be logged with the operator's power-cycle confirmation (FR-14.1). `[user]` R18 | approved |

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| FR-12b.2 | Not logged in · open the printer status page directly | sent to login page | status or decisions shown | integration |
| FR-12b.2 | Not logged in · POST a decision to the booth | refused, logged | decision applied | integration |
| FR-12b.13 | Log in on the phone | menu shows "Drucker" next to gallery and admin panel; tapping it opens the printer status page | button missing; button shown without login (phone) | integration |
| FR-12b.14 | Logged in, printer status page open for 30 min | still logged in, page still updating | logout; login page shown | integration |
| FR-12b.3 | `ERROR` on copy 2/3, `Paper Feed (02)`, printer query reports printed after the error | all listed fields shown, evidence with time | missing field | integration |
| FR-12b.4 | `ERROR` · tap "Foto ist da", no confirmation | nothing sent | decision sent on first tap | integration |
| FR-12b.4 | `LAYER_2` · open page | no decisions offered | decisions offered | integration |
| FR-12b.17 | `LAYER_2` · POST "Foto ist da" | ignored, logged | `DONE` | unit |
| FR-12b.18 | `ERROR`, two phones · phone A "Foto ist da", phone B "Foto fehlt" 1 s later | `DONE`, job not resent; phone B shows "Bereits entschieden: Foto ist da" | copy resent; duplicate photo | integration |
| FR-12b.6 | Page open · inject layer message | new stage within T_ui, no reload | stale stage | integration |
| FR-12b.7 | Page open, `ERROR` · cut the connection | "Keine Verbindung zur Booth", decision buttons gone | decision sent from stale page | integration |
| FR-12b.12 | Any state | no cancel / enable buttons | queue re-enabled from the web UI | integration |
| FR-12b.20 | Booth browser, `ERROR`, Wi-Fi off · admin login on the laptop → menu → "Drucker" → "Foto ist da", confirm | decision applied; queue re-enabled; guest print screen continues afterwards | page needs the phone; decision refused | e2e |
| FR-12b.21 | Switch auto-resume off on the phone · restart booth | still off on every printer page; next error waits for the operator | reset to on; automatic decision | integration |
| FR-12b.22 | `ERROR` `Paper Feed (02)`, simulated query: error still active · operator "Foto fehlt" | power-cycle prompt | normal confirmation shown; copy resent | integration |
| FR-12b.22 | `ERROR`, simulated query: *Idle*, no error · operator "Foto fehlt" | normal confirmation (Appendix C), copy resent once after it | power-cycle prompt shown | integration |
| FR-12b.23 | Power-cycle prompt shown · operator confirms "aus- und eingeschaltet" | "Foto fehlt" applied, copy resent once | copy resent before the confirmation; resent twice | integration |
| FR-12b.23 | Power-cycle prompt shown · operator closes it | copy stays in `ERROR`, nothing resent | copy resent; decision applied or logged | integration |
| FR-12b.24 | Prompt confirmed | log entry for the decision carries the power-cycle confirmation, time and browser | decision logged without it | unit |
| FR-12b.1 | Mobile browser (360×800 viewport) on the booth network, nothing installed · open `login/`, log in, menu → "Drucker" | printer status page shown | app, plugin or desktop browser needed | e2e |
| FR-12b.1 | Real booth, operator phone on the booth Wi-Fi · type the address from the label inside the booth | upstream login page loads | label missing or its address wrong | bench |
| FR-12b.9 | 360×800 viewport · every printer status page state (idle, printing, `ERROR` with decisions, confirmation, power-cycle prompt, "Blatt verbraucht?", counter entry) | all text German (Appendix C; raw CUPS message excepted); page width ≤ 360 px; every button, link and switch ≥ 48×48 CSS px | English text; horizontal scrolling; a target under 48 px in either dimension | e2e |
| FR-12b.10 | Booth started · request 1 printed; request 2 with 2 copies, copy 1 `Paper Feed (02)` → "Foto fehlt" → resent → both `DONE` | list: request 1 1 requested / 1 printed; request 2 2 requested / 2 printed / 1 failed, decision "Foto fehlt" with time | request missing; failed copy counted as printed; decision missing | integration |
| FR-12b.10 | After that scenario · restart the booth service | list empty; both requests still in the logs | requests from before the restart listed; logs lost | integration |
| FR-12b.11 | Printer status page, idle · set paper 18 and prints 50, then correct paper to 17; then `ERROR` → "Foto fehlt" confirmed | page shows 17 / 50; "Blatt verbraucht?" shown on the same page | counts only settable outside this page; question missing | e2e |
| FR-12b.15 | Printer status page open · tap the menu link | `login/menu.php` opens, session still valid | no link; link logs out or opens the login page | integration |
| FR-12b.19 | `SetEnv FAST_RETURN 0` removed · start booth, open printer status page; then restore it, restart | warning text from Appendix C shown; after the restore no warning | warning missing; warning shown while the line is set | integration |

---

# Part C — Foundation (L0)

## 13. CUPS + Gutenprint, USB host link

Configured, not owned: the `selphy` queue (photobooth-setup `native/setup-printer.sh`), the Gutenprint `canon-cp1300` backend, USB enumeration of the printer. Tested transitively through §9.

| ID | Pri | Requirement | Status |
|---|---|---|---|
| NFR-13.1 | Must | Queue options stay identical between Docker (`cups/entrypoint.sh`) and native (`setup-printer.sh`). `[code]` | approved |
| NFR-13.2 | Must | The Gutenprint backend shall run with `FAST_RETURN=0` (`SetEnv FAST_RETURN 0` in `cups-files.conf`, §4.3), so it stays attached for the whole print and reports stages and errors during it. `[derived]` R2 | approved (B-Q1: 4 clean prints, §17.3.2; R7) |
| NFR-13.3 | Must | Printing and the print screen shall keep working when the booth router or its Wi-Fi is down; only the operator web UI on the phone is lost (fallback: FR-12b.20). `[derived]` R10 | approved |
| NFR-13.4 | Should | At startup the booth shall check that `cups-files.conf` sets `FAST_RETURN 0` and log a warning otherwise (FR-12b.19). `[derived]` R2 | approved |

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| NFR-13.2 | Booth after a photobooth-setup run · read `/etc/cups/cups-files.conf`; during a simulated print read the backend process's environment | `SetEnv FAST_RETURN 0` present (not only in `cupsd.conf`); backend runs with `FAST_RETURN=0` | line missing or only in `cupsd.conf`; backend without it | integration |
| NFR-13.2 | Real SELPHY · one print | backend attached until `Print complete`; CUPS *completed* 1–3 s after the eject (Appendix A `T_cups_completed`) | job *completed* before `Printing LAMINATE` | bench |
| NFR-13.3 | Print running · switch the router off | print completes, print screen shows every stage | print or tracker stops | bench |
| NFR-13.4 | `SetEnv` line removed · start booth | warning logged and shown on the phone | silent start | integration |
| NFR-13.1 | CI, photobooth-setup via the read-only token (R13) · set up the queue with `cups/entrypoint.sh` in its container and with `native/setup-printer.sh` on the runner; dump `lpoptions -p selphy -l` and the queue's `lpstat -l -p selphy` options from each | identical option sets and values | an option set by one script only, or with a different value | integration |

---

# Part D — Cross-cutting

## 14. Logging

| ID | Pri | Requirement | Status |
|---|---|---|---|
| FR-14.1 | Must | Every stage change, backend message, error, printer status query result and operator decision shall be logged with timestamp, job ID, copy k of N and source (CUPS/backend/printer query/operator/timeout); ignored decisions (FR-12b.17, FR-12b.18) included. | approved |
| FR-14.2 | Should | Logs shall let staff reconstruct after an event how many copies were requested, printed, and failed. `[derived]` event findings | approved |

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| FR-14.1 | 2 copies · copy 1 `Paper Feed (02)`, printer query results, "Foto fehlt" from phone A, a second decision from phone B 1 s later, copy 2 printed | one entry per stage change, backend message, error, query result and decision, each with timestamp, job ID, copy k/N and source; phone B's decision logged as ignored | an event without an entry; an entry missing a field; wrong source; ignored decision not logged | integration |
| FR-14.2 | That scenario plus one clean single print, then restart the booth · count from the log files alone | 3 copies requested, 3 printed, 1 failed | counts differ from what happened; anything besides the logs needed | integration |

## 15. Security

Threat model: physical booth at a private event; the operator web UI on the booth Wi-Fi, which only the laptop and the operator phone join `[user]`; a root-run printer status query on the laptop. Accepted risk: someone with physical access can operate the printer and the laptop. `[user]`

| ID | Pri | Requirement | Status |
|---|---|---|---|
| NFR-15.1 | Must | The booth Wi-Fi shall use WPA2 or WPA3, and its password shall appear on no guest-visible screen, QR code, gallery page or label. `[user]` (narrowed v0.33; who may know the password is an operating rule for the user manual) | approved |
| NFR-15.2 | Must | The operator web UI shall require the upstream admin login (FR-12b.2), also from the booth Wi-Fi; at events `login.enabled` and admin protection (`protect.admin`) shall be on. A localhost exception for the booth laptop itself (`protect.localhost_admin` off) is allowed, as for the admin panel. `[derived]` R11 | approved |
| NFR-15.3 | Must | The operator web UI shall not be reachable from the internet (no port forwarding, not exposed through the public photo/QR host). `[derived]` | approved |
| NFR-15.4 | Must | The few commands the print stack needs as root (the printer status query) may run as root without a password, each as a fixed command line with no arguments taken from the web side, all listed in one dedicated sudoers drop-in file (`NOPASSWD`) that photobooth-setup installs; no other root permission is granted to the web server user. `[user]` R14 | approved |

| ID | Precondition · stimulus | Expected observation | Must NOT happen | Tier |
|---|---|---|---|---|
| NFR-15.1 | Real booth router · scan the booth Wi-Fi from a phone and read the router's security setting | WPA2 or WPA3 | open, WEP or WPA1 | bench |
| NFR-15.1 | Real booth · walk every guest page (start, result, gallery, QR) and decode every QR code shown; check the booth labels | booth Wi-Fi password found nowhere; no `WIFI:` QR for the booth network | password shown, encoded or printed for guests | bench |
| NFR-15.2 | Booth configured for an event · read the config | `login.enabled` and `protect.admin` both on | either off | integration |
| NFR-15.2 | Second host on the booth network (not localhost), no session · open the printer status page and POST a decision | login page; decision refused | status shown; decision applied | integration |
| NFR-15.3 | Real booth at the event site · from a device off the booth network (mobile data), request the printer status page and decision endpoint via the public photo/QR host and the router's WAN address | no response or 404; no printer data | page or decision endpoint reachable | bench |
| NFR-15.4 | Booth after a photobooth-setup run · `sudo -l -U <web server user>`; list `/etc/sudoers.d/` | only the fixed printer-query command line(s), `NOPASSWD`, all from the one drop-in file | any other root command; wildcards or arguments; a rule outside the drop-in | integration |

---

# Part E — Operations & Verification

## 16. Operational procedures (reading path)

Set up printer with `FAST_RETURN=0` and the printer-query root rule (§13, NFR-15.4) → switch on the booth router, log in on the phone with the admin login and open "Drucker" in the menu (§12b) → guest prints (§5, §7) → CUPS error appears (§6, Appendix B), shown on the phone → operator opens the back, fixes the printer; the printer prints the held photo by itself or, after a power cycle, does not; the phone shows what the printer status query saw → operator confirms on the phone (§12b) → booth reconciles and continues (FR-6.4, FR-5.12).

## 17. Verification & Validation

### 17.0 Test architecture

| Tier | Where | Covers |
|---|---|---|
| unit | CI, no hardware | §5 state model, §7 text mapping, §8b image preparation, §9 query interpretation, gate logic as pure functions |
| integration | Virtual test setup (§17.1) on the Docker stack, driven through the booth's HTTP API and CUPS | §5–§9, §12b, exactly-once contracts in simulation |
| e2e | Virtual test setup (§17.1) with real browsers (Playwright) acting as guest, admin and operator, the way people use the booth at an event | §6–§7, §12b, §17.5, exactly-once contracts as seen on screen |
| bench | Event laptop + real SELPHY | B-1 (§17.3), §5.4 contracts on hardware, Appendix A |

Traceability is generated from the requirement IDs in this document and the test plan (`testing/test-plan.yaml`, owned by `/build`) — not maintained by hand here.

**Where tests live:** all test code, scenarios, simulators and test workflows live in this fork (`starkerflamingo32/photobooth`, public), under `testing/` and `.github/workflows/`. `[user]` photobooth-setup (private) stays the deployment repo; the fork's CI checks it out read-only at a pinned commit to build the `cups` and `dummy-dslr` images, so the queue options under test are the deployed ones (NFR-13.1). `[derived]`

### 17.1 Virtual test setup

Required before booth logic is built (Phase 1). `[user]`

| Part | What it does |
|---|---|
| Docker stack | photobooth-setup's `compose.yaml` services (photobooth built from the commit under test, `cups`, `dummy-dslr`), plus the simulator below, from a test compose file in `testing/`. The fake DSLR already lets the booth take photos without a camera `[code]`. |
| Simulated SELPHY | A CUPS backend `sim-selphy://` replacing the USB device of the `selphy` queue; same PPD and queue options as `cups/entrypoint.sh`. It reads a scenario (per job: emit the backend's state messages — `Feeding Paper`, `Printing YELLOW/MAGENTA/CYAN/LAMINATE`, `Print complete` — at scripted times, as recorded in P-1; exit with `CUPS_BACKEND_STOP` and an Appendix B message, leaving the job pending; `CUPS_BACKEND_CANCEL` for the `0x0A` case; fast return on/off) and logs every job it receives, so a duplicate is visible. Real CUPS stays in the loop, so stopped queues, pending jobs and `cupsenable` behave as on the laptop. |
| Simulated printer query | Stands in for `gutenprint53+usb -s`: answers from the same scenario (held photo printed by itself after *t* s; idle without printing after a power cycle; hangs of ~17 s; wrong layer names around a hang) and fails the test if it is called while the queue is enabled (FR-9.7). |
| Scenario files | One YAML file per test: printer behaviour per job, printer query answers, and the browser steps (§17.1.1). Reused unchanged against upstream and against `booth`. |
| Simulated clock | Print timings scaled down (e.g. 1 s per layer) so a run takes minutes, not hours; Appendix A limits scaled the same way. |

#### 17.1.1 Browser test setup (e2e)

Real browsers do what people do at an event, against the virtual stack. `[user]`

| Browser | Viewport | Acts as | Typical steps |
|---|---|---|---|
| Booth | Acer Switch 3 screen, kiosk page | Guest | Start a photo, wait for the countdown, press "Drucken", choose copies, watch the print screen, open the gallery and print again |
| Admin | Desktop | Staff setting up | Log in, change `print.limit`, `print.max_multi`, `print.auto`; unlock printing, raise the limit, reset the print database |
| Operator phone (×2 where needed) | Phone size, touch | Operator | Log in, open "Drucker" in the menu (FR-12b.13), read the error and the printer query evidence, confirm "Foto ist da" / "Foto fehlt" |
| Stale page | Booth viewport | Guest on an old page | Gallery or result page opened before a print started, used later (FR-6.8, FR-6.9) |

- Several browsers run at the same time in one test, so cross-page effects (busy gate, stale pages, two operator phones) are tested as they happen.
- Each test checks what is on screen (texts from Appendix B/C, enabled/disabled buttons) and what reached the simulated SELPHY (number of jobs, duplicates).
- Each run keeps, per browser: video, screenshots at every step, a Playwright trace; plus the CUPS `error_log`, the simulated SELPHY job log, the printer query log, the tracker log and the booth's `data/` print files (`printed.csv`, `print.count`, `print.lock`).
- Test data is synthetic only (fake-DSLR images, test passwords); no event photos or real credentials, since artifacts of the public fork can be downloaded by others. `[derived]`

### 17.2 GitHub Actions plan

All workflows run in the fork. `[user]`

| Workflow | Trigger | Does | Time |
|---|---|---|---|
| `ci.yml` | every push / PR to `booth` | lint (upstream `linters.yaml` rules), PHPUnit, JS unit tests | ~3 min |
| `e2e.yml` | every push to `booth`, by hand | build the virtual stack (§17.1), run integration + e2e suites; upload Playwright report, videos, traces and logs as artifacts; failed tests listed in the run summary | ~15 min (assumed) |
| `upstream-characterisation.yml` | by hand, input: refs (default upstream `dev` 49b09a94, `76d76e36^`, and `booth`) | run the §17.5 suite against each ref; publish the L-Q answers as the run summary and an artifact | ~15 min (assumed) |
| `upstream-drift.yml` | weekly | merge upstream `dev` into a scratch branch, run `ci.yml` + `e2e.yml`; open an issue if anything breaks | |
| `package.yml` (existing, adapted) | tag | prebuilt release package so the laptop needs no npm/gulp build | |
| shellcheck | in photobooth-setup, on push | `native/*.sh` | |

- Upstream workflows not needed by the fork (crowdin ×3, `publish_docs.yml`, `build-faq.yml`, `release.yml`, `installer.yaml`) are disabled.
- Access to the private photobooth-setup repo: a fine-grained token, read-only on that one repo, stored as an Actions secret. Secrets are not given to pull requests from other forks, so those runs skip `e2e.yml` and run `ci.yml` only. (R13)
- **Not possible in Actions:** USB hardware — the real SELPHY (bench, acceptance); deploying to the laptop. No self-hosted runner on the booth laptop: on a public repo it would run code from strangers' pull requests. `[derived]`
- Public repo: standard GitHub-hosted runners cost nothing. `[code]` (repository visibility)

### 17.3 Bench session B-1 — real printer, five prints

Five prints answer the `FAST_RETURN=0` safety question and all real-printer questions without wasting paper. `[user]` Since v0.16 no sensor unit is needed; B-1 can run as soon as the logger is ready (it is, from P-1).

**Preconditions:** `selphy-autoresume` stopped and disabled (it would re-enable the queue by itself); `FAST_RETURN=0` set for CUPS (checked in each run's `envp`); CUPS `LogLevel debug`; `testing/bench/cups-poll-test.sh` logging CUPS state and backend messages about every second, and its `printer-poll` mode for the printer query while the queue is stopped; rear passes and the eject marked by key press; booth lighting as at an event; phone video of the printer front and back.

| # | Print | Questions answered |
|---|---|---|
| 1 | Normal print | B-Q1, B-Q2 |
| 2 | Normal print (second timing sample) | B-Q1, B-Q2 |
| 3 | Paper tray empty at start; after the error: refill, wait for the printer to print by itself (press its print button only if it does not) | B-Q3, B-Q4, B-Q5 |
| 4 | Ink cassette removed at start; after the error: reinsert, as print 3 | B-Q3, B-Q4, B-Q5 |
| 5 | Normal print (third timing sample) | B-Q1, B-Q2 |

No jam is provoked (R15). If a real jam happens during any print, follow the printer's display (power cycle), log it with the printer query running, and answer B-Q3…B-Q5 for it.

**Duplicate check without paper (prints 3–4, and a real jam if one happens):** after the photo has come out, remove the paper tray, then run `cupsenable selphy`. If CUPS sends the job again, the printer reports a paper error instead of printing, which shows the duplicate without using a sheet. Then cancel the job.

**Questions:**

| ID | Question |
|---|---|
| B-Q1 | Over all five prints, does `FAST_RETURN=0` work without confusing the printer (no hangs, no extra errors, no lost prints)? How long from send to `Print complete` / CUPS *completed*, compared with the eject? (R7, A6, A9, NFR-13.2) |
| B-Q2 | Exactly 4 layer messages per print, in order YELLOW, MAGENTA, CYAN, LAMINATE (A1)? Times: send → YELLOW, between layers, LAMINATE → `Print complete`. (Appendix A) |
| B-Q3 | For each error: what do CUPS job state, printer state and message show; is the queue stopped; is the job pending? (FR-9.2, FR-9.6) |
| B-Q4 | After the fix, does the printer print the held photo by itself, or only after its print button, or not at all? After a jam or ink-sheet error, was an ink panel set used up (check the cassette's ribbon), and how many sheets were lost? (A4, A7, FR-8.10) |
| B-Q5 | With the job still pending, does `cupsenable` send it again (duplicate)? What does CUPS report afterwards? (R3, FR-6.4) |
| B-Q6 | When `0x0A` ("Incorrect media for job") first occurs: was really nothing printed and no sheet used? Recorded only if it happens; can be provoked with a wrong-size paper tray or ink cassette if one is at hand. (R9, FR-5.18) |
B-Q7 (front sensor) was removed with the ESP32 unit in v0.16. B-Q8 (photo left on the tray) was answered by the owner in v0.17: no effect (A8). B-Q9 (printer query reliability) moved to the event observation E-1 (§17.6) as E-Q1; B-1 still runs the query in prints 3–5 and logs it.

**Exit:** B-Q1…B-Q5 answered (B-Q6 if seen); Appendix A filled; results added to this FSD's lifecycle (Appendix D, `related_test_baseline`). Met 2026-11-27 (§17.3.2).

#### 17.3.1 Pre-bench runs P-1 — 2026-11-26

A rehearsal of the B-1 logger. It does not replace B-1, and its numbers are single samples. `[code]`

**Setup:** event laptop, SELPHY CP1300 on USB, `selphy` queue as deployed; CUPS `LogLevel debug`. Script `testing/bench/cups-poll-test.sh` (fork): sends the print with Photobooth's own options (`lp -d selphy -o landscape -o fit-to-page`), and about every 1.2 s logs `lpstat -l -p selphy`, `lpstat -W all -o selphy`, and IPP `Get-Printer-Attributes` / `Get-Jobs` / `Get-Job-Attributes` (state, reasons, state message). Rear passes and the eject were marked by a person pressing a key, so those times include reaction time (~0.5–1 s). No booth flash, no phone video.

**Runs:**

| Job | Backend mode | Send → CUPS *completed* | Send → eject (human) | Result |
|---|---|---|---|---|
| 118 | fast return **on** (the `SetEnv` line was put in `cupsd.conf` and ignored, §4.3) | 10.4 s | ~58 s | backend exits at "Printing YELLOW"; CUPS sees nothing for the last ~48 s (R2) |
| 119 | `FAST_RETURN=0` (checked: `envp[0]="FAST_RETURN=0"` in the debug log) | 57.1 s | 54.5 s | backend stays until "Print complete"; *completed* ~2.6 s after the eject mark |

**Job 119 timeline** (seconds after the send; backend times from `error_log`, whole seconds):

| Event | Key press | Backend message | First poll that saw it |
|---|---|---|---|
| processing | | | 0.1 |
| Feeding Paper | | 1 | 1.4 |
| Rear pass 1 | 7.1 | | |
| Printing YELLOW | | 10 | 11.0 |
| Rear pass 2 | 19.5 | | |
| Printing MAGENTA | | 21 | 22.0 |
| Rear pass 3 | 31.4 | | |
| Printing CYAN | | 33 | 33.9 |
| Rear pass 4 | 42.7 | | |
| Printing LAMINATE | | 44 | 45.2 |
| Front eject | 54.5 | | |
| Print complete, backend exits | | 56 | 57.1 (job *completed*) |

**Preliminary answers (one print each; B-1 still decides):**

- **B-Q1:** with `FAST_RETURN=0`, CUPS *completed* came ~1–3 s after the eject (reaction time included). No hang, no extra error, no lost print: 1 of the 5 clean prints R7 asks for.
- **B-Q2:** exactly 4 layer messages, in order. Send → YELLOW: 10 s; between layers: 11 / 12 / 11 s; LAMINATE → Print complete: 12 s. Each rear pass comes ~1.5–3 s before the backend reports the next layer, so the sheet goes out at the back before each layer, including the first.
- **§9 / `T_cups_poll`:** each backend message reached the poll within ~1.5 s at a ~1.2 s poll interval.
- **Config:** `SetEnv` belongs in `cups-files.conf` on CUPS 2.4 (§4.3). Check every bench run for `FAST_RETURN=0` in the backend's `envp` lines.

**Error prints** (B-1 prints 3–4 rehearsed; all with `FAST_RETURN=0`, checked in each run's `envp`). After the error the CUPS poll was stopped and a second mode of the script, `printer-poll`, asked the printer itself about once a second (`BACKEND=canonselphyneo gutenprint53+usb -s`, root, only while the queue is disabled). A `watch` mode polls CUPS again without sending, for the duplicate check.

| Job | Provoked | CUPS error (send → error) | After fixing the cause | CUPS afterwards |
|---|---|---|---|---|
| 120 | empty paper tray inserted | `Printer error: Paper Feed (02)` after 14.5 s of "Feeding Paper"; seen by the poll 1.6 s later | printer printed the held photo **by itself** when the filled tray went in; photo out ~54 s later | job **pending**, queue stopped: CUPS did not see the print |
| 120 (duplicate check) | tray removed, then `cupsenable selphy` | job 120 *processing* again → `Printer error: No Paper (03)` after 5 s, queue stopped | — | **duplicate confirmed**: the already printed photo was sent again |
| 121 | empty paper tray inserted (rerun with `printer-poll`) | `Paper Feed (02)` after 14 s | printer itself: `Feeding Paper` + `Printer error: Paper Feed` → tray in → `Feeding Paper` (8 s) → `Printing YELLOW` → … → `Idle` after ~55 s; photo fine | job pending |
| 122 | ink cassette removed | `Printer error: No Ink (07)` after 5 s ("Sending spool data", "Feeding Paper" first) | printer itself: `Feeding Paper` + `No Ink` → cassette in → `Printing YELLOW` 8 s later → … → `Idle` after ~47 s | job pending |
| 123 | hand-held sheet at the back during pass 1 → 2 (meant as a jam) | gears slipped, sheet pulled in and out; at the 2nd hold the **ink sheet tore**, the magenta layer stuck to the paper, sheet stuck at the back. `Printer error: Unknown Error (0c)` at 35 s, **during printing**; poll saw it ~1 s later. Printer display: problem with the ink cassette — switch off, remove the cassette, tighten the ink sheet, reinsert, switch on | printer itself: `Printing MAGENTA` + `Unknown Error`; switched off (cassette could not be removed while off), on again, cassette replaced → **`Idle`, nothing printed: photo lost** | job pending; resent with `cupsenable` (correct here). The tray was out → `No Paper (03)` after 4 s, stopped again; tray in → printer printed the photo **by itself**, photo fine; job 123 still pending afterwards and had to be cancelled |

**Preliminary answers:**

- **B-Q3:** the error reaches CUPS and the poll within ~1–2 s; the queue stops and the job stays pending (FR-9.2, FR-9.6). `printer-state-reasons` is `media-empty,paused` for paper and ink alike; only the text differs. An empty tray gives `Paper Feed (02)`, the same text as a misfeed (R1); a missing tray gives `No Paper (03)`.
- **B-Q4:** paper and ink: the printer had the job and printed it by itself once the tray or cassette was back; no button (A4). Ink-sheet error (`0c`): the printer needs a power cycle and loses the photo; resend. Used: 1 sheet and the damaged cassette (replaced). A real paper jam was not produced (R15).
- **R2:** with `FAST_RETURN=0` an error **during** printing reaches CUPS (job 123, ~1 s), which fast return would have missed.
- **B-Q6:** status `0x0A` not seen; every error stopped the queue with the job pending.
- **B-Q5:** yes. `cupsenable` with the job still pending sends it again (R3), even though the photo was already printed.
- **Printer status query (R14, E-Q1):** shows the held print as *Feeding Paper → Printing … → Idle* while CUPS still reports the job pending. In both runs the query hung twice for ~17 s (`libusb error -7`, `Unable to attach to printer`) at layer changes, and the layer names around the hangs were inconsistent (e.g. LAMINATE for 1 s); *Printing → Idle* was reliable (2 of 2). The photo printed during the queries (job 121) was fine.

**Rule seen in all five error cases:** whenever the backend had sent the photo before the error, the printer keeps it and prints it by itself once the cause is fixed — unless it is switched off. CUPS sees none of this: the job stays pending, and `cupsenable` would print it again. Whether the photo came out can be told by the operator or the printer status query (*Printing → Idle* = printed; *Idle* after a power cycle without printing = lost). This is the basis for removing the ESP32 unit in v0.16.

**Still open:** a real paper jam (R15), ink panels used by a jam (A7), B-Q6 (not seen), four more complete prints with the backend attached under `FAST_RETURN=0` for R7 (only job 119 counts: the self-resumed prints ran with no backend attached; no misbehaviour in the attached parts of jobs 120–123).

Raw logs stay on the laptop (`~/poll-test-20261126-*`: 172543, 173429, 175408, 182634, 183252, 184007); the printer serial is redacted in the copied `error_log`.

#### 17.3.2 B-1 results — 2026-11-27

`FAST_RETURN=0` checked in every run's `envp`. All prints prepared as in §8b (B-1 tested that path; it was adopted as FR-8b.1–8b.3). Run as four planned prints plus one extra: print 1 was dropped by the owner to save paper, job 125 (a prescaled normal print made just before) is used as an extra timing sample. `[user]` Times in seconds after the send; backend times from `error_log` (whole seconds), passes and eject by key press (~0.5–1 s reaction).

| Job | Print | Feeding | YELLOW | MAGENTA | CYAN | LAMINATE | Print complete | Eject (key) | CUPS *completed* |
|---|---|---|---|---|---|---|---|---|---|
| 125 | extra, normal | 1 | 10 | 21 | 33 | 44 | 56 | 55.3 | 56.6 |
| 128 | 2, normal | 1 | 10 | 21 | 33 | 44 | 56 | 55.9 | 57.1 |
| 129 | 5, normal | 1 | 9 | 21 | 33 | 44 | 56 | 55.5 | 56.8 |

Rear passes (key) ~2 s before each layer message: 8.1–8.2, 20.6–21.2, 32.1–32.5, 43.6–44.7 s. Each message reached the poll within ~1.5 s.

| Job | Print | Error (poll) | After the fix (key `f`) | Ending |
|---|---|---|---|---|
| 126 | 3, empty tray | `Paper Feed (02)` at 14.1 s; queue stopped, job pending | printer printed the held photo by itself: first pass 16 s, eject 52 s after the fix | duplicate check: `cupsenable` resent job 126 → `No Paper (03)` 4 s later; job cancelled in CUPS; tray back in → **printer printed it again** (R18). 2 sheets used |
| 127 | 4, ink cassette out | `No Ink (07)` at 3.9 s; queue stopped, job pending | printed by itself: first pass 8 s, eject 55 s after the fix | as the booth will: job cancelled in CUPS, then `cupsenable` → nothing printed, queue idle |

**Answers:**

- **B-Q1:** no hang, no extra error, no lost print in 3 attached prints; *completed* 1.2–1.3 s after the eject mark. With P-1 job 119: 4 of the 5 prints R7 asks for (owner accepted, rest in §17.4).
- **B-Q2:** exactly 4 layer messages, in order, in 3 of 3. Send → YELLOW 9–10 s; between layers 11–12 s; LAMINATE → Print complete 12 s. Prescaling did not change the timing against P-1 (job 119, camera JPEG).
- **B-Q3:** as in P-1: `Paper Feed (02)` for an empty tray (~14 s, after the printer's feed retries), `No Ink (07)` for a missing cassette (~4 s); the queue stops and the job stays pending; `printer-state-reasons` as in P-1.
- **B-Q4:** paper and ink: the printer printed the held photo by itself once the cause was fixed, no button (A4); 1 sheet each. No jam happened, so A7 stays open.
- **B-Q5:** `cupsenable` resends a pending job (again). New: cancelling the job in CUPS does **not** remove it from the printer once it has been resent; the printer prints it when the cause is fixed (R18).
- **B-Q6:** `0x0A` not seen.
- **Printer status query (R14):** *printing → Idle* at the eject in 2 of 2 (138.2 s vs eject 138.9 s; 135.9 s vs 135.7 s). Hangs of 18.0–18.1 s with `libusb error -7` 3× (2 in job 126, 1 in job 127), at layer changes; layer names after a hang wrong again (CYAN 4 s after MAGENTA). The query saw the cleared paper error ~3 s before the operator's key press.

Logs on the laptop: `~/b1-*-20261127-*` (printer serial redacted in the copied `error_log`).

### 17.4 Acceptance (before the next event)

1. 10 consecutive single-copy prints: every stage shown, 10 photos out, "Fertig" never before the photo is out.
2. One request with 3 copies, no photo taken until the end: "Kopie k von 3 fertig – bitte Foto entnehmen" for T_take_hint after copies 1 and 2, each next copy starts by itself, 3 good photos out.
3. Each error in Appendix B provoked once on the real printer: correct text on the booth screen and the phone, printer query evidence shown, new prints rejected, photo/QR still work, operator fixes the printer and confirms on the phone, exactly one photo per requested copy.
4. A gallery page loaded before a print started: its print button is refused while the print runs.
5. Printer USB cable unplugged mid-print: "Drucker nicht verbunden" shown; after plugging back in, the operator decides and exactly one photo per copy results.
6. Booth router switched off mid-print: the print completes and the print screen shows every stage (NFR-13.3); a following error is resolved on the booth laptop (FR-12b.20).
7. Two phones decide the same error within a few seconds: only the first decision is applied, no duplicate (FR-12b.18).
8. Paper counter: load 3 sheets and enter 3 on the phone; print 3 photos — low warning shown, count 0 → refill warning on the phone, the 4th request still prints and stops with "Papier leer" (FR-8.6); refill, enter 18, the printer prints the held photo, operator confirms; empty the tray with the count at 5 → "Papiereinzug gestört" shown, as expected for a wrong count (FR-8.6).
9. `FAST_RETURN` line removed from `cups-files.conf`, CUPS restarted: warning on the phone (NFR-13.4); a print goes to `AWAIT_OPERATOR`, never "Fertig" early (FR-5.4). Restore the line afterwards.

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

### 17.6 Event observation E-1 — auto-resume at a real event

How reliable the printer status query and auto-resume (FR-6.15) are gets checked at a real event, not on the bench: errors there are real (jams, empty trays at random points), and there are more of them than B-1 can provoke without wasting paper. `[user]`

**Setup:** the first event with the new print stack (Phase 5). Auto-resume is on (FR-12b.21 default) unless the operator switches it off. The log (FR-14.1) already holds, per error: CUPS error, every query result with time, *no answer* periods, and the decision with its source (printer query or operator). The operator also notes for each error what really happened (photo came out / not), e.g. by a short note on the phone or afterwards from the event photos and the paper count.

| ID | Question |
|---|---|
| E-Q1 | Per error: did the query report *printed* exactly when the photo came out, and *nothing printed* when it did not? How often did it hang, for how long, and did a hang ever hide the *printing → Idle* change? Did any print made while the query ran come out damaged? Was any automatic decision wrong (duplicate or missing photo)? How many errors did auto-resume decide alone? (R14, FR-6.15, FR-9.8, FR-9.9) |

**Exit:** E-Q1 answered over all errors of the event; results in Appendix D (`related_test_baseline`). If any automatic decision was wrong, or a clean query result disagreed with the real outcome, the switch default becomes off (manual resume) until the cause is fixed; otherwise the default stays on. With few or no errors at the event, E-1 repeats at the next event.

---

## Appendix A — Timing parameters (measured in P-1 and B-1)

| Parameter | Meaning | Value |
|---|---|---|
| `T_sent_to_layer1` | max time from SENT to `Printing YELLOW`, through `FEEDING` (stall limit) | **40 s** `[user]`: 20 s for the stage plus 20 s for the printer's paper-feed retries. B-1: 9–10 s (3 prints), P-1: 10 s |
| `T_layer` | max time between layer messages (stall limit) | **20 s** `[user]`. B-1: 11–12 s, P-1: 11–12 s |
| `T_layer4_to_complete` | max time from `Printing LAMINATE` to `Print complete` (stall limit) | **20 s** `[user]`. B-1: 12 s, P-1: 12 s |
| `T_take_hint` | how long "Fertig – bitte Foto entnehmen" stays after the last copy, and "Kopie k von N fertig – bitte Foto entnehmen" between copies | **3 s** `[user]` |
| `T_cups_poll` | max delay for a CUPS error or backend message to reach the tracker | **2 s** `[user]`. B-1 and P-1: messages and errors within ~1.5 s at a ~1.3 s poll |
| `T_printer_poll` | interval of the printer status query while the queue is stopped | **1 s** `[user]` (as in P-1) |
| `T_query_hang` | a printer status query running longer than this is stopped and retried | **25 s** `[user]` (B-1 hangs 18.0–18.1 s, 3×; P-1 ~17 s) |
| `T_prepare` | max time to prepare a copy's print image (§8b) before falling back (FR-8b.4) | **10 s** `[user]` (B-1: 2.6 s on the booth laptop) |
| `T_ui` | max delay from event to screen (booth screen and operator web UI), and for print buttons to change state | **2 s** `[user]` |
| `T_resume` | max delay from operator confirmation to queue enabled (incl. stopping a running printer query) | **5 s** `[user]` |
| `LOW_THRESHOLD` | supply count at which the low warning starts (not a timing value; FR-8.2) | **3** `[user]` |
| `T_cups_completed` | send → CUPS *completed*: ~17 s observed with fast return on `[code]`; with `FAST_RETURN=0` | observation, not a limit: **56.6–57.1 s** with `FAST_RETURN=0` (B-1, 3 prints; 1.2–1.3 s after the eject); fast return on: 10.4 s (P-1) |

Removed in v0.16 with the ESP32 unit: `T_sent_to_pass1`, `T_pass`, `T_eject` (replaced by the layer-message limits above), `T_rear_block`, `T_eject_settle`, `T_debounce`, `F_raw`, `T_fault`, `T_output_wait`, `T_confirm`, `T_alive`.

## Appendix B — Messages (German; wording approved `[user]` v0.24)

**Stages**

| State | Text |
|---|---|
| `SENT` | Druckauftrag gesendet |
| `FEEDING` | Papier wird eingezogen |
| `LAYER_1` | Druck gestartet – Gelb wird gedruckt (1/4) |
| `LAYER_2` | Magenta wird gedruckt (2/4) |
| `LAYER_3` | Cyan wird gedruckt (3/4) |
| `LAYER_4` | Schutzschicht wird aufgetragen (4/4) |
| `DONE` (last copy, for T_take_hint) | Fertig – bitte Foto entnehmen |
| `DONE` of copy k < N, for T_take_hint (FR-7.19) | Kopie k von N fertig – bitte Foto entnehmen |
| `AWAIT_OPERATOR` | Druck wird geprüft – bitte Personal rufen |
| `CANCELLED` | Druck abgebrochen |
| copies (N > 1) | Kopie k von N |
| busy rejection (FR-6.10) | Drucker beschäftigt – bitte warten, bis der aktuelle Druck fertig ist |

**Printer errors (from CUPS)** — one staff hint per error (FR-7.7) `[user]` v0.19. Basis: P-1 (A4) — after paper and ink errors the printer prints the held photo by itself once the cause is fixed; after an ink-sheet error it needs a power cycle and the photo is lost. Hints marked *to be checked* are for errors not yet seen on the real printer and are corrected when first seen (B-1 or an event).

| Trigger | Guest text | Staff hint |
|---|---|---|
| `Paper Feed (02)`, paper left 0 (FR-8.6) | Papier leer | Papier auffüllen und Anzahl am Handy eingeben – der Drucker druckt das Foto danach selbst. Druckt er nicht innerhalb einer Minute von selbst: Drucktaste am Drucker drücken. Dann am Handy bestätigen, ob das Foto da ist. |
| `Paper Feed (02)`, paper left > 0 | Papiereinzug gestört | Papier prüfen und neu einlegen, Anzahl am Handy eingeben – der Drucker druckt das Foto danach selbst. Druckt er nicht innerhalb einer Minute von selbst: Drucktaste am Drucker drücken. Dann am Handy bestätigen, ob das Foto da ist. |
| `Paper Feed (02)`, count unknown `[code]` | Papier leer oder Papiereinzug gestört | Papier prüfen oder auffüllen, Anzahl am Handy eingeben – der Drucker druckt das Foto danach selbst. Druckt er nicht innerhalb einer Minute von selbst: Drucktaste am Drucker drücken. Dann am Handy bestätigen, ob das Foto da ist. |
| `No Paper (03)` `[code]` P-1: paper tray removed | Papierkassette fehlt | Papierkassette einsetzen – der Drucker druckt das Foto danach selbst. Druckt er nicht innerhalb einer Minute von selbst: Drucktaste am Drucker drücken. Dann am Handy bestätigen, ob das Foto da ist. |
| `No Ink (07)` `[code]` P-1: ink cassette removed | Farbkassette fehlt | Farbkassette einsetzen – der Drucker druckt das Foto danach selbst. Druckt er nicht innerhalb einer Minute von selbst: Drucktaste am Drucker drücken. Dann am Handy bestätigen, ob das Foto da ist. |
| `No Paper and Ink (09)` `[code]` | Papier- oder Farbkassette fehlt | Beide Kassetten prüfen und einsetzen – der Drucker druckt das Foto danach selbst. Druckt er nicht innerhalb einer Minute von selbst: Drucktaste am Drucker drücken. Dann am Handy bestätigen, ob das Foto da ist. |
| `Ink Cassette Empty (06)` `[code]` | Farbkassette leer | Farbkassette tauschen – der Drucker druckt das Foto danach meist selbst. Dann am Handy bestätigen, ob das Foto da ist. *(to be checked)* |
| `Unknown Error (0c)` `[code]` P-1: ink sheet torn / slack; `0x0C` is "Ink jam" in upstream Gutenprint, unnamed in Debian 12 | Problem mit der Farbkassette | Drucker ausschalten und wieder einschalten, Farbkassette entnehmen, Farbfolie straffen oder Kassette tauschen. Das Foto ist danach verloren: am Handy *Foto fehlt* bestätigen. |
| `Paper jam (0b)` `[code]` | Papierstau | Den Anweisungen am Druckerdisplay folgen (Drucker aus- und wieder einschalten). Dann am Handy bestätigen, ob das Foto da ist. *(to be checked; the printer display asks for a power cycle `[user]`, after which the held photo is probably lost, A4)* |
| `Incorrect media for job (0a)` `[code]` R9 | Falsche Papier- oder Farbkassette | Papierkassette und Farbkassette für Postkarten einsetzen, dann am Handy *Behoben – nochmal drucken* bestätigen. *(to be checked)* |
| `Unknown Error (08)` `[code]` — `0x08` is "Incorrect Ink loaded" in upstream Gutenprint; Debian 12 has no text for it. Seen at the 2026-11-22 event | Falsche Farbkassette | Farbkassette für Postkarten einsetzen. Druckt der Drucker danach nicht innerhalb einer Minute von selbst: Drucktaste am Drucker drücken. Dann am Handy bestätigen, ob das Foto da ist. *(to be checked)* |
| `Incorrect Paper loaded (05)` `[code]` | Falsche Papierkassette | Papierkassette für Postkarten einsetzen. Druckt der Drucker danach nicht innerhalb einer Minute von selbst: Drucktaste am Drucker drücken. Dann am Handy bestätigen, ob das Foto da ist. *(to be checked)* |
| printer not connected / not found (assumed) | Drucker nicht verbunden | USB-Kabel und Strom prüfen. |
| any other `[code]` | Druckerproblem – bitte Personal rufen | *(raw CUPS message)* |

**Information (not errors)**

| Source | Trigger | Text |
|---|---|---|
| Tracker | stall (FR-5.5) | Drucker reagiert nicht – bitte Personal rufen |
| Tracker | completed before `LAYER_4` (FR-5.4) | Druckstatus unklar – bitte Personal rufen |
| Counter | low (FR-8.2) | Papier fast leer – noch n Blatt / Farbkassette fast leer – noch n Drucke *(staff notice)* |
| Counter | zero (FR-8.4) | Papier laut Zähler leer / Farbkassette laut Zähler leer – bitte nachfüllen *(staff notice; printing continues)* |

## Appendix C — Operator texts (German; wording approved `[user]` v0.24)

**Web UI (phone, §12b)** — shows the full Appendix B texts, the raw CUPS message and the evidence list.

| Situation | Text |
|---|---|
| Decision prompt | Foto ist da · Foto fehlt *(for `0x0A`: Behoben – nochmal drucken, FR-5.18)* |
| Decision confirmation | Kopie k von N: Foto ist da – bestätigen? / Kopie k von N: Foto fehlt – nochmal drucken? |
| Decided in another browser (FR-12b.18) | Bereits entschieden: Foto ist da / Foto fehlt |
| Connection lost | Keine Verbindung zur Booth |
| Queue stopped | Druckwarteschlange angehalten |
| Printer query: printed (FR-5.14) | Drucker hat gedruckt (hh:mm:ss) |
| Printer query: idle, nothing printed | Drucker meldet keinen Druck – Foto vermutlich verloren |
| Printer query: printing | Drucker druckt gerade … |
| Printer query: no answer | Drucker antwortet nicht – neuer Versuch … |
| Automatic decision (FR-6.15) | Automatisch entschieden: Foto ist da – Drucker hat gedruckt (hh:mm:ss) / Foto fehlt – Drucker meldet keinen Druck |
| Auto-resume switch (FR-12b.21) | Automatisch fortsetzen: an / aus |
| FAST_RETURN missing (FR-12b.19) | Druckstatus unzuverlässig – FAST_RETURN nicht gesetzt |
| Power cycle before "Foto fehlt" (FR-12b.22, FR-12b.23) `[user]` v0.32 | Der Drucker hat das Foto vielleicht noch und druckt es nach dem Beheben. Bitte Drucker aus- und wieder einschalten. · Drucker wurde aus- und eingeschaltet – Foto fehlt bestätigen |
| After "Foto fehlt" (FR-8.10) | Blatt verbraucht? · Ja / Nein *(Ja: Papier −1, bei begonnenem Druck auch Farbe −1)* |

The panel texts (old Appendix C, first table) were removed with the panel in v0.16.

## Appendix D — Lifecycle metadata

```yaml
document_status: draft
fsd_version: 0.33
repository: starkerflamingo32/photobooth (branch booth)
baseline_commit: e8c2cbd3
applicable_firmware_version: n/a (no ESP32 unit since v0.16)
predecessor: fsd-esp32-v0.15.md (v0.15, setup with ESP32 unit; frozen 2026-11-27)
author: booth owner, with Claude
reviewers: []
approval_status: not approved
created: 2026-11-23
last_updated: 2026-11-27
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
  - 2026-11-26 v0.13: pre-bench run P-1 on the real SELPHY (§17.3.1): fast return on confirmed (completed 10.4 s, eject
      ~58 s); FAST_RETURN=0 keeps the backend attached (completed ~2.6 s after the eject), 1 of 5 clean prints for R7;
      SetEnv must be in cups-files.conf (§4.3, NFR-13.2); A6 confirmed; CUPS stage messages recorded (§9.1, OD1);
      preliminary pass timings in Appendix A
  - 2026-11-26 v0.14: P-1 error runs (§17.3.1): empty tray → Paper Feed (02), tray removed → No Paper (03),
      cassette removed → No Ink (07); printer prints the held photo by itself after the fix (A4 contradicted for
      paper/ink; FR-7.7 and Appendix B hints under review); duplicate on cupsenable confirmed (R3, B-Q5); standalone
      printer status query sees the held print while CUPS is blind, with ~17 s hangs (R14, OD1); jam still open
  - 2026-11-26 v0.15: P-1 job 123: hand-held "jam" tore the ink sheet → Unknown Error (0c) during printing reached
      CUPS (R2 settled for FAST_RETURN=0); power cycle loses the held photo, resend correct; resend into a missing
      tray → No Paper (03), then auto-print; A4 wrong both ways; per-error staff hints proposed (Appendix B);
      R15 (no hand-provoked jams), B-1 print 5 changed; B-Q6 not seen
  - 2026-11-27 v0.16: ESP32 unit removed completely [user] — the laptop gets the status from CUPS and the printer
      (v0.15 frozen as fsd-esp32-v0.15.md). Stages from backend messages (FR-5.2), DONE from Print complete after
      LAYER_4 (FR-5.3), completed before LAYER_4 → AWAIT_OPERATOR (FR-5.4); printer status query while the queue is
      stopped as evidence (FR-9.7…9.9, FR-5.14); §10, §11, §12 removed; phone is the only operator interface
      (decisions FR-12b.17/18, fallback OD12); OD11 reopened (WAIT_OUTPUT gone), OD13 auto-reconcile added;
      OD1, OD2, OD8 removed; R4, R6 removed, R16, R17 added; NFR-13.4 FAST_RETURN check, NFR-15.4 root rule;
      B-1 without unit (B-Q7 removed, B-Q8, B-Q9 added); Appendix A/B/C cut down to the sensor-free setup
  - 2026-11-27 v0.17: OD11 decided — a photo left on the tray does not disturb the next print; take-photo message
      for T_take_hint between copies, then the next copy (FR-7.19), FR-7.20 and R16 removed, B-Q8 answered;
      OD12 decided — fallback is the printer status page on the booth laptop via admin login and menu, no hidden
      gesture (FR-12b.20); NFR-15.4 — needed root commands run without a password, approved; printer query
      reliability tested at a real event (§17.6 E-1, E-Q1 replaces B-Q9, Phase 5), OD13 decided from it
  - 2026-11-27 v0.18: OD13 proposal — middle option after E-1: the booth decides by itself only on clean printer
      query evidence (printing → Idle without a hang; Idle after a power cycle without printing), the operator
      decides everything else; automatic decisions logged and shown on the phone with their reason
  - 2026-11-27 v0.19: OD13 made a planned decision point (does not block "defined"); review block 1 — FR-5.2,
      5.3, 5.4, 5.6, 5.14, FR-9.7…9.10 approved; FEEDING stage ("Papier wird eingezogen") added to FR-5.2
  - 2026-11-27 v0.20: review block 2 — FR-7.7 per-error staff hints approved; Appendix B hints rewritten: paper/ink
      errors "printer prints by itself" with the print button as fallback after a minute; 06 and 0b to be checked;
      0b follows the printer display (power cycle), no "remove paper" step
  - 2026-11-27 v0.21: review block 3 — OD3 (defaults 18 sheets / 56 prints), OD4 (restore print screen, FR-7.12),
      OD5 (busy notice, FR-6.10) decided; FR-8.4 changed to warn only: a count of 0 no longer blocks printing
      (FR-6.12 narrowed)
  - 2026-11-27 v0.22: review block 4 — T_take_hint 3 s, LOW_THRESHOLD 3, T_ui 2 s, T_resume 5 s
  - 2026-11-27 v0.23: review block 5 — FR-7.9, 8.9, 12b.7, 12b.10, 12b.12, 12b.15, 12b.19, NFR-13.4, FR-14.2
      approved; no requirement left `proposed`
  - 2026-11-27 v0.24: review block 6 — Appendix B and C wording approved; header "Load defined" updated
  - 2026-11-27 v0.25: 0x0A identified from the Gutenprint source (Debian 12 and upstream) as "Incorrect media for job";
      OD6 decided — 0x0A is a printer error, copy resent after the fix (FR-5.18); R9, B-Q6 updated; Appendix B
      rows for 0a, 08 ("Incorrect Ink loaded" upstream, seen at the 2026-11-22 event) and 05; 0c is "Ink jam" upstream
  - 2026-11-27 v0.26: OD13 decided — auto-resume (middle option) in force now (FR-6.15), switch "Automatisch
      fortsetzen" on the printer page, default on (FR-12b.21); E-1 now checks auto-resume and sets the default
  - 2026-11-27 v0.27: review of the simple open items — T_printer_poll 1 s, T_query_hang 20 s; A2, A3, booth router
      (same LTE router), §15 threat model confirmed [user]; NFR-15.4 root commands in one sudoers drop-in file;
      R15: no provoked jam at all, B-1 print 5 is a third normal print; real jam → power cycle cancels the held job
      (A4); B-1 runs now, ahead of Phase 1; §17.5 no longer blocks Load defined
  - 2026-11-27 v0.28: B-1 run (§17.3.2): Appendix A filled [user] — stall limits 40/20/20 s, T_cups_poll 2 s,
      T_query_hang 25 s (hangs 18 s); R7 at 4 of 5 clean prints, accepted; new §8b Print Image (FR-8b.1–8b.3,
      prescale to the 1248 x 1871 CUPS raster, algorithm normative) [user]; R18: a held job cannot be cancelled
      from CUPS (duplicate in B-1 job 126), mitigation proposed
  - 2026-11-27 v0.29: R18 mitigation approved [user] — power cycle before a manual "Foto fehlt" unless the printer
      is Idle without an error (FR-12b.22); operator text proposed (Appendix C)
  - 2026-11-27 v0.30: /define audit of v0.27–v0.29 (evolve-mode, requirement-quality): FR-12b.22 split into
      FR-12b.22–12b.24 (atomic); FR-8b.1 states the result only, FR-8b.2 no longer repeats FR-8b.3; FR-8b.3 input
      is upstream's print image [user]; FR-8b.4 fallback to the upstream path on failure [user], T_prepare 10 s
      proposed; FR-7.21 preparation shown as the SENT text [user]; §8b tolerances approved; §8b purpose
      present-state; §17.0 unit tier lists §8b
  - 2026-11-27 v0.31: T_prepare 10 s approved [user]
  - 2026-11-27 v0.32: Appendix C power-cycle text approved [user]; nothing proposed left
  - 2026-11-27 v0.33: contracts for the 20 Must/Should without one (NFR-13.2, FR-7.2, 7.3, 7.9, 7.12, 7.13, 8.9, 12b.1, 12b.9,
      12b.10, 12b.11, 12b.15, 12b.19, 14.1, 14.2, NFR-13.1, 15.1–15.4); FR-12b.9 touch targets ≥ 48×48 CSS px at 360 px
      width [user]; FR-12b.10 list covers requests since booth service start [user]; NFR-15.1 narrowed to WPA2/WPA3 and
      no guest exposure of the password, staff-only knowledge moved to the user manual [user]
superseded_requirements:
  # v0.30
  - FR-12b.22 v0.29 (prompt, apply-after-confirm and logging in one) — FR-12b.22, 12b.23, 12b.24
  - FR-8b.1 v0.28 (incl. lossless, ppi=300 and option list) — result only; means in §8b.1
  # v0.28
  - T_query_hang 20 s (v0.27) — 25 s; print path `lp -o landscape -o fit-to-page <camera JPEG>` — §8b
  # v0.27
  - R15 mitigation v0.15 and B-1 print 5 v0.15 (spent cassette or real jam) — no provoked jam; print 5 normal
  # v0.26
  - OD13 v0.18–v0.25 (evidence only until E-1, middle option as proposed result) — auto-resume in force now with a switch (FR-6.15, FR-12b.21)
  # v0.21
  - FR-8.4 v0.20 (count 0 blocks new prints and holds the next copy) — warning only (FR-8.4 v0.21)
  # v0.20
  - FR-7.7 v0.16 (one rule: press the print button, confirm on the phone) — per-error hints (FR-7.7 v0.20)
  # v0.17
  - FR-7.20 v0.16 (remind the guest to take an earlier photo before a new request) — not needed, photo on the tray does no harm
  - FR-7.19 v0.16 (mechanism open) — fixed time T_take_hint
  - R16 (photo left on the tray) — does no harm (A8)
  - B-Q8, B-Q9 — answered by the owner / moved to E-Q1 (§17.6)
  - OD11, OD12 — decided (FR-7.19, FR-12b.20)
  # v0.16 — ESP32 unit removed (full text in fsd-esp32-v0.15.md)
  - §10 Unit Interface FR-10.1, 10.2, 10.3, 10.5, 10.6, 10.9 — no unit
  - FR-10.7, FR-10.8 — continue as FR-12b.17, FR-12b.18 (decisions from browsers only)
  - §11 ESP32 Sensor Unit, all FR-11.x / NFR-11.16 — no unit
  - §12 Operator Panel, all FR-12.x — no panel; fallback OD12
  - FR-5.2, FR-5.3 v0.15 (stages / DONE from rear passes and front eject) — from backend messages (v0.16 FR-5.2, FR-5.3)
  - FR-5.4, FR-5.6 v0.15 (no CUPS event sets DONE) — CUPS sets DONE only after LAYER_4 (v0.16 FR-5.3, FR-5.4)
  - FR-5.10 (IR report is information) — no IR
  - FR-5.14 v0.15 (IR evidence) — evidence from backend messages and the printer query (v0.16 FR-5.14)
  - FR-5.16 (send only with the output clear, WAIT_OUTPUT) — no output sensor; OD11 reopened
  - FR-5.17 (front protrusion never DONE) — no front sensor
  - FR-6.13 (IR information never blocks) — no IR
  - FR-6.14 (occupied output does not reject) — no output sensor
  - FR-7.8 ("Sensor offline") — no unit
  - FR-7.11 (IR information styling) — no IR
  - FR-7.20 v0.15 (must wait for photo taken) — Should, depends on OD11
  - FR-12b.5 (web UI works while the unit is offline) — no unit
  - FR-12b.8 (decision made on the panel shown) — FR-12b.18
  - FR-12b.16 (output state on the phone) — no output sensor
  - OD1 (unit offline), OD2 (IR events without a job), OD8 (panel while booth unreachable) — no unit/panel
  - R4 (IR misreads), R6 (ESP32 on the shared USB hub) — no unit
  - B-Q7 (front sensor) — no unit
  # earlier
  - FR-11.18 v0.10 (digital OUT / analog cross-check) — removed; broken wire caught by a pull-down and FR-11.12
  - FR-11.9 v0.9 (Must: LED-off/LED-on subtraction for both sensors) — May, rear only; HW-201 LEDs are always on (FR-11.17)
  - FR-8.1–8.4 v0.6 (Should/May, later phase) — rewritten as §8.2 v0.7
  - Phase 5 (supply counters / print limit) — counters in Phase 2, no print limit
  - §17.5 v0.6 L-Q1…L-Q10, S1…S9 — reduced to L-Q1…L-Q4, S1…S5
  - FR-12.1 v0.3 (panel shows the full CUPS error text) — short text on the panel, full text on the web UI (FR-12b.3)
  - FR-12.2 v0.3 (panel must show IR information) — now May (FR-12.8); IR information shown on the web UI
  - FR-6.2 v0.2 (photos keep working while a print runs) — print screen stays up (FR-7.14)
  - FR-5.7 (fallback HANDED_OVER) — no booth without ESP32 unit (itself superseded by v0.16)
  - FR-5.8 (queue position) — no print queue (FR-6.1)
  - FR-6.3 (keep queued jobs) — no print queue
  - FR-6.5 (unit disconnect never blocks) — replaced by OD1
  - FR-6.6 (sensor error blocks) — IR reports are information (FR-5.10, FR-6.13)
  - FR-6.7 (accept prints while printing) — one print at a time (FR-6.1)
  - FR-7.7 v0.1 ("do not press the printer button") — pressing it is the only fix (A4)
  - FR-10.3 v0.1, FR-10.4 (CUPS-only fallback stages) — replaced by OD1
  - NFR-12.2 (some mechanism re-enables the queue) — booth reconciliation (FR-6.4, FR-6.11)
  - FR-13.x → FR-14.x (renumbered with the new §12)
open_decisions: none open
related_test_baseline: P-1 2026-11-26 (pre-bench, §17.3.1); B-1 2026-11-27 (jobs 125–129, §17.3.2); E-1 and §17.5 not run
```

## Related

- `fsd-esp32-v0.15.md` — this FSD's previous version, with the ESP32 sensor unit and operator panel (frozen)
- photobooth-setup: `event-2026-11-22-findings.md`, `paper-ink-counter.md`, `native/setup-printer.sh`, `native/selphy-autoresume.sh`, `cups/entrypoint.sh`
- This fork: `api/print.php`, `src/Service/PrintManagerService.php` (current print path, for reference only); `api/printLimit.php`, `api/printDB.php`, `api/admin.php`, `assets/js/core.js`, `assets/js/tools.js` (print limit, auto-print; §17.5); `login/menu.php`, `lib/boot.php` (§12b); `testing/`, `testing/bench/cups-poll-test.sh`, `.github/workflows/` (tests, §17)
- Gutenprint source: `src/cups/backend_common.c` (`FAST_RETURN`), `src/cups/backend_canonselphyneo.c` (CP1300 status loop)
