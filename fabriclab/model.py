"""Validate intent and compile it to reproducible, platform-aware artifacts."""
import ipaddress as ip
import json
import re
from pathlib import Path
import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
NAME = re.compile(r'^[a-z][a-z0-9-]{0,31}$')

class UniqueLoader(yaml.SafeLoader):
    pass

def unique_mapping(loader, node, deep=False):
    out = {}
    for k, v in node.value:
        key = loader.construct_object(k, deep=deep)
        if key in out:
            raise ValueError(f'duplicate YAML key: {key}')
        out[key] = loader.construct_object(v, deep=deep)
    return out

UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)

def load(path):
    return yaml.load(Path(path).read_text(), Loader=UniqueLoader)

def require(ok, message):
    if not ok:
        raise ValueError(message)

def keys(obj, expected, where):
    require(isinstance(obj, dict) and set(obj) == set(expected), f'{where}: expected keys {expected}')

def validate(m, p):
    keys(m, ['name','underlay','spines','leafs','networks','hosts'], 'fabric')
    require(bool(NAME.fullmatch(m['name'])), 'invalid fabric name')
    keys(m['underlay'], ['link_pool','mtu'], 'underlay')
    pool = ip.IPv4Network(m['underlay']['link_pool'])
    require(pool.prefixlen <= 28, 'link_pool must fit eight /31 links')
    require(type(m['underlay']['mtu']) is int and 1600 <= m['underlay']['mtu'] <= 9100, 'invalid underlay MTU')
    require(len(m['spines']) == 2 and len(m['leafs']) == 4, 'v1 requires two spines and four leafs')
    names = list(m['spines']) + list(m['leafs']) + list(m['hosts'])
    require(len(set(names)) == len(names) and all(NAME.fullmatch(n) for n in names), 'invalid or duplicate node names')
    loops = []
    for name, n in (m['spines'] | m['leafs']).items():
        keys(n, ['asn','loopback'], name)
        require(type(n['asn']) is int and 64512 <= n['asn'] <= 65534, f'{name}: use private 16-bit ASN')
        addr = ip.IPv4Interface(n['loopback'])
        require(addr.network.prefixlen == 32 and addr.ip not in pool, f'{name}: invalid/overlapping loopback')
        loops.append(addr.ip)
    require(len(set(loops)) == 6, 'duplicate loopback')
    leaf_asns = [n['asn'] for n in m['leafs'].values()]
    spine_asns = {n['asn'] for n in m['spines'].values()}
    require(len(set(leaf_asns)) == 4 and len(spine_asns)==2 and not set(leaf_asns) & spine_asns, 'all switch ASNs must be unique for full loopback reachability')
    vlans, vnis = [], []
    for name, net in m['networks'].items():
        require(bool(NAME.fullmatch(name)), 'invalid network name')
        keys(net, ['vlan','vni','subnet'], name)
        require(type(net['vlan']) is int and 1 <= net['vlan'] <= 4094, 'invalid VLAN')
        require(type(net['vni']) is int and 1 <= net['vni'] <= 16777215, 'invalid VNI')
        subnet = ip.IPv4Network(net['subnet'])
        require(not subnet.overlaps(pool) and not any(a in subnet for a in loops), 'host subnet overlaps fabric')
        vlans.append(net['vlan']); vnis.append(net['vni'])
    require(len(set(vlans)) == len(vlans) and len(set(vnis)) == len(vnis), 'duplicate VLAN/VNI')
    require(len(m['hosts']) == 4, 'v1 requires four test hosts')
    used_leafs, addresses = set(), set()
    for name, h in m['hosts'].items():
        keys(h, ['leaf','network','address'], name)
        require(h['leaf'] in m['leafs'] and h['leaf'] not in used_leafs, 'one test host per leaf required')
        used_leafs.add(h['leaf'])
        require(h['network'] in m['networks'], 'unknown network')
        addr = ip.IPv4Interface(h['address']); subnet = ip.IPv4Network(m['networks'][h['network']]['subnet'])
        require(addr.network == subnet and addr.ip not in (subnet.network_address, subnet.broadcast_address), 'invalid host address')
        # Distinct addresses across test segments allow unambiguous negative tests.
        require(addr.ip not in addresses, 'duplicate test host IP')
        addresses.add(addr.ip)
    for net in m['networks']:
        require(sum(h['network'] == net for h in m['hosts'].values()) >= 2, 'each segment needs at least two hosts')
    require(p['profile'] == 'community-sonic-split-unified', 'unsupported platform profile')
    require(len(p['leaf_uplinks']) == 2 and len(p['spine_downlinks']) == 4, 'invalid port counts')
    for group in [p['leaf_uplinks'] + [p['leaf_access']], p['spine_downlinks']]:
        for port in group:
            keys(port, ['air','sonic'], 'port')
            require(re.fullmatch(r'eth[1-9][0-9]*', port['air']) is not None, 'invalid Air port')
            require(re.fullmatch(r'Ethernet[0-9]+', port['sonic']) is not None, 'invalid SONiC port')
        for key in ['air','sonic']:
            require(len({x[key] for x in group}) == len(group), 'duplicate port')
    for role in ['sonic','host']:
        keys(p[role], ['cpu','memory','storage'], role)
        require(all(type(v) is int and v > 0 for v in p[role].values()), 'invalid resources')
    return m

