"""Fault injection and executable integration tests for the AP2 bridge."""
import importlib.util
import json
import os
from pathlib import Path
import random
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'xsb_ap2_realtime.py'
spec = importlib.util.spec_from_file_location('xsb_ap2_realtime_faults', SOURCE)
a = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = a
spec.loader.exec_module(a)
FAKE = (Path(__file__).parent / 'fake_cliairplay.py').read_text()


def simulator_env(td, overrides=None):
    exe = Path(td) / 'cliairplay'
    exe.write_text(FAKE)
    exe.chmod(0o755)
    env = {'AP2_HOST': '127.0.0.1', 'AP2_BINARY': str(exe),
           'AP2_RUNTIME_DIR': str(Path(td) / 'runtime'),
           'AP2_MAX_EFFECTIVE_QUEUE_MS': '500',
           'AP2_CLOCK_WAIT_S': '3',
           'AP2_START_WAIT_S': '2',
           'AP2_WRITE_TIMEOUT_MS': '8',
           'FAKE_CMD_LOG': str(Path(td) / 'command.log')}
    if overrides:
        env.update(overrides)
    return env


class ClockProtocolFaults(unittest.TestCase):
    def run_bridge(self, mode, expected, seconds='0.2'):
        with tempfile.TemporaryDirectory() as td:
            env = simulator_env(td, mode)
            with patch.dict(os.environ, env):
                result = a.main(['--seconds', seconds])
            self.assertEqual(result, expected, mode)
            return (Path(td) / 'command.log').read_text() if (Path(td) / 'command.log').exists() else ''

    def test_route_native_sends_pcm(self):
        cmds = self.run_bridge({}, 0)
        self.assertIn('ACTION=START', cmds)

    def test_raop_fallback_rejected(self):
        self.run_bridge({'SIM_ROUTE': 'compat'}, 1)

    def test_buffered_fallback_rejected(self):
        self.run_bridge({'SIM_ROUTE': 'buffered'}, 1)

    def test_queue_clamped_over_limit_rejected(self):
        self.run_bridge({'SIM_QUEUE_MS': '950'}, 1)

    def test_queue_at_limit_accepted(self):
        self.run_bridge({'SIM_QUEUE_MS': '500'}, 0)

    def test_unexpected_ntp_fallback_rejected(self):
        self.run_bridge({'SIM_CLOCK': 'ntp'}, 1)

    def test_initial_ptp_stalled_rejected(self):
        self.run_bridge({'SIM_CLOCK': 'stalled'}, 1)

    def test_ptp_stall_during_stream_rejected(self):
        self.run_bridge({'SIM_STALL_AFTER_MS': '200'}, 1, seconds='0.6')

    def test_sender_exits_before_clock(self):
        self.run_bridge({'SIM_FAIL_IMMEDIATELY': '1'}, 1)

    def test_missing_start_ack_rejected(self):
        self.run_bridge({'SIM_NO_ACK': '1'}, 1)

    def test_blocked_sender_pcm_aborts_not_falls_behind(self):
        # Popen stdin is a Linux pipe; a consumer that does not read from it
        # must trigger bounded backpressure rather than unbounded queued audio.
        self.run_bridge({'SIM_NEVER_READ': '1'}, 1, seconds='0.5')


