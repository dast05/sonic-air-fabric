import json
import os
from pathlib import Path
summary=['## SONiC Air lab','2 spines · 4 leafs · 4 test hosts · 2 TRex · 1 router','']
for name in ['simulation','report']:
    p=Path('build')/(name+'.json')
    if p.exists(): summary += ['```json',json.dumps(json.loads(p.read_text()),indent=2),'```']
text='\n'.join(summary)+'\n'
if os.environ.get('GITHUB_STEP_SUMMARY'):
    with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f: f.write(text)
else: print(text)
