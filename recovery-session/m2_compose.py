import sys,ast,json
sys.path.insert(0,'/private/tmp/kodezart-recovery-session')
from extract_m2 import *
p='src/kodezart/domain/fire_spec.py';(T/p).write_text('"""Read exact native criterion template fields without I/O."""\n')
for name in ['_CRITERION_ROW','_FENCE','CriterionField','_without_comments','criterion_field_bodies','criterion_check']:symbol(p,name)
symbol('src/kodezart/domain/errors.py','InvalidFireCriterionError')
symbol('src/kodezart/composition/passes.py','build_prompt_passes')
p='src/kodezart/composition/passes.py';text=(T/p).read_text();text=text.replace('from kodezart.composition.records import','from kodezart.composition.organize import build_organize_tick, verify_organize_configuration\nfrom kodezart.core.protocols import WorkspaceProvider\nfrom kodezart.composition.records import',1)
text=text.replace('            key.value: row.signals for key, row in prompt_pass_schedule(config).items()','            key.value: row.signals\n            for key, row in prompt_pass_schedule(config).items()\n            if key is not PromptKey.GROOMING_PASS or not operation.organize_scopes')
text=text.replace('    _verify_knowledge_destinations(config=config, operation=operation)','    organize = verify_organize_configuration(\n        config=config, operation=operation, tracker=tracker\n    )\n    _verify_knowledge_destinations(config=config, operation=operation)')
text=text.replace('    for key in prompt_pass_schedule(config):\n        _assert_renders','    for key in prompt_pass_schedule(config):\n        if organize and key is PromptKey.GROOMING_PASS:\n            continue\n        _assert_renders')
owner=node(text,'build_dispatch_runtime');lines=text.splitlines(keepends=True);chunk=''.join(lines[start(owner):owner.end_lineno]);chunk=chunk.replace('    cache: RepoCache,','    cache: RepoCache,\n    workspace: WorkspaceProvider,');chunk=chunk.replace('                recorder=recorder,','                recorder=recorder,\n                organize=build_organize_tick(\n                    config=config,\n                    operation=operation,\n                    tracker=None if dialled is None else dialled.tracker,\n                    runner=runner,\n                    workspace=workspace,\n                    git=git,\n                    prompts=prompts,\n                    skills=skills,\n                    gate=gate,\n                ),');lines[start(owner):owner.end_lineno]=[chunk];(T/p).write_text(''.join(lines))
p='src/kodezart/main.py';text=(T/p).read_text();a=text.index('await build_dispatch_runtime(');b=text.index('\n        )',a);chunk=text[a:b].replace('            cache=stack.cache,','            cache=stack.cache,\n            workspace=stack.workspace,');text=text[:a]+chunk+text[b:];(T/p).write_text(text)
# Move newly registered schemas before their dictionary, then add its two exact entries.
p='src/kodezart/types/domain/agent.py';text=(T/p).read_text()
for name in ['ORGANIZE_ADMISSION_SCHEMA','ORGANIZE_PROPOSAL_SCHEMA']:
 lines=text.splitlines(keepends=True);n=node(text,name);chunk=lines[start(n):n.end_lineno];del lines[start(n):n.end_lineno];pos=start(node(''.join(lines),'WIRE_SCHEMAS'));lines[pos:pos]=chunk+['\n'];text=''.join(lines)
text=text.replace('WIRE_SCHEMAS: dict[str, dict[str, object]] = {','WIRE_SCHEMAS: dict[str, dict[str, object]] = {\n    "ORGANIZE_ADMISSION_SCHEMA": ORGANIZE_ADMISSION_SCHEMA,\n    "ORGANIZE_PROPOSAL_SCHEMA": ORGANIZE_PROPOSAL_SCHEMA,');(T/p).write_text(text)
# M2 namespaces from their exact owner statements, keeping unrelated bindings local.
p='src/kodezart/core/prompt_namespaces.py';text=(T/p).read_text().replace('PER_CALL_VARIABLE_NAMES: frozenset[str] = frozenset(\n    {','PER_CALL_VARIABLE_NAMES: frozenset[str] = frozenset(\n    {\n        "organize_context", "mandate_rubric", "issue_body", "issue_key",\n        "linked_issue_bodies", "refusal_evidence", "defect_classes", "criterion_issue_bodies",');src=read(p);n=node(src,'operation_bindings');nodes=[x for x in n.body if isinstance(x,ast.Expr) and isinstance(x.value,ast.Call) and len(x.value.args)>1 and isinstance(x.value.args[1],ast.Constant) and x.value.args[1].value in ['scope_labels','organize_mandates']];chunk=''.join(''.join(src.splitlines(keepends=True)[start(x):x.end_lineno])+'\n' for x in nodes);pos=text.index('    _bind_absentable(',text.index('def operation_bindings'));text=text[:pos]+chunk+text[pos:];(T/p).write_text(text)
p='src/kodezart/types/domain/operation.py';text=(T/p).read_text().replace('    "issue_labels": ConfigOwnership.OWNED,','    "issue_labels": ConfigOwnership.OWNED,\n    "scope_labels": ConfigOwnership.OWNED,\n    "organize_mandates": ConfigOwnership.LOCAL,\n    "organize_scopes": ConfigOwnership.LOCAL,');(T/p).write_text(text)
for family in ['claude-opus','anthropic_v5']:
 for keyname in ['organize_assess','organize_author','organize_verify','organize_criteria_author']:whole(f'src/kodezart/prompts/sets/{family}/{keyname}.md')
 p=f'src/kodezart/prompts/sets/{family}/set.toml';text=(T/p).read_text()
 if family=='claude-opus':text+='\norganize_assess = ["code-review"]\norganize_author = ["code-review"]\norganize_verify = ["code-review"]\norganize_criteria_author = ["code-review"]\n'
 else:
  text=text.replace('    "grooming_pass",','    "grooming_pass",\n    "organize_author",\n    "organize_criteria_author",');text=text.replace('    "content_audit",','    "content_audit",\n    "organize_assess",\n    "organize_verify",')
 (T/p).write_text(text)
for p in sorted((D/'tests').rglob('*.py')):
 if 'organize' in p.name:whole(str(p.relative_to(D)))
for p in ['tests/tracker/test_criterion_creation.py','tests/tracker/test_body_revisions.py','tests/tracker/test_issue_identity_boundary.py','tests/tracker/test_scope_approval.py','tests/tracker/test_milestone_approval.py','tests/tracker/test_native_approval_aliases.py','tests/domain/test_topology.py','tests/domain/test_topology_cycles.py']:
 whole(p)
f=pathlib.Path('/private/tmp/kodezart-recovery-session/m2-extracted-hunks.json');f.write_text(json.dumps(json.loads(f.read_text())+records,indent=2)+'\n')
