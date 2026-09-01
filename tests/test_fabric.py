import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fabriclab.model import ROOT, load, compile_model, render
from fabriclab.air import shell_script, wait_instructions, image_ids

@pytest.fixture
def inputs():
    return [load(ROOT/'models'/f) for f in ['fabric.yml','platform.yml','traffic.yml','routing.yml']]

def compiled(inputs):
    m,p,t,r=inputs
    return compile_model(m,p,traffic=t,routing=r)

def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/(name+'.py'))
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

def test_topology_and_all_peer_links(inputs):
    topology,nodes=compiled(inputs)
    assert len(topology['nodes'])==13 and len(topology['links'])==18
    endpoints=[(e['node'],e['interface']) for link in topology['links'] for e in link]
    assert len(endpoints)==len(set(endpoints))
    directed=0
    for name,n in nodes.items():
        if n['expected']['kind']!='sonic': continue
        peers=n['expected']['peers']; assert len(peers)==(4 if name.startswith('spine') else 2)
        for peer in peers:
            remote=nodes[peer['name']]['expected']['peers']
            assert any(p['name']==name and p['local_ip']==peer['remote_ip'] and p['remote_ip']==peer['local_ip'] for p in remote)
            directed+=1
    assert directed==16

def test_router_and_trex_path(inputs):
    _,n=compiled(inputs)
    for leaf,net in [('leaf01','red'),('leaf02','red'),('leaf03','blue'),('leaf04','blue')]:
        vid=100 if net=='red' else 200
        cfg=n[leaf]['config']
        assert cfg['VLAN_MEMBER'][f'Vlan{vid}|Ethernet4']['tagging_mode']=='untagged'
        assert cfg['VXLAN_TUNNEL_MAP'][f'vtep|map_{10000+vid}_Vlan{vid}']['vni']==str(10000+vid)
    assert 'Vlan100|Ethernet16' in n['leaf01']['config']['VLAN_MEMBER']
    assert 'Vlan200|Ethernet16' in n['leaf03']['config']['VLAN_MEMBER']
    assert n['server01']['expected']['gateway']=='192.168.100.1'
    assert '192.168.200.14' in n['server01']['expected']['positive']
    assert n['server01']['expected']['negative']==[]

@pytest.mark.parametrize('fault',['loopback','asn','vni','port','subnet','traffic-port','gateway','spine-asn'])
def test_reject_bad_design(inputs,fault):
    m,p,t,r=inputs
    if fault=='loopback': m['leafs']['leaf02']['loopback']=m['leafs']['leaf01']['loopback']
    if fault=='asn': m['leafs']['leaf02']['asn']=65000
    if fault=='vni': m['networks']['blue']['vni']=10100
    if fault=='port': p['leaf_uplinks'][1]=p['leaf_uplinks'][0]
    if fault=='subnet': m['hosts']['server01']['address']='10.0.0.1/24'
    if fault=='traffic-port': t['leaf_port']=p['leaf_access']
    if fault=='spine-asn': m['spines']['spine02']['asn']=65000
    if fault=='gateway': r['interfaces']['red']['address']='192.168.100.11/24'
    with pytest.raises(ValueError): compiled(inputs)

def test_duplicate_yaml_keys_rejected(tmp_path):
    p=tmp_path/'bad.yml'; p.write_text('name: one\nname: two\n')
    with pytest.raises(ValueError,match='duplicate YAML'): load(p)

def test_config_preserves_hardware_and_management(inputs):
    _,nodes=compiled(inputs); overlay=nodes['leaf01']['config']; apply=module('node_apply')
    base={'DEVICE_METADATA':{'localhost':{'mac':'ab:cd','platform':'kvm','hwsku':'sn2700'}},'PORT':{p:{'lanes':'1,2,3,4','speed':'40000'} for p in overlay['PORT']},'MGMT_INTERFACE':{'eth0|192.0.2.1/24':{}},'BUFFER_POOL':{'pool':{'size':'100'}},'VLAN':{'Vlan999':{'vlanid':'999'}}}
    merged=apply.merge(base,overlay)
    assert merged['MGMT_INTERFACE']==base['MGMT_INTERFACE']
    assert merged['BUFFER_POOL']==base['BUFFER_POOL']
    assert merged['DEVICE_METADATA']['localhost']['mac']=='ab:cd'
    assert merged['PORT']['Ethernet0']['lanes']=='1,2,3,4'
    assert 'Vlan999' not in merged['VLAN']
    assert base['VLAN']=={'Vlan999':{'vlanid':'999'}}
    del base['PORT']['Ethernet0']
    with pytest.raises(ValueError): apply.merge(base,overlay)

def test_bgp_checks_identity_not_just_count():
    check=module('node_verify').check_bgp
    expected=[{'remote_ip':'10.0.0.1','remote_asn':65000}]
    good={'peers':{'10.0.0.1':{'state':'Established','remoteAs':65000}}}
    check(good,expected)
    for bad in [{'peers':{}},{'peers':{'10.0.0.1':{'state':'Active','remoteAs':65000}}},{'peers':{'10.0.0.1':{'state':'Established','remoteAs':65001}}}]:
        with pytest.raises(ValueError): check(bad,expected)

def test_render_deterministic(inputs):
    assert compiled(inputs)==compiled(copy.deepcopy(inputs))

def test_air_failure_is_not_success():
    api=Mock(); api.node_instructions.get.return_value=SimpleNamespace(state='failed')
    with pytest.raises(RuntimeError): wait_instructions(api,[SimpleNamespace(id='1',name='configure')])

def test_air_requires_agent(monkeypatch):
    monkeypatch.setenv('AIR_SONIC_IMAGE','id')
    api=Mock(); api.images.get.return_value=SimpleNamespace(name='old-sonic',includes_air_agent=False)
    with pytest.raises(RuntimeError,match='lacks the Air agent'): image_ids(api)

def test_payload_does_not_interpolate_shell():
    script=shell_script({'data.json':'$(touch /tmp/should-not-exist)'},'true')
    assert '$(touch' not in script
    with pytest.raises(ValueError): shell_script({'../escape':'x'},'true')

def test_workflows_have_no_pr_secret_path():
    text=(ROOT/'.github/workflows/air-lab.yml').read_text()
    assert 'pull_request_target' not in text and 'pull_request:' not in text
    assert "github.ref == 'refs/heads/main'" in text
    assert 'cancel-in-progress: false' in text

def test_trex_requires_packets_on_opposite_vni_port():
    check=module('traffic_assert').check_stats
    good={'flow_stats':{100:{'tx_pkts':{0:1000},'rx_pkts':{1:1000}},101:{'tx_pkts':{1:1000},'rx_pkts':{0:1000}}}}
    assert len(check(good,1000))==2
    for broken in [
        {'flow_stats':{}},
        {'flow_stats':{100:{'tx_pkts':{0:1000},'rx_pkts':{0:1000}}}},
        {'flow_stats':{100:{'tx_pkts':{0:1000},'rx_pkts':{1:100}}}},
    ]:
        with pytest.raises(ValueError): check(broken,1000)
