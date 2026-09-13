# Symbol-level ownership (2026-09-13)

Refinement of ownership_map.json: every added/modified/removed top-level symbol in 191 shared files is assigned to the lowest common ancestor (on the milestone tree main -> M1 -> M4 -> M3 -> {M2, M5}; M5 -> {M6, M7}) of the milestones whose files consume it. 9,184 symbols, 3,486 changed, 0 consumer-path violations, deterministic.
- ownership_symbols.json: {file: {symbol: {kind, owner, consumers, note}}}
- cut_specs.json: per milestone and file, which symbols to take from the donor / remove / keep at main.
- m1_adaptation_sites.json: 2,785 main-era call sites (118 files) an independently green M1 would have to adapt by hand.
- unsplittable.json: 120 modified symbols whose consumers sit on divergent branches.
- cut-m1-check.log: the gate log of the failed first M1 cut (28 mypy errors).
Verdict recorded on KOD-73 (2026-09-13): correct as arithmetic, not cut-ready; independently green intermediates require rewriting history by hand.
