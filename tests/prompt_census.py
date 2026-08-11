"""How many prompt functions the system has, written once.

Two suites assert that the default set supplies every key, and each held
its own copy of the number.  Two copies of one census are two numbers
that can disagree, and the merge of two lanes that each added a key is
where they do.

Adding a key is an edit here as well as to :class:`PromptKey`, which is
what keeps the completeness assertions a census rather than a
restatement of the enum they are checking.
"""

from typing import Final

PROMPT_FUNCTION_COUNT: Final[int] = 17
