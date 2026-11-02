"""Why a Turn was cut (ADR-0004), shared by the Conversation and the Notice modules' API."""

from typing import Literal

# Its last Step reached its Output cap (max_output_tokens) or the slot's limit, or a human
# interrupted it with Shift+Enter (ADR-0024).
type CutReason = Literal["output_cap", "slot_limit", "interrupted"]
