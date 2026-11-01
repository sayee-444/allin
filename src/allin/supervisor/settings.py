"""A Supervisor's Server Settings: ``servers/<name>.toml`` in the data folder (ADR-0008).

They are the load parameters ``llama serve`` starts with. Only a human writes them, since they
name files and open a port.
"""

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from allin.core.errors import ServerSettingsInvalid

# Truncating the Conversation in silence is never allowed (ADR-0017).
# The unified cache has its own key, so the two never contradict each other.
_FORBIDDEN = frozenset({"--context-shift", "--kv-unified", "--no-kv-unified"})


class ServerSettings(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, description="the .gguf file")
    mmproj: str | None = None
    context: int = Field(gt=0, description="-c: tokens of context for all slots")
    parallel: int = Field(default=1, ge=1, description="-np: slots")
    # --kv-unified: by default on with two or more slots, so each sees the whole pool.
    kv_unified: bool | None = None
    gpu_layers: str = "all"
    flash_attn: Literal["on", "off", "auto"] = "on"
    cache_type_k: str | None = None
    cache_type_v: str | None = None
    spec_type: str | None = None
    port: int = Field(ge=1, le=65535)
    extra: list[str] = Field(default_factory=list[str], description="any other flags, as typed")
    command: list[str] = Field(default_factory=lambda: ["llama", "serve"], min_length=1)

    @field_validator("extra")
    @classmethod
    def _no_forbidden_flags(cls, extra: list[str]) -> list[str]:
        forbidden = _FORBIDDEN.intersection(extra)
        if forbidden:
            msg = f"not allowed: {', '.join(sorted(forbidden))}"
            raise ValueError(msg)
        return extra

    def arguments(self) -> list[str]:
        """``llama serve`` with these Settings."""
        args = [*self.command, "-m", self.model, "-c", str(self.context)]
        args += ["-np", str(self.parallel), "-ngl", self.gpu_layers, "-fa", self.flash_attn]
        args += ["--port", str(self.port)]
        optional = {
            "--mmproj": self.mmproj,
            "-ctk": self.cache_type_k,
            "-ctv": self.cache_type_v,
            "--spec-type": self.spec_type,
        }
        for flag, value in optional.items():
            if value is not None:
                args += [flag, value]
        if self._kv_unified():
            args.append("--kv-unified")
        return [*args, *self.extra]

    def base_url(self) -> str:
        """Where Main sends the Steps of the Sessions linked to it."""
        return f"http://127.0.0.1:{self.port}/v1"

    def _kv_unified(self) -> bool:
        return self.kv_unified if self.kv_unified is not None else self.parallel >= 2


def load_server_settings(path: Path) -> ServerSettings:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return ServerSettings.model_validate(data)
    except FileNotFoundError as error:
        raise ServerSettingsInvalid(file=path.name, key=None, reason="missing") from error
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ServerSettingsInvalid(file=path.name, key=None, reason=str(error)) from error
    except ValidationError as error:
        failure = error.errors()[0]
        key = ".".join(str(part) for part in failure["loc"]) or None
        raise ServerSettingsInvalid(file=path.name, key=key, reason=failure["msg"]) from error
