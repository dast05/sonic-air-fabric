#!/usr/bin/env python3
"""Checks real BGP peer sets and forwarding; failures propagate to CI."""
import json
from pathlib import Path
import subprocess
import sys
import time

def command(*argv):
    return subprocess.run(argv,text=True,capture_output=True,timeout=15)

def check_bgp(data,expected):
    peers=data.get('peers')
    if not isinstance(peers,dict):
        raise ValueError('FRR JSON lacks peers map')
    if set(peers) != {p['remote_ip'] for p in expected}:
        raise ValueError('BGP peer set differs from intent')
    for p in expected:
        state=peers[p['remote_ip']]
        if state.get('state')!='Established' or int(state.get('remoteAs',-1))!=p['remote_asn']:
            raise ValueError('BGP state/ASN mismatch for '+p['remote_ip'])

def verify(e):
    if e['kind']=='sonic':
        for family in ['ipv4 unicast','l2vpn evpn']:
            r=command('docker','exec','bgp','vtysh','-c',f'show bgp {family} summary json')
            if r.returncode: raise ValueError('FRR query failed')
            data=json.loads(r.stdout)
            # FRR versions may wrap AF data in the AF name.
            key='ipv4Unicast' if family.startswith('ipv4') else 'l2VpnEvpn'
            check_bgp(data.get(key,data),e['peers'])
        targets=e['loopbacks']; source='Loopback0'
    else:
        targets=e['positive']; source='eth1'
    for addr in targets:
        if command('ping','-I',source,'-c','2','-W','2',addr).returncode:
            raise ValueError('Unreachable '+addr)
    if e['kind']=='host':
        # Only validate negatives after proving positive connectivity.
        for addr in e['negative']:
            r=command('ping','-I','eth1','-c','2','-W','2',addr)
            if r.returncode==0: raise ValueError('Isolation failure: '+addr)
            if r.returncode!=1: raise ValueError('Negative ping failed to execute: '+r.stderr)
    return True

def main():
    e=json.loads(Path(sys.argv[1]).read_text()); deadline=time.monotonic()+180
    while True:
        try:
            verify(e); print(json.dumps({'node':e['name'],'passed':True})); return
        except (ValueError,subprocess.TimeoutExpired) as err:
            if time.monotonic()>deadline: raise RuntimeError(str(err)) from err
            time.sleep(5)

if __name__=='__main__': main()
