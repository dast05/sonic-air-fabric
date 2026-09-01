"""Compile dedicated two-port TRex nodes into the fabric model."""
import ipaddress
import re
from .model import require, keys

def attach(m,p,t,topology,switches,trex_image):
    require(set(t['nodes'])=={'trex01','trex02'}, 'exactly trex01/trex02 required')
    require(set(t['ports'])=={'red','blue'} and set(m['networks'])=={'red','blue'}, 'TRex profile requires red/blue')
    require(type(t['pps']) is int and 1 <= t['pps'] <= 10000, 'TRex pps must be 1..10000 in Air')
    require(type(t['duration_seconds']) is int and 1 <= t['duration_seconds'] <= 60, 'duration must be 1..60')
    require(type(t['packet_size']) is int and 64 <= t['packet_size'] <= 1400, 'packet_size must be 64..1400')
    for resource in ['cpu','memory','storage']:
        require(type(t['resources'][resource]) is int and t['resources'][resource]>0, 'invalid TRex resources')
    require(t['ports']['red']['air']=='eth1' and t['ports']['blue']['air']=='eth2', 'TRex requires eth1/eth2 in this profile')
    lp=t['leaf_port']; keys(lp,['air','sonic'],'TRex leaf port')
    require(re.fullmatch(r'eth[1-9][0-9]*',lp['air']) and re.fullmatch(r'Ethernet[0-9]+',lp['sonic']), 'invalid TRex leaf port')
    require(all(lp[k] not in {x[k] for x in p['leaf_uplinks']+[p['leaf_access']]} for k in ['air','sonic']), 'TRex port collides with existing port')
    used=set(); macs=set()
    for name,node in t['nodes'].items():
        require(name not in topology['nodes'], 'duplicate node name')
        keys(node,['red','blue'],name)
        for net,a in node.items():
            keys(a,['leaf','address','mac'],name+'/'+net)
            require(a['leaf'] in m['leafs'] and a['leaf'] not in used, 'TRex attachment needs a unique leaf')
            used.add(a['leaf'])
            addr=ipaddress.IPv4Address(a['address']); subnet=ipaddress.ip_network(m['networks'][net]['subnet'])
            require(addr in subnet and addr not in [subnet.network_address,subnet.broadcast_address], 'invalid TRex IP')
            require(re.fullmatch(r'02(:[0-9a-f]{2}){5}',a['mac']) and a['mac'] not in macs, 'invalid/duplicate local TRex MAC')
            macs.add(a['mac'])
            switches[a['leaf']]['access'].append(dict(m['networks'][net],port=lp['sonic'],air=lp['air']))
            topology['links'].append([{'node':a['leaf'],'interface':lp['air']},{'node':name,'interface':t['ports'][net]['air']}])
        topology['nodes'][name]=dict(t['resources'],os=trex_image)
    return t
