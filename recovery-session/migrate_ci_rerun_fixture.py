import ast
from pathlib import Path
p=Path('tests/adapters/test_ci_rerun.py');s=p.read_text();tree=ast.parse(s);lines=s.splitlines(keepends=True);offs=[0]
for line in lines:offs.append(offs[-1]+len(line))
edits=[]
for n in ast.walk(tree):
 if isinstance(n,ast.With) and any(isinstance(c,ast.Call) and isinstance(c.func,ast.Attribute) and c.func.attr=='failed_check_names' for c in ast.walk(n)):
  edits.append((offs[n.lineno-1],offs[n.end_lineno],''));continue
 if isinstance(n,ast.Assert):
  awaits=[a for a in ast.walk(n) if isinstance(a,ast.Await) and isinstance(a.value,ast.Call) and isinstance(a.value.func,ast.Attribute) and a.value.func.attr=='wait_for_checks']
  if len(awaits)==1:
   a=awaits[0];expression=ast.get_source_segment(s,a);old=ast.get_source_segment(s,n);new=old.replace(expression,'observed')
   edits.append((offs[n.lineno-1]+n.col_offset,offs[n.end_lineno-1]+n.end_col_offset,'observed = '+expression+'\n'+' '*n.col_offset+new))
for a,b,r in sorted(edits,reverse=True):s=s[:a]+r+s[b:]
s=s.replace('await client.failed_check_names(repo_url=REPO, ref=SHA)', 'observed.failed_check_names').replace('await client.failed_check_names(\n        repo_url=REPO, ref="feature/one"\n    )','observed.failed_check_names').replace('await fake.failed_check_names(repo_url=REPO, ref=SHA)', 'observed.failed_check_names')
s=s.replace('assert result[0] is False','assert result.checks_passed is False')
s=s.replace('''        return await fake.wait_for_checks(
            repo_url=REPO, ref=SHA
        ), observed.failed_check_names''','''        return await fake.wait_for_checks(repo_url=REPO, ref=SHA)''')
s=s.replace('''    assert await asyncio.gather(first_sequence(), second_sequence()) == [
        ((False, "second"), frozenset({"second"})),
        ((True, "third"), frozenset()),
    ]''','''    first, second = await asyncio.gather(first_sequence(), second_sequence())
    assert first.commit_sha == second.commit_sha == SHA
    assert first.checks_passed is False and first.summary == "second"
    assert first.failed_check_names == {"second"}
    assert second.checks_passed is True and second.summary == "third"
    assert second.failed_check_names == frozenset()''')
p.write_text(s)