def compile_model(m, p, sonic_image=None, host_image=None, traffic=None, trex_image=None, routing=None):
    validate(m,p)
    switches = {}
    for role, group in [('spine',m['spines']),('leaf',m['leafs'])]:
        for name, n in sorted(group.items()):
            switches[name] = dict(n, name=name, role=role, router_id=str(ip.ip_interface(n['loopback']).ip), peers=[], ports={}, access=[])
    topology = {'oob':True, 'nodes':{}, 'links':[]}
    for name in switches:
        topology['nodes'][name] = dict(p['sonic'], os=sonic_image or p['sonic_image_hint'])
    subnets = ip.ip_network(m['underlay']['link_pool']).subnets(new_prefix=31)
    for li, leaf in enumerate(sorted(m['leafs'])):
        for si, spine in enumerate(sorted(m['spines'])):
            lp, sp = p['leaf_uplinks'][si], p['spine_downlinks'][li]
            subnet = next(subnets); sa, la = list(subnet)
            topology['links'].append([{'node':spine,'interface':sp['air']},{'node':leaf,'interface':lp['air']}])
            for local, remote, port, addr, peer in [(spine,leaf,sp,sa,la),(leaf,spine,lp,la,sa)]:
                switches[local]['peers'].append({'name':remote,'remote_ip':str(peer),'remote_asn':switches[remote]['asn'],'local_ip':str(addr),'port':port['sonic']})
                switches[local]['ports'][port['sonic']] = {'address':f'{addr}/31','air':port['air']}
    for name,h in sorted(m['hosts'].items()):
        topology['nodes'][name] = dict(p['host'], os=host_image or p['host_image_hint'])
        topology['links'].append([{'node':h['leaf'],'interface':p['leaf_access']['air']},{'node':name,'interface':'eth1'}])
        switches[h['leaf']]['access'].append(dict(m['networks'][h['network']], port=p['leaf_access']['sonic'], air=p['leaf_access']['air']))
    if traffic:
        from .traffic import attach
        attach(m,p,traffic,topology,switches,trex_image or 'generic/ubuntu2204')
    if routing:
        from .routing import attach_router
        attach_router(m,p,routing,topology,switches,host_image or p['host_image_hint'],traffic)
    env = Environment(loader=FileSystemLoader(ROOT/'templates'), undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    rendered = {}
    for name,s in switches.items():
        db = {t:{} for t in ['INTERFACE','LOOPBACK_INTERFACE','BGP_NEIGHBOR','VLAN','VLAN_INTERFACE','VLAN_MEMBER','VXLAN_TUNNEL','VXLAN_TUNNEL_MAP','VXLAN_EVPN_NVO']}
        db['DEVICE_METADATA'] = {'localhost':{'hostname':name,'bgp_asn':str(s['asn']),'type':'LeafRouter' if s['role']=='leaf' else 'SpineRouter','docker_routing_config_mode':'split-unified'}}
        db['PORT'] = {}
        for port,link in s['ports'].items():
            db['INTERFACE'][port] = {}; db['INTERFACE'][f"{port}|{link['address']}"] = {}
            db['PORT'][port] = {'admin_status':'up','mtu':str(m['underlay']['mtu'])}
        db['LOOPBACK_INTERFACE'] = {'Loopback0':{},f"Loopback0|{s['loopback']}":{}}
        for a in s['access']:
            vlan=f"Vlan{a['vlan']}"
            db['PORT'][a['port']] = {'admin_status':'up','mtu':'1500'}
            db['VLAN'].setdefault(vlan, {'vlanid':str(a['vlan']),'members':[]})['members'].append(a['port'])
            db['VLAN_INTERFACE'][vlan] = {}
            db['VLAN_MEMBER'][f"{vlan}|{a['port']}"] = {'tagging_mode':'untagged'}
            db['VXLAN_TUNNEL_MAP'][f"vtep|map_{a['vni']}_{vlan}"] = {'vlan':vlan,'vni':str(a['vni'])}
        if s['role']=='leaf':
            db['VXLAN_TUNNEL']={'vtep':{'src_ip':s['router_id']}}
            db['VXLAN_EVPN_NVO']={'nvo':{'source_vtep':'vtep'}}
        rendered[name] = {'config':db,'frr':env.get_template('frr.conf.j2').render(**s)+'\n', 'expected':{'kind':'sonic','name':name,'peers':s['peers'],'loopbacks':[x['router_id'] for k,x in switches.items() if k != name]}, 'air_ports':list({x['air'] for x in s['ports'].values()} | {x['air'] for x in s['access']})}
    for name,h in m['hosts'].items():
        expected={'kind':'host','name':name,'address':h['address'],'positive':[],'negative':[]}
        for other,oh in m['hosts'].items():
            if other != name:
                expected['positive' if routing or h['network']==oh['network'] else 'negative'].append(str(ip.ip_interface(oh['address']).ip))
        if routing:
            expected['gateway']=str(ip.ip_interface(routing['interfaces'][h['network']]['address']).ip)
        rendered[name]={'expected':expected}
    if traffic:
        for name,node in traffic['nodes'].items():
            rendered[name]={'expected':{'kind':'trex','name':name},'traffic':node}
    if routing:
        rendered[routing['name']]={'expected':{'kind':'router','name':routing['name']},'routing':routing}
    return topology, rendered

def render(model='models/fabric.yml', platform='models/platform.yml', out='build', sonic_image=None, host_image=None, traffic='models/traffic.yml', trex_image=None, routing='models/routing.yml'):
    m,p=load(model),load(platform)
    topology,nodes=compile_model(m,p,sonic_image,host_image,load(traffic) if traffic else None,trex_image,load(routing) if routing else None)
    dest=Path(out); dest.mkdir(parents=True,exist_ok=True)
    (dest/'topology.json').write_text(json.dumps(topology,indent=2)+'\n')
    (dest/'manifest.json').write_text(json.dumps({'name':m['name'],'profile':p['profile'],'nodes':nodes},indent=2)+'\n')
    for name,node in nodes.items():
        d=dest/name; d.mkdir(exist_ok=True)
        (d/'expected.json').write_text(json.dumps(node['expected'],indent=2)+'\n')
        if 'config' in node:
            (d/'config_db.overlay.json').write_text(json.dumps(node['config'],indent=2)+'\n')
            (d/'frr.conf').write_text(node['frr'])
    return topology,nodes