class PcmBufferStress(unittest.TestCase):
    def test_randomized_reference_model(self):
        rng = random.Random(20261109)
        b = a.LivePcmBuffer(96)
        expected = bytearray()
        dropped = 0
        silence = 0
        for _ in range(50000):
            op = rng.randrange(4)
            frames = rng.randrange(0, 40)
            size = frames * 4
            if op in (0, 1):
                data = rng.randbytes(size)
                b.push(data)
                expected.extend(data)
                if len(expected) > 96:
                    count = len(expected) - 96
                    del expected[:count]
                    dropped += count
            elif op == 2:
                output = b.pull(size)
                actual = bytes(expected[:size])
                del expected[:size]
                silence += size - len(actual)
                self.assertEqual(output, actual + bytes(size-len(actual)))
            else:
                count = min(len(expected), size)
                discarded = b.discard_oldest(size)
                self.assertEqual(discarded, count)
                del expected[:count]
                dropped += count
            self.assertTrue(b.depth_ms() <= 96 / 192)
        self.assertEqual(b.dropped_bytes, dropped)
        self.assertEqual(b.empty_bytes, silence)

    def test_threaded_pcm_stress(self):
        b = a.LivePcmBuffer(a.bytes_for_ms(60))
        failures = []
        def worker(seed):
            rng = random.Random(seed)
            try:
                for i in range(20000):
                    frames = rng.randint(0, 480)
                    b.push(bytes([seed]) * (frames * 4))
                    if i % 3 == 0:
                        b.pull(rng.randint(0, 480) * 4)
            except Exception as e:
                failures.append(e)
        threads = [threading.Thread(target=worker, args=(i+1,)) for i in range(3)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertFalse(failures, failures)
        self.assertGreaterEqual(b.dropped_bytes, 0)
        self.assertTrue(0 <= b.depth_ms() <= 60)

    def test_missed_tick_discards_stale_pcm(self):
        b = a.LivePcmBuffer(a.bytes_for_ms(60))
        b.push(b'\x01' * a.bytes_for_ms(30))
        self.assertEqual(b.discard_oldest(a.bytes_for_ms(20)), a.bytes_for_ms(20))
        self.assertEqual(b.pull(a.bytes_for_ms(10)), b'\x01' * a.bytes_for_ms(10))
        self.assertEqual(b.dropped_bytes, a.bytes_for_ms(20))

    def test_invalid_partial_discard(self):
        b = a.LivePcmBuffer(12)
        with self.assertRaises(ValueError):
            b.discard_oldest(3)


class ConfigBoundaryTests(unittest.TestCase):
    def test_out_of_range_validation(self):
        for key, value in [('AP2_PORT', '0'), ('AP2_PERIOD_MS', '4'),
                           ('AP2_CAPTURE_BUFFER_MS', '10'), ('AP2_LATENCY_MS', '0'),
                           ('AP2_WRITE_TIMEOUT_MS', '0'), ('AP2_START_WAIT_S', '1'),
                           ('AP2_MAX_EFFECTIVE_QUEUE_MS', '30000')]:
            with self.subTest(key=key), patch.dict(os.environ, {'AP2_HOST': '127.0.0.1', key: value}):
                with self.assertRaises(ValueError): a.Settings.from_env()

    def test_period_adapts_alsa(self):
        from unittest.mock import MagicMock
        with patch('subprocess.Popen') as popen:
            popen.return_value = MagicMock()
            s = a.Settings('127.0.0.1', period_ms=20)
            t = a.UsbCapture(s, a.LivePcmBuffer(a.bytes_for_ms(60)), threading.Event())
            t._start()
            call_args = popen.call_args.args[0]
            self.assertIn('--period-time=20000', call_args)
            self.assertIn('--buffer-time=80000', call_args)


class SyntheticAlsaFlow(unittest.TestCase):
    def test_fake_usb_capture_to_fake_ap2_pcm(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            env = simulator_env(td)
            executables = td / 'bin'
            executables.mkdir()
            amixer = executables / 'amixer'
            amixer.write_text("""#!/usr/bin/env python3
import sys
q = ' '.join(sys.argv)
if 'Capture Rate' in q: print('  : values=48000')
elif 'PCM Capture Volume' in q: print('  : values=71')
elif 'PCM Capture Switch' in q: print('  : values=on')
else: print('  : values=0')
""")
            amixer.chmod(0o755)
            capture = executables / 'arecord'
            capture.write_text("""#!/usr/bin/env python3
import sys,time,struct
p=sys.stdout.buffer
for tick in range(600):
    # L=positive / R=negative, wave varies by tick to detect frozen PCM.
    sample=2000+(tick%100)
    p.write(struct.pack('<hh',sample,-sample)*480)
    p.flush()
    time.sleep(.01)
""")
            capture.chmod(0o755)
            env.update({'PATH': str(executables) + os.pathsep + os.environ.get('PATH',''),
                        'FAKE_PCM_FILE': str(td / 'played.raw')})
            with patch.dict(os.environ, env):
                code = a.main(['--seconds', '1.8'])
            self.assertEqual(code, 0)
            data = (td/'played.raw').read_bytes()
            self.assertGreater(len(data), a.bytes_for_ms(400))
            self.assertEqual(len(data)%4, 0)
            self.assertIn(b'\xd0\x07\x30\xf8', data)  # +2000, -2000 stereo
            self.assertIn(b'\xd1\x07\x2f\xf8', data)  # +2001, -2001 stereo
            cmds=(td/'command.log').read_text()
            self.assertIn('VOLUME=50',cmds)
            self.assertIn('ACTION=START',cmds)


if __name__ == '__main__': unittest.main()
