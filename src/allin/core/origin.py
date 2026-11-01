"""Where a Prompt or a command comes from (ADR-0013).

In F1 everything is typed by a human, in a Window or in Main's Chat. The chain of Sessions an
action came through, and the weakest link that bounds its authority, arrive with F2.
"""

from typing import Literal

# ``main``: what Main asks on its own, which never counts as human.
type Origin = Literal["human", "session", "main"]

HUMAN: Origin = "human"
