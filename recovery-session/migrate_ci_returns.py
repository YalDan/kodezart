import ast
from pathlib import Path
for p in Path('tests').rglob('*.py'):
    source=p.read_text()
    if not ('wait_for_checks' in source or 'failed_check_names(' in source): continue
    tree=ast.parse(source);lines=source.splitlines(keepends=True);offsets=[0]
    for line in lines:offsets.append(offsets[-1]+len(line))
    edits=[]
    for n in ast.walk(tree):
        if isinstance(n,ast.Subscript) and isinstance(n.value,ast.Await) and isinstance(n.slice,ast.Constant) and n.slice.value==0:
            call=n.value.value
            if isinstance(call,ast.Call) and isinstance(call.func,ast.Attribute) and call.func.attr=='wait_for_checks':
                edits.append((offsets[n.lineno-1]+n.col_offset,offsets[n.end_lineno-1]+n.end_col_offset, f'({ast.get_source_segment(source,n.value)}).checks_passed'))
    for a,b,s in sorted(edits,reverse=True):source=source[:a]+s+source[b:]
    if source!=p.read_text():p.write_text(source)
