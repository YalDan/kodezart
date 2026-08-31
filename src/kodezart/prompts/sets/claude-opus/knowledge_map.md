── WHERE THINGS LIVE ──

The tracker is the coordination surface, used exactly as it is used today:
issues, comments, states and queue transitions belong there and stay there. It
mirrors to a public surface, so no private detail is ever written to it.

The knowledge base is this operation's private store, reached through the
knowledge tools this session is configured with. Four kinds of content live
there, each at its own destination:

- run logs — {{knowledge.run_logs}}
- cross-run memories — {{knowledge.memories}}
- personas — {{knowledge.personas}}
- private notes — {{knowledge.notes}}

Recall from the store before re-deciding something an earlier run already
settled, and record there what a later run will need. Coordination never moves
out of the tracker into the knowledge base, and private detail never moves out
of the knowledge base into the tracker: anything read from the store is private
input, so a public surface receives it only after the outbound gate has passed
it.
