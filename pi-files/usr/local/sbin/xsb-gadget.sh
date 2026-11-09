#!/bin/bash
# Xiaomi Sound Bridge: build the UAC2 USB audio gadget via configfs, so a host
# computer sees a USB sound card named "Xiaomi Sound Bridge".
#
# Adapted from pods-usb-gadget.sh in pods-head-tracker
# (https://github.com/batubozkan/pods-head-tracker, GPL-3.0): ACM serial
# function removed, names and descriptor knobs changed.
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Usage: xsb-gadget.sh start|stop|restart   (run by xsb-gadget.service at boot)
#
# Rescue mode (XSB_RESCUE=1, used by xsb-rescue.sh when Wi-Fi stays down):
# adds a CDC-NCM network function next to the sound card, so the host gets a
# USB network link and "ssh <user>@<hostname>.local" works over the cable.
# It uses its own product ID so Windows does not reuse the cached driver
# setup of the audio-only device.
set -euo pipefail

G=/sys/kernel/config/usb_gadget/xsb
NAME="Xiaomi Sound Bridge"
[ -r /etc/xsb/xsb.conf ] && . /etc/xsb/xsb.conf
SRATE="${USB_SRATE:-48000}"
SYNC="${USB_SYNC:-async}"
PID="${USB_PRODUCT_ID:-0x0104}"
RESCUE="${XSB_RESCUE:-0}"
RESCUE_PID="${USB_RESCUE_PRODUCT_ID:-0x0105}"
[ "$RESCUE" = 1 ] && PID="$RESCUE_PID"

# Stable locally administered MAC derived from the board serial, so the host
# keeps one network service for the rescue link instead of a new one per boot.
mac_from_serial() {
    local h
    h=$(tr -d '\0' < /proc/device-tree/serial-number 2>/dev/null | md5sum | cut -c1-8)
    printf '02:58:%s:%s:%s:%s' "${h:0:2}" "${h:2:2}" "${h:4:2}" "$1"
}

start() {
    modprobe libcomposite

    # Already built and bound: nothing to do. An unbound UDC file still holds
    # a newline, so test for non-blank content, not file size.
    if [ -d "$G" ] && grep -q '[^[:space:]]' "$G/UDC" 2>/dev/null; then
        exit 0
    fi

    mkdir -p "$G"
    cd "$G"

    # Linux Foundation "Multifunction Composite Gadget" IDs; UAC2 uses the
    # inbox class drivers on macOS, Windows 10/11 and Linux.
    echo 0x1d6b > idVendor
    echo "$PID" > idProduct
    echo 0x0100 > bcdDevice
    echo 0x0200 > bcdUSB

    mkdir -p strings/0x409
    # Stable serial so hosts treat it as the same device across reboots.
    tr -d '\0' < /proc/device-tree/serial-number > strings/0x409/serialnumber \
        2>/dev/null || echo 0123456789 > strings/0x409/serialnumber
    echo "Xiaomi Sound Bridge project" > strings/0x409/manufacturer
    echo "$NAME" > strings/0x409/product

    mkdir -p configs/c.1/strings/0x409
    echo "UAC2 audio" > configs/c.1/strings/0x409/configuration
    echo 250 > configs/c.1/MaxPower   # 2 mA units -> 500 mA

    # UAC2: c_* is the gadget's CAPTURE side = the host's PLAYBACK direction.
    mkdir -p functions/uac2.0
    # Windows names the playback device after this interface string.
    echo "$NAME"  > functions/uac2.0/function_name 2>/dev/null || true
    echo 3        > functions/uac2.0/c_chmask   # stereo
    echo "$SRATE" > functions/uac2.0/c_srate
    echo 2        > functions/uac2.0/c_ssize    # S16_LE
    echo "$SYNC"  > functions/uac2.0/c_sync     # async = feedback endpoint
    echo 0        > functions/uac2.0/p_chmask   # no microphone direction
    # More requests in flight smooths isochronous delivery on dwc2.
    echo 4        > functions/uac2.0/req_number 2>/dev/null || true

    [ -L configs/c.1/uac2.0 ] || ln -s functions/uac2.0 configs/c.1/

    if [ "$RESCUE" = 1 ]; then
        mkdir -p functions/ncm.usb0
        mac_from_serial 01 > functions/ncm.usb0/dev_addr
        mac_from_serial 02 > functions/ncm.usb0/host_addr
        [ -L configs/c.1/ncm.usb0 ] || ln -s functions/ncm.usb0 configs/c.1/
        echo "UAC2 audio + NCM rescue" > configs/c.1/strings/0x409/configuration
    fi

    ls /sys/class/udc | head -n1 > UDC
}

stop() {
    [ -d "$G" ] || exit 0
    echo "" > "$G/UDC" 2>/dev/null || true
    rm -f "$G"/configs/c.1/uac2.0 "$G"/configs/c.1/ncm.usb0 2>/dev/null || true
    rmdir "$G"/configs/c.1/strings/0x409 "$G"/configs/c.1 \
          "$G"/functions/uac2.0 "$G"/functions/ncm.usb0 \
          "$G"/strings/0x409 "$G" 2>/dev/null || true
}

case "${1:-start}" in
    start) start ;;
    stop)  stop ;;
    restart) ( stop ); start ;;
    *) echo "usage: $0 start|stop|restart" >&2; exit 2 ;;
esac
