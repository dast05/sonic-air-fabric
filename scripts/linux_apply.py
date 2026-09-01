#!/usr/bin/env python3
"""Configure only lab data interfaces; preserve eth0 management/default route."""
import ipaddress as ip
import json
from pathlib import Path
import subprocess
import sys

def run(*args): subprocess.run(args,check=True,timeout=30)

def main():
    data=json.loads(Path(sys.argv[1]).read_text()); kind=data['expected']['kind']
    if kind=='host':
        e=data['expected']; run('ip','link','set','eth1','up')
        run('ip','address','flush','dev','eth1','scope','global')
        run('ip','address','add',e['address'],'dev','eth1')
        for addr in e['positive']:
            if ip.ip_address(addr) not in ip.ip_interface(e['address']).network:
                run('ip','route','replace',addr+'/32','via',e['gateway'],'dev','eth1')
    elif kind=='router':
        run('sysctl','-w','net.ipv4.ip_forward=1')
        for a in data['routing']['interfaces'].values():
            dev=a['air']; run('ip','link','set',dev,'down'); run('ip','link','set',dev,'address',a['mac'])
            run('ip','address','flush','dev',dev,'scope','global')
            run('ip','address','add',a['address'],'dev',dev); run('ip','link','set',dev,'up')
            run('sysctl','-w',f'net.ipv4.conf.{dev}.send_redirects=0')
        # Static neighbor entries let the router forward to stateless TRex ports,
        # which are not kernel IP endpoints and need not answer ARP.
        for node in data['trex_nodes'].values():
            for net,a in node.items():
                run('ip','neigh','replace',a['address'],'lladdr',a['mac'],'nud','permanent','dev',data['routing']['interfaces'][net]['air'])
    else: raise ValueError('unsupported Linux role')
    unit='[Unit]\nDescription=Fabric lab data interfaces\nAfter=network-online.target\n[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/usr/bin/python3 /var/lib/fabriclab/payload/linux_apply.py /var/lib/fabriclab/payload/node.json\n[Install]\nWantedBy=multi-user.target\n'
    Path('/etc/systemd/system/fabriclab-network.service').write_text(unit)
    run('systemctl','daemon-reload'); run('systemctl','enable','fabriclab-network')

if __name__=='__main__': main()
