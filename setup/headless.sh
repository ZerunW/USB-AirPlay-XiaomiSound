#!/bin/bash
# Turn a Raspberry Pi OS (desktop) install into a headless audio appliance:
# boot to console without autologin, and keep PipeWire/WirePlumber from
# starting (they would compete with BlueALSA for Bluetooth A2DP).
# Not needed on Raspberry Pi OS Lite.
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
raspi-config nonint do_boot_behaviour B1
systemctl --global mask pipewire.socket pipewire.service \
    pipewire-pulse.socket pipewire-pulse.service wireplumber.service
echo "Done. Reboot to apply."
