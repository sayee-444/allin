"""A Supervisor's Server Settings: ``servers/<name>.toml`` in the data folder (ADR-0008).

They are the load parameters ``llama serve`` starts with. A key left unset is not passed, so
llama's own default applies (``docs/research/llama-serve-settings.md``). The keys that name a
file or open the network are marked here: only a human sets them (ADR-0013).
"""

import json
import shlex
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from allin.core.errors import (
    HumanOnly,
    InvalidSetting,
    ServerNotAdoptable,
    ServerSettingsInvalid,
    UnknownSetting,
)
from allin.core.files import write_atomically
from allin.core.origin import HUMAN, Origin
from allin.core.settings import SettingName, failing_key

# Truncating the Conversation in silence is never allowed (ADR-0017).
_CONTEXT_SHIFT = "--context-shift"
# llama also reads it from its environment.
CONTEXT_SHIFT_ENV = "LLAMA_ARG_CONTEXT_SHIFT"
# The unified cache has its own key, so the two never contradict each other.
_FORBIDDEN = frozenset({_CONTEXT_SHIFT, "--kv-unified", "--no-kv-unified", "-kvu", "-no-kvu"})
_LLAMA_PORT = 8080  # llama's own, for a line that does not say

type FlashAttn = Literal["on", "off", "auto"]
type Reach = Literal["file", "network"]

# The keys that name a file or open the network. The Supervisor is not enclosed, so a Session
# that set them would make it read or open what its tools cannot reach (ADR-0008, ADR-0013).
# ``command`` runs a program, and ``extra`` may carry any flag of either kind.
REACHING_KEYS: Mapping[SettingName, frozenset[Reach]] = {
    SettingName("model"): frozenset({"file"}),
    SettingName("mmproj"): frozenset({"file"}),
    SettingName("cwd"): frozenset({"file"}),
    SettingName("command"): frozenset({"file"}),
    SettingName("port"): frozenset({"network"}),
    SettingName("extra"): frozenset({"file", "network"}),
}


class ServerSettings(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, description="-m: the .gguf file")
    mmproj: str | None = Field(default=None, description="--mmproj: the image projector")
    context: int | None = Field(default=None, gt=0, description="-c: tokens for all slots")
    parallel: int | None = Field(default=None, ge=1, description="-np: slots")
    # --kv-unified: unset, on with two or more slots, so each sees the whole pool.
    kv_unified: bool | None = None
    gpu_layers: str | None = Field(default=None, description="-ngl: a number, auto or all")
    flash_attn: FlashAttn | None = Field(default=None, description="-fa")
    cache_type_k: str | None = Field(default=None, description="-ctk")
    cache_type_v: str | None = Field(default=None, description="-ctv")
    spec_type: str | None = Field(default=None, description="--spec-type")
    port: int = Field(ge=1, le=65535, description="--port, on 127.0.0.1")
    extra: list[str] = Field(default_factory=list[str], description="any other flags, as typed")
    command: list[str] = Field(default_factory=lambda: ["llama", "serve"], min_length=1)
    cwd: str | None = Field(default=None, description="the folder relative paths start from")

    @field_validator("extra")
    @classmethod
    def _no_forbidden_flags(cls, extra: list[str]) -> list[str]:
        forbidden = _FORBIDDEN.intersection(word.split("=", 1)[0] for word in extra)
        if forbidden:
            msg = f"not allowed: {', '.join(sorted(forbidden))}"
            if _CONTEXT_SHIFT in forbidden:
                msg += " (it would truncate the Conversation in silence)"
            raise ValueError(msg)
        return extra

    @field_validator("cwd")
    @classmethod
    def _absolute(cls, cwd: str | None) -> str | None:
        if cwd is not None and not Path(cwd).is_absolute():
            msg = "must be an absolute path"
            raise ValueError(msg)
        return cwd

    def arguments(self) -> list[str]:
        """``llama serve`` with these Settings."""
        args = [*self.command, "-m", self.model]
        optional = {
            "--mmproj": self.mmproj,
            "-c": self.context,
            "-np": self.parallel,
            "-ngl": self.gpu_layers,
            "-fa": self.flash_attn,
            "-ctk": self.cache_type_k,
            "-ctv": self.cache_type_v,
            "--spec-type": self.spec_type,
        }
        for flag, value in optional.items():
            if value is not None:
                args += [flag, str(value)]
        unified = self.unified_cache()
        if unified is not None:
            args.append("--kv-unified" if unified else "--no-kv-unified")
        return [*args, "--port", str(self.port), *self.extra]

    def base_url(self) -> str:
        """Where Main sends the Steps of the Sessions linked to it."""
        return f"http://127.0.0.1:{self.port}/v1"

    def unified_cache(self) -> bool | None:
        """Whether the Server gets ``--kv-unified`` (or ``--no-kv-unified``); ``None``: neither,
        and llama decides."""
        if self.kv_unified is None and (self.parallel or 1) >= 2:
            return True
        return self.kv_unified

    def effective(self) -> "ServerSettings":
        """The same Settings with what they leave to their own rules spelled out, so two that
        launch the same Server compare equal."""
        return self.model_copy(update={"kv_unified": self.unified_cache()})


