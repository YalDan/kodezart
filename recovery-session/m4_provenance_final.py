import ast
import hashlib
import json
from pathlib import Path
import subprocess
ROOT=Path('/private/tmp/kodezart-v03-m4-verifier-extraction')
OUT=Path('/private/tmp/kodezart-recovery-session')
BASE='57dcc8c3ae4836207307a715812fe389c301e130'
DONOR='7892ca1a47adbcfd91fbcfb1e580dfc86f066a99'
def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT)
def blob(ref,path):
 p=subprocess.run(['git','show',f'{ref}:{path}'],cwd=ROOT,capture_output=True)
 return p.stdout if p.returncode==0 else None
def digest(b):return hashlib.sha256(b).hexdigest() if b is not None else None
paths=sorted(set(git('diff','--name-only',BASE).decode().splitlines()+git('ls-files','--others','--exclude-standard').decode().splitlines()))
special={
 'src/kodezart/services/audit_sessions.py':('7042032','judge_in_workspace only; exact historical string/list evaluation boundary; FreshAuditSession excluded M6; final6994552 ToolPreset migration pending shared M1 boundary'),
 'src/kodezart/services/tracker_artifacts.py':('7042032','whole initial three-surface reader; later161ca93 label/criterion and child-set arm pending M4 after minimal native reads M1; later graph/split arms remain M2'),
 'src/kodezart/types/domain/audit.py':(DONOR,'AuditVerdict and TrackerArtifact only with minimal imports; all M2/M6 models excluded'),
 'src/kodezart/services/git_observations.py':(DONOR,'read_workspace_head only and settle/GitService imports; other Git observation helpers excluded'),
 'src/kodezart/core/protocols.py':(DONOR,'GitService.has_replace_refs only; no Git subprocess/error taxonomy change'),
 'src/kodezart/adapters/subprocess_git_service.py':(DONOR,'SubprocessGitService.has_replace_refs only; existing _run_output unchanged'),
 'src/kodezart/domain/errors.py':(DONOR,'WriteBackReadError only'),
 'src/kodezart/types/domain/agent.py':(DONOR,'WriteBackFinding import; write_back_verify RaiseSite; WRITE_BACK_SCHEMA exact own schema; WIRE_SCHEMAS row only'),
 'src/kodezart/types/domain/prompts.py':(DONOR,'WRITE_BACK_VERIFY only'),
 'src/kodezart/core/prompt_namespaces.py':(DONOR,'written_artifact only; base_ref already present'),
 'src/kodezart/prompts/sets/claude-opus/set.toml':(DONOR,'write_back_verify code-review skill loadout only'),
 'src/kodezart/prompts/sets/anthropic_v5/set.toml':(DONOR,'write_back_verify evaluative role registration only'),
 'tests/fakes.py':(DONOR,'FakeGitService has_replace_refs_result constructor/store and has_replace_refs method only'),
 'tests/chains/test_write_back_verifier.py':(DONOR,'all original behavioral assertions unchanged; omit later FakeTrackerPort marker_prefixes argument unsupported at M1 base'),
 'tests/chains/test_fresh_write_back_judge.py':(DONOR,'actual FreshWriteBackJudge/AgentService oracles; existing FakeAgentExecutor and SessionType.SCHEDULED_PASS/string-list evaluation boundary; add six actual subprocess cancellation phases'),
 'tests/chains/test_write_back_workspace_ownership.py':(DONOR,'adapt tests/services/test_audit_session_ownership.py seven actual Git replacement/cancellation controls to FreshWriteBackJudge and AgentService; original 5s timeout/oracles preserved; FakeRepoCache external boundary only'),
 'tests/chains/write_back_read_cancellation.py':(DONOR,'tests/git_read_cancellation.py assert_git_read_settles_before_release only; M6 cache helper excluded'),
 'tests/chains/write_back_fixtures.py':(None,'new bounded test fixture validated ResultEvent and recording existing fake workspace; no application abstraction'),
 'tests/chains/test_write_back_tracker_boundary.py':(None,'new actual Linear adapter+RunSurfaceLease+WriteBackVerifier controls; only external MCP/clock/semantic judgment doubles'),
 'tests/types/test_wire_schemas.py':(DONOR,'WriteBackFinding roster and exact one-forwarder lexical AST schema ownership census; M6 bridge/callers excluded; four source damage controls; original raw-schema/census assertions retained'),
 'tests/prompt_census.py':(DONOR,'existing explicit census18->19 for exactly one actual new role; all enum/count assertions retained'),
 'docs/architecture.md':(None,'new bounded shared verification explanation and honest pending surface/config/adoption closure'),
}
rows=[]
for path in paths:
 content=(ROOT/path).read_bytes()
 ref,note=special.get(path,(DONOR,'whole donor file'))
 expected=blob(ref,path) if ref else None
 rows.append({'path':path,'sha256':digest(content),'base_sha256':digest(blob(BASE,path)), 'donor':ref,'donor_path':path,'donor_sha256':digest(expected),'whole_file_identical_to_donor':content==expected,'mapping':note})
obj={'base':BASE,'maintained_equivalent':'a4575cd11133c5e631fff454de8cbac682f5a30e','base_tree':git('rev-parse',BASE+'^{tree}').decode().strip(),'accepted_donor':DONOR,'watermark':'d2c6fceab762191d4e40b23c8cd349ef476e4b12','files':rows,'pending':[
 {'owner':'M1','responsibility':'minimal native classification/read_criteria shared port/model prerequisite; root serialization; not M2'},
 {'owner':'M4','source':'161ca93','responsibility':'all accepted label/criterion and criterion-child-set reader deltas after M1 native read prerequisite; exact final reader preserved in donor'},
 {'owner':'M4/M1 boundary','source':'6994552','responsibility':'historical judge string/list boundary replaced by exact current ToolPreset.EVALUATION consumer when shared boundary extracted'},
 {'owner':'M4 later slice','responsibility':'WriteBackSettings1..10 configured consumers; LaneEscalation; native semantic/state/ruling/evidence closure'},
 {'owner':'M2','responsibility':'graph/split artifact arms and Organize author/admission/runtime; no dependency inverted into M4'},
 {'owner':'M6','responsibility':'FreshAuditSession and other Audit models/composition remain later'}]}
(OUT/'m4-verifier-provenance.json').write_text(json.dumps(obj,indent=2)+'\n')
print('Mapped',len(rows),'files; exact working-file SHA256 captured.')
