import sys,ast,json
from pathlib import Path
sys.path.insert(0,'/private/tmp/kodezart-recovery-session');import extract_m2 as h
h.T=Path('/private/tmp/kodezart-v03-m3-plan-walk-census');O=Path('/private/tmp/kodezart-recovery-session')
h.whole('tests/adapters/test_git_branch_merger.py')
for n in ['_generate_live','_judge_lines']:h.symbol('tests/integration/test_live_criteria_probes.py',n)
p=h.T/'tests/integration/test_live_criteria_probes.py';s=p.read_text().replace('        committer_name="kodezart-live-probe",\n','').replace('        committer_email="probe@kodezart-test.invalid",\n','');p.write_text(s)
h.symbol('tests/prompts/test_skills_loadouts.py','test_configured_skills_reach_the_executor_through_chain_dispatch')
for name in ['tests/prompts/test_skills_loadouts.py','tests/prompts/test_v5_fragments.py']:
 p=h.T/name;s=p.read_text();needle='    PromptKey.KNOWLEDGE_MAP,' if 'loadouts' in name else '        PromptKey.KNOWLEDGE_MAP.value,';s=s.replace(needle,needle+'\n'+('    PromptKey.NATIVE_WRITER_CONTRACT,' if 'loadouts' in name else '        PromptKey.NATIVE_WRITER_CONTRACT.value,'));p.write_text(s)
p=h.T/'tests/prompts/sets.py';s=p.read_text();needle='EXTENDED_CASES: dict[str, tuple[PromptKey, dict[str, object]]] = {'
s=s.replace(needle,needle+'''
    "native_writer_contract": (PromptKey.NATIVE_WRITER_CONTRACT, {"pinned_rulings": "Current pinned ruling fixture"}),
    "amendment_judge": (PromptKey.AMENDMENT_JUDGE, {
        "claim": "Exact native criterion claim", "criteria": "Current tracker Check",
        "pinned_rulings": "Current pinned ruling fixture", "base_sha": "a" * 40,
    }),
    "amendment_author": (PromptKey.AMENDMENT_AUTHOR, {
        "claim": "Exact native criterion claim", "judgment": "Independent base judgment",
        "prior": "Exact prior tracker artifact", "finding": "Fresh readback finding",
        "preserve_subject": True,
    }),''');p.write_text(s)
p=h.T/'tests/prompts/test_v5_wiring.py';s=p.read_text().replace('ARTIFACT_TAGS: dict[str, tuple[str, ...]] = {','''ARTIFACT_TAGS: dict[str, tuple[str, ...]] = {
    "native_writer_contract": ("pinned_rulings",),
    "amendment_judge": ("claim", "current_criteria", "pinned_rulings", "base_sha"),
    "amendment_author": ("claim", "independent_judgment", "exact_prior_artifact", "write_back_finding", "preserve_subject"),''');p.write_text(s)
p=h.T/'tests/types/test_wire_schemas.py';s=p.read_text().replace('from kodezart.types.domain.criteria import CRITERION_ID_PATTERN','from kodezart.types.domain.amendment import AmendmentJudgment, NativeWriterOutput\nfrom kodezart.types.domain.amendment_write import AmendmentTextOutput\nfrom kodezart.types.domain.remediation import RemediationPlan\nfrom kodezart.types.domain.criteria import CRITERION_ID_PATTERN');s=s.replace('WIRE_MODELS: dict[str, type[BaseModel]] = {','''WIRE_MODELS: dict[str, type[BaseModel]] = {
    "NATIVE_WRITER_SCHEMA": NativeWriterOutput,
    "AMENDMENT_JUDGMENT_SCHEMA": AmendmentJudgment,
    "AMENDMENT_TEXT_SCHEMA": AmendmentTextOutput,
    "REMEDIATION_SCHEMA": RemediationPlan,''')
# Exact actual caller registrations, preserving the existing forwarding guard.
tree=ast.parse((h.T/'src/kodezart/services/native_amendments.py').read_text())
for name in ['native_amendments','amendment_writeback']:
 tree=ast.parse((h.T/f'src/kodezart/services/{name}.py').read_text())
 for cls in [n for n in tree.body if isinstance(n,ast.ClassDef)]:
  for fn in [n for n in cls.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))]:
   for call in [n for n in ast.walk(fn) if isinstance(n,ast.Call)]:
    for kw in call.keywords:
     if kw.arg=='output_schema':
      value=(f'services/{name}.py',(cls.name,fn.name),ast.unparse(call.func),ast.unparse(kw.value))
      s=s.replace('AUDIT_SCHEMA_BINDINGS = [','AUDIT_SCHEMA_BINDINGS = [\n    '+repr(value)+',',1)
p.write_text(s)
(O/'m3-census-extracted-hunks.json').write_text(json.dumps(h.records,indent=2)+'\n')
