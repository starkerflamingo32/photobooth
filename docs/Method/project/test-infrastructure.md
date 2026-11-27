# Photobooth Booth — Test infrastructure

How the tests behind the FSD's verification contracts are run: where they live, the virtual test setup, the browser setup and the CI workflows. The tiers and what each covers are in the FSD ([§17.0](../../Functionality/fsd.md#170-test-architecture)); which test exists and what it produced is in `testing/test-plan.yaml` ([test plan](#test-plan)). Requirement IDs (FR-…, NFR-…) and R-numbers refer to the [FSD](../../Functionality/fsd.md).

## Where tests live

all test code, scenarios, simulators and test workflows live in this fork (`starkerflamingo32/photobooth`, public), under `testing/` and `.github/workflows/`. `[user]` photobooth-setup (private) stays the deployment repo; the fork's CI checks it out read-only at a pinned commit to build the `cups` and `dummy-dslr` images, so the queue options under test are the deployed ones (NFR-13.1). `[derived]`

## Where tests run

The same test stack (one compose file in `testing/`) runs in two places. `[user]`

| Where | Role |
|---|---|
| **Development VM, local Docker** | The development loop: build, run and debug single tests and scenarios without a push. photobooth-setup is used from its sibling clone (`../photobooth-setup`), so no token is needed. |
| **GitHub Actions** | The gate: every push runs the suites on clean runners ([GitHub Actions plan](#github-actions-plan)). A change is done when CI is green, not when it passed locally. |

- Local and CI use the same compose file and scenario files; nothing is set up differently for CI except where photobooth-setup comes from (sibling clone locally, pinned checkout in CI).
- Local runs must fit the VM's memory: integration runs need the stack only; e2e runs with several browsers need more. If an e2e run fails locally for lack of memory, it is run in CI instead, never skipped.
- Local evidence stays on the VM under `test-results/` (ignored by git); CI evidence is uploaded as workflow artifacts.

## Test plan

`testing/test-plan.yaml` lists every test. It is this project's own format; the Embedded-AI-Harness `/build` and `/harness` skills are not used, since they target an ESP32 workbench. `[user]`

```yaml
tests:
  - id: T-5.2-a                      # T-<chapter>-<letter>, stable, never reused
    verifies: [FR-5.2]               # requirement IDs; coverage is computed from these
    contract: "<stimulus> → <expected observation>"   # the FSD contract row it implements
    tier: unit                       # unit | integration | e2e | bench (FSD §17.0)
    file: testing/unit/tracker_stage_test.php
    scenario: null                   # scenario file for integration/e2e
    status: planned                  # planned | written | passing | failing | not-done
    last_run: {date: null, commit: null, where: null, evidence: null}
```

- One entry per contract row in the FSD; a contract row with no entry is a gap. `testing/tools/test_plan.py sync` adds an entry for every new contract row (§5.4 contracts once per tier, §17.5 scenarios as `T-17.5-*`) and never renames existing ones.
- `status` and `last_run` are updated from real runs only; a test is never marked `passing` without a run that produced evidence.
- Coverage (requirements with no test, with failing tests, with tests not yet run) is generated from this file, never written by hand.

## Virtual test setup

The FSD (§17.1, Phase 1) requires it before booth logic is built.

| Part | What it does |
|---|---|
| Docker stack | `testing/compose.yaml`: photobooth-setup's `cups`, `dummy-dslr` and `web-ftp` images (built from `SETUP_DIR`), Photobooth built from the source under test with photobooth-setup's glue (fake `gphoto2`, `client.conf`, `apply-config`), the simulator below, and a Playwright runner. The fake DSLR lets the booth take photos without a camera `[code]`; `web-ftp` is included because the booth shows an error instead of the result screen when its upload fails. |
| Simulated SELPHY | `testing/stack/cups-sim/selphy_sim.py`. The deployed CUPS image runs unchanged (its entrypoint creates the `selphy` queue with the deployed options, NFR-13.1); the queue's device is then switched to `sim-selphy://`. A daemon holds a physical printer model (tray, ink cassette, held job, power, USB); the backend `sim-selphy` writes the stderr lines and exit codes of Gutenprint 5.3.4's `canonselphyneo` as recorded in P-1/B-1 (`Printer state: Feeding Paper`, `Printing YELLOW/MAGENTA/CYAN/LAMINATE`, `Print complete (0 copies remaining)`, `Printer error: <text> (<code>)` with `CUPS_BACKEND_STOP`, `CUPS_BACKEND_CANCEL` for `0x0A`). Fast return follows the `FAST_RETURN` value CUPS gives the backend, as the real backend does. Held jobs, self-printing after a fix, loss on a power cycle and duplicates after `cupsenable` come out of the model, not out of scripts. Every job the printer receives and every photo that comes out is logged, so a duplicate is visible. Real CUPS stays in the loop, so stopped queues, pending jobs and `cupsenable` behave as on the laptop. |
| Simulated printer query | The command line of the booth host, `BACKEND=canonselphyneo /usr/lib/cups/backend/gutenprint53+usb -s`, exists in the Photobooth container and asks the simulator over the stack network. It answers from the printer model with the lines the real query printed (held photo printed after a fix, *Idle* without printing after a power cycle, ~17 s hangs with `libusb error -7` at layer changes, a wrong layer name after a hang, or scripted answers). A query while the queue is enabled or a backend is attached is recorded as a violation, and any violation fails the test (FR-9.11). |
| Control API | HTTP on `cups:8631`, for tests only: load a scenario, physical actions (`fix`, `refill_paper`, `remove_tray`, `insert_empty_tray`, `remove_ink`, `print_button`, `power_cycle`, `unplug`, `plug`), the logs (`/state`), CUPS helpers (`lp`, `cupsenable`, `cancel`, status, `error_log`) and `SetEnv FAST_RETURN` in `cups-files.conf` (for NFR-13.2/13.4). Each test starts from a reset: deployed `cups-files.conf`, no jobs, queue enabled, default printer. |
| Scenario files | `testing/scenarios/*.yaml`: the printer side of a test (initial physical state, per-job faults, query behaviour, timing overrides, timed actions). The browser steps are the Playwright spec that loads the scenario, so the same spec and scenario run against upstream refs and against `booth`. |
| Simulated clock | `SIM_TIME_SCALE` (default 0.1) real seconds per simulated second: a print takes ~5.6 s instead of 56 s. The booth's Appendix A limits are scaled by the same value, which the stack passes to the Photobooth container. |

### Scenario files

```yaml
id: ink-sheet-0c
description: what it reproduces and its source (P-1/B-1 job, R-number)
clock: {scale: 0.1}                     # optional, overrides SIM_TIME_SCALE
printer:                                # physical state at the start
  tray: ok                              # ok | empty | missing
  ink: ok                               # ok | missing | empty | wrong
  paper_sheets: 18                      # optional counts; 0 → Paper Feed (02) / Ink Cassette Empty (06)
  ink_prints: 56
  wrong_media: false                    # → Incorrect media for job (0a)
  self_print: true                      # false: printer waits for its print button after a fix
jobs:                                   # per job the printer receives, in order (resends count)
  - fault: {code: "0c", layer: MAGENTA, after: 2}   # or error: "02" while feeding
query:
  hang_on_layer_change: true
  hang_s: 17
  wrong_layer_after_hang: true
  answers: [idle, "printing:MAGENTA", {hang: 17}, "error:Paper Feed"]  # scripted, consumed first
timing: {layers: [10, 21, 33, 44]}      # overrides of the B-1 timeline (simulated s)
events: [{at: 30, action: fix}]         # timed physical actions (simulated s after loading)
```

### Running

```sh
testing/run.sh test integration            # or e2e, characterisation; extra args go to playwright test
testing/run.sh test e2e -g T-17.1-k        # one test
BOOTH_REF=49b09a94 testing/run.sh test characterisation   # Photobooth built from another ref
testing/run.sh up | collect | down
```

The runner writes the Playwright report, per-role videos, traces and screenshots, and per test the simulator state and CUPS `error_log`, to `test-results/`; `collect` adds the stack logs and the booth's `data/` print files. `testing/tools/test_plan.py record test-results/results.json <where>` sets `status`/`last_run`; `test_plan.py coverage` prints the coverage. The setup's own checks (positive controls of the simulator, browser roles) are `T-17.1-*` and run in every integration and e2e run.

### Browser test setup (e2e)

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
- Roles are fixtures in `testing/e2e/lib/fixtures.ts`: `booth` (1920×1200, touch), `admin` (1440×900), `operator`/`operator2` (360×800, touch), `stale` (booth viewport).
- Test data is synthetic only (fake-DSLR images, test passwords); no event photos or real credentials, since artifacts of the public fork can be downloaded by others. `[derived]`

## GitHub Actions plan

All workflows run in the fork. `[user]`

| Workflow | Trigger | Does | Time |
|---|---|---|---|
| `ci.yml` | every push / PR to `booth` | upstream build and checks (cgl, lint, PHPStan, PHPUnit, ESLint), test plan coverage in the run summary, shellcheck of `testing/` scripts | ~3 min |
| `e2e.yml` | every push to `booth`, by hand | build the virtual stack (above), run integration + e2e suites; upload Playwright report, videos, traces and logs as artifacts; failed tests listed in the run summary | ~15 min (assumed) |
| `upstream-characterisation.yml` | by hand, input: refs (default upstream `dev` 49b09a94, `76d76e36^`, and `booth`) | run the FSD §17.5 suite against each ref; publish the L-Q answers as the run summary and an artifact | ~15 min (assumed) |
| `upstream-drift.yml` | weekly | merge upstream `dev` into a scratch branch, run `ci.yml` + `e2e.yml`; open an issue if anything breaks | |
| `package.yml` (existing, adapted) | tag | prebuilt release package so the laptop needs no npm/gulp build | |
| shellcheck | in photobooth-setup, on push | `native/*.sh` | |

- Upstream workflows not needed by the fork (crowdin ×3, `publish_docs.yml`, `build-faq.yml`, `release.yml`, `installer.yaml`) are disabled.
- photobooth-setup is checked out at the commit in `testing/setup-ref`; bumping that file is how the fork picks up a deployment change.
- Access to the private photobooth-setup repo: a fine-grained token, read-only on that one repo, stored as the Actions secret `SETUP_REPO_TOKEN`; `e2e.yml` fails with that message when it is missing. Secrets are not given to pull requests from other forks, so those runs skip `e2e.yml` and run `ci.yml` only. (R13)
- **Not possible in Actions:** USB hardware — the real SELPHY (bench, acceptance); deploying to the laptop. No self-hosted runner on the booth laptop: on a public repo it would run code from strangers' pull requests. `[derived]`
- Public repo: standard GitHub-hosted runners cost nothing. `[code]` (repository visibility)
