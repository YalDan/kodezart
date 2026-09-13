import ast,subprocess
from pathlib import Path
T=Path('/private/tmp/kodezart-v03-m5-delivery-termination')
def drop(p,name):
 s=p.read_text();t=ast.parse(s);n=next(n for n in t.body if getattr(n,'name',None)==name);lines=s.splitlines(keepends=True);start=min([n.lineno]+[d.lineno for d in getattr(n,'decorator_list',[])])-1;del lines[start:n.end_lineno];p.write_text(''.join(lines))
p=T/'tests/adapters/test_forge_query.py';drop(p,'TestSelectionByOrigin');p.write_text(p.read_text().replace('from kodezart.composition.forge import forge_query_for_origin\n',''))
p=T/'tests/test_forge_origin_selection.py';drop(p,'test_native_pr_state_reader_is_selected_before_any_forge_read');s=p.read_text().replace('    pr_state_reader_for_origin,\n','').replace('settings=AppConfig().queue,','config=AppConfig(),');p.write_text(s)
# Keep the maintained original builder oracle in addition to the donor's new scoped cases.
src=subprocess.check_output(['git','show','10c51a7:tests/test_forge_origin_selection.py'],cwd=T,text=True);n=next(n for n in ast.parse(src).body if getattr(n,'name',None)=='test_the_builder_wires_both_arms_and_routes_between_them');chunk=''.join(src.splitlines(keepends=True)[n.lineno-1:n.end_lineno]);chunk=chunk.replace('engine = build_workflow_engine(','engine = build_workflow_engine(\n            repositories=(),',1);p.write_text(p.read_text()+'\n\n'+chunk)
