# Photobooth Booth — Test infrastructure

How the tests behind the FSD's verification contracts are run: where they live, the virtual test setup, the browser setup and the CI workflows. The tiers and what each covers are in the FSD ([§17.0](../../Functionality/fsd.md#170-test-architecture)); which test exists and what it produced is in `testing/test-plan.yaml` (owned by `/build`). Requirement IDs (FR-…, NFR-…) and R-numbers refer to the [FSD](../../Functionality/fsd.md).

## Where tests live

all test code, scenarios, simulators and test workflows live in this fork (`starkerflamingo32/photobooth`, public), under `testing/` and `.github/workflows/`. `[user]` photobooth-setup (private) stays the deployment repo; the fork's CI checks it out read-only at a pinned commit to build the `cups` and `dummy-dslr` images, so the queue options under test are the deployed ones (NFR-13.1). `[derived]`

## Virtual test setup

The FSD (§17.1, Phase 1) requires it before booth logic is built.

| Part | What it does |
|---|---|
| Docker stack | photobooth-setup's `compose.yaml` services (photobooth built from the commit under test, `cups`, `dummy-dslr`), plus the simulator below, from a test compose file in `testing/`. The fake DSLR already lets the booth take photos without a camera `[code]`. |
| Simulated SELPHY | A CUPS backend `sim-selphy://` replacing the USB device of the `selphy` queue; same PPD and queue options as `cups/entrypoint.sh`. It reads a scenario (per job: emit the backend's state messages — `Feeding Paper`, `Printing YELLOW/MAGENTA/CYAN/LAMINATE`, `Print complete` — at scripted times, as recorded in P-1; exit with `CUPS_BACKEND_STOP` and an Appendix B message, leaving the job pending; `CUPS_BACKEND_CANCEL` for the `0x0A` case; fast return on/off) and logs every job it receives, so a duplicate is visible. Real CUPS stays in the loop, so stopped queues, pending jobs and `cupsenable` behave as on the laptop. |
| Simulated printer query | Stands in for `gutenprint53+usb -s`: answers from the same scenario (held photo printed by itself after *t* s; idle without printing after a power cycle; hangs of ~17 s; wrong layer names around a hang) and fails the test if it is called while the queue is enabled (FR-9.7). |
| Scenario files | One YAML file per test: printer behaviour per job, printer query answers, and the browser steps (below). Reused unchanged against upstream and against `booth`. |
| Simulated clock | Print timings scaled down (e.g. 1 s per layer) so a run takes minutes, not hours; Appendix A limits scaled the same way. |

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
- Test data is synthetic only (fake-DSLR images, test passwords); no event photos or real credentials, since artifacts of the public fork can be downloaded by others. `[derived]`

## GitHub Actions plan

All workflows run in the fork. `[user]`

| Workflow | Trigger | Does | Time |
|---|---|---|---|
| `ci.yml` | every push / PR to `booth` | lint (upstream `linters.yaml` rules), PHPUnit, JS unit tests | ~3 min |
| `e2e.yml` | every push to `booth`, by hand | build the virtual stack (above), run integration + e2e suites; upload Playwright report, videos, traces and logs as artifacts; failed tests listed in the run summary | ~15 min (assumed) |
| `upstream-characterisation.yml` | by hand, input: refs (default upstream `dev` 49b09a94, `76d76e36^`, and `booth`) | run the FSD §17.5 suite against each ref; publish the L-Q answers as the run summary and an artifact | ~15 min (assumed) |
| `upstream-drift.yml` | weekly | merge upstream `dev` into a scratch branch, run `ci.yml` + `e2e.yml`; open an issue if anything breaks | |
| `package.yml` (existing, adapted) | tag | prebuilt release package so the laptop needs no npm/gulp build | |
| shellcheck | in photobooth-setup, on push | `native/*.sh` | |

- Upstream workflows not needed by the fork (crowdin ×3, `publish_docs.yml`, `build-faq.yml`, `release.yml`, `installer.yaml`) are disabled.
- Access to the private photobooth-setup repo: a fine-grained token, read-only on that one repo, stored as an Actions secret. Secrets are not given to pull requests from other forks, so those runs skip `e2e.yml` and run `ci.yml` only. (R13)
- **Not possible in Actions:** USB hardware — the real SELPHY (bench, acceptance); deploying to the laptop. No self-hosted runner on the booth laptop: on a public repo it would run code from strangers' pull requests. `[derived]`
- Public repo: standard GitHub-hosted runners cost nothing. `[code]` (repository visibility)
