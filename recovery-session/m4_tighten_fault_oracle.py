from pathlib import Path
p=Path('/private/tmp/kodezart-v03-m4-verifier-extraction/tests/chains/test_write_back_tracker_boundary.py')
s=p.read_text().replace('    ISSUE,\n','    ISSUE,\n    MARKER,\n')
s=s.replace('    original = server.call_tool\n\n    async def fail_save', '    original = server.call_tool\n    raised = failure("external artifact save failure")\n    failed_saves = []\n\n    async def fail_save')
s=s.replace('''        if name == "save_comment" and "fixture-evidence" in str(arguments):
            raise failure("external failure")''','''        if name == "save_comment" and str(arguments.get("body", "")).startswith(MARKER + "\\n"):
            failed_saves.append(dict(arguments))
            raise raised''')
s=s.replace('''    with pytest.raises(failure):
        await WriteBackVerifier''','''    with pytest.raises(failure) as error:
        await WriteBackVerifier''')
s=s.replace('''    assert step.findings == [None] and not judge.seen''','''    assert error.value is raised
    assert len(failed_saves) == 1
    assert failed_saves[0]["body"] == MARKER + "\\n" + FIRST_OUTPUT
    assert step.findings == [None] and not judge.seen''')
p.write_text(s)
