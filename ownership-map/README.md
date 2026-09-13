# Ownership map, main 4661a24b -> donor eae9a940 (2026-09-13)

- ownership_map.json: {path: {owner: M1..M7, reason, split}} for all 752 delta paths; 44 hunk-split files carry split.symbols_by_milestone.
- ownership_map_flat.json: path -> owner.
- ownership_facts_full.json: per-path status, churn, kind, first-seen milestone head, module-level kodezart.* imports (parsed with python 3.12; ownership_facts.json is the python 3.9 run with 46 unparsed files, kept for provenance).
- check_order.py: import-order checker. Order main -> M1 -> M4 -> M3 -> {M2,M5}; M5 -> {M6,M7}; M2/M5 and M6/M7 may not import each other; M6/M7 may not import M2.
  Run: python3 check_order.py ownership_map_flat.json ownership_facts_full.json -> 120 edges, 117 on hunk-split files (resolved by the recorded splits), 3 irreducible seams (see initiative comment 2a747867 and reply 33ce8751).
