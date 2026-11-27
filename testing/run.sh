#!/bin/bash
# Virtual test setup driver (FSD §17.1). Same commands locally and in CI.
#
#   testing/run.sh up                         build and start the stack
#   testing/run.sh test <project> [args…]     run a Playwright project against it
#                                             (integration | e2e | characterisation),
#                                             extra args go to `playwright test`
#   testing/run.sh collect                    copy stack evidence to test-results/stack
#   testing/run.sh down                       stop the stack and delete its volumes
#
# Environment: SETUP_DIR (photobooth-setup checkout, default ../photobooth-setup
# next to this fork), SIM_TIME_SCALE (default 0.1).
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
root=$(dirname "$here")
export SETUP_DIR=${SETUP_DIR:-$(cd "$root/.." && pwd)/photobooth-setup}
[ -d "$SETUP_DIR/cups" ] || { echo "photobooth-setup not found at $SETUP_DIR (set SETUP_DIR)" >&2; exit 2; }
export TESTING_DIR=$here
# BOOTH_REF: build Photobooth from this commit/ref instead of the working tree
# (e.g. upstream refs for §17.5). Cloned into test-results/src (ignored).
if [ -n "${BOOTH_REF:-}" ]; then
    src=$root/test-results/src-$(echo "$BOOTH_REF" | tr -c 'A-Za-z0-9._\n-' '_')
    if [ ! -d "$src/.git" ]; then
        git clone -q --no-checkout "$root" "$src"
    fi
    git -C "$src" checkout -q --detach "$BOOTH_REF"
    export BOOTH_SRC=$src
else
    export BOOTH_SRC=$root
fi
RUN_UID=$(id -u)
RUN_GID=$(id -g)
export RUN_UID RUN_GID
compose() { docker compose -f "$here/compose.yaml" "$@"; }

up() {
    compose --profile base build cups-base
    compose build cups dummy-dslr web-ftp photobooth
    compose up -d --wait cups dummy-dslr web-ftp photobooth
    echo "stack up: booth http://localhost:${TEST_PHOTOBOOTH_PORT:-18080}"
}

collect() {
    local out=$root/test-results/stack
    rm -rf "$out" && mkdir -p "$out"
    compose logs --no-color > "$out/compose.log" 2>&1 || true
    compose cp cups:/var/log/cups/error_log "$out/cups-error_log.txt" 2>/dev/null || true
    # The booth's print files (printed.csv, print.count, print.lock) and log.
    for f in printed.csv print.count print.lock; do
        compose cp "photobooth:/app/data/$f" "$out/$f" 2>/dev/null || echo "(absent)" > "$out/$f.absent"
    done
    compose cp photobooth:/app/var/log "$out/photobooth-log" 2>/dev/null || true
    compose exec -T photobooth cat /app/config/my.config.inc.php > "$out/my.config.inc.php" 2>/dev/null || true
    # Test data only: no secrets beyond the fixed test passwords in compose.yaml.
    echo "evidence in $out"
}

case ${1:-} in
    up) up ;;
    test)
        project=${2:?usage: run.sh test <integration|e2e|characterisation> [playwright args]}
        shift 2
        up
        compose --profile test build runner
        mkdir -p "$root/test-results"
        status=0
        compose --profile test run --rm runner --project "$project" "$@" || status=$?
        collect
        exit $status
        ;;
    collect) collect ;;
    down) compose --profile base --profile test down -v ;;
    *) sed -n '2,13p' "$0"; exit 2 ;;
esac
