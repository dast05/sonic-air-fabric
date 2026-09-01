#!/usr/bin/env python3
"""Runs ON an isolated SONiC Air VM. Same payload via Air agent or Ansible."""
import argparse
import copy
import json
from pathlib import Path
import shutil
import subprocess
import time

MANAGED = ['INTERFACE','LOOPBACK_INTERFACE','BGP_NEIGHBOR','VLAN','VLAN_INTERFACE','VLAN_MEMBER','VXLAN_TUNNEL','VXLAN_TUNNEL_MAP','VXLAN_EVPN_NVO']

def run(*args):
    return subprocess.run(args,check=True,text=True,capture_output=True,timeout=240).stdout

def merge(base, overlay):
    result=copy.deepcopy(base)
    for table in MANAGED:
        result[table]=copy.deepcopy(overlay[table])
    result.setdefault('DEVICE_METADATA',{}).setdefault('localhost',{}).update(overlay['DEVICE_METADATA']['localhost'])
    # Retain hardware MAC, lanes, speed, buffers, management and credentials.
    for port, values in overlay['PORT'].items():
        if port not in result.get('PORT',{}):
            raise ValueError('Image does not contain expected SONiC port '+port)
        result['PORT'][port].update(values)
    return result

def preflight(overlay):
    for binary in ['sonic-cfggen','config','docker','ip']:
        if not shutil.which(binary):
            raise RuntimeError('Missing '+binary)
    base=json.loads(run('sonic-cfggen','-d','--print-data'))
    if base.get('PORTCHANNEL') or base.get('PORTCHANNEL_MEMBER'):
        raise RuntimeError('Existing PortChannels: use a clean lab image')
    merge(base,overlay)
    run('docker','exec','bgp','vtysh','-c','show version')
    # Reject profiles with another routing configuration owner.
    if base.get('DEVICE_METADATA',{}).get('localhost',{}).get('frr_mgmt_framework_config')=='true':
        raise RuntimeError('This image uses frr-mgmt-framework; select a compatible image/profile')
    return base

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('directory'); ap.add_argument('--preflight',action='store_true'); args=ap.parse_args()
    d=Path(args.directory); overlay=json.loads((d/'config_db.overlay.json').read_text())
    base=preflight(overlay)
    if args.preflight:
        print('PREFLIGHT_OK'); return
    merged=merge(base,overlay)
    db=Path('/etc/sonic/config_db.json'); frr=Path('/etc/sonic/frr/frr.conf')
    backup=Path('/var/lib/fabriclab/backups')/str(time.time_ns()); backup.mkdir(parents=True)
    # Snapshot running ConfigDB, not potentially stale startup config.
    (backup/'config_db.json').write_text(json.dumps(base,indent=2))
    if frr.exists(): shutil.copy2(frr,backup/'frr.conf')
    (backup/'metadata.json').write_text(json.dumps({'had_frr':frr.exists()}))
    frr.parent.mkdir(parents=True,exist_ok=True)
    db.write_text(json.dumps(merged,indent=2)+'\n'); shutil.copy2(d/'frr.conf',frr)
    try:
        run('config','reload','-y','-f')
    except Exception:
        shutil.copy2(backup/'config_db.json',db)
        if (backup/'frr.conf').exists(): shutil.copy2(backup/'frr.conf',frr)
        elif frr.exists(): frr.unlink()
        run('config','reload','-y','-f')
        raise
    print('APPLIED; backup='+str(backup))

if __name__=='__main__': main()
