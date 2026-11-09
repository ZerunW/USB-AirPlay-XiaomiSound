#!/bin/bash
# Xiaomi Sound Bridge: forward host-playback audio from the UAC2 gadget's ALSA
# capture card into the ALSA Loopback card, from where xsb-bt-stream sends it
# to the speaker over Bluetooth.
#
# Adapted from pods-usb-audio.sh in pods-head-tracker
# (https://github.com/batubozkan/pods-head-tracker, GPL-3.0). Changes: only
# opens the capture side while the host is actually streaming ("Capture Rate"
# != 0) and logs stream start/stop; writes to the Loopback instead of
# BlueALSA (Bluetooth is handled by xsb-bt-stream).
# SPDX-License-Identifier: GPL-3.0-or-later
#
# One attempt per run, exit on any failure: the unit's Restart=always is the
# retry loop. Stopped by xsb-source-switch.sh while AirPlay is playing.
set -uo pipefail

LATENCY_MS="${AUDIO_LATENCY_MS:-50}"
CARD=UAC2Gadget

capture_rate() {
    # Cheap: one amixer call, parsed in bash (no extra processes).
    local out
    out=$(amixer -c "$CARD" cget iface=PCM,name='Capture Rate' 2>/dev/null) || return
    [[ $out =~ :\ values=([0-9]+) ]] && echo "${BASH_REMATCH[1]}"
}

# USB enumeration is asynchronous: give the sound card a moment to appear.
for _ in $(seq 30); do
    [ -e /proc/asound/$CARD ] && break
    sleep 1
done
[ -e /proc/asound/$CARD ] || { echo "no $CARD sound card - is xsb-gadget.service healthy?" >&2; exit 1; }
[ -e /proc/asound/Loopback ] || { echo "no Loopback card (snd-aloop not loaded)" >&2; sleep 5; exit 1; }

# Wait until the host is streaming. "Capture Rate" reads 0 when the host is
# not playing or the cable is unplugged. Opening the capture PCM while the
# host is idle can starve forever (f_uac2/dwc2 behaviour), so don't.
while :; do
    r=$(capture_rate)
    [ -n "$r" ] && [ "$r" != 0 ] && break
    sleep 1
done
echo "host started streaming at ${r} Hz"

# Stop alsaloop when the host stops streaming, so the Loopback is released
# and the next start reopens the capture path fresh.
(
    zero=0
    # alsaloop makes itself real-time at a high priority; demote it to
    # USB_FORWARD_RTPRIO (default 0 = normal scheduling) so it can never starve
    # the Bluetooth side (xsb-bt-stream 40, bluealsa 50). A value below 40 makes
    # it real-time but still below Bluetooth; FIFO 20 was tried and did not
    # reduce Loopback underruns, so the default stays normal scheduling.
    sleep 1
    prio="${USB_FORWARD_RTPRIO:-0}"
    for p in $(pgrep -f "alsaloop -C hw:CARD=$CARD"); do
        if [ "$prio" -gt 0 ]; then chrt -a -f -p "$prio" "$p"; else chrt -a -o -p 0 "$p"; fi 2>/dev/null
    done
    while sleep 1; do
        r=$(capture_rate)
        if [ -z "$r" ] || [ "$r" = 0 ]; then zero=$((zero + 1)); else zero=0; fi
        if [ "$zero" -ge 3 ]; then
            echo "host stopped streaming"
            pkill -f "alsaloop -C hw:CARD=$CARD" 2>/dev/null
            exit 0
        fi
    done
) &
WATCH=$!
trap 'kill $WATCH 2>/dev/null; pkill -f "alsaloop -C hw:CARD=$CARD" 2>/dev/null' EXIT

# Host USB clock and Pi clock are independent: -S 1 lets alsaloop compensate
# (it drives the gadget's "Capture Pitch" feedback control, so the host
# adjusts its rate instead of the Pi resampling).
# A dense burst of underruns means the capture path is starved: exit and let
# systemd reopen everything.
alsaloop \
    -C hw:CARD=$CARD,DEV=0 \
    -P hw:CARD=Loopback,DEV=0 \
    -r 48000 -f S16_LE -c 2 \
    -t $((LATENCY_MS * 1000)) -S 1 2>&1 | {
    n=0
    win_start=0
    while IFS= read -r line; do
        printf '%s\n' "$line"
        case $line in
        *underrun*)
            now=$(date +%s)
            if [ $((now - win_start)) -gt 30 ]; then n=0; win_start=$now; fi
            n=$((n + 1))
            if [ "$n" -ge 8 ]; then
                echo "sustained underruns - reopening the capture path" >&2
                pkill -f "alsaloop -C hw:CARD=$CARD" 2>/dev/null || true
                exit 1
            fi
            ;;
        esac
    done
}
exit 0
