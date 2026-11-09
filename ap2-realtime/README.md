# Experimental: USB → native AirPlay 2 Realtime (type 96)

**Independent output path; the original Bluetooth solution is unchanged.**

```
Computer UAC2 48 kHz stereo
  → xsb-gadget
  → xsb-ap2-usb-forward (alsaloop -S 1, 100ms polling, 20ms latency target)
  → snd-aloop capture
  → xsb-ap2-realtime (bounded latest-PCM queue + 10 ms pacing)
  → cliairplay native HAP/ALAC/RTP/UDP/PTP, buffered stream disabled
  → Xiaomi Wi-Fi stereo group (if firmware accepts realtime mode)
```

## What is implemented (and what is not)

* An opt-in Python daemon with 48 kHz S16_LE stereo, host-stream detection,
  ALSA capture/reopen, USB volume/mute mapping, silent keepalive, bounded
  latest-audio buffering and backlog diagnostics. Bounded stdin writes abort
  on sustained sender backpressure rather than silently stretching the PCM
  timeline. Clock stalls during playback abort the session for systemd retry.
* Experimental USB forwarding gets its own `xsb-ap2-usb-forward.service`,
  with a 100ms activation poll and 20ms `alsaloop` target latency. The original
  `xsb-usb-forward.service` still uses its previous 1-second/50ms defaults.
* Waits for cliairplay's actual START acknowledgement and checks the reported
  `warm_lead_ms` against `AP2_MAX_EFFECTIVE_QUEUE_MS` before declaring the
  experimental sender ready. This does **not** prove speaker sound output.
* Native AirPlay 2 **Realtime type 96 requested**, not Buffered type 103.
* The protocol engine is **music-assistant/airplay-cli** (`cliairplay`):
  encrypted RTSP, HAP, ALAC, RTP, timing and retransmission are provided by
  that externally built GPL-3.0 program. This project is **not** a newly
  written AirPlay 2 protocol/cryptography implementation.
* Its `--latency 300` is a **requested sender queue depth**, **not** 300 ms
  acoustic end-to-end latency. There is NO validated Xiaomi L16B result.
* It may fail with Xiaomi receivers that only support Buffered AirPlay 2,
  that enforce a long render delay, or that don't support native transient
  HAP pairing. Never overwrite an existing working Bluetooth install.
* The existing `alsaloop -S 1` is deliberately retained for asynchronous USB
  host/Pi clock compensation. Direct `arecord` on UAC2Gadget without feedback
  would accumulate clock drift, drop frames or generate pops. This does not by
  itself prove that the output PCM rate perfectly follows the AirPlay PTP clock.
  The capture/ALAC timing domain still needs real long-run validation.
* Only single-target AirPlay output is implemented; configure the Mi Home
  native **stereo group** as the one receiver. Do not configure separate
  mono AirPlay sends to independent speakers.

## Prerequisites

* Pi Zero 2 W with working USB gadget + ALSA Loopback + `xsb-usb-forward`.
* Network reachability to the Xiaomi Sound native AirPlay 2 receiver.
* `alsa-utils` (`arecord`, `amixer`), Python 3.9+ and systemd.
* A **working `cliairplay` executable for your OS/CPU** at
  `/usr/local/bin/cliairplay` (or set `AP2_BINARY`). It must support `--check`,
  `--ap2-native`, `--latency`, and `CLIAIRPLAY_BUFFERED=0`.

