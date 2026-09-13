"""Read-only seven-milestone inventory; proposed ownership is not acceptance."""

import ast
from collections import Counter, defaultdict
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess

ROOT = Path('/private/tmp/kodezart-v03-recovery-integration')
OUT = Path('/private/tmp/kodezart-recovery-session')
BASE = '4661a24b599d75503a997f3ce122f3ad2da77048'
HEAD = 'd2c6fceab762191d4e40b23c8cd349ef476e4b12'
LABELS = {'M1': 'L1 scope input and port surface', 'M2': 'L2 organize', 'M3': 'L3/L9 plan and walk', 'M4': 'L4 criterion lifecycle and tracker state', 'M5': 'L5/L6 deliver and terminate', 'M6': 'L7 audit', 'M7': 'L8 run supervisor'}


def git(*args, cwd=ROOT):
    return subprocess.check_output(['git', *args], cwd=cwd)


def blob(ref, path):
    try:
        return git('show', f'{ref}:{path}').decode()
    except subprocess.CalledProcessError:
        return ''


GROUPS = {
 'M1': '''_agents_mapping _mcp_mapping _permission_modes _sdk_mapping agent_content_scanner asyncio_job_queue claude_agent_executor claude_client_executor git_worktree_provider hosted_mcp_session http_mcp_tool_caller in_repo_prompt_registry linear_history_receipt linear_issue_identity linear_markers linear_mcp_tracker linear_mcp_types linear_references linear_scope_reader linear_scope_types mcp_result_decoding outbound_admission pagination reference_content_scanner regex_content_scanner stdio_mcp_tool_caller subprocess_git_service dependencies gating jobs knowledge preflight prompts tracker workspace agent_settings backoff config constants errors git_settings http_settings job_queue_settings knowledge_settings logging logging_settings owned_tasks prompt_namespaces prompt_rendering protocols retry stream_drain tracker_settings comment_markers criterion_creation git_url issue_tree scope_approval self_writes surface_lease tracker_writes fire_context git_observations owned_workspace repo_observations run_surface_lease scope_membership scope_resolution tracker_artifacts tracker_boot credentials issue_identity privacy scope scope_address self_writes session surface tracker transport job_acceptance agent_handler'''.split(),
 'M2': '''organize organize_author organize_owner organize_tick organize_settings mandate_graph organize_criteria_author organize_assess organize_verify ticket_generation criteria_feasibility criterion_sources'''.split(),
 'M3': '''criteria fire_consolidation fire_implementation fire_remediation fire_review fire_specification ralph_loop ralph_workflow remediation scope_walker scope_runtime accept_gate agent criteria criteria_grading dispatch fire_spec gap ticket topology workflow_state agent_service base_resolver claim_heartbeat dispatch_pass fire_dispatcher pass_gate pass_scheduler prompt_pass scope_dispatcher scope_planning union_composition union_identity union_tick accept branch fire fire_spec remediation scope_ready topology union union_tick workflow'''.split(),
 'M4': '''linear_record_sink notion_record_properties notion_record_sink record_failures records run_records langgraph_run_state_reader git_artifact_persister git_change_persister write_back_verifier node_sessions criterion_evidence escalation_resolution lane_record rulings run_event_stream escalation_records fire_record_facts lane_escalation lane_records lifecycle_watcher ruling_records run_recorder tracker_lifecycle criterion_lifecycle criterion_ref escalation node_session notion_records run_event run_state write_back fire_record'''.split(),
 'M5': '''github_api github_types no_forge_delivery subprocess_check_chain authored_checks authored_delivery authored_publication delivery_coordinator lane_delivery native_delivery delivery forge authored_outcome check_chain outcome pr_body stall_report check_classification lane_reports scope_tally check_observation github pr_state scope_terminal'''.split(),
 'M6': '''audit_detection_removal audit_evidence audit_forge audit_overclaim audit_pass audit_sweep audit_collection audit_coverage audit_requests audit_sessions audit_sources audit_terminal audit audit_claim audit_mandate content_audit subprocess_git_source_reader'''.split(),
 'M7': '''assertion_drift run_alarm_record run_shape barren_record_signals escalation_signals lane_record_signals recorded_assertion_drift run_alarm'''.split(),
}
STEMS = {name: owner for owner, names in GROUPS.items() for name in names}
MIXED = {
 'src/kodezart/core/protocols.py', 'src/kodezart/core/config.py',
 'src/kodezart/domain/errors.py', 'src/kodezart/types/domain/agent.py',
 'src/kodezart/types/domain/operation.py', 'src/kodezart/types/domain/prompts.py',
 'src/kodezart/types/domain/workflow.py', 'src/kodezart/composition/engine.py',
 'src/kodezart/composition/passes.py', 'src/kodezart/main.py',
 'src/kodezart/adapters/linear_mcp_tracker.py', 'src/kodezart/adapters/github_api.py',
 'src/kodezart/types/domain/audit.py', 'src/kodezart/types/domain/session.py',
 'tests/fakes.py', 'tests/conftest.py', 'tests/test_composition_root.py',
 'tests/types/test_wire_schemas.py', 'tests/types/test_wire_renames.py',
 'tests/tracker/test_tracker_conformance.py', 'tests/tracker/test_linear_mcp_tracker.py',
 'tests/core/test_config.py', 'tests/prompts/test_prompt_wiring.py',
 'tests/chains/test_dispatch_definitions.py',
 'src/kodezart/types/domain/tracker.py', 'src/kodezart/types/domain/assertion_drift.py',
 'src/kodezart/api/v1/endpoints/agent.py', 'src/kodezart/api/v1/endpoints/jobs.py',
}

