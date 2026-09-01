import argparse
import json
from .model import load,render,validate

def main():
    parser=argparse.ArgumentParser(description='SONiC fabric / NVIDIA DSX Air lab')
    parser.add_argument('command',choices=['validate','render','discover','provision','deploy','stop'])
    args=parser.parse_args()
    if args.command in ['validate','render']:
        topology,nodes=render()
        print(json.dumps({'validated':True,'nodes':len(nodes),'data_links':len(topology['links'])}))
    else:
        from . import air
        api=air.api_client()
        if args.command=='discover': air.discover(api)
        elif args.command=='provision': air.provision(api,require_agent=False)
        elif args.command=='deploy': air.deploy(api)
        elif args.command=='stop': air.stop(api)

if __name__=='__main__': main()
