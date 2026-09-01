#!/usr/bin/env python3
"""Install version-pinned TRex and native Linux AF_PACKET configuration."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request

VERSION='3.06'
URL=f'https://trex-tgn.cisco.com/trex/release/v{VERSION}.tar.gz'

def run(*args): subprocess.run(args,check=True,timeout=300)

def main():
    data=json.loads(Path(sys.argv[1]).read_text())
    osrelease=Path('/etc/os-release').read_text()
    if 'VERSION_ID="22.04"' not in osrelease:
        raise RuntimeError('TRex 3.06 profile requires Ubuntu 22.04/Python 3.10')
    home=Path('/opt/trex'); binary=home/f'v{VERSION}'/'t-rex-64'
    if not binary.exists():
        home.mkdir(parents=True,exist_ok=True)
        run('apt-get','update'); run('apt-get','install','-y','python3','python3-distutils','iproute2','ca-certificates','libnuma1')
        archive=home/f'v{VERSION}.tar.gz'
        with urllib.request.urlopen(URL,timeout=90) as source,archive.open('wb') as dest:
            shutil.copyfileobj(source,dest)
        digest=hashlib.file_digest(archive.open('rb'),'sha256').hexdigest() if hasattr(hashlib,'file_digest') else hashlib.sha256(archive.read_bytes()).hexdigest()
        (home/'archive.sha256').write_text(digest+'\n')
        if data.get('archive_sha256') and digest != data['archive_sha256']:
            raise RuntimeError('TRex archive SHA256 mismatch')
        with tarfile.open(archive) as tar:
            # Reject escapes even on Python versions without tar extraction filters.
            for member in tar.getmembers():
                target=(home/member.name).resolve()
                link=(target.parent/member.linkname).resolve() if member.issym() else (home/member.linkname).resolve()
                if not target.is_relative_to(home) or ((member.issym() or member.islnk()) and not link.is_relative_to(home)) or member.isdev():
                    raise RuntimeError('Unsafe archive entry; inspect release manually')
            tar.extractall(home)
    interface_commands=[]
    cfg=['- version: 2','  port_limit: 2',"  interfaces: ['eth1', 'eth2']",'  low_end: true','  low_end_core: 0','  port_info:']
    for net in ['red','blue']:
        a=data['traffic'][net]; dev='eth1' if net=='red' else 'eth2'
        run('ip','link','set',dev,'down'); run('ip','link','set',dev,'address',a['mac'])
        run('ip','addr','flush','dev',dev,'scope','global'); run('ip','link','set',dev,'up')
        interface_commands += [f'ip link set {dev} down',f"ip link set {dev} address {a['mac']}",f'ip addr flush dev {dev} scope global',f'ip link set {dev} up']
        cfg += [f"    - src_mac: '{a['mac']}'", f"      dest_mac: '{data['gateways'][net]['mac']}'"]
    Path('/etc/trex_cfg.yaml').write_text('\n'.join(cfg)+'\n')
    Path('/opt/trex/interfaces.sh').write_text('#!/bin/bash\nset -euo pipefail\n'+'\n'.join(interface_commands)+'\n')
    unit=f'''[Unit]
Description=TRex Air functional traffic generator
After=network-online.target
[Service]
Type=simple
WorkingDirectory={binary.parent}
ExecStartPre=/bin/bash /opt/trex/interfaces.sh
ExecStart={binary} -i --cfg /etc/trex_cfg.yaml --software -c 1 --no-scapy-server
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
'''
    Path('/etc/systemd/system/trex.service').write_text(unit)
    run('systemctl','daemon-reload'); run('systemctl','enable','trex'); run('systemctl','restart','trex')
    print('TREX_INSTALLED')

if __name__=='__main__': main()
