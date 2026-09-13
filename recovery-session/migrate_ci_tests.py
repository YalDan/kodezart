import ast
from pathlib import Path

for path in Path('tests').rglob('*.py'):
    source=path.read_text()
    if 'wait_for_checks' not in source or path.name in {'test_ci_watch_evidence.py','test_check_classification.py','test_ci_watch_result.py','fakes.py'}:
        continue
    tree=ast.parse(source)
    lines=source.splitlines(keepends=True)
    offsets=[0]
    for line in lines: offsets.append(offsets[-1]+len(line))
    edits=[]
    for node in ast.walk(tree):
        if not isinstance(node,ast.Assign) or len(node.targets)!=1 or not isinstance(node.targets[0],ast.Tuple): continue
        if not isinstance(node.value,ast.Await) or not isinstance(node.value.value,ast.Call): continue
        func=node.value.value.func
        if not isinstance(func,ast.Attribute) or func.attr!='wait_for_checks': continue
        target=node.targets[0]
        if len(target.elts)!=2 or not all(isinstance(x,ast.Name) for x in target.elts): continue
        passed,summary=[x.id for x in target.elts]
        indent=' '*node.col_offset
        replacement=f'observed = {ast.get_source_segment(source,node.value)}\n{indent}{passed} = observed.checks_passed if isinstance(observed, ObservedChecks) else None\n{indent}{summary} = observed.summary'
        edits.append((offsets[node.lineno-1]+node.col_offset,offsets[node.end_lineno-1]+node.end_col_offset,replacement))
    for start,end,replacement in sorted(edits,reverse=True): source=source[:start]+replacement+source[end:]
    if edits and 'from kodezart.types.domain.check_observation import ' not in source:
        # Put beside the first non-future import, then let Ruff order imports.
        tree=ast.parse(source)
        first=next(n for n in tree.body if isinstance(n,(ast.Import,ast.ImportFrom)) and not (isinstance(n,ast.ImportFrom) and n.module=='__future__'))
        ls=source.splitlines(keepends=True);ls.insert(first.lineno-1,'from kodezart.types.domain.check_observation import ObservedChecks\n');source=''.join(ls)
    if source!=path.read_text(): path.write_text(source)
