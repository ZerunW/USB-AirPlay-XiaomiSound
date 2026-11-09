#!/bin/bash
# Xiaomi Sound Bridge: reboot when the Bluetooth controller wedges.
#
# On the Pi Zero 2 W the combined Wi-Fi/Bluetooth chip's Bluetooth side can
# stall while streaming to two speakers: the kernel logs
#   Bluetooth: hci0: killing stalled connection <addr>
#   Bluetooth: hci0: command 0x.... tx timeout     (every ~2 s from then on)
# and every speaker drops. Re-binding only the Bluetooth driver brings the
# links back but leaves Wi-Fi/Bluetooth coexistence broken (speakers crackle),
# so the only clean recovery found is a full reboot (the chip is powered up
# and initialised as a whole).
#
# Reboots when it sees STALL_EVENTS (default 3) such kernel lines within
# STALL_WINDOW_S (default 30) seconds. To avoid a reboot loop it reboots at
# most MAX_REBOOTS_PER_HOUR (default 3) times per hour; beyond that it only logs.
# SPDX-License-Identifier: GPL-3.0-or-later
set -u

[ -r /etc/xsb/xsb.conf ] && . /etc/xsb/xsb.conf
EVENTS="${STALL_EVENTS:-3}"
WINDOW="${STALL_WINDOW_S:-30}"
MAXR="${MAX_REBOOTS_PER_HOUR:-3}"
STATE=/var/lib/xsb/bt-watchdog-reboots
mkdir -p "$(dirname "$STATE")"

hits=()

reboot_allowed() {
    local now cutoff n=0 t
    now=$(date +%s); cutoff=$((now - 3600))
    if [ -f "$STATE" ]; then
        while read -r t; do [ "${t:-0}" -ge "$cutoff" ] && n=$((n + 1)); done < "$STATE"
    fi
    [ "$n" -lt "$MAXR" ]
}

echo "watching the kernel log for Bluetooth controller stalls ($EVENTS events in ${WINDOW}s)"
journalctl -k -f -n 0 -o cat | while read -r line; do
    case "$line" in
        *"hci0: killing stalled connection"*|*"hci0: command 0x"*" tx timeout"*) ;;
        *) continue ;;
    esac
    now=$(date +%s)
    kept=()
    for t in "${hits[@]}"; do [ $((now - t)) -le "$WINDOW" ] && kept+=("$t"); done
    hits=("${kept[@]}" "$now")
    echo "stall sign ${#hits[@]}/$EVENTS: $line"
    [ "${#hits[@]}" -ge "$EVENTS" ] || continue

    if reboot_allowed; then
        echo "Bluetooth controller wedged: rebooting to re-initialise the chip"
        date +%s >> "$STATE"
        tail -n 20 "$STATE" > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
        sync
        systemctl reboot
        exit 0
    fi
    echo "Bluetooth controller wedged, but already rebooted $MAXR times this hour: not rebooting"
    hits=()
done