# Explicit review-owner vocabulary for mixed symbol sections. These rules assign
# extraction responsibility only; they do not infer runtime semantics.
SYMBOLS = {
 'M7': ('Alarm', 'alarm', 'Barren', 'barren', 'AssertionDrift', 'assertion_drift', 'run_shape', 'RunShape', 'supervisor', 'Supervisor', 'escalation_age', 'record_superseded', 'record_consistency'),
 'M6': ('Audit', 'audit', 'DetectorRemoval', 'detector_removal', 'overclaim', 'Overclaim', 'GitSource', 'git_source', 'find_source', 'read_source', 'resolve_commit'),
 'M2': ('Organize', 'organize', 'Mandate', 'mandate', 'AdmissionVerdict', 'AdmissionAction', 'SpecFinding', 'SpecGap', 'admission_round', 'criterion_creation', 'create_criterion', 'TicketGeneration', 'ticket_generation'),
 'M4': ('Ruling', 'ruling', 'Amendment', 'amendment', 'WriteBack', 'write_back', 'CriterionEvidence', 'criterion_evidence', 'criterion_satisfaction', 'CriterionLifecycle', 'criterion_lifecycle', 'RunEvent', 'run_event', 'LaneRun', 'lane_run', 'LaneRecord', 'lane_record', 'run_state', 'RunRecord', 'run_record', 'RecordSink', 'record_sink', 'RecordDestination', 'RunIdentity', 'RunOutcome', 'FireRecord', 'fire_record', 'Escalation', 'escalation', 'NodeSession', 'node_session', 'NodeInvocation', 'RunKind', 'record_destination', 'run_outcome'),
 'M5': ('CIWatch', 'CIMonitor', 'CheckChain', 'CheckObservation', 'ObservedChecks', 'check_chain', 'check_classif', 'check_observ', 'Delivery', 'delivery', 'PRState', 'PRCreator', 'ForgeQuery', 'open_pr', 'create_pr', 'pr_state', 'pull_request', 'rerun', 'check_runs', 'declared_workflow', 'workflow_run', 'PRBody', 'pr_body', 'Terminal', 'terminal', 'ScopeTally', 'scope_tally', 'LaneReport', 'lane_report', 'Outcome', 'outcome', 'stalled', 'consolidation', 'branch_web_url'),
 'M3': ('ScopePlan', 'scope_plan', 'ScopeReady', 'scope_ready', 'ScopeRuntime', 'scope_runtime', 'ScopeWalk', 'scope_walk', 'ScopeDispatch', 'scope_dispatch', 'Topology', 'topology', 'BaseSpec', 'BaseResolver', 'base_resolv', 'Union', 'union', 'FireSpec', 'fire_spec', 'TrackerSpec', 'TrackerCriterion', 'tracker_criterion', 'FireCriteria', 'fire_criteria', 'read_criterion', 'read_current', 'resolve_criterion', 'CriterionResolution', 'ExecutionCriterion', 'Ralph', 'ralph', 'Workflow', 'workflow', 'FireImplementation', 'FireReview', 'FireConsolidation', 'FireRemediation', 'FireSpecification', 'workflow_engine', 'fire_dispatch', 'Dispatch', 'dispatch', 'Remediation', 'remediation', 'acceptance_criteria', 'evaluate', 'evaluation'),
}


