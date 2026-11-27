# Photobooth Booth — User Manual

How to set up and run the booth at an event. Human procedures, present-state. The FSD
([`../Functionality/fsd.md`](../Functionality/fsd.md)) says what the booth does; this manual says how you make it do
it.

**Status: the booth print stack is not deployable yet.** The print screen, operator web UI and supply counters
described in the FSD are not built (FSD Phase 1 not started). Events run on upstream Photobooth with plain CUPS
printing, installed through `photobooth-setup`. Chapters marked *(not built yet)* describe procedures that start to
apply once the print stack is deployed.

## Contents

1. What you need
2. Installation
3. Booth Wi-Fi
4. At an event
5. Printer errors
6. Recovery

## 1. What you need

- Event laptop (Acer Switch 3) with the booth installed natively
- Canon EOS 80D on USB
- Canon SELPHY CP1300 on USB, with postcard-size paper tray and ink cassette, plus spare paper and cassettes
- The booth router, providing the booth Wi-Fi
- A staff phone with a current browser (no app needed) and the admin login

## 2. Installation

Installation is done with the private `photobooth-setup` repository:

- Development and testing in a VM: its `README.md` (Docker test stack).
- The event laptop: its `native-setup-plan.md` and `native/setup-printer.sh`.

Printer check after any installation or CUPS update: `FAST_RETURN 0` must be set in `/etc/cups/cups-files.conf`.
A `SetEnv` line in `cupsd.conf` is ignored by CUPS 2.4.

```sh
grep FAST_RETURN /etc/cups/cups-files.conf    # expect: SetEnv FAST_RETURN 0
```

`selphy-autoresume` stays disabled; it re-enables the printer queue on its own and causes duplicate prints.

## 3. Booth Wi-Fi

- The booth Wi-Fi uses WPA2 or WPA3.
- Only staff know the Wi-Fi password. Guests are never given it, and it is never shown on a booth screen, in a QR
  code, in the gallery or on a label.
- Only the booth laptop and the operator phone join the booth Wi-Fi.
- The label inside the booth carries the address of the login page, not the Wi-Fi password.

## 4. At an event

*(not built yet: steps 3–6 apply once the print stack is deployed)*

1. Set up the printer: paper tray and ink cassette in, printer on, USB connected.
2. Switch on the booth router.
3. On the phone, join the booth Wi-Fi, open the address on the label, log in with the admin login and open
   **Drucker** in the menu. One login lasts the whole event.
4. Enter the paper and ink counts after loading the printer (defaults offered: 18 sheets, 56 prints).
5. Guests print. The phone shows each print as it runs.
6. When a printer error appears, the phone shows the error, what to do about it, and what the printer reported.
   Fix the printer at the back. After a paper or ink fix the printer usually prints the held photo by itself;
   after a power cycle it does not. Then confirm on the phone: **Foto ist da** or **Foto fehlt**. The booth
   continues by itself.

Without the phone or Wi-Fi, the same printer page opens on the booth laptop: admin login → menu → **Drucker**.

## 5. Printer errors

*(not built yet)* For every printer error the print screen and the phone show a staff hint saying what to do. The
hints are listed in FSD Appendix B.

## 6. Recovery

*(not built yet)* Written when the print stack is deployed.
