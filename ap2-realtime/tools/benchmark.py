#!/usr/bin/env python3
"""Host-only 10-ms pacing and queue stress benchmark (not Pi CPU benchmark)."""
import importlib.util
import json
from pathlib import Path
import resource
import statistics
import sys
import threading
import time

SOURCE=Path(__file__).resolve().parents[1]/'xsb_ap2_realtime.py'
spec=importlib.util.spec_from_file_location('xsb_benchmark',SOURCE)
a=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=a
spec.loader.exec_module(a)

class Sink:
    def __init__(self,stall_interval=0,stall_ms=0):
        self.stalled=threading.Event()
        self.sent_bytes=0
        self.skipped_ticks=0
        self.times=[]
        self.stall_interval=stall_interval
        self.stall_ms=stall_ms
        self.count=0
    def send_pcm(self,packet):
        self.count+=1
        self.times.append(time.monotonic())
        self.sent_bytes+=len(packet)
        if self.stall_interval and self.count%self.stall_interval==0:
            time.sleep(self.stall_ms/1000)


def bench(seconds=4,stall_interval=0,stall_ms=0):
    pcm=a.LivePcmBuffer(a.bytes_for_ms(60))
    stop=threading.Event()
    def producer():
        tick=time.monotonic()
        while not stop.is_set():
            pcm.push(bytes(a.bytes_for_ms(10)))
            tick+=.01
            stop.wait(max(0,tick-time.monotonic()))
    t=threading.Thread(target=producer,daemon=True)
    sink=Sink(stall_interval,stall_ms)
    cpu0=time.process_time()
    t.start()
    try:
        a.paced_stream(sink,pcm,stop,10,seconds)
    finally:
        stop.set();t.join(timeout=1)
    elapsed_cpu=time.process_time()-cpu0
    intervals=[(b-a0)*1000 for a0,b in zip(sink.times,sink.times[1:])]
    intervals.sort()
    return dict(seconds=seconds,stall_every_n_packets=stall_interval,
                injected_stall_ms=stall_ms,sent_packets=sink.count,
                skipped_ticks=sink.skipped_ticks,
                cumulative_drop_ms=round(pcm.dropped_bytes/192,2),
                cumulative_silence_ms=round(pcm.empty_bytes/192,2),
                cpu_seconds=round(elapsed_cpu,3),
                host_rss_max_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                send_interval_p50_ms=round(statistics.median(intervals),3),
                send_interval_p95_ms=round(intervals[int(.95*(len(intervals)-1))],3),
                send_interval_max_ms=round(max(intervals),3))

def main():
    print(json.dumps({'type':'x86_host_scheduler_benchmark_not_pi_or_airplay_measurement',
                      'results':[bench(),bench(stall_interval=30,stall_ms=30)]},indent=2))
if __name__=='__main__': main()
