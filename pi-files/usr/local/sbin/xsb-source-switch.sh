#!/bin/bash
# Xiaomi Sound Bridge: AirPlay-has-priority source switch, called by
# shairport-sync session hooks via a narrowly scoped sudoers rule.
#   airplay: stop USB forwarding (frees the BlueALSA PCM) and make sure the
#            speaker(s) are connected (one attempt, no retry loop).
#   usb:     resume USB forwarding.
# SPDX-License-Identifier: GPL-3.0-or-later
set -u
[ -r /etc/xsb/xsb.conf ] && . /etc/xsb/xsb.conf

case "${1:-}" in
airplay)
    # Remember when this AirPlay session began: xsb-volume-sync ignores the
    # sender's volume for the first seconds (it is not a user change).
    mkdir -p /run/xsb && date +%s.%N > /run/xsb/airplay-session-start
    chmod 644 /run/xsb/airplay-session-start
    systemctl stop xsb-usb-forward.service
    for MAC in ${LEFT_MAC:-} ${RIGHT_MAC:-}; do
        [[ $MAC =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]] || continue
        bluetoothctl info "$MAC" 2>/dev/null | grep -q "Connected: yes" || \
            timeout 15 bluetoothctl connect "$MAC" > /dev/null 2>&1 || true
    done
    ;;
usb)
    # shairport's ExecStopPost may run during AP2 mode transition. Do not
    # asynchronously resurrect the legacy forwarder against its AP2 replacement.
    if [ ! -e /run/xsb-ap2-mode-active ]; then
        systemctl --no-block start xsb-usb-forward.service
    fi
    ;;
*)
    echo "usage: $0 airplay|usb" >&2
    exit 2
    ;;
esac
exit 0
