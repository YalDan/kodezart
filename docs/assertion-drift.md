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

An omitted or null designation lapses: the comparison records the lapse against
that record's comment key and its owning identity, drops that record's
protection, and goes on. Refusing the whole comparison over one record's absent
designation is what it no longer does.
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

Returned claims are observations, not audit judgments or permission to rewrite
assertions. What acts on them is the native writer's guard. Before a harness
commit is published it compares the writer's own starting HEAD with that
commit, over the designations the pinned records it holds carry — less those
of a record this run amended through the canonical writer, whose departure was
claimed and independently judged. An assertion the later reading no longer
carries is a loss; an added, reordered or reformatted assertion is not. Each
loss mints one `criterion` sub-issue on the lane, gated exactly as derived
content under that lane's criterion child-set lease, so the lane's rollup
carries the obligation and cannot converge while it stands, and publication is
refused. The mark names the test and the record that designates it, and no
assertion text leaves the repository. A comparison that cannot be made refuses
with no mark: an unreadable designation is not evidence that the assertions
survived. Deleting, renaming or moving a designated test, or its file, leaves
nothing to compare, so publication is refused with no mark.

The reading compares which assertions a designated test carries, not whether
they run: an assertion moved where it no longer runs still reads as carried.
That covers an inner definition nothing calls, a branch that never runs, code
after a `return`, and a handler that swallows `AssertionError`. A skip mark on
a designated test is the census's concern, not this reading's.

`RecordedAssertionDriftDetector`, which addresses a designation by its comment
identity and rereads the criterion family around the comparison, is not
constructed anywhere in the composition root. The current pinned-record
producer and historical declaration migration remain separate work.
