"""Token counts of a Turn's Steps, from the ``usage`` the server returns and llama.cpp's ``timings``.

Every Step reads the whole Conversation again, so its ``input_tokens`` already include what
earlier Steps read: the Context is the last Step's input plus output, never a sum over Steps.
"""

from allin.main.conversation import Step


def context_tokens(steps: list[Step]) -> int | None:
    """The Context after these Steps; ``None`` if the last one has no usage."""
    if not steps or steps[-1].usage is None:
        return None
    usage = steps[-1].usage
    return usage["input_tokens"] + usage["output_tokens"]


def tokens_per_second(steps: list[Step]) -> float | None:
    """How fast the last Step generated, per llama.cpp; ``None`` without its timings."""
    if not steps or steps[-1].timings is None:
        return None
    return steps[-1].timings.get("predicted_per_second")


def turn_tokens(steps: list[Step]) -> int:
    """What the Turn cost: each Step's output plus the input the server had not cached."""
    return sum(_new_input(step) + step.usage["output_tokens"] for step in steps if step.usage)


def _new_input(step: Step) -> int:
    assert step.usage is not None
    if step.timings is not None and "prompt_n" in step.timings:
        return step.timings["prompt_n"]
    cached = (step.usage.get("input_tokens_details") or {}).get("cached_tokens") or 0
    return step.usage["input_tokens"] - cached