def semantic_owner(text, default):
    # Shared judgment value primitives serve verification, not the Audit pass.
    if text in {'AuditVerdict', 'TrackerArtifact'}:
        return 'M4'
    for owner, words in SYMBOLS.items():
        if any(word in text for word in words):
            return owner
    return default


def path_owner(path):
    stem = Path(path).stem.removeprefix('test_')
    if stem in {'scope_events', 'criterion_identity', 'criterion_identity_conformance', 'native_fresh_boundaries', 'native_port_failures'}:
        return 'M3', 'native scope/criterion event or identity contract'
    if path == 'src/kodezart/domain/criterion_creation.py':
        return 'M2', 'organize criterion authoring representation'
    if stem in STEMS:
        return STEMS[stem], 'explicit source-family ownership'
    if '/prompts/' in path and stem in {'set', 'prompt_wiring', 'pass_roster', 'operation_config', 'set_completeness', 'v5_wiring', 'v5_fragments', 'skills_loadouts'}:
        return 'M1', 'mixed prompt registry; sections require feature ownership'
    if stem in {'evaluation', 'post_merge_review', 'criteria_validation'}:
        return 'M3', 'native implementation/evaluation prompt consumer'
    if stem in {'acceptance_criteria', 'grooming_pass', 'fire_prep_pass'}:
        return 'M2', 'organize or authored specification prompt consumer'
    if stem == 'pr_description':
        return 'M5', 'PR publication prompt consumer'
    if any(part in stem for part in ('audit', 'detector_removal')):
        return 'M6', 'audit feature file'
    if any(part in stem for part in ('organize', 'mandate_graph', 'mandate_binding', 'criterion_creation', 'criteria_generation')):
        return 'M2', 'organize feature file'
    if any(part in stem for part in ('alarm', 'barren', 'assertion_drift', 'escalation_age', 'record_superseded', 'record_consistency', 'supervision')):
        return 'M7', 'supervisor feature file'
    if any(part in stem for part in ('ruling', 'lane_record', 'lane_escalation', 'escalation_record', 'escalation_resolution', 'criterion_evidence', 'criterion_lifecycle', 'write_back', 'writeback', 'run_event', 'run_record', 'fire_record', 'fire_log', 'node_session', 'record_publication', 'record_failure', 'recorded_landing', 'parent_rollup', 'lifecycle')):
        return 'M4', 'tracker lifecycle/record feature file'
    if any(part in stem for part in ('delivery', 'ci_', 'pr_state', 'pr_footer', 'pr_content', 'union_check', 'check_classif', 'check_chain', 'forge_', 'scope_terminal', 'scope_tally', 'lane_report', 'stalled', 'landing_dispatch')):
        return 'M5', 'delivery/termination feature file'
    if any(part in stem for part in ('fire_', 'native_fire', 'native_fresh', 'native_port', 'scope_runtime', 'scope_launch', 'scope_plan', 'scope_gap', 'scope_ready', 'scope_observation', 'scope_dispatch', 'topology', 'base_', 'landing_base', 'union_', 'stacked_scope', 'workflow_', 'ralph', 'remediation', 'empty_fire', 'criterion_reader', 'criterion_resolution', 'criterion_record', 'issue_key_carriage', 'accept_gate')):
        return 'M3', 'native fire/planning feature file'
    return 'M1', 'shared boundary/configuration/test infrastructure; owner proposed'


cache = {}
def source(ref, path):
    key=(ref,path)
    if key not in cache:
        cache[key]=blob(ref,path)
    return cache[key]


