#!/usr/bin/env python3
"""Deterministic protocol-shell simulator, NOT an AirPlay packet emulator.

Accepts the subset of cliairplay's CLI/commands used by the bridge. Simulates
status transitions, stdin backpressure, and transparent PCM consumption.
"""
import os
import sys
import threading
import time
from pathlib import Path

if '--check' in sys.argv:
    if os.getenv('SIM_BAD_BINARY'):
        sys.exit(1)
    print('cliairplay fake check')
    sys.exit(0)

def setting(name, default=''):
    return os.environ.get(name, default)

def status(s, err=False):
    print(s, file=sys.stderr if err else sys.stdout, flush=True)

if setting('SIM_FAIL_IMMEDIATELY') == '1':
    sys.exit(17)

pipe = Path(sys.argv[sys.argv.index('--cmdpipe') + 1])
mode = setting('SIM_ROUTE', 'native')
status('[STATUS] route protocol=airplay2 flow=' + ('raop-compat' if mode == 'compat' else 'native')
       + ' timing=ptp buffered=' + ('1' if mode == 'buffered' else '0'))
status('[STATUS] connected', err=True)
queue = setting('SIM_QUEUE_MS', '300')
status('[STATUS] latency lead_ms=2000 device_min_frames=11025'
       + ' device_max_frames=88200 warm_lead_ms=' + queue)
clock_mode = setting('SIM_CLOCK', 'ready')
if clock_mode == 'ready':
    status('[STATUS] clock_ready mode=ptp state=ready streak_ms=4000', err=True)
elif clock_mode == 'ntp':
    status('[STATUS] clock_ready mode=ntp state=cold streak_ms=0', err=True)
elif clock_mode == 'stalled':
    status('[STATUS] clock_ready mode=ptp state=stalled streak_ms=0', err=True)
elif clock_mode == 'cold':
    status('[STATUS] clock_ready mode=ptp state=cold streak_ms=0', err=True)

if setting('SIM_STALL_AFTER_MS'):
    def stall():
        time.sleep(int(setting('SIM_STALL_AFTER_MS')) / 1000)
        status('[STATUS] clock_ready mode=ptp state=stalled streak_ms=0', err=True)
    threading.Thread(target=stall, daemon=True).start()

log = setting('FAKE_CMD_LOG')

def receive_commands():
    while True:
        try:
            with pipe.open('r') as file:
                chunk = file.read()
        except (FileNotFoundError, OSError):
            return
        if not chunk:
            time.sleep(.01)
            continue
        if log:
            with open(log, 'a') as file:
                file.write(chunk)
        if 'ACTION=START' in chunk and setting('SIM_NO_ACK') != '1':
            start_at = int(time.time() * 1000) + 250
            status('[STATUS] started requested_unix_ms=0 at_unix_ms=' + str(start_at), err=True)
        if 'ACTION=DISCONNECT' in chunk:
            return

threading.Thread(target=receive_commands, daemon=True).start()

out_path = setting('FAKE_PCM_FILE')
read_sleep = int(setting('SIM_READ_SLEEP_MS', '0')) / 1000
stop_read = setting('SIM_NEVER_READ') == '1'
if stop_read:
    while True:
        time.sleep(0.5)
else:
    file = open(out_path, 'wb') if out_path else None
    try:
        while True:
            data = sys.stdin.buffer.read1(8192)
            if not data:
                break
            if file:
                file.write(data)
            if read_sleep:
                time.sleep(read_sleep)
    finally:
        if file:
            file.close()
