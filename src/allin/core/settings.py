"""Settings of Main and of each Session; always inherited by copy."""

import getpass
from typing import Any

from pydantic import BaseModel, Field


class Sampling(BaseModel, frozen=True):
    """LLM sampling parameters; ``None`` means "not sent", so the server's default applies."""

    temperature: float | None = None
    top_p: float | None = None
    max_output_tokens: int | None = None

    def request_params(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class SessionSettings(BaseModel, frozen=True):
    assistant_name: str = "AllIn"
    username: str = Field(default_factory=getpass.getuser)
    sysprompt: str = "default.txt"  # a file in the sysprompts folder
    model: str = "allin"
    sampling: Sampling = Sampling()


class MainSettings(BaseModel, frozen=True):
    base_url: str = "http://127.0.0.1:9931/v1"
    parallel: int = 1  # Turns at once: the server's slots (llama-server -np)
    session: SessionSettings = Field(default_factory=SessionSettings)  # copied by /new
