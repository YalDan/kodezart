# View cutter (2026-09-14)

cut_views.py regenerates the seven review-view branches from a union/donor tree by symbol-level ownership (ownership_symbols.json + cut_specs.json), member-wise for containers, in the order M1, M4, M3, M2, M5, M6, M7; the union is the merge of the leaves M2, M6, M7 plus a reconciliation commit taking the donor blob for shared files whose splice order drifts. Run after every accepted change on v03/union so the views never drift from the tested tree. view_annotations_M*.json are the per-PR "shown in another view" lists written into the PR bodies.
