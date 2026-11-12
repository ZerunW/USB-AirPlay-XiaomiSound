# USB-AirPlay-XiaomiSound

English | [简体中文](README.zh-CN.md)

A Raspberry Pi Zero 2 W that turns two Xiaomi Sound speakers (model L16B) into one stereo output:

- for a computer (macOS, Windows, Ubuntu) it is a **USB sound card** named "Xiaomi Sound Bridge";
- for an iPhone or Mac it is an **AirPlay 2** receiver;
- it sends the left channel to one speaker and the right channel to the other over **Bluetooth**, with the speakers' own (absolute) volume kept in sync everywhere.

## The verified setup

```
  iPhone / Mac ---- AirPlay 2 (Wi-Fi) ----+
                                          v
                                +--------------------+   Bluetooth A2DP, AAC mono 112 kbps
                                | Raspberry Pi       |   one link per speaker
                                | Zero 2 W           +-----------+--------------+
                                |                    |           |              |
                                | shairport-sync     |   +-------v------+ +-----v--------+
                                | xsb-usb-forward    |   | left speaker | | right speaker|
                                | xsb-bt-stream@L/R  |   | plays L      | | plays R      |
                                +--------------------+   +--------------+ +--------------+
                                          ^
  Computer ---- USB (UAC2 sound card) ----+
```

What runs on the Pi, and why:

