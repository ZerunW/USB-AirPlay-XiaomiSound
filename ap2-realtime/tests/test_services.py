"""Mock systemctl integration tests, never touch host systemd."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
MODE = ROOT / 'ap2-realtime/xsb-ap2-mode'
SOURCE_SWITCH = ROOT / 'pi-files/usr/local/sbin/xsb-source-switch.sh'

MOCK_SYSTEMCTL = r'''#!/usr/bin/env bash
root="$FAKE_SYSTEMD_ROOT"
printf '%s\n' "$*" >> "$root/log"
operation="${1:-}"
case "$operation" in
  cat) [[ "${SIM_NO_UNIT:-0}" != 1 ]]; exit $? ;;
  is-active) unit="${@: -1}"; [[ -e "$root/$unit" ]]; exit $? ;;
  start)
    if [[ "${SIM_START_FAIL:-0}" == 1 && "$*" == *xsb-ap2-realtime.service* ]]; then exit 1; fi
    shift
    for unit in "$@"; do touch "$root/$unit"; done
    if [[ " $* " == *' xsb-ap2-realtime.service '* && "${SIM_NO_READY:-0}" != 1 ]]; then
      printf '%s\n' '{"sender_started_at_unix_ms":123,"effective_queue_ms":300}' > "$root/ready.json"
    fi
    exit 0 ;;
  stop)
    shift
    for unit in "$@"; do rm -f "$root/$unit"; done
    exit 0 ;;
esac
exit 0
'''


class ServiceTransitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        bin_dir = self.root/'bin'; bin_dir.mkdir()
        fake = bin_dir/'systemctl'; fake.write_text(MOCK_SYSTEMCTL); fake.chmod(0o755)
        (self.root/'config').write_text('AP2_HOST=192.168.1.20\n')
        mode_script = MODE.read_text().replace('/run/xsb-ap2-mode-previous-services',str(self.root/'previous'))
        mode_script = mode_script.replace('/run/xsb-ap2-mode-active',str(self.root/'guard'))
        mode_script = mode_script.replace('/run/xsb-ap2/ready.json',str(self.root/'ready.json'))
        mode_script = mode_script.replace('/etc/xsb/ap2-realtime.conf',str(self.root/'config'))
        # Avoid a real 30s wait in the negative test: mock script checks marker after 3 polls.
        mode_script = mode_script.replace('seq 1 300','seq 1 3')
        (self.root/'mode').write_text(mode_script)
        (self.root/'mode').chmod(0o755)
        self.env=os.environ.copy()
        self.env.update({'PATH':str(bin_dir)+os.pathsep+self.env['PATH'],
                         'FAKE_SYSTEMD_ROOT':str(self.root)})
        self.initial={'bluetooth.service','shairport-sync.service',
                      'xsb-rescue.service','xsb-usb-forward.service'}
        for unit in self.initial: (self.root/unit).touch()

    def run_mode(self,cmd,more=None):
        env=self.env.copy()
        if more: env.update(more)
        return subprocess.run([str(self.root/'mode'),cmd],env=env,
                              text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20)

    def active(self):
        return {p.name for p in self.root.glob('*.service')}

    def test_mode_start_stop_restores_exact_services(self):
        start=self.run_mode('start')
        self.assertEqual(start.returncode,0,start.stderr)
        self.assertTrue((self.root/'guard').exists())
        self.assertTrue((self.root/'ready.json').exists())
        self.assertEqual(self.active(),{'xsb-ap2-realtime.service','xsb-ap2-usb-forward.service'})
        stop=self.run_mode('stop')
        self.assertEqual(stop.returncode,0,stop.stderr)
        self.assertFalse((self.root/'guard').exists())
        self.assertEqual(self.active(),self.initial)
        self.assertFalse((self.root/'previous').exists())

    def test_mode_start_failure_rolls_back(self):
        result=self.run_mode('start',{'SIM_START_FAIL':'1'})
        self.assertEqual(result.returncode,1)
        self.assertEqual(self.active(),self.initial)
        self.assertFalse((self.root/'guard').exists())

    def test_mode_no_ready_rolls_back(self):
        result=self.run_mode('start',{'SIM_NO_READY':'1'})
        self.assertEqual(result.returncode,1)
        self.assertEqual(self.active(),self.initial)
        self.assertFalse((self.root/'guard').exists())

    def test_mode_refuses_placeholder_without_state_change(self):
        (self.root/'config').write_text('AP2_HOST=CHANGE_ME_IP\n')
        result=self.run_mode('start')
        self.assertEqual(result.returncode,1)
        self.assertEqual(self.active(),self.initial)

    def test_source_switch_guard_suppresses_legacy_forward(self):
        src=SOURCE_SWITCH.read_text().replace('/run/xsb-ap2-mode-active',str(self.root/'guard'))
        (self.root/'switch').write_text(src)
        (self.root/'switch').chmod(0o755)
        (self.root/'guard').touch()
        proc=subprocess.run([str(self.root/'switch'),'usb'],env=self.env,
                            text=True,capture_output=True,timeout=5)
        self.assertEqual(proc.returncode,0,proc.stderr)
        self.assertFalse((self.root/'log').exists())
        (self.root/'guard').unlink()
        proc=subprocess.run([str(self.root/'switch'),'usb'],env=self.env,
                            text=True,capture_output=True,timeout=5)
        self.assertEqual(proc.returncode,0)
        self.assertIn('start xsb-usb-forward.service',(self.root/'log').read_text())

    def test_unit_is_opt_in_and_requires_fast_usb(self):
        svc=(ROOT/'pi-files/etc/systemd/system/xsb-ap2-realtime.service').read_text()
        forward=(ROOT/'pi-files/etc/systemd/system/xsb-ap2-usb-forward.service').read_text()
        self.assertIn('Requires=xsb-gadget.service xsb-ap2-usb-forward.service',svc)
        self.assertIn('RuntimeDirectory=xsb-ap2',svc)
        self.assertNotIn('WantedBy=multi-user.target',svc)
        self.assertIn('USB_FORWARD_POLL_S=0.1',forward)
        self.assertIn('Conflicts=xsb-usb-forward.service',forward)

if __name__=='__main__': unittest.main()
