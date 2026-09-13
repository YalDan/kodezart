import sys
sys.path.insert(0,'/private/tmp/kodezart-recovery-session')
from extract_m2 import *
p='src/kodezart/types/domain/gating.py';text=(T/p).read_text().replace('    OutboundDestination.TRACKER_COMMENT: OutboundSurface.TRACKER,','    OutboundDestination.TRACKER_COMMENT: OutboundSurface.TRACKER,\n    OutboundDestination.TRACKER_DESCRIPTION: OutboundSurface.TRACKER,\n    OutboundDestination.TRACKER_TITLE: OutboundSurface.TRACKER,');(T/p).write_text(text)
p='src/kodezart/core/config.py';text=(T/p).read_text().replace('                "tracker_surface_lease_seconds",','                "tracker_surface_lease_seconds",\n                "organize_max_admission_rounds",\n                "organize_max_convergence_rounds",\n                "write_back_max_verify_rounds",');(T/p).write_text(text)
# Exact accepted documentation sections, preserving unrelated current deployment vocabulary.
p='docs/configuration.md';text=(T/p).read_text();d=read(p);a=d.index('## Organize phase configuration');b=d.index('## Knowledge environment migration',a);text+='\n'+d[a:b];a=d.index('The native Organize owner requires explicit');b=d.index('\n## ',a);text+='\n'+d[a:b]+'\n';(T/p).write_text(text)
p='docs/architecture.md';text=(T/p).read_text();d=read(p);a=d.index('Body revisions retain the exact same-read body');b=d.index('## Workflow Pipeline',a);text+='\n## Organize current-source admission\n\n'+d[a:b];text=text.replace('Graph/split artifacts and their current native graph ownership checks\nbelong to their later Organize integration.', 'Graph and split artifacts are read through the same canonical verifier\nfor the configured Organize owner.');(T/p).write_text(text)
p='.env.example';text=(T/p).read_text();text+='\n# Optional configured Organize owner; both bounds are required when declared.\n# KODEZART_ORGANIZE__MAX_ADMISSION_ROUNDS=3\n# KODEZART_ORGANIZE__MAX_CONVERGENCE_ROUNDS=3\n# Independent canonical write verification bound, required by the owner.\n# KODEZART_WRITE_BACK__MAX_VERIFY_ROUNDS=3\n';(T/p).write_text(text)
# Preserve actual current graph/split native artifact controls and their import fixtures.
for p in ['tests/tracker/test_artifact_native_fields.py','tests/tracker/test_artifact_split_identity_independent.py']:
 whole(p)
