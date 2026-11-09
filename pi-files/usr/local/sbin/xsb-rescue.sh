#!/bin/bash
# Xiaomi Sound Bridge: Wi-Fi watchdog and USB network rescue.
#
# 1. Wi-Fi retry. On some routers (WPA2/WPA3 transition mode) the Zero 2 W's
#    association or 4-way handshake fails now and then. NetworkManager then
#    asks for a new key, finds no agent on a headless box and gives up for
#    good ("no secrets"), so Wi-Fi stays down until someone intervenes. A plain
#    "nmcli connection up" succeeds. While wlan0 is down this re-activates the
#    Wi-Fi connection: first after RETRY_AFTER_S (default 60), then backing
#    off 60 s -> 120 s -> 300 s, pausing the Bluetooth streams meanwhile
#    (they starve Wi-Fi authentication on the shared radio). It changes no
#    Wi-Fi settings.
# 2. USB rescue. If wlan0 has been down for RESCUE_AFTER_S (default 180), the
#    USB gadget is rebuilt with a CDC-NCM network function next to the sound
#    card. The host then gets a USB network link; both ends pick link-local
#    addresses and Avahi answers "<hostname>.local" on it, so
#    "ssh <user>@<hostname>.local" works over the cable without Wi-Fi.
#    Rescue mode stays on until the next reboot, so it never flaps.
# SPDX-License-Identifier: GPL-3.0-or-later
set -u

[ -r /etc/xsb/xsb.conf ] && . /etc/xsb/xsb.conf
RETRY_AFTER="${RETRY_AFTER_S:-60}"
GRACE="${RESCUE_AFTER_S:-180}"
BACKOFF=(60 120 300)
STEP=10
down=0
rescued=0
step=0
next_retry="$RETRY_AFTER"

wifi_connected() {
    nmcli -t -f DEVICE,STATE device 2>/dev/null | grep -qx 'wlan0:connected'
}

wifi_conn() {
    nmcli -t -f NAME,TYPE connection show 2>/dev/null \
        | awk -F: '$2 == "802-11-wireless" { print $1; exit }'
}

while :; do
    if wifi_connected; then
        [ "$down" -gt 0 ] && echo "wlan0 connected again after ${down}s"
        down=0; step=0; next_retry="$RETRY_AFTER"
        sleep "$STEP"
        continue
    fi
    down=$((down + STEP))

    if [ "$rescued" = 0 ] && [ "$down" -ge "$GRACE" ]; then
        echo "wlan0 down for ${down}s: switching USB gadget to audio + network rescue"
        XSB_RESCUE=1 /usr/local/sbin/xsb-gadget.sh restart
        echo "rescue gadget up (exit $?); ssh over USB to $(hostname).local"
        rescued=1
    fi

    if [ "$down" -ge "$next_retry" ]; then
        c=$(wifi_conn)
        if [ -n "$c" ]; then
            # Running A2DP streams starve Wi-Fi authentication on the shared
            # radio: pause them for the attempt (a few seconds of silence).
            streams=$(systemctl list-units --plain --no-legend --state=active \
                      'xsb-bt-stream@*' 2>/dev/null | awk '{ print $1 }')
            [ -n "$streams" ] && systemctl stop $streams
            echo "wlan0 down for ${down}s: re-activating '$c' (Bluetooth streams paused)"
            if timeout 60 nmcli connection up "$c" > /dev/null 2>&1; then
                echo "Wi-Fi re-activation succeeded"
            else
                echo "Wi-Fi re-activation failed"
            fi
            [ -n "$streams" ] && systemctl start $streams
        fi
        next_retry=$((down + BACKOFF[step]))
        [ "$step" -lt $((${#BACKOFF[@]} - 1)) ] && step=$((step + 1))
    fi
    sleep "$STEP"
done
