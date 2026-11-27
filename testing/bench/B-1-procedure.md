# Bench session B-1 — procedure

FSD §17.3. Five prints on the real SELPHY CP1300 with `FAST_RETURN=0`. Fills the
measured values in FSD Appendix A and answers B-Q1…B-Q5.

Uses 5 sheets and 5 ink panels. No jam is provoked (R15); a real jam is logged if
one happens.

**Downscaling test (bench experiment, not yet in the FSD):** by default `run` first
downscales the photo with `prescale-for-selphy.py` to the exact CUPS raster size
(1248 x 1871 px at 300 dpi, borderless, crop-to-fill, Lanczos + light sharpening) and
sends it with `ppi=300`, so CUPS places it 1:1. `run --original` sends the camera JPEG
the way the booth does today (CUPS shrinks it with `fit-to-page`). **Print 1 is the
`--original` comparison print; prints 2–5 are prescaled.** Compare prints 1 and 2 side
by side (same photo): fine detail (hair, text, edges), moiré, sharpness, white edges.

## Before you start

- Booth laptop on, SELPHY on USB, full paper tray (≥ 5 sheets), ink cassette with
  ≥ 5 prints left. Booth lighting as at an event.
- `cups-poll-test.sh` and `prescale-for-selphy.py` are in `~/` on the laptop (all commands
  below run in `~/`).
- One test photo, e.g. `~/b1.jpg`. Use the same JPG for all five prints.
- Two terminals open on the laptop: **A** (prints) and **B** (printer query, sudo).
- Phone on a stand, filming the printer front and back. At the start of each print, film
  the laptop clock once.

```sh
sudo systemctl disable --now selphy-autoresume   # must not run (re-enables the queue)
cancel -a selphy                                 # queue empty
sudo ./cups-poll-test.sh prepare                 # FAST_RETURN=0 + LogLevel debug
```

`prepare` must print `cups-files.conf: SetEnv FAST_RETURN 0`.

## Keys while a print runs (terminal A)

| Key | When |
|---|---|
| `p` | the sheet comes out at the **back** (once per pass, 4× per print) |
| `e` | the finished photo is **ejected at the front** |
| `f` | you fixed the cause (tray refilled / cassette back in) |
| `b` | you pressed the printer's own print button |
| `t` / `i` | paper tray removed / put back |
| `x` | something odd: type a short note |
| `q` | stop polling |

Press the key the moment you see the event: the timings are taken from your key presses.

## Prints 1, 2, 5 — normal print

```sh
./cups-poll-test.sh run --original ~/b1.jpg b1-p1-original   # print 1: today's path
./cups-poll-test.sh run ~/b1.jpg b1-p2-prescaled             # print 2: same photo, prescaled
./cups-poll-test.sh run ~/b1.jpg b1-p5-prescaled             # print 5
```

1. Press Enter to send. Press `p` on every rear pass and `e` on the eject.
2. Polling stops by itself 60 s after the job is *completed* (only once `e` has been pressed).
3. `sudo ./cups-poll-test.sh collect ~/b1-p1-original-*` (etc.) → it must say
   `OK: the backend got FAST_RETURN=0`.

## Prints 3 and 4 — error, fix, duplicate check

Print 3: put the paper tray in **empty**. Print 4: take the **ink cassette out**
(full paper tray in).

```sh
./cups-poll-test.sh run ~/b1.jpg b1-p3-empty-tray    # p4: b1-p4-no-ink
```

1. **A:** press Enter. Wait for the error (`Paper Feed (02)` or `No Ink (07)`, printer *stopped*).
   Do not press `q`.
2. **B:** `sudo ./cups-poll-test.sh printer-poll ~/b1-p3-empty-tray-*`
3. Fix the cause (refill and insert the tray / put the cassette back), then press `f` in **A**.
4. The printer should print the held photo by itself. Mark `p` / `e` in **A**.
   Only if nothing happens for 1 minute: press the printer's print button, then `b`.
5. Photo out: **B:** Ctrl-C (always **before** any `cupsenable`). **A:** `q`.

**Duplicate check** (B-Q5; answered in P-1 and B-1 print 3, so later runs can skip it and end as
the booth does: Ctrl-C in **B**, `q`, `sudo cancel -a selphy`, `sudo cupsenable selphy` — nothing
prints, as in B-1 print 4):

6. **A:** `./cups-poll-test.sh watch ~/b1-p3-empty-tray-*`
7. Pull the paper tray out, then press `t` in **A**.
8. **B:** `sudo cupsenable selphy` (`cupsenable` is in `/usr/sbin`).
9. Watch **A**: if the job goes *processing* and then `No Paper (03)`, CUPS resent it,
   which means it would have been a **duplicate**. Press `x` and note what you saw.
10. **With the tray still out, switch the printer off and on** (press `x`, note "power cycle").
    The resent job is now inside the printer: `cancel` only removes it from CUPS, and putting
    the tray back in would print the duplicate (happened in B-1 print 3). Then
    **B:** `sudo cancel -a selphy`.
11. Put the tray back in and press `i`. The printer must stay idle; if it prints, press `x`
    and note it. Then `q` in **A**.
12. `sudo ./cups-poll-test.sh collect ~/b1-p3-empty-tray-*`, then `sudo cupsenable selphy`.

## If a real jam happens (any print)

Do not stop **A**. Start the printer query in **B** as in step 2. Press `x` and note
"jam". Follow the printer's display (switch it off and on, clear the sheet), press `f`.
Note whether the printer printed anything afterwards. Then do the duplicate check
(steps 5–12).

## After the session

```sh
sudo ./cups-poll-test.sh restore --keep-fast-return-off   # LogLevel back, FAST_RETURN=0 kept
```

Leave the laptop on and on the network; the run folders `~/b1-*` get copied from there
to fill FSD Appendix A. Also write down sheets used and anything odd.
