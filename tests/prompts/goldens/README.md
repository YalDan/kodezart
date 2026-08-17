Golden fixtures for the prompt registry.

`claude_opus_empty_skills/` pins the rendered output of every claude-opus
member with the set-level skills fragment bound EMPTY. They are permanent —
KOD-46 adds its own goldens over body plus a populated fragment rather than
re-baselining these.

`claude_opus_populated_skills/` pins the same renders with the fragment
populated from the set's `[skills]` loadouts.

Two provenances live side by side, and the distinction is about what a
golden's bytes are evidence OF, never about how strongly it binds:

- The **relocated** keys' bytes are the 92597c0 output of the Python prompt
  modules the set replaced — byte-identity evidence for the migration.
- The keys with no 92597c0 ancestor (`content_audit`, `fire_prep_pass`,
  `grooming_pass`, `knowledge_map`, `remediation_ticket`) were added by
  KOD-83 so that no registered function key renders unfrozen. Their bytes
  are a freeze taken at the composed base, not a migration baseline.

Both are held to the same rule: a golden is re-baselined only when the
template it renders genuinely moved in the same commit, which
`tests/test_lane_verification.py` enforces from git history.
