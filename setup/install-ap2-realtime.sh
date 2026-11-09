#!/usr/bin/env bash
# Installs the independent mode without modifying legacy Bluetooth configs,
# and without enabling or starting any service.
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo 'Run with sudo' >&2; exit 1; fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if ! id xsb-ap2 >/dev/null 2>&1; then
  useradd --system --no-create-home --home-dir /nonexistent \
    --shell /usr/sbin/nologin --user-group --groups audio xsb-ap2
fi
install -m 0755 "$ROOT/ap2-realtime/xsb_ap2_realtime.py" /usr/local/bin/xsb-ap2-realtime
install -m 0755 "$ROOT/ap2-realtime/xsb-ap2-mode" /usr/local/sbin/xsb-ap2-mode
install -m 0644 "$ROOT/pi-files/etc/systemd/system/xsb-ap2-realtime.service" \
  /etc/systemd/system/xsb-ap2-realtime.service
install -m 0644 "$ROOT/pi-files/etc/systemd/system/xsb-ap2-usb-forward.service" \
  /etc/systemd/system/xsb-ap2-usb-forward.service
install -d -m 0755 /etc/xsb
if [[ ! -e /etc/xsb/ap2-realtime.conf ]]; then
  install -m 0640 -o root -g xsb-ap2 \
    "$ROOT/pi-files/etc/xsb/ap2-realtime.conf.example" /etc/xsb/ap2-realtime.conf
  echo 'Created /etc/xsb/ap2-realtime.conf; set AP2_HOST before activation.'
else
  echo 'Preserved existing /etc/xsb/ap2-realtime.conf'
fi
systemctl daemon-reload
if ! command -v cliairplay >/dev/null 2>&1; then
  echo 'NOTE: cliairplay is not installed. See ap2-realtime/README.md.'
fi
printf '%s\n' 'Installed (NOT started). Once configured, use:' \
  '  sudo xsb-ap2-mode start' \
  '  sudo xsb-ap2-mode status' \
  '  sudo xsb-ap2-mode stop'