| Part | What it does |
|---|---|
| Dual Bluetooth | The Pi pairs with each speaker separately and splits the stereo signal itself. The speakers must **not** be a Mi Home stereo group (see [Test results](#test-results) for why). |
| AAC, mono, 112 kbps per speaker | Self-built BlueALSA 4.3.1 with fdk-aac (`setup/build-bluealsa-aac.sh`). Better sound than SBC at the same air time. |
| Absolute volume, kept in sync | Native AVRCP volume (`--a2dp-volume`). `xsb-volume-sync` keeps both speakers, the Mi Home app, the speaker buttons and voice commands, the computer's volume and the AirPlay slider at the same level. |
| Left/right alignment | `LEFT_LATENCY_MS` / `RIGHT_LATENCY_MS` delay one speaker so both play together. Calibrate once for your room (see [Calibration](#calibrate-the-leftright-alignment)). |
| Streams only while something plays | The Bluetooth streams stop 15 s after the source stops (`BT_IDLE_STOP_S`). The links stay connected, so volume keeps working. |
| Sniff mode with wake-up | Idle links go into Bluetooth sniff mode (`xsb-bt-sniff`), which frees the 2.4 GHz radio that Wi-Fi shares on the Zero 2 W. Before a stream starts, the Pi wakes the link explicitly, so every start behaves the same. |
| AirPlay has priority | While AirPlay plays, the USB path is paused; it resumes 5 s after AirPlay stops. |
| Self-healing | Automatic reconnect with back-off, a Bluetooth controller watchdog, and a Wi-Fi watchdog with a USB network fallback. |

## Test results

Measured on 2026-11-09 with the reference setup (two L16B speakers, Pi Zero 2 W, Mac over USB). Latency was measured from the moment the Mac sends a beep to the moment a microphone at the listening position hears it, with both sides on the Mac's clock.

| Test | Result |
|---|---|
| Total latency, computer to speaker, AAC (2 min continuous) | left 389-433 ms, right 403-429 ms |
| Total latency, AAC, 6 stop/start cycles | 377-419 ms (one start out of about 13 came up at ~750 ms; the next start was normal again) |
| Total latency, SBC (for comparison) | left 359-369 ms, right 335-359 ms |
| Of which on the Pi | about 150 ms (USB forwarding 50 ms + Bluetooth buffer 80-100 ms) |
| Left/right alignment after calibration | within about 10-30 ms |
| Channel separation | each speaker plays only its own channel |
| Dropouts (20 s steady tones) | AAC: 2 (left) and 7 (right), 10-40 ms each. SBC: 5 and 10. Often on both speakers at the same moment: Wi-Fi and Bluetooth share one radio. |
| Underruns on the Pi (2 min continuous) | 0 |
| Volume sync | changing one speaker's volume moves the other within 4 s |
| Reboot | both speakers reconnect about 1 min after boot; codec, volume and all services come back |
| CPU (BlueALSA, two AAC streams) | 5-6 % |
| Sniff wake-up before a stream | both links active within 20-50 ms |

Why not the Mi Home stereo group: fed over Bluetooth, the group plays the mono mix (L+R)/2 on its second speaker, and its own sync buffer made the total latency 765-1700 ms and drift upwards during playback. Xiaomi has said publicly that the group keeps a buffer to sync the two speakers.

Limits:

- About 400 ms is too much for strict lip sync. Use a player with an audio-delay setting for video.
- After the stream has been idle for more than 15 s, the very beginning of the next sound can be cut short while the Bluetooth stream starts.
- Short dropouts are possible because the Zero 2 W shares one 2.4 GHz radio between Wi-Fi and Bluetooth.
- One source plays at a time (AirPlay before USB). Playback only.

## Installation

You need a Raspberry Pi Zero 2 W, a microSD card, a powered USB dock (computer and Pi), a micro-USB data cable, a separate power supply for the Pi, and two Xiaomi Sound speakers.

Wiring: connect the Pi's micro-USB port marked **USB** (not **PWR**) to the dock, and the dock to the computer. Power the Pi through **PWR**.

All commands below run on the Pi over SSH (`ssh <user>@<hostname>.local`).

### 1. Flash the OS

Flash Raspberry Pi OS (trixie, 32-bit) with Raspberry Pi Imager. In the OS customisation settings, set the hostname, user name and password, Wi-Fi and enable SSH. For the desktop image, make it headless:

```bash
git clone https://github.com/ZerunW/USB-AirPlay-XiaomiSound.git ~/USB-AirPlay-XiaomiSound
cd ~/USB-AirPlay-XiaomiSound
sudo bash setup/headless.sh
sudo reboot
```

(With the Lite image, just clone the repository.)

### 2. Install dependencies

```bash
sudo apt update
sudo apt install -y bluez-alsa-utils libasound2-plugin-bluez libasound2-plugins rfkill iw dkms
sudo apt install -y build-essential git autoconf automake libtool libpopt-dev \
  libconfig-dev libasound2-dev avahi-daemon libavahi-client-dev libssl-dev \
  libsoxr-dev libplist-dev libsodium-dev uuid-dev libgcrypt20-dev xxd \
  libplist-utils libavutil-dev libavcodec-dev libavformat-dev systemd-dev
```

### 3. Build NQPTP and shairport-sync (AirPlay 2)

```bash
cd ~
git clone https://github.com/mikebrady/nqptp.git
cd nqptp && git checkout 1.2.9
autoreconf -fi && ./configure --with-systemd-startup && make && sudo make install
sudo systemctl enable --now nqptp
cd ~
git clone https://github.com/mikebrady/shairport-sync.git
cd shairport-sync && git checkout 5.6.2
autoreconf -fi
./configure --sysconfdir=/etc --with-alsa --with-soxr --with-avahi \
  --with-ssl=openssl --with-systemd-startup --with-airplay-2 \
  --with-dbus-interface --with-metadata
make && sudo make install
```

The D-Bus interface lets the AirPlay volume slider follow the speakers.

### 4. Build BlueALSA with AAC

```bash
cd ~/USB-AirPlay-XiaomiSound
sudo bash setup/build-bluealsa-aac.sh
```

This builds fdk-aac 2.0.3 and BlueALSA 4.3.1 into `/usr/local` and creates the storage symlink `/usr/local/var/lib/bluealsa -> /var/lib/bluealsa`.

### 5. Install the project files

```bash
cd ~/USB-AirPlay-XiaomiSound
sudo install -m 0755 -o root -g root pi-files/usr/local/sbin/xsb-*.sh pi-files/usr/local/sbin/xsb-volume-sync /usr/local/sbin/
sudo install -m 0644 pi-files/etc/systemd/system/xsb-*.service /etc/systemd/system/
sudo install -d /etc/systemd/system/bluealsa.service.d /etc/systemd/system/shairport-sync.service.d /etc/systemd/journald.conf.d
sudo install -m 0644 pi-files/etc/systemd/system/bluealsa.service.d/override.conf /etc/systemd/system/bluealsa.service.d/
sudo install -m 0644 pi-files/etc/systemd/system/shairport-sync.service.d/override.conf /etc/systemd/system/shairport-sync.service.d/
sudo install -m 0644 pi-files/etc/systemd/journald.conf.d/xsb-persistent.conf /etc/systemd/journald.conf.d/
sudo install -m 0644 pi-files/etc/shairport-sync.conf /etc/shairport-sync.conf
sudo install -m 0644 pi-files/etc/dbus-1/system.d/shairport-sync-dbus.conf /etc/dbus-1/system.d/
sudo install -m 0644 pi-files/etc/alsa/conf.d/60-xsb.conf /etc/alsa/conf.d/
sudo install -m 0644 pi-files/etc/modules-load.d/xsb-aloop.conf /etc/modules-load.d/
sudo install -m 0644 pi-files/etc/modprobe.d/xsb-aloop.conf /etc/modprobe.d/
sudo install -m 0644 pi-files/etc/NetworkManager/conf.d/60-xsb-wifi-powersave.conf /etc/NetworkManager/conf.d/
sudo install -m 0600 pi-files/etc/NetworkManager/system-connections/xsb-usb-rescue.nmconnection /etc/NetworkManager/system-connections/
visudo -cf pi-files/etc/sudoers.d/xsb-shairport && \
  sudo install -m 0440 -o root -g root pi-files/etc/sudoers.d/xsb-shairport /etc/sudoers.d/
sudo systemctl daemon-reload
```

### 6. Enable USB device mode and the UAC2 name fix

Add this line at the end of `/boot/firmware/config.txt` (in the `[all]` section):

```
dtoverlay=dwc2,dr_mode=peripheral
```

Install the DKMS module that makes macOS show the name "Xiaomi Sound Bridge" (without it the device is called "Playback Inactive"):

```bash
cd ~/USB-AirPlay-XiaomiSound
sudo mkdir -p /usr/src/xsb-uac2-1.0
sudo cp dkms/xsb-uac2/* /usr/src/xsb-uac2-1.0/
sudo dkms add xsb-uac2/1.0
sudo dkms install xsb-uac2/1.0 -k "$(uname -r)"
```

### 7. Pair the speakers

If the speakers are in a Mi Home stereo group, dissolve the group in the Mi Home app first. Put each speaker in Bluetooth pairing mode, then:

```bash
sudo rfkill unblock bluetooth
sudo bluetoothctl
```

```
agent NoInputNoOutput
default-agent
scan on
# note the two speakers' MAC addresses, then:
pair LEFT_MAC
trust LEFT_MAC
pair RIGHT_MAC
trust RIGHT_MAC
scan off
exit
```

### 8. Configure

```bash
sudo install -d /etc/xsb
sudo install -m 0644 pi-files/etc/xsb/xsb.conf.example /etc/xsb/xsb.conf
sudo nano /etc/xsb/xsb.conf     # set LEFT_MAC= and RIGHT_MAC=
```

The other values in the template are the ones measured above.

### 9. Enable and reboot

```bash
sudo systemctl enable bluealsa nqptp shairport-sync \
  xsb-gadget xsb-usb-forward xsb-bt-reconnect xsb-volume-sync \
  xsb-bt-stream@left xsb-bt-stream@right xsb-bt-sniff \
  xsb-bt-watchdog xsb-rescue
sudo reboot
```

### 10. Check

About a minute after boot both speakers connect by themselves. Then:

```bash
bluealsa-cli list-pcms                 # one .../a2dpsrc/sink line per speaker
for p in $(bluealsa-cli list-pcms | grep a2dpsrc); do bluealsa-cli codec "$p" -v | tail -1; done
```

Both lines should start with `Selected codec: AAC`. If a speaker shows SBC, switch it once while nothing is playing (BlueZ remembers the choice):

```bash
bluealsa-cli codec /org/bluealsa/hci0/dev_XX_XX_XX_XX_XX_XX/a2dpsrc/sink AAC
```

Select "Xiaomi Sound Bridge" (the speaker icon, not the AirPlay one) as the output on the computer and play something. `journalctl -u xsb-bt-stream@left -n 5` should show "woke left link" and "source playing: starting the left stream".

### Calibrate the left/right alignment

Each speaker buffers on its own, so the two may not start at the same moment. Put a microphone at the listening position, play a short beep on the left channel only and one on the right channel only, and compare when each arrives. Add the difference to the `LEFT_LATENCY_MS` or `RIGHT_LATENCY_MS` of the speaker that is early (the reference setup uses 80 and 100), then:

```bash
sudo systemctl restart xsb-bt-stream@left xsb-bt-stream@right
```

## Configuration

`/etc/xsb/xsb.conf` (template: `pi-files/etc/xsb/xsb.conf.example`):

| Setting | Value used | Meaning |
|---|---|---|
| `LEFT_MAC`, `RIGHT_MAC` | your speakers | Bluetooth addresses |
| `BLUEALSA_OPTS` | `-c AAC --aac-bitrate=112000 --sbc-quality=low --a2dp-force-mono --a2dp-volume --initial-volume=50 --keep-alive=5` | AAC mono 112 kbps, absolute volume |
| `LEFT_LATENCY_MS`, `RIGHT_LATENCY_MS` | `80`, `100` | per-speaker buffer, used for alignment |
| `AUDIO_LATENCY_MS` | `50` | USB forwarding buffer |
| `BT_IDLE_STOP_S` | `15` | stop the Bluetooth streams this long after the source stops |
| `BT_IDLE_SNIFF_MS` | `2000` | idle time before a link enters sniff mode |
| `WIFI` | `on` | set `off` to run without Wi-Fi (see below) |

Restart the streams after a change; restart `bluealsa` after changing `BLUEALSA_OPTS`.

### Running without Wi-Fi

Wi-Fi and Bluetooth share one radio on the Zero 2 W. If you do not need Wi-Fi (the AirPlay receiver needs it), switch it off:

1. Set `WIFI=off` in `/etc/xsb/xsb.conf`. The gadget then always includes the USB network link, the Wi-Fi watchdog does nothing, and the streams do not wait for Wi-Fi at boot.
2. `sudo systemctl restart xsb-gadget xsb-rescue xsb-usb-forward`. The computer sees the sound card again, with the product ID `USB_RESCUE_PRODUCT_ID`.
3. Check that `ssh <user>@<hostname>.local` works over the USB cable, then `sudo nmcli radio wifi off`. NetworkManager keeps this across reboots.

To undo it: set `WIFI=on`, `sudo nmcli radio wifi on`, and restart the same services.

## Credits and license

GPL-3.0, see `LICENSE`. Built on [shairport-sync](https://github.com/mikebrady/shairport-sync), [NQPTP](https://github.com/mikebrady/nqptp), [BlueALSA](https://github.com/arkq/bluez-alsa) and [fdk-aac](https://github.com/mstorsjo/fdk-aac). The USB gadget scripts are adapted from [batubozkan/pods-head-tracker](https://github.com/batubozkan/pods-head-tracker) (GPL-3.0). `dkms/xsb-uac2` patches the kernel's `f_uac2.c` (GPL-2.0) at build time; no kernel source is included.
