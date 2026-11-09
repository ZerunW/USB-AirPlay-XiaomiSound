"""Portable tests; do not require a Pi, Xiaomi speaker or cliairplay."""
import importlib.util
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

FILE = Path(__file__).resolve().parents[1] / 'xsb_ap2_realtime.py'
spec = importlib.util.spec_from_file_location('xsb_ap2_realtime', FILE)
a = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = a
spec.loader.exec_module(a)


class BufferTests(unittest.TestCase):
    def test_capacity_discards_oldest_complete_frames(self):
        b = a.LivePcmBuffer(16)
        b.push(b'AAAABBBBCCCC')
        b.push(b'DDDDEEEE')
        self.assertEqual(b.dropped_bytes, 4)
        self.assertEqual(b.pull(16), b'BBBBCCCCDDDDEEEE')

    def test_empty_returns_silence(self):
        b = a.LivePcmBuffer(16)
        b.push(b'ABCD')
        self.assertEqual(b.pull(12), b'ABCD' + bytes(8))
        self.assertEqual(b.empty_bytes, 8)
        self.assertEqual(b.depth_ms(), 0)

    def test_largest_packet_keeps_latest_only(self):
        b = a.LivePcmBuffer(8)
        b.push(b'AAAABBBBCCCCDDDD')
        self.assertEqual(b.pull(8), b'CCCCDDDD')
        self.assertEqual(b.dropped_bytes, 8)

    def test_refuse_partial_frames(self):
        b = a.LivePcmBuffer(8)
        with self.assertRaises(ValueError):
            b.push(b'abc')
        with self.assertRaises(ValueError):
            b.pull(3)


class ConfigTests(unittest.TestCase):
    def test_invalid_host(self):
        with patch.dict(os.environ, {'AP2_HOST': 'CHANGE_ME_IP'}):
            with self.assertRaises(ValueError):
                a.Settings.from_env()

    def test_invalid_queue(self):
        with patch.dict(os.environ, {'AP2_HOST': '192.168.1.33', 'AP2_LATENCY_MS': '30'}):
            with self.assertRaises(ValueError):
                a.Settings.from_env()

    def test_native_type96_option(self):
        with patch.dict(os.environ, {'AP2_HOST': '192.168.1.33', 'AP2_LATENCY_MS': '300'}):
            s = a.Settings.from_env()
        t = a.AirPlayTransport(s, threading.Event())
        args = t.arguments()
        self.assertIn('--ap2-native', args)
        self.assertEqual(args[args.index('--latency') + 1], '300')
        self.assertEqual(args[args.index('--timing') + 1], 'ptp')

    def test_clock_status_patterns(self):
        self.assertIsNotNone(a.READY_RE.search('[STATUS] clock_ready mode=ptp state=ready streak_ms=2000'))
        self.assertIsNotNone(a.NTP_RE.search('[STATUS] clock_ready mode=ntp state=cold'))
        self.assertIsNotNone(a.STALLED_RE.search('[STATUS] clock_ready mode=ptp state=stalled'))


class VolumeTests(unittest.TestCase):
    def test_existing_macos_usb_volume_curve(self):
        self.assertEqual(a.gadget_to_percent(0), 0)
        self.assertEqual(a.gadget_to_percent(71), 50)
        self.assertEqual(a.gadget_to_percent(87), 75)
        self.assertEqual(a.gadget_to_percent(100), 100)
        self.assertEqual(a.gadget_to_percent(500), 100)

    def test_host_mute_sends_zero_then_restores_volume(self):
        class FakeTransport:
            def __init__(self):
                self.commands = []
            def send_command(self, command):
                self.commands.append(command)
        t = FakeTransport()
        monitor = a.UsbVolume(t, threading.Event())
        with patch.object(a, 'mixer_value', side_effect=['71', 'on', '71', 'off', '71', 'on']):
            monitor._sync()
            monitor._sync()
            monitor._sync()
        self.assertEqual(t.commands, ['VOLUME=50\n', 'VOLUME=0\n', 'VOLUME=50\n'])


FAKE = (Path(__file__).parent / 'fake_cliairplay.py').read_text()


class TransportIntegrationTests(unittest.TestCase):
    def test_fake_native_transport_lifecycle(self):
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td) / 'cliairplay'
            fake.write_text(FAKE)
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            log = Path(td) / 'cmdlog'
            env = {'AP2_HOST': '127.0.0.1', 'AP2_BINARY': str(fake),
                   'AP2_RUNTIME_DIR': str(Path(td) / 'runtime'),
                   'FAKE_CMD_LOG': str(log)}
            with patch.dict(os.environ, env):
                self.assertEqual(a.main(['--seconds', '0.15']), 0)
            cmds = log.read_text()
            self.assertIn('ACTION=START', cmds)
            self.assertIn('START_UNIX_MS=0', cmds)
            self.assertFalse((Path(td) / 'runtime' / 'commands').exists())


if __name__ == '__main__':
    unittest.main()
