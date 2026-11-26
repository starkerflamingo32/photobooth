#!/bin/bash
# Bench test: one SELPHY print while polling CUPS status every second.
# FSD §17.3 (B-1 precondition logger), answers B-Q1 for one print and
# exercises §9 (FR-9.2, FR-9.3, FR-9.4, T_cups_poll).
#
# Run on the booth laptop:
#   sudo ./cups-poll-test.sh prepare      # FAST_RETURN=0 + LogLevel debug, restart CUPS
#   ./cups-poll-test.sh run <image.jpg>   # poll, send one print, mark events by key
#   sudo ./cups-poll-test.sh collect <dir> # copy the CUPS error_log for the run (redacted)
#   sudo ./cups-poll-test.sh restore [--keep-fast-return-off]
set -eu

QUEUE=selphy
CONF=/etc/cups/cupsd.conf
BACKUP=/etc/cups/cupsd.conf.before-poll-test
FILES_CONF=/etc/cups/cups-files.conf
FILES_BACKUP=/etc/cups/cups-files.conf.before-poll-test
LP_OPTS=(-o landscape -o fit-to-page)   # same as Photobooth's print command
POLL=1           # seconds between polls (FSD: every second)
TAIL_AFTER=60    # keep polling this long after the job leaves processing

now() { date +%H:%M:%S.%3N; }
need_root() { [ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo $0 $*" >&2; exit 1; }; }

prepare() {
    need_root prepare
    if lpstat -W not-completed -o "$QUEUE" | grep -q .; then
        echo "Queue $QUEUE has unfinished jobs; clear them first (lpstat -o)." >&2
        exit 1
    fi
    # Back up both files once; a second prepare must not overwrite the originals.
    [ -f "$BACKUP" ] || cp -p "$CONF" "$BACKUP"
    [ -f "$FILES_BACKUP" ] || cp -p "$FILES_CONF" "$FILES_BACKUP"
    # LogLevel lives in cupsd.conf. SetEnv must go in cups-files.conf: CUPS 2.4
    # ignores it in cupsd.conf ("Please move ... to cups-files.conf").
    sed -i -e '/^SetEnv FAST_RETURN /d' -e 's/^LogLevel .*/LogLevel debug/' "$CONF"
    grep -q '^LogLevel ' "$CONF" || echo 'LogLevel debug' >> "$CONF"
    sed -i '/^SetEnv FAST_RETURN /d' "$FILES_CONF"
    echo 'SetEnv FAST_RETURN 0' >> "$FILES_CONF"
    local since; since=$(date '+%Y-%m-%d %H:%M:%S')
    systemctl restart cups
    sleep 2
    echo "cupsd.conf:       $(grep -E '^(LogLevel|SetEnv)' "$CONF" | tr '\n' ' ')"
    echo "cups-files.conf:  $(grep -E '^SetEnv' "$FILES_CONF" | tr '\n' ' ')"
    if grep -E 'Please move|Unknown directive' /var/log/cups/error_log | tail -n 3 | grep -q .; then
        echo "CUPS complained about the config (check the time; older lines may show):"
        grep -E 'Please move|Unknown directive' /var/log/cups/error_log | tail -n 3
    fi
    echo "CUPS restarted at $since. FAST_RETURN reaching the backend is checked by 'collect'."
    lpstat -p "$QUEUE"
}

restore() {
    need_root restore
    [ -f "$BACKUP" ] || [ -f "$FILES_BACKUP" ] || { echo "No backups; nothing to restore." >&2; exit 1; }
    [ -f "$BACKUP" ] && cp -p "$BACKUP" "$CONF" && rm -f "$BACKUP"
    [ -f "$FILES_BACKUP" ] && cp -p "$FILES_BACKUP" "$FILES_CONF" && rm -f "$FILES_BACKUP"
    if [ "${1:-}" = "--keep-fast-return-off" ]; then
        echo 'SetEnv FAST_RETURN 0' >> "$FILES_CONF"
        echo "Kept SetEnv FAST_RETURN 0 in cups-files.conf; LogLevel restored."
    fi
    systemctl restart cups
    echo "cupsd.conf:       $(grep -E '^(LogLevel|SetEnv)' "$CONF" | tr '\n' ' ')"
    echo "cups-files.conf:  $(grep -E '^SetEnv' "$FILES_CONF" | tr '\n' ' ')"
    lpstat -p "$QUEUE"
}

# One poll: the two lpstat commands from the FSD plus an IPP query that keeps
# completed / cancelled / aborted apart (FR-9.3) and gives the printer message.
poll_once() {
    local t; t=$(now)
    {
        echo "=== $t"
        lpstat -l -p "$QUEUE" 2>&1 | sed -n '1,3p'
        lpstat -W all -o "$QUEUE" 2>&1 | tail -n 2
    } >> "$OUT/lpstat.log"
    {
        echo "=== $t"
        ipptool -tv "ipp://localhost/printers/$QUEUE" "$OUT/poll.test" 2>&1 \
            | grep -E '^\s+(printer-state|printer-state-reasons|printer-state-message|printer-is-accepting-jobs|marker-levels|marker-message|job-id|job-state|job-state-reasons|job-printer-state-message|time-at-processing|time-at-completed) ' \
            | sed 's/^ *//'
    } >> "$OUT/ipp.log"
}

# Compact one-line summary per poll, also printed live.
summary_line() {
    local t=$1 pstate jstate=""
    pstate=$(lpstat -p "$QUEUE" 2>&1 | head -n 1 | sed "s/^printer $QUEUE //")
    [ "$JOB" -gt 0 ] && jstate=$(ipptool -tv "ipp://localhost/printers/$QUEUE" "$OUT/job.test" 2>/dev/null \
        | awk '/ job-state \(enum\)/{s=$NF} / job-state-reasons /{r=$NF} END{print s" "r}')
    echo "$t | printer: $pstate | job $JOB: $jstate"
}

write_ipp_tests() {
    cat > "$OUT/poll.test" <<'EOF'
{
    OPERATION Get-Printer-Attributes
    GROUP operation-attributes-tag
    ATTR charset attributes-charset utf-8
    ATTR naturalLanguage attributes-natural-language en
    ATTR uri printer-uri $uri
    ATTR keyword requested-attributes printer-state,printer-state-reasons,printer-state-message,printer-is-accepting-jobs,marker-levels,marker-message
}
{
    OPERATION Get-Jobs
    GROUP operation-attributes-tag
    ATTR charset attributes-charset utf-8
    ATTR naturalLanguage attributes-natural-language en
    ATTR uri printer-uri $uri
    ATTR keyword which-jobs not-completed
    ATTR keyword requested-attributes job-id,job-state,job-state-reasons,job-printer-state-message,time-at-processing,time-at-completed
}
EOF
    [ "$JOB" -gt 0 ] || return 0
    # Our own job by id: Get-Jobs "all" returns the oldest jobs first.
    cat > "$OUT/job.test" <<EOF
{
    OPERATION Get-Job-Attributes
    GROUP operation-attributes-tag
    ATTR charset attributes-charset utf-8
    ATTR naturalLanguage attributes-natural-language en
    ATTR uri printer-uri \$uri
    ATTR integer job-id $JOB
    ATTR keyword requested-attributes job-id,job-state,job-state-reasons,job-printer-state-message,time-at-processing,time-at-completed
}
EOF
    cat "$OUT/job.test" >> "$OUT/poll.test"
}

run() {
    local img=${1:?usage: $0 run <image.jpg>}
    [ -r "$img" ] || { echo "Cannot read $img" >&2; exit 1; }
    if lpstat -W not-completed -o "$QUEUE" | grep -q .; then
        echo "Queue $QUEUE has unfinished jobs; clear them first." >&2; exit 1
    fi
    OUT=~/poll-test-$(date +%Y%m%d-%H%M%S)
    mkdir -p "$OUT"
    JOB=0
    write_ipp_tests
    exec > >(tee -a "$OUT/console.log") 2>&1

    echo "Output: $OUT"
    echo "Image: $img"
    # cupsd.conf is root-only; the prepare backup's existence is readable by anyone.
    if [ -e "$BACKUP" ]; then
        echo "prepare has run (backup $BACKUP exists): FAST_RETURN=0, LogLevel debug"
    else
        echo "WARNING: no $BACKUP - prepare has not run, fast return is probably ON"
    fi
    echo "Start the phone video of printer front and back now; show this screen's clock in it once."
    echo
    echo "Keys while printing:  p = paper out the BACK (a pass)   e = photo EJECTED at the front"
    echo "  error prints:       f = cause fixed   b = printer's print button pressed   t = paper tray removed"
    echo "                      x = something odd (type a note)   q = stop"
    read -r -p "Press Enter to send the print... " _

    poll_once
    echo "$(now) SEND lp -d $QUEUE ${LP_OPTS[*]} $img" | tee -a "$OUT/events.log"
    local resp; resp=$(lp -d "$QUEUE" "${LP_OPTS[@]}" "$img")
    echo "$(now) LP $resp" | tee -a "$OUT/events.log"
    JOB=$(echo "$resp" | grep -o "$QUEUE-[0-9]*" | sed 's/.*-//')
    write_ipp_tests

    local passes=0 done_at=0 last_state="" key line state
    while :; do
        local t; t=$(now)
        poll_once
        line=$(summary_line "$t")
        echo "$line" >> "$OUT/summary.log"
        state=${line##*job $JOB: }
        if [ "$state" != "$last_state" ]; then
            echo "$t STATE $state" | tee -a "$OUT/events.log"
            last_state=$state
        fi
        echo "$line"
        case $state in
            completed*|canceled*|aborted*) [ "$done_at" -gt 0 ] || done_at=$SECONDS ;;
        esac
        if [ "$done_at" -gt 0 ] && [ $((SECONDS - done_at)) -ge "$TAIL_AFTER" ] && grep -q ' EJECT' "$OUT/events.log"; then
            echo "Job finished, eject marked and ${TAIL_AFTER}s tail done."
            break
        fi
        if read -r -s -n 1 -t "$POLL" key; then
            case $key in
                p) passes=$((passes + 1)); echo "$(now) PASS $passes (back)" | tee -a "$OUT/events.log" ;;
                e) echo "$(now) EJECT (front)" | tee -a "$OUT/events.log" ;;
                f) echo "$(now) FIXED (cause removed: paper refilled / cassette back / jam cleared)" | tee -a "$OUT/events.log" ;;
                b) echo "$(now) BUTTON (printer's own print button pressed)" | tee -a "$OUT/events.log" ;;
                t) echo "$(now) TRAY OUT (paper tray removed for the duplicate check)" | tee -a "$OUT/events.log" ;;
                x) read -r -p "note (polling pauses while typing): " note; echo "$(now) NOTE $note" | tee -a "$OUT/events.log" ;;
                q) echo "$(now) STOP by user" | tee -a "$OUT/events.log"; break ;;
            esac
        fi
    done

    echo
    echo "== Events"; cat "$OUT/events.log"
    echo
    echo "Next: sudo $0 collect $OUT"
}

