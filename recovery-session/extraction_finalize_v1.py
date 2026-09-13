import ast, json, subprocess
from pathlib import Path
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, UTC
S=Path('/private/tmp/kodezart-recovery-session')
script=(S/'extraction_inventory.py').read_text()
ns={}; exec(compile(script.split('\nstatuses=[]')[0], str(S/'extraction_inventory.py'), 'exec'), ns)
old_semantic=ns['semantic_owner']
def semantic(text, default):
    if text in {'AuditVerdict','TrackerArtifact','ProtectedTestRef'}: return 'M4'
    if text=='GitSourceBlob': return 'M6'
    if text=='judge_in_workspace': return 'M2'
    return old_semantic(text, default)
ns['semantic_owner']=semantic
ns['MIXED'].add('src/kodezart/services/audit_sessions.py')
D=json.loads((S/'extraction-manifest-d2c6fce.json').read_text()); paths=D['paths']
explicit={'tests/chains/test_authored_check_routing.py':'M5','tests/probes/notion_records.py':'M4','src/kodezart/services/scope_runtime.py':'M5','src/kodezart/composition/scope_runtime.py':'M5','src/kodezart/types/domain/scope_runtime.py':'M5','src/kodezart/chains/authored_publication.py':'M3','src/kodezart/domain/authored_outcome.py':'M3'}
for r in paths:
    p=r['path']
    if p in explicit:
        for u in r['units']: u.update(owner=explicit[p], reason='root agreed concrete component ownership')
        r['default_owner']=explicit[p]
    if p in {'src/kodezart/services/audit_sessions.py','src/kodezart/types/domain/assertion_drift.py'}:
        for side,ref,path in [('old',ns['BASE'],r['old_path']),('new',ns['HEAD'],p)]:
            units=[u for u in r['units'] if u['side']==side]
            if not units: continue
            owners=ns['owners_by_line'](ref,path)
            for u in units:
                owner,section,reason=owners[min(u['line'],len(owners)-1)];u.update(owner=owner,section=section,reason=reason)
    if p in {'src/kodezart/chains/authored_checks.py','src/kodezart/chains/authored_delivery.py'}:
        for u in r['units']:
            owner='M3'; line=u['line']; content=ns['source'](ns['HEAD'] if u['side']=='new' else ns['BASE'],p).splitlines(); text=content[line-1]
            if p.endswith('authored_checks.py') and (9<=line<=22 or 35<=line<=37 or 42<=line<=47 or 73<=line<=108 or 113<=line<=115): owner='M5'
            if p.endswith('authored_delivery.py') and ('CheckRedClass' in text or 'ci_red_class' in text or 'ci_run_absent' in text): owner='M5'
            u.update(owner=owner,reason='M3 existing authored compatibility extraction; M5 typed CI delta per root split')
    if p=='src/kodezart/composition/engine.py':
        for u in r['units']:
            if u['side']!='new': continue
            line=u['line'];owner='M3'
            if line in {24,25,80,84,185,341} or 114<=line<=123 or 296<=line<=298 or 304<=line<=337: owner='M5'
            u.update(owner=owner,reason='root agreed M3 shared engine/authored compatibility; M5 CI and final scope assembly')
    if p in {'src/kodezart/types/domain/prompts.py','src/kodezart/types/domain/agent.py'}:
        for u in r['units']:
            upper=u['section'].upper()
            if 'ORGANIZE' in upper: u['owner']='M2'
            elif 'WRITE_BACK' in upper: u['owner']='M4'
            elif upper.startswith('AUDIT_'): u['owner']='M6'
    r['owners']=sorted({u['owner'] for u in r['units']}) or [r['default_owner']]
# Historical objects were repaired by authorized refetch; retain old diagnostic logs.
history=json.loads((S/'extraction-history-d2c6fce.json').read_text())
def repair(row):
    if row['diagnostic']:
        row['paths']=ns['git']('diff-tree','--no-commit-id','--name-only','-r',row['sha']).decode().splitlines()
        row['prior_diagnostic']=row['diagnostic'];row['diagnostic']=None
    row['owners']=sorted({o for p in row['paths'] for o in next((r['owners'] for r in paths if p in {r['path'],r['old_path']}), [ns['path_owner'](p)[0]])})
    return row
history=list(ThreadPoolExecutor(max_workers=4).map(repair,history))
# Associate source PRs only by actual ancestry, preserving all ancestors rather than claiming a unique original PR.
prs=json.loads((S/'extraction-pr-census.json').read_text()); bysha={h['sha']:h for h in history}
for h in history: h['containing_source_prs']=[]
for pr in prs:
    if pr['number']<72: continue
    try: ancestors=ns['git']('rev-list',pr['headRefOid']).decode().splitlines()
    except subprocess.CalledProcessError: continue
    for sha in ancestors:
        if sha in bysha: bysha[sha]['containing_source_prs'].append(pr['number'])
for r in paths:
    relevant=[h for h in history if r['path'] in h['paths'] or r['old_path'] in h['paths']]
    r['source_history_commits']=[h['sha'] for h in relevant]
    r['containing_source_prs']=sorted({p for h in relevant for p in h['containing_source_prs']})
coverage=Counter(u['owner'] for r in paths for u in r['units']); fragments=[]
for r in paths:
    for u in r['units']:
        key=[r['path'],u['hunk'],u['side'],u['owner'],u['section']]
        if fragments and fragments[-1]['key']==key and fragments[-1]['end']+1==u['line']:
            fragments[-1]['end']=u['line'];fragments[-1]['count']+=1
        else: fragments.append({'key':key,'path':r['path'],'hunk':u['hunk'],'side':u['side'],'start':u['line'],'end':u['line'],'count':1,'owner':u['owner'],'section':u['section']})
D['summary'].update(revised_at=datetime.now(UTC).isoformat(),changed_lines_by_proposed_owner=dict(sorted(coverage.items())),historical_tree_errors=sum(bool(h['diagnostic']) for h in history),ownership_revision='v1 root agreed M3/M5 composition split; line responsibility is proposed, not a directly applicable standalone patch')
assert len(paths)==659 and sum(coverage.values())==103233 and sum(f['count'] for f in fragments)==103233
for name,value in [('manifest',D),('history',history),('fragments',fragments)]: (S/f'extraction-{name}-d2c6fce.json').write_text(json.dumps(value,indent=2)+'\n')
(S/'extraction-ownership-d2c6fce.tsv').write_text('status\tpath\towners\thunks\tchanged_lines\n'+''.join(f"{r['status']}\t{r['path']}\t{','.join(r['owners'])}\t{r['hunk_count']}\t{r['changed_lines']}\n" for r in paths))
print(json.dumps(D['summary'],indent=2))
