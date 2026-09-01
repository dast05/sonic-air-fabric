#!/usr/bin/env python3
"""Run on each TRex VM with a shared start time; collect per-stream RX proof."""
import json
from pathlib import Path
import sys
import time
from traffic_assert import check_stats

sys.path.insert(0,'/opt/trex/v3.06/automation/trex_control_plane/interactive')
import trex_stl_lib.api as stl

def main():
    cfg=json.loads(Path(sys.argv[1]).read_text()); client=stl.STLClient(server='127.0.0.1')
    report={'node':cfg['name'],'passed':False,'paths':[]}
    target=Path('/var/lib/fabriclab/reports'); target.mkdir(parents=True,exist_ok=True)
    try:
        client.connect(); client.reset(ports=[0,1])
        for port,net in enumerate(['red','blue']):
            other='blue' if net=='red' else 'red'
            packet=stl.Ether(src=cfg['local'][net]['mac'],dst=cfg['gateways'][net]['mac'])/stl.IP(src=cfg['local'][net]['address'],dst=cfg['remote'][other]['address'],ttl=64)/stl.UDP(sport=12345,dport=7000)
            packet=packet/stl.Raw(load=b'x'*max(0,cfg['packet_size']-4-len(packet)))
            client.add_streams(stl.STLStream(packet=stl.STLPktBuilder(pkt=packet),mode=stl.STLTXSingleBurst(total_pkts=cfg['pps']*cfg['duration_seconds'],pps=cfg['pps']),flow_stats=stl.STLFlowStats(pg_id=100+port)),ports=[port])
        client.clear_stats()
        delay=cfg['start_at']-time.time()
        if delay<0: raise RuntimeError('Missed shared start time; rerun both nodes')
        time.sleep(delay)
        client.start(ports=[0,1]); client.wait_on_traffic(ports=[0,1],timeout=cfg['duration_seconds']+20)
        time.sleep(5)
        stats=client.get_stats(); report['statistics']=stats
        # Each peer sends pg_id 100 from red into our blue port, and 101 vice versa.
        # Raw port counters alone are insufficient: require TRex stream matches.
        wanted=cfg['pps']*cfg['duration_seconds']
        report['paths']=check_stats(stats,wanted)
        report['passed']=True
    finally:
        (target/'trex.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
        client.disconnect()
    print(json.dumps(report))

if __name__=='__main__': main()