collect() {
    need_root collect
    local dir=${1:?usage: sudo $0 collect <run dir>}
    local start; start=$(head -n 1 "$dir/events.log" | cut -c1-8)
    # error_log lines carry [26/Nov/2026:17:12:01 +0100]; take everything since the send.
    awk -v s="$start" 'match($0, /:[0-9][0-9]:[0-9][0-9]:[0-9][0-9] /) { t = substr($0, RSTART+1, 8) } t >= s' \
        /var/log/cups/error_log > "$dir/cups-error_log.txt"
    # Redact the printer's USB serial (FSD: logs must be redacted).
    sed -i -E 's#(gutenprint53\+usb://canon-cp1300/)[A-Za-z0-9]+#\1SERIAL#g; s/(serial|Serial|SERIAL)([ :=]+)[A-Za-z0-9]{8,}/\1\2REDACTED/g' "$dir/cups-error_log.txt"
    chown -R "${SUDO_USER:-root}:" "$dir"
    echo "Wrote $dir/cups-error_log.txt ($(wc -l < "$dir/cups-error_log.txt") lines)"
    if grep -q 'envp\[[0-9]*\]="FAST_RETURN=0"' "$dir/cups-error_log.txt"; then
        echo "OK: the backend got FAST_RETURN=0."
    else
        echo "WARNING: FAST_RETURN=0 is not in the backend's environment; this print ran with fast return ON."
    fi
}

case ${1:-} in
    prepare) prepare ;;
    run) shift; run "$@" ;;
    collect) shift; collect "$@" ;;
    restore) shift; restore "$@" ;;
    *) sed -n '2,12p' "$0"; exit 1 ;;
esac
