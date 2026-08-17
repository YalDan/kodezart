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

PROMPT_FUNCTION_COUNT: Final[int] = 18


def configured_investigation_cap() -> int:
    """The shipped fan-out cap, read off the field declaration.

    Off the DECLARATION rather than a constructed config: a suite that
    builds ``AppConfig()`` to learn the default asks the ambient
    environment what the application ships, and the answer changes with
    whoever exported a variable last.
    """
    from kodezart.core.config import AppConfig

    return int(AppConfig.model_fields["investigation_cap"].default)
