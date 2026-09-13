import sys,json
from pathlib import Path
sys.path.insert(0,'/private/tmp/kodezart-recovery-session');import extract_m2 as h
h.T=Path('/private/tmp/kodezart-v03-m3-plan-walk')
for n in ['_RerunTarget','_RerunBatch','_RerunContext']:h.symbol('src/kodezart/adapters/github_api.py',n)
for n in ['__init__','_request_with_retry','_parsed_with_retry','_rerun_context','_remember_rerun','_rerun_error','_terminal_checks','_attempt_jobs','rerun_checks','_dispatch_rerun','_rerun_observation','_wait_for_rerun','_grace_polls_for','_no_checks_summary','_verdict','_fetch_check_runs','_probe_workflows','checks_declared','_completed_watch','wait_for_checks']:h.symbol('src/kodezart/adapters/github_api.py',n,'GitHubAPIClient')
for n in ['CheckSuiteIdentity','CheckRun','CheckRunsResponse','DeclaredWorkflow','DeclaredWorkflowsResponse','CommitIdentity','WorkflowRun','WorkflowRunsResponse','WorkflowJob','WorkflowJobsResponse']:h.symbol('src/kodezart/adapters/github_types.py',n)
for setname in ['anthropic_v5','claude-opus']:
 for role in ['native_writer_contract','amendment_judge','amendment_author','criteria_validation','evaluation','post_merge_review']:
  h.whole(f'src/kodezart/prompts/sets/{setname}/{role}.md')
 p=h.T/f'src/kodezart/prompts/sets/{setname}/set.toml';s=p.read_text()
 if setname=='claude-opus':s=s.replace('criteria_validation = ["code-review"]','criteria_validation = ["code-review"]\nnative_writer_contract = []\namendment_judge = ["code-review"]\namendment_author = ["code-review"]')
 else:
  s=s.replace('"pr_description", "knowledge_map"]','"pr_description", "knowledge_map", "native_writer_contract"]')
  s=s.replace('    "organize_verify",','    "organize_verify",\n    "amendment_judge",\n    "amendment_author",')
 p.write_text(s)
h.whole('src/kodezart/prompts/sets/claude-opus/acceptance_criteria.md')
log=Path('/private/tmp/kodezart-recovery-session/m3-extracted-hunks.json');log.write_text(json.dumps(json.loads(log.read_text())+h.records,indent=2)+'\n')
