Golden fixtures for the prompt registry.

`claude_opus_empty_skills/` pins the rendered output of every claude-opus
member with the set-level skills fragment bound EMPTY. Those bytes are the
92597c0 output of the Python prompt modules the set replaced. They are
permanent — KOD-46 adds its own goldens over body plus a populated fragment
rather than re-baselining these.

`claude_opus_populated_skills/` pins the same renders with the fragment
populated from the set's `[skills]` loadouts.
