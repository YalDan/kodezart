"""One durable run event, as a lane's stream carries it.

The vocabulary and its notification partition are owned by
``types/domain/run_event.py``; this is the value a producer posts and a
reader enumerates.
"""

from pydantic import ConfigDict, Field

from kodezart.types.base import CamelCaseModel
from kodezart.types.domain.run_event import RunEventKind


class RunEventRecord(CamelCaseModel):
    """One thing that happened to a lane, posted and never edited.

    Posting and editing are different writes with different meanings: an
    event is a thing that happened and takes its place in an order, while
    a record the run keeps current is one current fact. A stream that
    admitted edited records would report the last edit as the moment.

    ``subject_ref`` is the producer's own address for what the event is
    about, carried verbatim. One lane's stream mixes events about its
    criteria, its surfaces and its alarms, and a stream that could not say
    which is not one anybody can read.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    lane_key: str = Field(min_length=1, pattern=r"\S")
    kind: RunEventKind
    subject_ref: str = Field(min_length=1, pattern=r"\S")
