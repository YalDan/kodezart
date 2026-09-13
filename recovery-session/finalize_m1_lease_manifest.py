from pathlib import Path
import subprocess,json,re,hashlib
S=Path('/private/tmp/kodezart-recovery-session');R=Path('/private/tmp/kodezart-v03-m1-lease-extraction');BASE='a2ee4c6bebd438359b53fb7a9e11c3966d24ceb5';HEAD='90aea37cc25b0a8dc44df9509ca7392d92a5334d';D2='d2c6fceab762191d4e40b23c8cd349ef476e4b12'
def git(*args):return subprocess.check_output(['git',*args],cwd=R,text=True)
audit=json.loads((S/'m1-lease-equivalence-audit.json').read_text());operations=json.loads((S/'m1-lease-source-map-stage6.json').read_text())
partial={
'LinearMarkers':'Existing configured codec, excluding M4 WorkRef.landing reader/body fields.',
'LinearMarkers.work_ref_pattern':'Only configured prefix replaces existing literal; M4 landing field excluded.',
'LinearMarkers.work_ref_body':'Only configured prefix replaces existing literal; M4 landing field excluded.',
'_READ_TOOLS':'Add attribution get_user to existing M1 read-only vocabulary; later tools excluded.',
'LinearMcpTracker':'Partial class extraction: methods individually audited; later consumer methods stay absent.',
'LinearMcpTracker.__init__':'Replace retry scalars with existing RetryPolicy and inject configured LinearMarkers; no later mapping fields.',
'LinearMcpTracker.create_issue':'Keep M1 creation arguments; record exact declared title/description mutation receipt. No issue identity carrier.',
'LinearMcpTracker.update_issue':'Existing args unchanged; pass written=arguments into donor shared receipt tail. No M2 identity preservation.',
'LinearMcpTracker._save_state':'Existing state operation unchanged; record returned state-field receipt. M4 state-history and state policy excluded.',
'LinearMcpTracker.set_queue_state':'Existing labels operation unchanged; record exact declared labels receipt. Later idempotence hunk excluded.',
'LinearMcpTracker.work_refs':'Existing WorkRef interpretation plus configured prefix preflight. M4 landing/malformed-marker interpretation excluded.',
'LinearMcpTracker.recorded_repository':'Configured pattern resolved before complete comment read. Existing M1 prose retained.',
'build_dispatch_passes':'Only actual terminal writer marker-prefix and TrackerSettings lease-duration injection.',
'build_run_recorder':'Only grouped tracker.server_name configuration read.',
'make_mcp_tool_caller':'Use TrackerSettings fields with existing M1 HttpMcpToolCaller constructor (token/auth_header_name/auth_scheme).',
'build_tracker':'Donor writer attribution/lease-marker/retry settings; exclude Organize configuration and event-table validation.',
'boot_tracker':'Donor attributable writer guard and owned cleanup with actual existing M1 factory interface.',
'AppConfig':'Only tracker field group extraction and nested delimiter; other subsystem fields retain base shape.',
'AppConfig.settings_customise_sources':'Same donor source composition and retired-value rejection, limited to migrated tracker fields.',
'TrackerPort':'Only claim/surface/comment identity+expected/movement methods; remaining port unchanged.',
'lifespan':'Only boot_tracker(settings=config.tracker) composition argument.',
'TrackerLifecycleWriter':'Only constructor/on_terminal_outcome extracted, methods exact AST; other legacy lifecycle authority unchanged.',
'OperationConfig':'Only configured marker_prefixes field and uniqueness validation; other operation contracts unchanged.',
'OperationConfig._check_structure':'Only marker-prefix uniqueness loop extracted.',
'FIELD_OWNERSHIP':'Only marker_prefixes LOCAL owner entry extracted.'}
for row in audit:
 if row['status']=='partial hunk normalization required':
  assert row['symbol'] in partial,row
  row['normalization']=partial[row['symbol']]
 elif row['status']=='removed base node':row['normalization']='Retired tracker flat field or replaced legacy claim/parser helper. Replacement recorded in operation log and patch.'
paths=git('diff','--name-only',BASE,HEAD).splitlines(); files=[]
for path in paths:
 def blob(rev):
  p=subprocess.run(['git','rev-parse',f'{rev}:{path}'],cwd=R,text=True,capture_output=True);return p.stdout.strip() if p.returncode==0 else None
 patch=git('diff','--unified=0',BASE,HEAD,'--',path)
 files.append({'path':path,'milestone':'M1','base_blob':blob(BASE),'donor_blob':blob(D2),'extracted_blob':blob(HEAD),'sha256':hashlib.sha256((R/path).read_bytes()).hexdigest(),'hunks':[line for line in patch.splitlines() if line.startswith('@@')],'operations':[op for op in operations if op['file']==path],'source_nodes':[row for row in audit if row['file']==path]})
output={'base':BASE,'candidate':HEAD,'tree':git('rev-parse',HEAD+'^{tree}').strip(),'donor':D2,'donor_tree':'4e98a9622f828fe5f8cce7bd65af6198dd0e185f','scope':'M1 lease, configured marker/comment, tracker-group and exact movement receipt closure; not full L1 acceptance','post_watermark':{'89b3751f0f1a7a20d0eb9c9d7c23001b9fce3735':'native final comment snapshot+expected/attribution/grant preconditions','76478e23bd1ebe7af9f35162a74971fdd0788aab':'generic retry and whole fresh authorized comment attempt','8fc655d2ddca93357f9fc9475b41839d62652037':'grant-pattern configuration preflight','b803fe2':'excluded: later classification consumer policy'},'normalization_notes':['M1 queue production test uses actual current WorkflowRequest/build_job_queue API; queue-generated job-id assertions remain unchanged.','Shared test fakes and constructor fixtures migrate only required tracker settings, writer attribution, native comments and receipts.','Server-name documentation census retains exact two-consumer assertion, using grouped path/TrackerSettings typed input instead of globally matching ambiguous server_name attribute.','Later M7 alarm-parser and M2/M4 classification tests remain unmodified in donor and excluded by function, with explicit source-map ownership.','Ruff-only import placement/format normalization; no source edit during frozen full gate.'],'source_audit_counts':{s:sum(r['status']==s for r in audit) for s in sorted({r['status'] for r in audit})},'files':files,'operation_log':operations}
(S/'m1-lease-extraction-manifest-90aea37.json').write_text(json.dumps(output,indent=2)+'\n')
print(f'{len(files)} paths, {sum(len(f["hunks"]) for f in files)} hunks; every partial source node has explicit normalization; {output["source_audit_counts"]}')