def owners_by_line(ref, path):
    content=source(ref,path)
    default, reason=path_owner(path)
    lines=content.splitlines()
    result=[(default,'file',reason)]*(len(lines)+1)
    mixed=path in MIXED or path.startswith('docs/') or path in {'.env.example','README.md','CHANGELOG.md','Dockerfile','Makefile','pyproject.toml','uv.lock'} or path.endswith('/set.toml')
    if not mixed:
        return result
    if path.endswith('.py'):
        try: tree=ast.parse(content)
        except SyntaxError: return result
        nodes=list(ast.walk(tree))
        # Outer classes first, methods/fields override within them.
        for node in sorted(nodes,key=lambda n:-(getattr(n,'end_lineno',0)-getattr(n,'lineno',0))):
            if isinstance(node,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
                name=node.name
            elif isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name):
                name=node.target.id
            elif isinstance(node,ast.Assign):
                name=' '.join(t.id for t in node.targets if isinstance(t,ast.Name))
            else: continue
            owner=semantic_owner(name,result[node.lineno][0])
            for i in range(node.lineno,min(node.end_lineno,len(lines))+1):
                result[i]=(owner,name,'AST section owner')
        for node in nodes:
            if isinstance(node,ast.ImportFrom) and node.module and node.module.startswith('kodezart'):
                modpath='src/'+node.module.replace('.','/')+'.py'
                modowner=path_owner(modpath)[0]
                for i in range(node.lineno,min(node.end_lineno,len(lines))+1):
                    actual=[n.name for n in node.names if re.search(r'\b'+re.escape(n.name)+r'\b',lines[i-1])]
                    label=' '.join(actual) or node.module
                    owner=semantic_owner(label,modowner)
                    result[i]=(owner,'import '+label,'import owner / split by actual name line')
    else:
        owner=default; section='preamble'
        for i,line in enumerate(lines,1):
            if line.startswith('#') or re.match(r'\[.*\]',line):
                section=line.strip(); owner=semantic_owner(section,default)
            explicit=semantic_owner(line,owner)
            result[i]=(explicit,section,'documentation/configuration section; review required')
    return result


statuses=[]
for line in git('diff','--name-status',BASE,HEAD).decode().splitlines():
    bits=line.split('\t'); statuses.append({'status':bits[0],'old_path':bits[1],'path':bits[-1]})
fullpatch=git('diff','--no-ext-diff','--binary','--full-index','--unified=0',BASE,HEAD)
(OUT/'extraction-watermark-d2c6fce.patch').write_bytes(fullpatch)
rawstats=git('diff','--numstat',BASE,HEAD).decode()
(OUT/'extraction-numstat-d2c6fce.tsv').write_text(rawstats)

manifest=[]; fragments=[]; coverage=Counter(); symbols=[]
for item in statuses:
    path=item['path']; old=item['old_path']; default,reason=path_owner(path)
    oldowners=owners_by_line(BASE,old) if item['status']!='A' else [('M1','absent','absent')]
    newowners=owners_by_line(HEAD,path) if item['status']!='D' else [('M1','absent','absent')]
    patch=git('diff','--no-ext-diff','--unified=0',BASE,HEAD,'--',old,path).decode()
    hunk=-1; oldline=newline=0; units=[]; hunkcounts=Counter()
    for raw in patch.splitlines(keepends=True):
        m=re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@',raw)
        if m:
            hunk+=1;oldline=int(m[1]);newline=int(m[3]);continue
        if hunk<0 or raw.startswith('\\'): continue
        if raw.startswith('-'):
            owner,label,why=oldowners[min(oldline,len(oldowners)-1)]; ln=oldline;oldline+=1;side='old'
        elif raw.startswith('+'):
            owner,label,why=newowners[min(newline,len(newowners)-1)]; ln=newline;newline+=1;side='new'
        else:
            oldline+=1;newline+=1;continue
        units.append({'hunk':hunk,'side':side,'line':ln,'owner':owner,'section':label,'reason':why,'raw_sha256':sha256(raw.encode()).hexdigest()})
        coverage[owner]+=1;hunkcounts[owner]+=1
    owner_set=sorted(hunkcounts) or [default]
    record={**item,'default_owner':default,'owners':owner_set,'reason':reason,'assignment_status':'proposed; not extraction or review acceptance','destination_pr':None,'destination_commit':None,'hunk_count':hunk+1,'changed_lines':len(units),'patch_sha256':sha256(patch.encode()).hexdigest(),'units':units}
    manifest.append(record)
    # One compact run per contiguous hunk/side/owner/section; every +/- line
    # has exactly one owner and never appears in two fragments.
    for u in units:
        key=(path,u['hunk'],u['side'],u['owner'],u['section'])
        if fragments and fragments[-1]['key']==key and fragments[-1]['end']+1==u['line']:
            fragments[-1]['end']=u['line'];fragments[-1]['count']+=1
        else:
            fragments.append({'key':key,'path':path,'hunk':u['hunk'],'side':u['side'],'start':u['line'],'end':u['line'],'count':1,'owner':u['owner'],'section':u['section']})