**Important Pi OS compatibility warning:** the current upstream
[`music-assistant/airplay-cli`](https://github.com/music-assistant/airplay-cli)
CI builds Linux x86_64 and aarch64, but not 32-bit ARMv7. The project's
`libraop` submodule requires prebuilt libraries. **Do not assume it builds
on your current 32-bit Pi OS.** The Zero 2 W has a 64-bit CPU and can use
64-bit Pi OS, or an external AArch64 build host can produce a binary.
Evaluate an architecture-compatible executable before switching services.
This setup script deliberately does **not** fetch unpinned code or pretend
that `airplay-cli` was successfully compiled.

On a suitable Linux host with dependencies/submodule available, follow the
upstream build instructions and test the resulting binary:

```bash
git clone --recurse-submodules https://github.com/music-assistant/airplay-cli.git
cd airplay-cli
make STATIC=1   # use the upstream documented target/toolchain for your host
# copy the correctly matched executable to /usr/local/bin/cliairplay on Pi
/usr/local/bin/cliairplay --check
```

Do not install a 64-bit binary on a 32-bit userland: it won't execute.

## Install the experiment

```bash
cd ~/USB-AirPlay-XiaomiSound
sudo apt install -y alsa-utils python3
sudo bash setup/install-ap2-realtime.sh
sudo nano /etc/xsb/ap2-realtime.conf
```

Set `AP2_HOST` to the receiver IP (native Wi-Fi **stereo group**). Start with
`AP2_LATENCY_MS=600` if troubleshooting, then 400, 300, 250. All requested
values are constrained to 250–3000 ms. Choose `AP2_TIMING=ptp` initially;
`nqptp` must not bind UDP 319/320 at the same time. The service grants only
`CAP_NET_BIND_SERVICE` to the non-root user for privileged PTP UDP ports.

```bash
sudo /usr/local/sbin/xsb-ap2-mode start
sudo journalctl -fu xsb-ap2-realtime
# restore exact services that were active before switching:
sudo /usr/local/sbin/xsb-ap2-mode stop
```

`xsb-ap2-mode` stops the old Bluetooth/BlueALSA/Shairport/NQPTP services,
legacy USB forwarder, and Wi-Fi rescue watchdog, then starts the **separate**
fast-poll USB forwarder and AirPlay bridge. This avoids the rescue daemon
rebinding the USB gadget mid-test. The Wi-Fi watchdog is restored on stop, so
Wi-Fi auto-recovery is unavailable during the experiment. The switching script
records which services were active in `/run` and restores them on `stop`. The
original units are not disabled, and nothing new is enabled at boot. A temporary
mode guard also prevents shairport-sync's ExecStopPost from starting the legacy
forwarder in the middle of the changeover.

## Diagnostic evidence / performance acceptance

1. Check `cliairplay` logs include native route + **realtime** (not buffered),
   latency `warm_lead_ms`, `device_min_frames`, `device_max_frames` and a
   `clock_ready ... state=ready` status. If PTP stalls, check UDP 319/320,
   discovery and that `nqptp` is stopped.
2. Verify correct L/R material using a 48 kHz USB audio stream. If the group
   plays the same mono channel from both speakers, stop: that firmware route
   does not provide the required output.
3. Do a microphone loopback *on the same host clock* for end-to-end USB beep
   → acoustic arrival. Measure median, p95, worst-case and variance across
   sustained 10-minute audio, cold/warm starts, and 50 short notifications.
4. Try `AP2_LATENCY_MS` 600 → 400 → 300 → 250, at each step measuring both
   real latency AND silence/dropout counts. A healthy sender log alone
   cannot prove sound was actually rendered on the speaker.
5. Review queue counters. `dropped` indicates host/transport backpressure;
   `silence` while idle is expected. Rapidly rising `sender_skipped` under
   active USB playback means the host can't sustain a realtime 10-ms cadence.

### Measured versus designed

| Path | Status |
|---|---|
| Existing USB → dual Bluetooth | **Measured** approximately 389–433 ms AAC |
| Experimental AP2 sender queue | **Configured** 300 ms; may be clamped |
| USB → Xiaomi acoustic latency | **Not measured**; may exceed 300 ms |
| Native stereo synchronization | **Not verified** |

### Known limitations

* USB forwarding polls the gadget's `Capture Rate` every 100ms, and the
  `arecord` thread separately polls it every 100ms. A 20ms notification from
  fully idle has only about a 20% chance of intersecting one periodic 100ms
  poll, **even before** ALSA startup time; such notifications can still be
  clipped. Continuous USB streams are far more suitable than cold starts.
  True low-latency short notifications may require a verified event-driven
  UAC2 trigger or persistent-hot capture design.
* If the sender blocks stdin for more than `AP2_WRITE_TIMEOUT_MS` (default
  8ms), the bridge fails and systemd restarts it rather than silently skipping
  whole future packets. Its Linux pipe and upstream `cliairplay` internal ring
  may still contain previously submitted audio. A true live audio drop/flush
  policy inside the actual ALAC RTP transmitter is future work.
* The PCM bridge does not perform high-quality adaptive resampling; `alsaloop`
  is responsible for USB clock feedback. During a source stall, it drops old
  PCM rather than replaying stale data.
* USB gadget volume/mute writes are forwarded to the AirPlay sender; volume
  changes made directly on the Wi-Fi speakers are not synchronized back to USB.
* A service restart reconnects and waits for PTP lock; it may take seconds.
  This is a one-time setup delay, not the expected steady-state audio lag.
* Grouped Xiaomi receivers may have firmware-enforced >=1 s buffering even
  when the sending protocol is type 96. In that case a 300ms target is
  impossible without receiver-side change or a different output path.
* `AP2_AUTH` is read from a root-owned, group-readable config file, but
  cliairplay's `--auth` argument can be visible in process listings. Never
  publish logs/process snapshots containing HAP credentials; secure the host
  before using persistent pairing credentials.
* The 20ms USB forwarding latency target can underrun on real Pi hardware.
  If it does, increase `AUDIO_LATENCY_MS` in the experimental forwarder unit
  to 30 or 50 and rerun the measurements. This trades latency for stability.

## Reproducible offline tests and simulations

These checks are repeatable on any modern Linux machine. The fake sender
**does not implement RTSP, HAP, ALAC, PTP, RTP or a Xiaomi renderer**:

```bash
python3 -m unittest discover -s ap2-realtime/tests -v
python3 ap2-realtime/tools/simulate.py --minutes 10 --output sim.json
python3 ap2-realtime/tools/benchmark.py
bash -n ap2-realtime/xsb-ap2-mode setup/install-ap2-realtime.sh \
  pi-files/usr/local/sbin/xsb-usb-forward.sh \
  pi-files/usr/local/sbin/xsb-source-switch.sh
```

`simulate.py` tests PCM buffer overflow, CPU scheduling jitter, independent
oscillator drift of ±100/500ppm, USB pause bursts, an *idealized* receiver queue,
a short-notification detection bound, and hypothetical acoustic-latency budgets.
`benchmark.py` evaluates 10ms timer cadence with/without intentionally injected
30ms local stalls on the **test host CPU**, not Pi Zero 2 W. These are math and
host scheduling simulations, not evidence of Xiaomi firmware compliance or
300ms acoustic latency. Review the checked-in test report for actual numbers.

Config options added in this validation revision:

```ini
# Reject native sender startup if its effective reported queue exceeds 500ms.
AP2_MAX_EFFECTIVE_QUEUE_MS=500
# Maximum total wait for one realtime PCM pipe packet; restart on timeout.
AP2_WRITE_TIMEOUT_MS=8
# Sender must explicitly acknowledge ACTION=START before experiment is ready.
AP2_START_WAIT_S=6
```
