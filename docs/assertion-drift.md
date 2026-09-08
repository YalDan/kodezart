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

`RecordedAssertionDriftDetector.compare` supplies those inputs through actual
native readers. It reads the current criterion's structured Evidence SHA and
the current remote head of the recorded lane branch through `AuditSourceReader`.
It enumerates configured ruling comments on the lane issue and its current
criterion children. Each ruling can explicitly designate `protectedTests` using
the existing reference shape: its own `RulingId` as `sourceRef`, a canonical
Python path, and a qualified test name. `RulingProtectedTestRef` narrows the
existing reference's owner to `RulingId`; its path/name validation and wire shape
are inherited. Generic detector references still carry native comment addresses.
The existing marker, question identity,
required authorship, complete comment reader, and strict JSON parser remain the
record boundary; there is no second protection marker or inferred file owner.

An omitted or null designation remains unknown and the comparison refuses it.
An explicit empty list means that ruling designates no protected tests. A
successfully read empty configured ruling set also yields no comparisons;
unmarked historical prose is outside that record set and is not silently
migrated. Generic ruling readers remain compatible with unknown designation,
and rendering an unknown designation preserves the previous record bytes.

The collector binds every selected test to its actual native ruling comment
identity, compares immutable Git objects, then rereads the complete ruling sets,
criterion family, criterion Evidence, lane record and remote head. Changed or
unreadable inputs refuse a result. The supplied criterion establishes the graded
baseline only: a deviation belongs to the ruling source, and does not itself
refute that criterion. Neither the recorded Evidence test string nor a dispatch
base is interpreted as a protection declaration.

The current ruling-node producer, historical declaration migration, complete
scheduled audit invocation and claim publication remain separate work. Returned
claims are observations, not audit judgments or permission to rewrite assertions.
This reader introduces no tracker write or protection enforcement; any eventual
record publication must use the authorized lease and outbound gate boundaries.
