# Protected assertion comparison

`AssertionDriftDetector.compare` takes the complete graded commit SHA, a head
reference, and explicitly supplied `ProtectedTestRef` values. Each reference
names its owning source, a repository-relative Python path, and a function or
class-qualified method. Both commits are pinned before source reads. The
detector returns typed deviation claims with commit and blob identities,
source locations, and the before/after assertion expressions.

The native `GitSourceReader` reads local commit and blob objects. It does not
check out files, execute test code, fetch remote references, or change the
repository. Replacement objects and pathspec interpretation are disabled;
raw blob bytes preserve source encodings and line endings. The Git operations
are described by [rev-parse](https://git-scm.com/docs/git-rev-parse),
[ls-tree](https://git-scm.com/docs/git-ls-tree), and
[cat-file](https://git-scm.com/docs/git-cat-file). Cancellation settles the
owned subprocess before returning.

Comparison uses Python assertion condition syntax in source order. Formatting,
comments, assertion diagnostic messages, and an added unrelated test do not
change existing assertions. A changed condition or removal of an assertion
from the named test produces a claim. The integration fixture runs both
commits successfully, changes an implementation and its protected expected
value together, and still observes the deviation. Test greenness never enters
the detector as an exemption.

This is a syntactic detection service. It does not evaluate helper functions,
parameter tables, fixture values, or other transitive expectation dependencies.
Missing or ambiguous definitions, unavailable objects, nonregular files,
invalid Python, and a graded test with no Python `assert` statements refuse
with typed errors. Explicitly supplying no protected references yields no
claims; the service does not infer a protection declaration from source prose.

The source-owned reference producer, audit invocation and claim publication
remain separate work. Returned claims are observations, not audit judgments or
permission to rewrite assertions. No tracker writer, protection enforcement,
or durable carrier is introduced here.
