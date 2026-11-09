#!/bin/bash
# Xiaomi Sound Bridge: one speaker's A2DP stream.
#   xsb-bt-stream.sh left   left channel (mono) to LEFT_MAC
#   xsb-bt-stream.sh right  right channel (mono) to RIGHT_MAC
# Audio sources (USB forwarding, AirPlay) write stereo into the ALSA Loopback
# card; this copies one channel of the Loopback capture side to BlueALSA, but
# only while a source plays. Streaming silence while idle only costs radio
# time, which the Zero 2 W shares with Wi-Fi (idle crackle on the speakers,
# Wi-Fi unable to authenticate). The Bluetooth link itself stays connected,
# so speaker volume changes (AVRCP) keep working while idle.
# SPDX-License-Identifier: GPL-3.0-or-later
set -uo pipefail
SIDE="${1:?usage: $0 left|right}"
case "$SIDE" in
    left)  MAC="${LEFT_MAC:-}";  CAP="xsb_cap_left";  BUF="${LEFT_LATENCY_MS:-${BT_LATENCY_MS:-100}}" ;;
    right) MAC="${RIGHT_MAC:-}"; CAP="xsb_cap_right"; BUF="${RIGHT_LATENCY_MS:-${BT_LATENCY_MS:-100}}" ;;
    *) echo "usage: $0 left|right" >&2; exit 2 ;;
esac
if ! [[ $MAC =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]]; then
    echo "MAC for '$SIDE' not configured in /etc/xsb/xsb.conf" >&2
    sleep 30
    exit 1
fi

[ -e /proc/asound/Loopback ] || { echo "no Loopback card (snd-aloop not loaded)" >&2; sleep 5; exit 1; }

# At boot, let Wi-Fi associate first. The Zero 2 W shares one 2.4 GHz radio
# between Wi-Fi and Bluetooth; with A2DP streams already running, Wi-Fi
# authentication timed out (status 16) again and again, while it connected in
# a second once the streams were stopped. Wait up to WIFI_WAIT_S (default 90)
# in the first 5 minutes after boot, then stream regardless (USB-only setups
# without Wi-Fi must still play).
if [ "$(cut -d. -f1 /proc/uptime)" -lt 300 ]; then
    for ((w = 0; w < ${WIFI_WAIT_S:-90}; w += 2)); do
        nmcli -t -f DEVICE,STATE device 2>/dev/null | grep -qx 'wlan0:connected' && break
        [ "$w" = 0 ] && echo "waiting for Wi-Fi before starting the $SIDE stream"
        sleep 2
    done
fi

# Never page here (paging an absent speaker starves Wi-Fi); xsb-bt-reconnect
# does the backed-off paging. Just wait for the link.
bluetoothctl info "$MAC" 2>/dev/null | grep -q "Connected: yes" || {
    echo "$SIDE speaker not connected yet - waiting for xsb-bt-reconnect" >&2
    sleep 5
    exit 1
}

PCM="/org/bluealsa/hci0/dev_${MAC//:/_}/a2dpsrc/sink"
for _ in $(seq 10); do bluealsa-cli info "$PCM" > /dev/null 2>&1 && break; sleep 1; done
bluealsa-cli info "$PCM" > /dev/null 2>&1 || { echo "no BlueALSA PCM for $SIDE speaker yet" >&2; exit 1; }

# arecord | aplay rather than alsaloop: both ends run on the Pi's clock, so no
# rate adaption is needed. aplay starts once its buffer is full, so the buffer
# time is this speaker's added delay: set LEFT_LATENCY_MS / RIGHT_LATENCY_MS
# to line the two speakers up.
pipeline() {
    arecord -q -D "$CAP" -f S16_LE -c 1 -r 48000 -t raw --buffer-time=100000 \
      | aplay -q -D "xsb_bt:MAC=$MAC" -f S16_LE -c 1 -r 48000 -t raw \
            --buffer-time=$((BUF * 1000))
}

PB=/proc/asound/Loopback/pcm0p/sub0/status   # the side sources write into
playing() { local l; read -r l < "$PB" 2>/dev/null; [ -n "$l" ] && [ "$l" != closed ]; }
IDLE="${BT_IDLE_STOP_S:-15}"

# BT_IDLE_STOP_S=0: stream all the time (silence while idle). The default
# (15 s) stops the stream after a source has been idle, which frees the shared
# radio for Wi-Fi; the stream restarts when a source plays again. Use 0 only if
# a speaker must never leave its stream (for example to rule out start-up
# effects while testing). Costs radio time that Wi-Fi then lacks.
if [ "$IDLE" = 0 ]; then
    echo "Loopback ($SIDE, ${BUF} ms) -> $MAC continuously"
    pipeline
    echo "$SIDE stream ended (speaker gone?)" >&2
    exit 1
fi

# An idle link sits in sniff mode (xsb-bt-sniff), which frees the shared radio
# for Wi-Fi. Waking it lazily with the first audio packets made each speaker
# pick a random buffer at stream start (measured 400-1100 ms, left/right apart
# by up to ~480 ms; ~400 ms and aligned with sniff off). So leave sniff mode
# explicitly (HCI Exit Sniff Mode) and give the link BT_WAKE_S to settle
# before any audio goes out.
wake_link() {
    local h
    h=$(hcitool con 2>/dev/null | awk -v m="${MAC^^}" 'toupper($3) == m { for (i = 1; i < NF; i++) if ($i == "handle") print $(i + 1) }')
    [ -n "$h" ] || return 0
    local r
    r=$(hcitool cmd 0x02 0x0004 "$(printf '%02x' $((h & 255)))" "$(printf '%02x' $((h >> 8)))" 2>&1 | tail -1)
    echo "woke $SIDE link (handle $h): ${r:-no reply}"
    sleep "${BT_WAKE_S:-0.15}"
}

echo "Loopback ($SIDE, ${BUF} ms) -> $MAC only while a source plays (stops after ${IDLE}s idle)"

while :; do
    until playing; do
        sleep 0.2
        # Let systemd re-check the speaker if it went away while idle.
        if (( SECONDS % 10 == 0 )); then
            bluetoothctl info "$MAC" 2>/dev/null | grep -q "Connected: yes" || exit 1
            sleep 1
        fi
    done
    echo "source playing: starting the $SIDE stream"
    wake_link
    pipeline &
    pid=$!
    idle=0
    while kill -0 "$pid" 2>/dev/null; do
        if playing; then idle=0; else idle=$((idle + 1)); fi
        [ "$idle" -ge $((IDLE * 5)) ] && break
        sleep 0.2
    done
    if kill -0 "$pid" 2>/dev/null; then
        echo "no source for ${IDLE}s: stopping the $SIDE stream"
        pkill -P "$pid" 2>/dev/null
        wait "$pid" 2>/dev/null
    else
        wait "$pid" 2>/dev/null
        echo "$SIDE stream ended (speaker gone?)" >&2
        exit 1
    fi
done
