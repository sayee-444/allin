"""The ``@`` directives a human types in a Prompt: Command prompts (#14).

Only a human's input has them; in text from another Session ``@…`` is literal (ADR-0013).

- ``@key=value`` anywhere: a Sobrescritura de parámetro, for this Turn only. Its key comes
  from a closed list of what llama applies per Step (docs/research/llama-serve-settings.md);
  it leaves the text.
- ``@/…``, ``@~…`` or ``@./…`` after a space or at the start: a Referencia a archivo, which
  ``attachments.attach`` copies into the Carpeta de trabajo; the text then names the copy.
- ``@word …`` at the very start: a Command session, which arrives with F2.
- Any other ``@`` (an address, ``hola @juan``) is literal.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from allin.core.errors import CommandSessionsNotYet, ParamOverrideInvalid
from allin.core.settings import Sampling

_OVERRIDE = re.compile(r"@([a-z_]+)=(\S*)")
_REFERENCE = re.compile(r"@((?:/|~|\./)\S*)")
_COMMAND_SESSION = re.compile(r"\s*@([a-z][a-z0-9_]*)(?:\s|$)")
_SPACE = re.compile(r"(\s+)")
# Sent as arguments of the SDK, as the Session's Sampling settings are.
_SAMPLING_KEYS = ("temperature", "top_p", "max_output_tokens")


class TurnOverrides(BaseModel, frozen=True):
    """The parameters a Prompt may change for its Turn: the closed list (#26). Never
    functions, Sysprompt nor Alcance; ``None`` leaves the Session's own."""

    model_config = ConfigDict(extra="forbid")

    temperature: float | None = Field(default=None, ge=0)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    top_k: int | None = Field(default=None, ge=0)
    min_p: float | None = Field(default=None, ge=0, le=1)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    repeat_penalty: float | None = Field(default=None, ge=0)
    seed: int | None = None
    reasoning_budget_tokens: int | None = Field(default=None, ge=-1)
    # The model's template decides which it takes: one it refuses fails the Step with the
    # Server's 500, never silently (the research behind the list, point 3).
    effort: Literal["low", "medium", "high", "xhigh"] | None = None

    def sampling(self, base: Sampling) -> Sampling:
        """The Session's Sampling with this Turn's overrides on it."""
        changed = {key: getattr(self, key) for key in _SAMPLING_KEYS}
        return base.model_copy(update={k: v for k, v in changed.items() if v is not None})

    def extra_params(self) -> dict[str, Any]:
        """What else goes in the request: ``reasoning`` and llama's own ``extra_body``."""
        params: dict[str, Any] = {}
        if self.effort is not None:
            params["reasoning"] = {"effort": self.effort}
        extra = self.model_dump(exclude_none=True, exclude={*_SAMPLING_KEYS, "effort"})
        if extra:
            params["extra_body"] = extra
        return params


@dataclass(frozen=True)
class _Reference:
    path: str  # as typed, after the @


@dataclass(frozen=True)
class ParsedPrompt:
    parts: tuple[str | _Reference, ...]
    overrides: TurnOverrides

    @property
    def references(self) -> tuple[str, ...]:
        return tuple(part.path for part in self.parts if isinstance(part, _Reference))

    def text_with(self, names: Mapping[str, str]) -> str:
        """The text to send, each Referencia a archivo given the name its copy has (as typed if
        ``names`` lacks it)."""
        return "".join(
            names.get(part.path, f"@{part.path}") if isinstance(part, _Reference) else part
            for part in self.parts
        )


def parse_prompt(text: str) -> ParsedPrompt:
    """Find the Command prompts in a human's Prompt; raise if one cannot be used, so the
    Prompt is not sent."""
    command = _COMMAND_SESSION.match(text)
    if command is not None:
        raise CommandSessionsNotYet(command=command.group(1))
    parts: list[str | _Reference] = []
    overrides: dict[str, str] = {}
    # An override leaves with the space before it, or the one after it if it starts the text;
    # nothing else changes, so a Prompt without Command prompts goes exactly as typed.
    dropped = False
    for token in _SPACE.split(text):
        if not token:
            continue
        if token.isspace():
            if not dropped:
                parts.append(token)
            dropped = False
            continue
        dropped = False
        if (override := _OVERRIDE.fullmatch(token)) is not None:
            overrides[override.group(1)] = override.group(2)
            if parts and isinstance(parts[-1], str) and parts[-1].isspace():
                parts.pop()
            else:
                dropped = True
        elif (reference := _REFERENCE.fullmatch(token)) is not None:
            parts.append(_Reference(reference.group(1)))
        else:
            parts.append(token)
    return ParsedPrompt(tuple(parts), _overrides(overrides))


def _overrides(values: Mapping[str, str]) -> TurnOverrides:
    allowed: list[JsonValue] = [*sorted(TurnOverrides.model_fields)]
    for key in values:
        if key not in TurnOverrides.model_fields:
            raise ParamOverrideInvalid(key=key, reason="not in the closed list", allowed=allowed)
    try:
        return TurnOverrides.model_validate(dict(values))
    except ValidationError as error:
        problem = error.errors()[0]
        key = str(problem["loc"][0])
        raise ParamOverrideInvalid(key=key, value=values[key], reason=problem["msg"]) from error
