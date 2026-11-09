#!/usr/bin/env python3
"""Offline PCM drift/jitter + hypothetical receiver buffering experiments.

Models only the defined capture/queue semantics; it does not emulate Apple's
HAP, AirPlay RTSP, ALAC encoding, PTP servo or Xiaomi firmware behavior.
Results are repeatable and are not acoustic measurements.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import math
from pathlib import Path
import random
import statistics
import sys

BRIDGE = Path(__file__).resolve().parents[1] / 'xsb_ap2_realtime.py'
spec = importlib.util.spec_from_file_location('ap2_sim_bridge', BRIDGE)
bridge = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bridge
spec.loader.exec_module(bridge)


def simulate_source(*, minutes=10, ppm=0, jitter_ms=0, outage_ms=0, seed=1):
    """10ms virtual ticks: a source oscillator, an ALSA read, bounded PCM ring."""
    rng = random.Random(seed)
    length = int(minutes*6000)
    q = bridge.LivePcmBuffer(bridge.bytes_for_ms(60))
    fractional = 0.0
    hold = []
    depths = []
    stop_at = length//3
    outage = int(outage_ms / 10)
    # prefill 10ms to isolate normal scheduler startup from steady state.
    q.push(bytes(bridge.bytes_for_ms(10)))
    for tick in range(length):
        frames_float = 480 * (1 + ppm / 1e6) + fractional
        frames = int(frames_float)
        fractional = frames_float - frames
        data_len = frames * bridge.FRAME_BYTES
        # Source activity may be delayed by software scheduling jitter.
        # Delayed packets remain pending, but are emitted as a batch at arrival.
        jitter_ticks = round(rng.uniform(0,jitter_ms) / 10)
        delivery = tick+jitter_ticks
        if outage and stop_at <= tick < stop_at + outage:
            delivery = stop_at + outage
        hold.append((delivery,data_len))
        if hold:
            pending = []
            for when, byte_count in hold:
                if when <= tick:
                    q.push(bytes(byte_count))
                else:
                    pending.append((when, byte_count))
            hold = pending
        q.pull(bridge.bytes_for_ms(10))
        if tick % 10 == 0:
            depths.append(q.depth_ms())
    return {
        'duration_minutes':minutes,'ppm':ppm,'jitter_ms':jitter_ms,
        'outage_ms':outage_ms,'buffer_capacity_ms':60,
        'pcm_discard_ms':round(q.dropped_bytes/192,3),
        'pcm_silence_ms':round(q.empty_bytes/192,3),
        'queue_mean_ms':round(statistics.mean(depths),3),
        'queue_p95_ms':round(sorted(depths)[int(.95*(len(depths)-1))],3),
        'queue_max_ms':round(max(depths),3),
    }


def simulate_network(queue_ms, outage_ms):
    """Idealized RTP queue with no retransmit latency, integer 10ms ticks.

    Assume network feeding is scheduled 1:1 with playtime and can refill
    missing output up to 2x after loss. Timebase and ALAC receiver behavior
    are NOT modeled, so only a capacity thought experiment.
    """
    q=queue_ms
    rebuffering_ms=0
    for step in range(1500):
        lost = 300 <= step < 300+outage_ms//10
        received = 0 if lost else (20 if q<queue_ms else 10)
        q = min(queue_ms,q+received)
        if q >= 10:
            q -= 10
        else:
            rebuffering_ms += 10
            q = 0
    return {'ideal_queue_ms':queue_ms,'network_pause_ms':outage_ms,
            'modeled_underrun_ms':rebuffering_ms}



def source_detection_probability(duration_ms, poll_ms, seed=1, trials=20000):
    """Only probability a periodic poll intersects the active USB window.

    Does NOT imply the audio was delivered: forwarding startup consumes time.
    """
    rng=random.Random(seed)
    detected=sum(1 for _ in range(trials) if rng.random()*poll_ms < duration_ms)
    return {'notification_ms':duration_ms,'poll_ms':poll_ms,
            'observed_at_least_once_probability':round(detected/trials,4)}


def latency_budget(seed=1, runs=20000, queue_ms=300, receiver_dsp_ms=30, usb_forward_ms=20):
    rng=random.Random(seed)
    warm=[]; cold=[]
    for _ in range(runs):
        # Delays below are assumptions, not measurements.
        pcm_and_encoding=rng.uniform(10,45)
        warm.append(queue_ms+usb_forward_ms+pcm_and_encoding+receiver_dsp_ms)
        cold.append(queue_ms+usb_forward_ms+pcm_and_encoding+receiver_dsp_ms
                    +rng.uniform(0,100) # fast forwarder's 100ms poll
                    +rng.uniform(0,100) # arecord active-rate poll
                    +rng.uniform(5,35)) # ALSA startup latency
    def summ(x):
        x=sorted(x)
        return {'p50_ms':round(x[len(x)//2],1),'p95_ms':round(x[int(.95*len(x))],1),
                'min_ms':round(x[0],1),'max_ms':round(x[-1],1)}
    return {'assumptions':{'queue_ms':queue_ms,'receiver_dsp_ms':receiver_dsp_ms,'usb_forward_ms':usb_forward_ms,
                          'other_pipeline_ms_range':[10,45],
                          'fast_usb_poll_ms':100,'alsa_poll_ms':100},
            'warm':summ(warm),'from_idle':summ(cold)}


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--minutes',type=float,default=10)
    p.add_argument('--seed',type=int,default=20261109)
    p.add_argument('--output',default='-')
    a=p.parse_args(argv)
    scenarios=[
        dict(ppm=0,jitter_ms=0),
        dict(ppm=100,jitter_ms=0),
        dict(ppm=-100,jitter_ms=0),
        dict(ppm=500,jitter_ms=0),
        dict(ppm=-500,jitter_ms=0),
        dict(ppm=0,jitter_ms=20),
        dict(ppm=0,jitter_ms=50),
        dict(ppm=0,jitter_ms=0,outage_ms=100),
        dict(ppm=0,jitter_ms=0,outage_ms=500),
    ]
    out={'type':'offline_mathematical_simulation_not_hardware_measurement',
         'source':[simulate_source(minutes=a.minutes,seed=a.seed,**sc) for sc in scenarios],
         'ideal_network':[simulate_network(q,o) for q in (250,300,500,600)
                          for o in (100,200,400,800)],
         'latency_budget':[latency_budget(queue_ms=q,seed=a.seed) for q in (250,300,600,2000)],
         'usb_detection':[source_detection_probability(d,p,a.seed)
                          for p in (1000,100) for d in (20,50,100,250,1000)]}
    s=json.dumps(out,ensure_ascii=False,indent=2)
    if a.output=='-': print(s)
    else: Path(a.output).write_text(s+'\n')
    return 0
if __name__=='__main__': raise SystemExit(main())
