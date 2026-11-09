#!/usr/bin/env python3
"""Live USB/ALSA-to-AirPlay 2 bridge (independent of the Bluetooth output).

Uses the GPL-3.0 cliairplay binary for HAP/ALAC/RTP/PTP, but all source
management, bounded live PCM buffering, pacing and watchdogs are here.
This is an experimental transmitter, not a guarantee of 300 ms latency.
SPDX-License-Identifier: GPL-3.0-or-later
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import errno
import json
import logging
import os
from pathlib import Path
import re
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Optional

LOG = logging.getLogger("xsb-ap2")
FRAME_BYTES = 4                      # S16_LE, stereo
RATE = 48000
CAPTURE_RATE_RE = re.compile(r"\bvalues=(\d+)")
READY_RE = re.compile(r"\[STATUS\] clock_ready\b.*\bstate=ready\b")
NTP_RE = re.compile(r"\[STATUS\] clock_ready\b.*\bmode=ntp\b")
STALLED_RE = re.compile(r"\[STATUS\] clock_ready\b.*\bstate=stalled\b")
MIXER_RE = re.compile(r"\bvalues=(\S+)")
START_RE = re.compile(r"^\[STATUS\] started\b.*\bat_unix_ms=(\d+)\b")
LATENCY_RE = re.compile(r"^\[STATUS\] latency\b")
# Measured curve from the existing xsb-volume-sync implementation.
USB_VOLUME_CURVE = [(0, 0), (32, 10), (50, 25), (65, 42),
                    (71, 50), (87, 75), (100, 100)]


def gadget_to_percent(v: int) -> int:
    v = max(0, min(100, v))
    for (v0, p0), (v1, p1) in zip(USB_VOLUME_CURVE, USB_VOLUME_CURVE[1:]):
        if v <= v1:
            return round(p0 + (v - v0) * (p1 - p0) / (v1 - v0))
    return 100


def mixer_value(name: str) -> Optional[str]:
    try:
        output = subprocess.run(
            ["amixer", "-c", "UAC2Gadget", "cget", f"iface=MIXER,name='{name}'"],
            capture_output=True, text=True, timeout=1, check=True,
        ).stdout
        m = MIXER_RE.search(output)
        return m.group(1) if m else None
    except (OSError, subprocess.SubprocessError):
        return None


def env_int(key: str, default: int, lower: int, upper: int) -> int:
    raw = os.environ.get(key, str(default))
    try:
        result = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer, got {raw!r}") from exc
    if not lower <= result <= upper:
        raise ValueError(f"{key} must be between {lower} and {upper}, got {result}")
    return result


@dataclass(frozen=True)
class Settings:
    host: str
    port: int = 7000
    latency_ms: int = 300
    period_ms: int = 10
    capture_buffer_ms: int = 60
    capture_poll_ms: int = 100
    timing: str = "ptp"
    binary: str = "/usr/local/bin/cliairplay"
    capture_device: str = "hw:CARD=Loopback,DEV=1"
    volume: int = 65
    command_directory: str = "/run/xsb-ap2"
    clock_wait_s: int = 12
    auth: str = ""
    bind_ip: str = ""
    max_effective_queue_ms: int = 500
    write_timeout_ms: int = 8
    start_wait_s: int = 6

    @classmethod
    def from_env(cls) -> "Settings":
        host = os.environ.get("AP2_HOST", "").strip()
        if not host or host.startswith("CHANGE_ME"):
            raise ValueError("AP2_HOST is required (IP of the Xiaomi stereo group's AirPlay receiver)")
        timing = os.environ.get("AP2_TIMING", "ptp").strip().lower()
        if timing not in ("ptp", "ntp", "auto"):
            raise ValueError("AP2_TIMING must be ptp, ntp or auto")
        period = env_int("AP2_PERIOD_MS", 10, 5, 40)
        if (RATE * period) % 1000:
            raise ValueError("period does not contain an integral number of PCM frames")
        buffer_ms = env_int("AP2_CAPTURE_BUFFER_MS", 60, 20, 250)
        if buffer_ms < period:
            raise ValueError("capture buffer must exceed PCM period")
        return cls(
            host=host,
            port=env_int("AP2_PORT", 7000, 1, 65535),
            latency_ms=env_int("AP2_LATENCY_MS", 300, 250, 3000),
            period_ms=period,
            capture_buffer_ms=buffer_ms,
            capture_poll_ms=env_int("AP2_CAPTURE_POLL_MS", 100, 50, 1000),
            timing=timing,
            binary=os.environ.get("AP2_BINARY", "/usr/local/bin/cliairplay"),
            capture_device=os.environ.get("AP2_CAPTURE_DEVICE", "hw:CARD=Loopback,DEV=1"),
            volume=env_int("AP2_VOLUME", 65, 0, 100),
            command_directory=os.environ.get("AP2_RUNTIME_DIR", "/run/xsb-ap2"),
            clock_wait_s=env_int("AP2_CLOCK_WAIT_S", 12, 3, 45),
            auth=os.environ.get("AP2_AUTH", "").strip(),
            bind_ip=os.environ.get("AP2_BIND_IP", "").strip(),
            max_effective_queue_ms=env_int("AP2_MAX_EFFECTIVE_QUEUE_MS", 500, 250, 3000),
            write_timeout_ms=env_int("AP2_WRITE_TIMEOUT_MS", 8, 1, period),
            start_wait_s=env_int("AP2_START_WAIT_S", 6, 2, 20),
        )


def bytes_for_ms(ms: int) -> int:
    return RATE * FRAME_BYTES * ms // 1000


class LivePcmBuffer:
    """Keep newest PCM, never allow seconds of stale USB audio to accumulate."""
    def __init__(self, capacity_bytes: int):
        if capacity_bytes < FRAME_BYTES or capacity_bytes % FRAME_BYTES:
            raise ValueError("capacity must be whole stereo frames")
        self.capacity = capacity_bytes
        self._data = bytearray()
        self._lock = threading.Lock()
        self.dropped_bytes = 0
        self.empty_bytes = 0

    def push(self, payload: bytes) -> None:
        if len(payload) % FRAME_BYTES:
            raise ValueError("PCM chunks must be whole stereo frames")
        with self._lock:
            self._data.extend(payload)
            excess = max(0, len(self._data) - self.capacity)
            if excess:
                del self._data[:excess]
                self.dropped_bytes += excess

    def pull(self, size: int) -> bytes:
        if size % FRAME_BYTES:
            raise ValueError("PCM requests must be whole stereo frames")
        with self._lock:
            take = min(size, len(self._data))
            result = bytes(self._data[:take])
            del self._data[:take]
            self.empty_bytes += size - take
        return result + bytes(size - take)

    def discard_oldest(self, size: int) -> int:
        """Skip stale frames after a missed playout tick, preserving frame alignment."""
        if size % FRAME_BYTES:
            raise ValueError("discard requests must be whole stereo frames")
        with self._lock:
            drop = min(size, len(self._data))
            del self._data[:drop]
            self.dropped_bytes += drop
            return drop

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def depth_ms(self) -> float:
        with self._lock:
            return len(self._data) * 1000 / (RATE * FRAME_BYTES)


class UsbCapture(threading.Thread):
    """Only open Loopback while the USB host actually has an active stream.

    xsb-usb-forward.service must be running: its alsaloop -S 1 manages
    USB asynchronous clock feedback and bridges the gadget to Loopback.
    """
    def __init__(self, settings: Settings, pcm: LivePcmBuffer, stop: threading.Event):
        super().__init__(name="alsa-capture", daemon=True)
        self.settings = settings
        self.pcm = pcm
        self.stop_event = stop
        self.proc: Optional[subprocess.Popen] = None
        self.active = False
        self._partial = b""

    def _rate(self) -> int:
        try:
            output = subprocess.run(
                ["amixer", "-c", "UAC2Gadget", "cget", "iface=PCM,name=Capture Rate"],
                capture_output=True, text=True, timeout=1, check=True,
            ).stdout
            m = CAPTURE_RATE_RE.search(output)
            return int(m.group(1)) if m else 0
        except (OSError, subprocess.SubprocessError):
            return 0

    def _start(self) -> None:
        # ALSA period/buffer times are only requests; hardware may round them.
        # Read directly from Loopback; the USB gadget's clock feedback continues
        # to be driven by xsb-usb-forward's alsaloop.
        cmd = ["arecord", "-q", "-D", self.settings.capture_device,
               "-f", "S16_LE", "-r", str(RATE), "-c", "2",
               f"--period-time={self.settings.period_ms * 1000}",
               f"--buffer-time={self.settings.period_ms * 4000}", "-t", "raw"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, bufsize=0)
        self._partial = b""
        self.active = True
        LOG.info("USB source active; capturing Loopback at 48000 Hz")

    def _close(self) -> None:
        proc = self.proc
        self.proc = None
        self.active = False
        self._partial = b""
        self.pcm.clear()
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1)
            if proc.stdout is not None:
                proc.stdout.close()

    def run(self) -> None:
        zero_count = 0
        next_check = 0.0
        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                if now >= next_check:
                    rate = self._rate()
                    next_check = now + self.settings.capture_poll_ms / 1000
                    if rate not in (0, RATE):
                        LOG.warning("USB host rate %d Hz unsupported; waiting for 48000 Hz", rate)
                    valid = rate == RATE
                    zero_count = 0 if valid else zero_count + 1
                    if valid and self.proc is None:
                        try:
                            self._start()
                        except OSError as e:
                            LOG.warning("arecord failed to start: %s", e)
                    elif not valid and zero_count >= 3 and self.proc is not None:
                        LOG.info("USB host idle; replacing PCM with live silence")
                        self._close()
                if self.proc is None or self.proc.stdout is None:
                    self.stop_event.wait(0.03)
                    continue
                if self.proc.poll() is not None:
                    LOG.warning("arecord exited (status %s); reopening on next poll", self.proc.returncode)
                    self._close()
                    self.stop_event.wait(0.2)
                    continue
                readable, _, _ = select.select([self.proc.stdout], [], [], 0.04)
                if readable:
                    data = os.read(self.proc.stdout.fileno(), 8192)
                    if not data:
                        LOG.warning("arecord returned EOF")
                        self._close()
                        continue
                    combined = self._partial + data
                    full = len(combined) - len(combined) % FRAME_BYTES
                    self._partial = combined[full:]
                    if full:
                        self.pcm.push(combined[:full])
        finally:
            self._close()


class UsbVolume(threading.Thread):
    """Forward host USB volume to the AirPlay receiver, without BlueALSA.

    This does not make changes made in Mi Home propagate back to the USB host.
    """
    def __init__(self, transport: AirPlayTransport, stop: threading.Event):
        super().__init__(name="usb-volume", daemon=True)
        self.transport = transport
        self.stop_event = stop
        self.last_level: Optional[int] = None

    def _sync(self) -> None:
        raw = mixer_value("PCM Capture Volume")
        mute = mixer_value("PCM Capture Switch")
        if raw is None or not raw.isdecimal():
            return
        level = 0 if mute == "off" else gadget_to_percent(int(raw))
        if level != self.last_level:
            self.transport.send_command(f"VOLUME={level}\n")
            LOG.info("USB volume → AirPlay level %d", level)
            self.last_level = level

    def run(self) -> None:
        # Using timed reads avoids depending on the exact stdout format of
        # `amixer events` (which differs among ALSA versions). 500-ms polling
        # costs ~2 local mixer reads/s and does not touch the audio thread.
        while not self.stop_event.is_set():
            try:
                self._sync()
            except (OSError, RuntimeError) as exc:
                LOG.warning("USB volume update failed: %s", exc)
            self.stop_event.wait(0.5)


class AirPlayTransport:
    def __init__(self, settings: Settings, stop: threading.Event):
        self.settings = settings
        self.stop_event = stop
        self.process: Optional[subprocess.Popen] = None
        self.connected = threading.Event()
        self.route_reported = threading.Event()
        self.route_acceptable = False
        self.clock_ready = threading.Event()
        self.started = threading.Event()
        self.started_at_unix_ms: Optional[int] = None
        self.actual_queue_ms: Optional[int] = None
        self.latency_reported = threading.Event()
        self.unexpected_ntp = threading.Event()
        self.stalled = threading.Event()
        self.cmd_path = Path(settings.command_directory) / "commands"
        self._threads: list[threading.Thread] = []
        self._command_lock = threading.Lock()
        self.skipped_ticks = 0
        self.sent_bytes = 0

    def arguments(self) -> list[str]:
        s = self.settings
        args = [s.binary, "--protocol", "airplay2", "--ap2-native",
                "--port", str(s.port), "--timing", s.timing,
                "--latency", str(s.latency_ms), "--samplerate", str(RATE),
                "--bitdepth", "16", "--channels", "2", "--volume", str(s.volume),
                "--cmdpipe", str(self.cmd_path)]
        if s.bind_ip:
            args.extend(["--if", s.bind_ip])
        if s.auth:
            args.extend(["--auth", s.auth])
        args.append(s.host)
        return args

    def _log_reader(self, stream, tag: str) -> None:
        try:
            for line in iter(stream.readline, ""):
                line = line.rstrip("\r\n")
                if not line:
                    continue
                LOG.info("cliairplay/%s %s", tag, line)
                if line.startswith("[STATUS] route "):
                    # The upstream binary reports exactly these attributes;
                    # refuse silent RAOP/Buffered fallbacks for this experiment.
                    attrs = dict(item.split("=", 1) for item in line.split()[2:]
                                 if "=" in item)
                    self.route_acceptable = (attrs.get("protocol") == "airplay2" and
                                             attrs.get("flow") == "native" and
                                             attrs.get("buffered") == "0" and
                                             (self.settings.timing == "auto" or
                                              attrs.get("timing") == self.settings.timing))
                    self.route_reported.set()
                if "[STATUS] connected" in line:
                    self.connected.set()
                if READY_RE.search(line):
                    self.clock_ready.set()
                if NTP_RE.search(line):
                    if self.settings.timing == "ptp":
                        self.unexpected_ntp.set()
                    else:
                        self.clock_ready.set()
                if STALLED_RE.search(line):
                    self.stalled.set()
                    self.clock_ready.clear()
                if LATENCY_RE.search(line):
                    attrs = dict(item.split("=", 1) for item in line.split()[2:]
                                 if "=" in item)
                    try:
                        self.actual_queue_ms = int(attrs["warm_lead_ms"])
                        LOG.info("receiver effective warm queue %dms (target %dms)",
                                 self.actual_queue_ms, self.settings.latency_ms)
                    except (KeyError, ValueError):
                        LOG.warning("latency status missing warm_lead_ms; cannot assess queue")
                    self.latency_reported.set()
                m = START_RE.match(line)
                if m:
                    self.started_at_unix_ms = int(m.group(1))
                    self.started.set()
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def start(self) -> None:
        s = self.settings
        if shutil.which(s.binary) is None:
            raise RuntimeError(f"missing {s.binary}; build/install music-assistant/airplay-cli first")
        try:
            subprocess.run([s.binary, "--check"], capture_output=True, text=True,
                           timeout=5, check=True)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"invalid/incompatible cliairplay binary: {exc}") from exc
        runtime = Path(s.command_directory)
        runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.cmd_path.exists():
            if not self.cmd_path.is_fifo():
                raise RuntimeError(f"refusing non-FIFO command path {self.cmd_path}")
            self.cmd_path.unlink()
        os.mkfifo(self.cmd_path, mode=0o600)
        env = os.environ.copy()
        env["CLIAIRPLAY_BUFFERED"] = "0"  # force native realtime RTP/UDP type 96
        self.process = subprocess.Popen(self.arguments(), stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        bufsize=0, text=False, env=env)
        assert self.process.stdin and self.process.stdout and self.process.stderr
        os.set_blocking(self.process.stdin.fileno(), False)
        # Read BOTH streams, otherwise either pipe can fill and deadlock.
        for name, stream in (("stdout", self.process.stdout), ("stderr", self.process.stderr)):
            import io
            wrapped = io.TextIOWrapper(stream, encoding="utf-8", errors="replace", line_buffering=True)
            t = threading.Thread(target=self._log_reader, args=(wrapped, name), daemon=True)
            t.start()
            self._threads.append(t)
        LOG.info("opening native AirPlay 2 realtime session to %s:%s target_queue=%dms",
                 s.host, s.port, s.latency_ms)

    def wait_clock(self) -> None:
        deadline = time.monotonic() + self.settings.clock_wait_s
        while time.monotonic() < deadline and not self.stop_event.is_set():
            if self.process is None or self.process.poll() is not None:
                raise RuntimeError("AirPlay sender exited before clock became ready")
            if self.route_reported.is_set() and not self.route_acceptable:
                raise RuntimeError("sender selected non-native or Buffered AirPlay; refusing fallback")
            if self.stalled.is_set():
                raise RuntimeError("receiver PTP clock stalled; check UDP 319/320 and nqptp conflict")
            if self.unexpected_ntp.is_set():
                raise RuntimeError("receiver fell back to NTP although AP2_TIMING=ptp; refusing unverified timing")
            if (self.clock_ready.is_set() and self.route_acceptable and self.connected.is_set()
                    and self.latency_reported.is_set()):
                if self.actual_queue_ms is None:
                    raise RuntimeError("sender latency status did not include effective warm_lead_ms")
                if self.actual_queue_ms > self.settings.max_effective_queue_ms:
                    raise RuntimeError(
                        f"receiver effective queue {self.actual_queue_ms}ms exceeds "
                        f"limit {self.settings.max_effective_queue_ms}ms")
                LOG.info("AirPlay receiver timing ready")
                return
            self.stop_event.wait(0.1)
        raise RuntimeError("AirPlay clock not ready; check receiver PTP/NTP logs")

    def send_command(self, payload: str, allow_shutdown: bool = False) -> None:
        with self._command_lock:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and (allow_shutdown or not self.stop_event.is_set()):
                try:
                    fd = os.open(self.cmd_path, os.O_WRONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
                    try:
                        data = payload.encode("ascii")
                        while data:
                            n = os.write(fd, data)
                            data = data[n:]
                    finally:
                        os.close(fd)
                    return
                except OSError as exc:
                    if exc.errno not in (errno.ENXIO, errno.ENOENT, errno.EAGAIN):
                        raise
                    self.stop_event.wait(0.05)
            raise RuntimeError("cliairplay never opened command FIFO")

    def send_pcm(self, packet: bytes) -> None:
        """Bounded whole-packet write; never silently advance audio after EAGAIN.

        On Linux a 5–40 ms packet can exceed PIPE_BUF, so partial writes are
        possible. Retrying preserves PCM frames; timing out aborts the session
        rather than replaying stale samples indefinitely.
        """
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("AirPlay sender not running")
        if self.process.poll() is not None:
            raise RuntimeError(f"AirPlay sender exited: {self.process.returncode}")
        fd = self.process.stdin.fileno()
        offset = 0
        deadline = time.monotonic() + self.settings.write_timeout_ms / 1000
        while offset < len(packet):
            if self.stop_event.is_set():
                return
            try:
                sent = os.write(fd, packet[offset:])
            except BlockingIOError:
                sent = 0
            except BrokenPipeError as exc:
                raise RuntimeError("AirPlay sender closed PCM input") from exc
            offset += sent
            self.sent_bytes += sent
            if offset == len(packet):
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    f"AirPlay stdin backpressure, only {offset}/{len(packet)} bytes sent "
                    f"within {self.settings.write_timeout_ms}ms; aborting to prevent stale PCM")
            select.select([], [fd], [], remaining)

    def wait_started(self) -> None:
        deadline = time.monotonic() + self.settings.start_wait_s
        while time.monotonic() < deadline and not self.stop_event.is_set():
            if self.process is None or self.process.poll() is not None:
                raise RuntimeError("AirPlay sender exited without START acknowledgement")
            if self.stalled.is_set():
                raise RuntimeError("receiver timing stalled while starting")
            if self.started.wait(0.05):
                assert self.started_at_unix_ms is not None
                LOG.info("AirPlay start confirmed by sender, target audible UTC ms=%d",
                         self.started_at_unix_ms)
                return
        raise RuntimeError("AirPlay sender failed to acknowledge START")

    def close(self) -> None:
        if self.process:
            if self.process.poll() is None and self.connected.is_set():
                try:
                    self.send_command("ACTION=DISCONNECT\n", allow_shutdown=True)
                except (OSError, RuntimeError):
                    pass
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
            for t in self._threads:
                t.join(timeout=1)
            for h in (self.process.stdin, self.process.stdout, self.process.stderr):
                if h and not h.closed:
                    try:
                        h.close()
                    except OSError:
                        pass
        try:
            self.cmd_path.unlink(missing_ok=True)
        except OSError:
            pass


def paced_stream(transport: AirPlayTransport, pcm: LivePcmBuffer,
                 stop: threading.Event, period_ms: int, seconds: float = 0.0) -> None:
    period = period_ms / 1000
    packet_bytes = bytes_for_ms(period_ms)
    next_tick = time.monotonic()
    deadline = next_tick + seconds if seconds else float("inf")
    last_report = next_tick
    while not stop.is_set() and time.monotonic() < deadline:
        now = time.monotonic()
        if now < next_tick:
            stop.wait(next_tick - now)
            continue
        # Avoid catch-up bursts that would replay audio captured in the past.
        if now - next_tick >= period:
            missed = int((now - next_tick) / period)
            transport.skipped_ticks += missed
            pcm.discard_oldest(missed * packet_bytes)
            next_tick += missed * period
        if transport.stalled.is_set():
            raise RuntimeError("AirPlay receiver clock stalled during active playback")
        packet = pcm.pull(packet_bytes)
        transport.send_pcm(packet)
        next_tick += period
        if now - last_report >= 10:
            LOG.info("queue=%.1fms dropped=%.1fms silence=%.1fms sender_skipped=%d",
                     pcm.depth_ms(), pcm.dropped_bytes / bytes_for_ms(1),
                     pcm.empty_bytes / bytes_for_ms(1), transport.skipped_ticks)
            last_report = now


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Low-queue USB ALSA to native AirPlay 2 Realtime")
    parser.add_argument("--check-config", action="store_true", help="validate settings, do not connect")
    parser.add_argument("--seconds", type=float, default=0.0,
                        help="run a limited number of seconds (integration testing)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings = Settings.from_env()
        if args.check_config:
            LOG.info("valid host=%s port=%d protocol=native-AP2-realtime target_queue=%dms capture=%s",
                     settings.host, settings.port, settings.latency_ms, settings.capture_device)
            return 0
        # Stale markers from an earlier process must not certify this session.
        (Path(settings.command_directory) / "ready.json").unlink(missing_ok=True)
        stop = threading.Event()
        def handle_stop(_signum, _frame):
            stop.set()
        signal.signal(signal.SIGTERM, handle_stop)
        signal.signal(signal.SIGINT, handle_stop)
        buffer = LivePcmBuffer(bytes_for_ms(settings.capture_buffer_ms))
        transport = AirPlayTransport(settings, stop)
        capture = UsbCapture(settings, buffer, stop)
        volume = UsbVolume(transport, stop)
        try:
            transport.start()
            transport.wait_clock()
            # One ALAC frame of silence makes the first START independently
            # of source activity; keep the AP2 session alive through USB idle.
            transport.send_pcm(bytes(bytes_for_ms(40)))
            transport.send_command("START_UNIX_MS=0\nACTION=START\n")
            transport.wait_started()
            # Only now advertise success to the mode-switch controller.
            ready_path = Path(settings.command_directory) / "ready.json"
            ready_path.write_text(json.dumps({
                "sender_started_at_unix_ms": transport.started_at_unix_ms,
                "effective_queue_ms": transport.actual_queue_ms,
                "queue_requested_ms": settings.latency_ms,
                "note": "sender acknowledgement only; not an acoustic latency measurement",
            }) + "\n")
            capture.start()
            volume.start()
            paced_stream(transport, buffer, stop, settings.period_ms, args.seconds)
        finally:
            stop.set()
            if capture.is_alive():
                capture.join(timeout=2)
            if volume.is_alive():
                volume.join(timeout=2)
            transport.close()
            (Path(settings.command_directory) / "ready.json").unlink(missing_ok=True)
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        LOG.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
