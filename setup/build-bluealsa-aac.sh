#!/bin/bash
# Build and install BlueALSA 4.3.1 with AAC support (fdk-aac 2.0.3) into
# /usr/local. Debian's bluez-alsa package has SBC only; the Mi speakers
# negotiate AAC with phones, and this project's bluealsa.service drop-in
# starts /usr/local/bin/bluealsa with "-c AAC" (see BLUEALSA_OPTS in
# pi-files/etc/xsb/xsb.conf.example).
#
# Run on the Pi as root (sudo). Sources are cloned into $BUILD_DIR
# (default: $HOME/build of the invoking user). Takes a while on a Zero 2 W.
#   sudo bash setup/build-bluealsa-aac.sh
# Afterwards: sudo systemctl daemon-reload && sudo systemctl restart bluealsa
# SPDX-License-Identifier: GPL-3.0-or-later
set -ex
export DEBIAN_FRONTEND=noninteractive

# Under sudo, $HOME may be root's; prefer the invoking user's home.
if [ -z "${BUILD_DIR:-}" ]; then
    BUILD_DIR="$(getent passwd "${SUDO_USER:-$USER}" | cut -d: -f6)/build"
fi
mkdir -p "$BUILD_DIR"

apt-get install -y --no-install-recommends git build-essential autoconf automake libtool pkg-config \
  libasound2-dev libbluetooth-dev libdbus-1-dev libglib2.0-dev libsbc-dev python3-docutils

cd "$BUILD_DIR"
[ -d fdk-aac ] || git clone --depth 1 --branch v2.0.3 https://github.com/mstorsjo/fdk-aac.git
cd fdk-aac && autoreconf -fi && ./configure --prefix=/usr/local --disable-static && make -j2 && make install && ldconfig

cd "$BUILD_DIR"
[ -d bluez-alsa ] || git clone --depth 1 --branch v4.3.1 https://github.com/arkq/bluez-alsa.git
cd bluez-alsa && autoreconf -fi && mkdir -p build && cd build
PKG_CONFIG_PATH=/usr/local/lib/pkgconfig ../configure --prefix=/usr/local --enable-aac --enable-cli \
  --with-alsaplugindir=/usr/local/lib/alsa-plugins --with-dbusconfdir=/usr/local/share/dbus-1/system.d
make -j2
make install
ldconfig

# The self-built daemon stores per-device state (volumes, codec choice) under
# /usr/local/var/lib/bluealsa; point that at the usual location, otherwise the
# journal fills with "Couldn't save storage" errors.
mkdir -p /usr/local/var/lib /var/lib/bluealsa
[ -e /usr/local/var/lib/bluealsa ] || ln -s /var/lib/bluealsa /usr/local/var/lib/bluealsa

echo INSTALL_DONE
