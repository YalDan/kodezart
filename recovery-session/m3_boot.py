import sys,json
from pathlib import Path
sys.path.insert(0,'/private/tmp/kodezart-recovery-session');import extract_m2 as h
h.T=Path('/private/tmp/kodezart-v03-m3-plan-walk')
h.symbol('src/kodezart/adapters/asyncio_job_queue.py','_run_job','AsyncioJobQueue')
p=h.T/'src/kodezart/main.py';s=p.read_text().replace('from kodezart.composition.engine import build_workflow_engine','from kodezart.chains.criteria import TrackerCriteria\nfrom kodezart.composition.engine import build_workflow_engine');anchor='            repositories=operation.repos,\n';assert anchor in s;s=s.replace(anchor,anchor+'            criteria=TrackerCriteria(tracker=dialled.tracker) if dialled is not None else None,\n            scope_tracker=dialled.tracker if dialled is not None else None,\n            operation=operation,\n',1);p.write_text(s)
log=Path('/private/tmp/kodezart-recovery-session/m3-extracted-hunks.json');log.write_text(json.dumps(json.loads(log.read_text())+h.records,indent=2)+'\n')
