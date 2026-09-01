"""NVIDIA DSX Air v3 orchestration using the pinned official SDK."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import time
from urllib.parse import urlparse
import urllib.request
from .model import ROOT, load, render

OWNER='sonic-air-fabric/v1'

def api_client():
    from air_sdk import AirApi
    key=os.environ.get('NGC_API_KEY')
    if not key: raise RuntimeError('NGC_API_KEY is missing; configure the air-lab GitHub Environment secret')
    return AirApi.with_api_key(api_key=key)

def discover(api):
    out=[]
    for im in api.images.list():
        if any(x in im.name.lower() for x in ['sonic','ubuntu']):
            out.append({'id':im.id,'name':im.name,'version':im.version,'includes_air_agent':im.includes_air_agent,'default_username':im.default_username,'minimum_resources':str(im.minimum_resources)})
    Path('build').mkdir(exist_ok=True)
    Path('build/images.json').write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps(out,indent=2))

def image_ids(api,require_agent=True):
    result={}
    for env,key in [('AIR_SONIC_IMAGE','sonic_image'),('AIR_HOST_IMAGE','host_image'),('AIR_TREX_IMAGE','trex_image')]:
        value=os.environ.get(env)
        if not value: raise RuntimeError(f'{env} is missing. Run discover and set an exact image UUID.')
        im=api.images.get(value)
        if require_agent and not im.includes_air_agent:
            raise RuntimeError(f'{im.name} lacks the Air agent. Use provision + documented Ansible/SSH transport, or select an agent-enabled image.')
        result[key]=str(im.id)
    return result

def provision(api,*,require_agent=True):
    topology,nodes=render(**image_ids(api,require_agent))
    manifest=json.loads(Path('build/manifest.json').read_text())
    digest=hashlib.sha256(json.dumps(topology,sort_keys=True).encode()).hexdigest()
    owner_repo=os.environ.get('GITHUB_REPOSITORY','local')
    matches=[s for s in api.simulations.list(name=manifest['name']) if s.name==manifest['name']]
    if len(matches)>1: raise RuntimeError('Multiple simulations with target name; select/rename explicitly')
    if matches:
        sim=matches[0]
        metadata=json.loads(sim.metadata or '{}')
        if metadata.get('owner')!=OWNER or metadata.get('repository')!=owner_repo:
            raise RuntimeError('Existing simulation is not owned by this repo; choose another name')
        if metadata.get('topology_sha256')!=digest:
            raise RuntimeError('Topology or image changed. Change fabric.name to create a separate lab; no destructive rebuild is automatic.')
    else:
        sim=api.simulations.import_from_data(format='JSON',name=manifest['name'],content=topology)
        # Save ID immediately so an interrupted import remains identifiable.
        Path('build/simulation.json').write_text(json.dumps({'id':str(sim.id),'name':manifest['name']})+'\n')
        sim.wait_for_state('INACTIVE',timeout=timedelta(minutes=10),error_states='INVALID')
        sim.update(metadata=json.dumps({'owner':OWNER,'repository':owner_repo,'topology_sha256':digest}))
    Path('build/simulation.json').write_text(json.dumps({'id':str(sim.id),'name':sim.name},indent=2)+'\n')
    sim.set_sleep_time(datetime.now(timezone.utc)+timedelta(hours=4))
    if sim.state=='INACTIVE': sim.start()
    sim.wait_for_state('ACTIVE',timeout=timedelta(minutes=15),error_states=['INVALID','DELETING'])
    actual={n.name:n for n in sim.nodes.list()}
    if not set(nodes)<=set(actual): raise RuntimeError('Air did not create all intended nodes')
    return sim,nodes,actual

def shell_script(files,command):
    lines=['#!/bin/bash','set -euo pipefail','install -d -m 700 /var/lib/fabriclab/payload']
    for name,content in files.items():
        if '/' in name or name.startswith('.'): raise ValueError('invalid payload filename')
        data=base64.b64encode(content.encode()).decode()
        lines.append(f"printf '%s' {shlex.quote(data)} | base64 -d > /var/lib/fabriclab/payload/{name}")
    lines.append(command)
    return '\n'.join(lines)+'\n'

def submit(api,node,label,files,command):
    return api.node_instructions.create(node=node.id,name=label,executor='shell',data={'commands':[shell_script(files,command)]},run_again_on_rebuild=False)

def wait_instructions(api,instructions,timeout=900):
    deadline=time.monotonic()+timeout; pending={str(i.id):i.name for i in instructions}; results=[]
    while pending:
        for ident,name in list(pending.items()):
            inst=api.node_instructions.get(ident); state=inst.state.lower()
            if state in {'failed','cancelled','canceled','error'}:
                raise RuntimeError(f'{name} failed ({ident}); inspect node instruction in Air')
            if state=='completed':
                results.append({'instruction':name,'id':ident,'state':state}); del pending[ident]
        if time.monotonic()>deadline: raise TimeoutError('Air instructions timed out: '+', '.join(pending.values()))
        if pending: time.sleep(5)
    return results

def payload(name, data):
    files={'node.json':json.dumps(data),'expected.json':json.dumps(data['expected'])}
    if data['expected']['kind']=='sonic':
        files.update({'config_db.overlay.json':json.dumps(data['config']),'frr.conf':data['frr'],'node_apply.py':(ROOT/'scripts/node_apply.py').read_text()})
    files['node_verify.py']=(ROOT/'scripts/node_verify.py').read_text()
    files['linux_apply.py']=(ROOT/'scripts/linux_apply.py').read_text()
    return files

def deploy(api):
    sim,nodes,actual=provision(api)
    t,r=load('models/traffic.yml'),load('models/routing.yml')
    runid=os.environ.get('GITHUB_RUN_ID',str(time.time_ns()))
    report={'simulation_id':str(sim.id),'run':runid,'passed':False,'stages':{}}
    try:
        pending=[]
        for name,data in nodes.items():
            kind=data['expected']['kind']
            if kind=='sonic': cmd='python3 /var/lib/fabriclab/payload/node_apply.py /var/lib/fabriclab/payload --preflight'
            elif kind=='trex': cmd="python3 -c 'import sys; assert sys.version_info[:2] == (3,10), \"TRex profile requires Python 3.10 / Ubuntu 22.04\"'"
            else: cmd='command -v python3 && command -v ip && command -v ping'
            pending.append(submit(api,actual[name],f'{runid}-preflight-{name}',payload(name,data),cmd))
        report['stages']['preflight']=wait_instructions(api,pending)
        pending=[]
        for name,data in nodes.items():
            kind=data['expected']['kind']
            if kind=='trex': continue
            if kind=='router': data['trex_nodes']=t['nodes']
            if kind=='sonic':
                # Match NVIDIA's virtual image outer-NIC MTU requirement; leave
                # management and all filtering rules untouched.
                cmd='\n'.join('ip link set dev '+shlex.quote(dev)+' mtu 9238' for dev in data['air_ports'])
                cmd+='\npython3 /var/lib/fabriclab/payload/node_apply.py /var/lib/fabriclab/payload'
            else: cmd='python3 /var/lib/fabriclab/payload/linux_apply.py /var/lib/fabriclab/payload/node.json'
            pending.append(submit(api,actual[name],f'{runid}-configure-{name}',payload(name,data),cmd))
        report['stages']['configure']=wait_instructions(api,pending)
        pending=[]
        for name,data in nodes.items():
            if data['expected']['kind'] in {'sonic','host'}:
                pending.append(submit(api,actual[name],f'{runid}-verify-{name}',{},'python3 /var/lib/fabriclab/payload/node_verify.py /var/lib/fabriclab/payload/expected.json'))
        report['stages']['network_tests']=wait_instructions(api,pending,timeout=420)
        pending=[]
        for name in t['nodes']:
            data=dict(nodes[name],gateways=r['interfaces'],archive_sha256=os.environ.get('TREX_ARCHIVE_SHA256',''))
            files={'trex_install.py':(ROOT/'scripts/trex_install.py').read_text(),'trex_install.json':json.dumps(data)}
            pending.append(submit(api,actual[name],f'{runid}-install-{name}',files,'python3 /var/lib/fabriclab/payload/trex_install.py /var/lib/fabriclab/payload/trex_install.json'))
        report['stages']['trex_install']=wait_instructions(api,pending,timeout=900)
        report['stages']['trex']=traffic_test(api,sim,actual,t,r,runid)
        report['passed']=True
    finally:
        Path('build/report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

def traffic_test(api,sim,actual,t,r,runid):
    # Both servers must be ready before choosing a common future start time.
    ready=[]
    for name in t['nodes']:
        cmd="python3 - <<'PY'\nimport socket,time\nfor i in range(60):\n try:\n  with socket.create_connection(('127.0.0.1',4501),timeout=1): break\n except OSError: time.sleep(1)\nelse: raise RuntimeError('TRex RPC not ready')\nPY"
        ready.append(submit(api,actual[name],f'{runid}-trex-ready-{name}',{},cmd))
    wait_instructions(api,ready,timeout=180)
    start=time.time()+90; tasks=[]
    for name in t['nodes']:
        remote='trex02' if name=='trex01' else 'trex01'
        cfg={'name':name,'local':t['nodes'][name],'remote':t['nodes'][remote],'gateways':r['interfaces'],'start_at':start,**{k:t[k] for k in ['pps','duration_seconds','packet_size']}}
        tasks.append(submit(api,actual[name],f'{runid}-traffic-{name}',{'trex_run.py':(ROOT/'scripts/trex_run.py').read_text(),'traffic_assert.py':(ROOT/'scripts/traffic_assert.py').read_text(),'traffic_run.json':json.dumps(cfg)},'python3 /var/lib/fabriclab/payload/trex_run.py /var/lib/fabriclab/payload/traffic_run.json'))
    outcome=wait_instructions(api,tasks,timeout=300)
    metrics=collect_reports(api,sim,actual,runid)
    return {'instructions':outcome,'metrics':metrics}

def stop(api):
    m=load('models/fabric.yml'); matches=[s for s in api.simulations.list(name=m['name']) if s.name==m['name']]
    if len(matches)!=1: raise RuntimeError('Expected one matching simulation')
    sim=matches[0]; meta=json.loads(sim.metadata or '{}')
    if meta.get('owner')!=OWNER or meta.get('repository')!=os.environ.get('GITHUB_REPOSITORY','local'):
        raise RuntimeError('Refusing to stop an unowned simulation')
    if sim.state=='ACTIVE': sim.shutdown(create_checkpoint=True)
    elif sim.state!='INACTIVE': raise RuntimeError('Simulation is busy: '+sim.state)
    sim.wait_for_state('INACTIVE',timeout=timedelta(minutes=10),error_states='INVALID')
    print('Lab stopped; checkpoint retained')


def collect_reports(api,sim,actual,runid):
    """Expose only synthetic test reports briefly; no control API or secrets."""
    result={}
    for name in ['trex01','trex02']:
        unit='fabriclab-reports'
        cmd='systemctl stop '+unit+' 2>/dev/null || true\nsystemd-run --unit='+unit+' --collect /usr/bin/python3 -m http.server 8765 --directory /var/lib/fabriclab/reports'
        task=submit(api,actual[name],f'{runid}-reports-{name}',{},cmd)
        wait_instructions(api,[task],timeout=90)
        service=None
        try:
            service=sim.create_service(node_name=name,interface_name='eth0',node_port=8765,name=f'{runid}-reports-{name}',service_type='HTTP')
            deadline=time.monotonic()+90
            while True:
                service.refresh()
                if service.worker_fqdn and service.worker_port:
                    try:
                        # No credentials sent to this endpoint; returned Air host only.
                        url=f'http://{service.worker_fqdn}:{service.worker_port}/trex.json'
                        with urllib.request.urlopen(url,timeout=10) as response:
                            data=json.load(response)
                        if data.get('node')!=name or not data.get('passed'):
                            raise RuntimeError('TRex report failed identity/result validation')
                        result[name]=data; break
                    except (OSError,ValueError):
                        if time.monotonic()>deadline: raise
                if time.monotonic()>deadline: raise TimeoutError('Report service did not become reachable')
                time.sleep(3)
            Path(f'build/{name}-metrics.json').write_text(json.dumps(result[name],indent=2)+'\n')
        finally:
            if service is not None: service.delete()
            cleanup=submit(api,actual[name],f'{runid}-reports-stop-{name}',{},'systemctl stop fabriclab-reports')
            wait_instructions(api,[cleanup],timeout=60)
    return result
