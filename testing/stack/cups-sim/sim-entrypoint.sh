#!/bin/bash
# Starts the simulator daemon and the deployed CUPS entrypoint unchanged, then
# points the `selphy` queue the entrypoint created at the simulated printer.
set -u

/opt/selphy-sim/selphy_sim.py daemon &
sim_pid=$!

/usr/local/bin/entrypoint.sh &
cups_pid=$!
trap 'kill "$cups_pid" "$sim_pid"' TERM INT

until lpstat -v selphy 2>/dev/null | grep -q 'gutenprint53+usb://'; do
    if ! kill -0 "$cups_pid" 2>/dev/null; then
        echo "deployed CUPS entrypoint exited before creating the selphy queue" >&2
        exit 1
    fi
    sleep 1
done
lpadmin -p selphy -v sim-selphy://canon-cp1300/SIM0001
echo "selphy queue switched to the simulated SELPHY: $(lpstat -v selphy)"
touch /run/sim-ready

wait -n "$cups_pid" "$sim_pid"
echo "cupsd or the simulator exited" >&2
exit 1
