#!/bin/bash
# Xiaomi Sound Bridge: keep the speakers (LEFT_MAC and RIGHT_MAC) connected.
# Checks bluetoothd's cached state every 10 s (no radio use). When a speaker
# is disconnected it pages it, backing off 10 s -> 30 s -> 60 s (cap) per
# speaker, because each page to an absent device monopolizes the Pi Zero 2 W's
# shared 2.4 GHz radio for ~5 s and frequent retries can knock Wi-Fi off
# entirely (lesson from pods-head-tracker v0.2).
# SPDX-License-Identifier: GPL-3.0-or-later
set -u
is_mac() { [[ $1 =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]]; }
MACS=()
for m in ${LEFT_MAC:-} ${RIGHT_MAC:-}; do is_mac "$m" && MACS+=("$m"); done
if [ ${#MACS[@]} -eq 0 ]; then
    echo "no speaker MAC configured in /etc/xsb/xsb.conf - idle until it is set"
    exec sleep infinity
fi

BACKOFF=(10 30 60)
declare -A step state next
for m in "${MACS[@]}"; do step[$m]=0; state[$m]=unknown; next[$m]=0; done

while :; do
    now=$(date +%s)
    for m in "${MACS[@]}"; do
        if bluetoothctl info "$m" 2>/dev/null | grep -q "Connected: yes"; then
            [ "${state[$m]}" = yes ] || echo "$m connected"
            state[$m]=yes; step[$m]=0; next[$m]=0
            continue
        fi
        [ "${state[$m]}" = no ] || echo "$m not connected"
        state[$m]=no
        [ "$now" -ge "${next[$m]}" ] || continue
        if timeout 20 bluetoothctl connect "$m" > /dev/null 2>&1; then
            echo "$m reconnect succeeded"; step[$m]=0
        else
            d=${BACKOFF[${step[$m]}]}
            echo "$m reconnect failed - next attempt in ${d}s"
            next[$m]=$(( $(date +%s) + d ))
            [ "${step[$m]}" -lt $((${#BACKOFF[@]} - 1)) ] && step[$m]=$(( step[$m] + 1 ))
        fi
    done
    sleep 10
done
