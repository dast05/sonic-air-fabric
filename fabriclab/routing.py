import ipaddress as ip
import re
from .model import require, keys, NAME

def attach_router(m,p,r,topology,switches,image,traffic):
    keys(r,['name','resources','leaf_port','interfaces'],'routing')
    require(NAME.fullmatch(r['name']) and r['name'] not in topology['nodes'], 'invalid router name')
    require(set(r['interfaces'])==set(m['networks']), 'router must connect every network')
    lp=r['leaf_port']; keys(lp,['air','sonic'],'router leaf port')
    require(re.fullmatch(r'eth[1-9][0-9]*',lp['air']) and re.fullmatch(r'Ethernet[0-9]+',lp['sonic']), 'invalid router leaf port')
    taken=p['leaf_uplinks']+[p['leaf_access']]+([traffic['leaf_port']] if traffic else [])
    require(all(lp[k] not in {x[k] for x in taken} for k in ['air','sonic']), 'router port collision')
    leaves=set(); ports=set(); macs=set(); subnets=[]
    for net,a in r['interfaces'].items():
        keys(a,['leaf','air','address','mac'],'router interface')
        require(a['leaf'] in m['leafs'] and a['leaf'] not in leaves, 'duplicate/invalid router attachment')
        require(re.fullmatch(r'eth[1-9][0-9]*',a['air']) and a['air'] not in ports, 'invalid/duplicate router port')
        require(re.fullmatch(r'02(:[0-9a-f]{2}){5}',a['mac']) and a['mac'] not in macs, 'invalid router MAC')
        leaves.add(a['leaf']); ports.add(a['air']); macs.add(a['mac'])
        addr=ip.ip_interface(a['address']); subnet=ip.ip_network(m['networks'][net]['subnet'])
        require(addr.network==subnet and addr.ip not in [subnet.network_address,subnet.broadcast_address], 'router address outside subnet')
        require(not any(subnet.overlaps(s) for s in subnets), 'routed networks must not overlap')
        require(all(str(ip.ip_interface(h['address']).ip)!=str(addr.ip) for h in m['hosts'].values()), 'router/host IP collision')
        subnets.append(subnet)
        switches[a['leaf']]['access'].append(dict(m['networks'][net],port=lp['sonic'],air=lp['air']))
        topology['links'].append([{'node':a['leaf'],'interface':lp['air']},{'node':r['name'],'interface':a['air']}])
    topology['nodes'][r['name']]=dict(r['resources'],os=image)