history=[]
for row in git('log','--reverse','--format=%H%x09%s',f'{BASE}..{HEAD}').decode().splitlines():
    commit,title=row.split('\t',1)
    diagnostic=None
    try:
        paths=git('diff-tree','--no-commit-id','--name-only','-r',commit).decode().splitlines()
    except subprocess.CalledProcessError as exc:
        paths=[]; diagnostic=f'Historical tree unavailable: git diff-tree exit {exc.returncode}; full watermark diff remains inventoried'
    ownership=sorted({o for p in paths for o in next((r['owners'] for r in manifest if r['path']==p or r['old_path']==p),[path_owner(p)[0]])})
    history.append({'sha':commit,'title':title,'paths':paths,'owners':ownership,'diagnostic':diagnostic,'disposition':'included donor ancestry; live retained delta is watermark patch, superseded intermediate code not revived','destination_commit':None})

imports=[]
for row in manifest:
    p=row['path']
    if not p.startswith('src/') or not p.endswith('.py') or row['status']=='D': continue
    text=source(HEAD,p)
    try: tree=ast.parse(text)
    except SyntaxError: continue
    lineowners=owners_by_line(HEAD,p)
    for node in ast.walk(tree):
        if not isinstance(node,ast.ImportFrom) or not node.module or not node.module.startswith('kodezart'): continue
        target='src/'+node.module.replace('.','/')+'.py'
        for alias in node.names:
            dst=semantic_owner(alias.name,path_owner(target)[0]); srcs=set(row['owners'])
            for src in srcs:
                if src!=dst:
                    imports.append({'source':p,'line':node.lineno,'source_owner':src,'target':target,'symbol':alias.name,'target_owner':dst,'kind':'syntactic import dependency; validate symbol consumption before extraction'})

worktrees=[]
blocks=git('worktree','list','--porcelain').decode().strip().split('\n\n')
for block in blocks:
    lines=block.splitlines();loc=lines[0].removeprefix('worktree ')
    if not ('recovery' in loc or loc=='/Users/kodezart/Projects/kodezart'): continue
    meta={line.split(' ',1)[0]:line.split(' ',1)[1] for line in lines if ' ' in line}
    try:
        status=git('status','--porcelain=v1','-z',cwd=loc)
        diff=git('diff','--binary',cwd=loc);staged=git('diff','--cached','--binary',cwd=loc)
    except (subprocess.CalledProcessError,FileNotFoundError): continue
    untracked=[]
    for token in status.decode().split('\0'):
        if token.startswith('?? '):
            p=Path(loc)/token[3:]
            if p.is_file(): untracked.append({'path':token[3:],'sha256':sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size})
    worktrees.append({**meta,'captured_at':datetime.now(UTC).isoformat(),'status':status.decode().replace('\0','\n'),'unstaged_sha256':sha256(diff).hexdigest(),'staged_sha256':sha256(staged).hexdigest(),'untracked':untracked,'acceptance':'not inferred from a worktree/ref'})

refs=[]
for row in git('for-each-ref','--format=%(objectname)%09%(refname)','refs/heads/codex').decode().splitlines():
    sha,ref=row.split('\t',1);refs.append({'sha':sha,'ref':ref})

summary={'captured_at':datetime.now(UTC).isoformat(),'main':BASE,'donor':HEAD,'donor_tree':git('rev-parse',HEAD+'^{tree}').decode().strip(),'patch_sha256':sha256(fullpatch).hexdigest(),'path_count':len(manifest),'history_commit_count':len(history),'changed_line_count':sum(coverage.values()),'changed_lines_by_proposed_owner':dict(coverage),'milestones':LABELS,'all_lines_assigned_once':sum(x['count'] for x in fragments)==sum(coverage.values()),'ownership_is_proposed':True,'extraction_performed':False,'destination_prs_verified':False,'tree_equivalence_tested':False}
for name,value in [('manifest',{'summary':summary,'paths':manifest}),('fragments',fragments),('history',history),('imports',imports),('worktrees',worktrees),('refs',refs)]:
    (OUT/f'extraction-{name}-d2c6fce.json').write_text(json.dumps(value,indent=2)+'\n')
(OUT/'extraction-ownership-d2c6fce.tsv').write_text('status\tpath\towners\thunks\tchanged_lines\n'+''.join(f"{r['status']}\t{r['path']}\t{','.join(r['owners'])}\t{r['hunk_count']}\t{r['changed_lines']}\n" for r in manifest))
print(json.dumps(summary,indent=2))