def shares_pool(arguments: list[str]) -> bool:
    """Whether a Server running ``arguments`` shares one pool among its slots
    (``--kv-unified``); a line Main cannot read counts as shared, the careful guess."""
    try:
        return parse_cmdline(arguments).unified_cache() is not False
    except ServerNotAdoptable:
        return True


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
        reason = failure["msg"]
        raise ServerSettingsInvalid(
            file=path.name, key=failing_key(failure), reason=reason
        ) from error


def save_server_settings(path: Path, settings: ServerSettings) -> None:
    """Write them whole, as TOML: only the keys that are set."""
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomically(path, server_toml(settings.model_dump(exclude_none=True)))


def server_toml(values: Mapping[str, object]) -> str:
    """Server Settings as their TOML file has them."""
    return "".join(f"{key} = {_toml_value(value)}\n" for key, value in values.items())


def _toml_value(value: object) -> str:
    """A key's value in TOML: what JSON writes for a string, a number, a bool or a list of
    strings is valid TOML too."""
    return json.dumps(value, ensure_ascii=False)


def check_server_authority(keys: Iterable[SettingName], origin: Origin) -> None:
    """Raise ``HumanOnly`` for a key that names a file or opens the network, unless a human
    sets it (ADR-0013)."""
    for key in keys:
        if key in REACHING_KEYS and origin != HUMAN:
            raise HumanOnly(action=key, origin=origin)


def apply_server_assignments(
    settings: ServerSettings | None, assignments: Mapping[SettingName, str]
) -> ServerSettings:
    """``settings`` (or none at all) with each key set to its value as typed; an empty value
    unsets it. Raises ``UnknownSetting`` or ``InvalidSetting`` and changes nothing if any key
    is unknown or any value invalid."""
    data = settings.model_dump(exclude_none=True) if settings is not None else {}
    fields = ServerSettings.model_fields
    for key, raw in assignments.items():
        field = fields.get(key)
        if field is None:
            raise UnknownSetting(key=key)
        if raw == "":
            data.pop(key, None)
        elif list[str] in (field.annotation, *get_args(field.annotation)):
            data[key] = shlex.split(raw)
        else:
            data[key] = raw
    try:
        return ServerSettings.model_validate(data)
    except ValidationError as error:
        failure = error.errors()[0]
        raise InvalidSetting(key=failing_key(failure), reason=failure["msg"]) from error


# How ``llama serve`` spells each flag that has its own key.
_VALUED = {
    "-m": "model",
    "--model": "model",
    "-mm": "mmproj",
    "--mmproj": "mmproj",
    "-c": "context",
    "--ctx-size": "context",
    "-np": "parallel",
    "--parallel": "parallel",
    "-ngl": "gpu_layers",
    "--gpu-layers": "gpu_layers",
    "--n-gpu-layers": "gpu_layers",
    "-ctk": "cache_type_k",
    "--cache-type-k": "cache_type_k",
    "-ctv": "cache_type_v",
    "--cache-type-v": "cache_type_v",
    "--spec-type": "spec_type",
    "--port": "port",
}
_KV_UNIFIED = {"-kvu": True, "--kv-unified": True, "-no-kvu": False, "--no-kv-unified": False}
_FLASH_ATTN = frozenset({"-fa", "--flash-attn"})
_FLASH_ATTN_VALUES = frozenset(get_args(FlashAttn.__value__))


def parse_cmdline(argv: list[str], cwd: str | None = None) -> ServerSettings:
    """The Server Settings that launch ``argv`` (a ``llama serve`` line, as
    ``/proc/<pid>/cmdline`` has it) again, from ``cwd``. Raises ``ServerNotAdoptable``."""
    command: list[str] = []
    while len(command) < len(argv) and not argv[len(command)].startswith("-"):
        command.append(argv[len(command)])
    if not command:
        raise ServerNotAdoptable(reason="not a llama serve line")
    data: dict[str, object] = {"command": command, "cwd": cwd}
    extra: list[str] = []
    rest = argv[len(command) :]
    at = 0
    while at < len(rest):
        word = rest[at]
        at += 1
        flag, equals, inline = word.partition("=")
        following = rest[at] if at < len(rest) else ""
        if flag == _CONTEXT_SHIFT:
            reason = "it runs with --context-shift, which truncates the Conversation in silence"
            raise ServerNotAdoptable(reason=reason)
        if flag in _VALUED:
            data[_VALUED[flag]] = inline if equals else following
            at += 0 if equals else 1
        elif flag in _KV_UNIFIED:
            data["kv_unified"] = _KV_UNIFIED[flag]
        elif flag in _FLASH_ATTN:  # its value is optional: bare, it means on
            takes_next = not equals and following in _FLASH_ATTN_VALUES
            data["flash_attn"] = inline if equals else (following if takes_next else "on")
            at += 1 if takes_next else 0
        else:
            extra.append(word)
    data["extra"] = extra
    data.setdefault("port", _LLAMA_PORT)
    slots = data.get("parallel")
    if "kv_unified" not in data and isinstance(slots, str) and slots.isdigit() and int(slots) >= 2:
        data["kv_unified"] = False  # llama leaves it off when the slots are given
    try:
        return ServerSettings.model_validate(data)
    except ValidationError as error:
        failure = error.errors()[0]
        raise ServerNotAdoptable(key=failing_key(failure), reason=failure["msg"]) from error
