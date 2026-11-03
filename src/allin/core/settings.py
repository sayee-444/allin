"""Settings of Main and of each Session; always inherited by copy.

The models below are the registry of keys: each field declares its type, its default, its
validation and what it is for, and whether it is Main's or a Session's follows from the model
it sits in. ``setting_keys`` lists them by the dotted name ``/set`` takes.
"""

import getpass
import shlex
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, NewType, get_origin

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError
from pydantic_core import ErrorDetails

from allin.core.errors import InvalidSetting, MalformedAssignment, UnknownSetting
from allin.core.ids import SUPERVISOR_NAME_PATTERN, NotificationSource
from allin.core.themes import (
    DEFAULT_THEME,
    BorderKeys,
    ColorKeys,
    FontKeys,
    GlyphKeys,
    LooseKeys,
    ThemeName,
    WindowKeys,
)

type SettingScope = Literal["main", "session"]
# A Setting's dotted key as ``/set`` takes it: ``session.sampling.temperature``.
SettingName = NewType("SettingName", str)
# What ``model_dump`` gives of a Settings model: nested dicts of plain values.
type SettingsData = dict[str, Any]


class Sampling(BaseModel, frozen=True):
    """LLM sampling parameters; ``None`` means "not sent", so the server's default applies."""

    model_config = ConfigDict(extra="forbid")

    temperature: float | None = Field(
        default=None, ge=0, description="sampling temperature; empty: the Server's"
    )
    top_p: float | None = Field(
        default=None, ge=0, le=1, description="nucleus sampling; empty: the Server's"
    )
    max_output_tokens: int | None = Field(
        default=None,
        ge=1,
        description="most tokens a Step may generate; empty: 16384, or a quarter of the slot if"
        " smaller",
    )

    def request_params(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class CompactSettings(BaseModel, frozen=True):
    """When and how a Session's Conversation is compacted (ADR-0017)."""

    model_config = ConfigDict(extra="forbid")

    auto: bool = Field(
        default=True,
        description="compact on its own before a Turn whose Step would pass the threshold",
    )
    threshold: float = Field(
        default=0.85,
        gt=0,
        le=1,
        description="share of the slot's limit a Step may need before it compacts first",
    )
    keep: float = Field(
        default=0.25,
        ge=0,
        lt=1,
        description="share of the slot's limit the last whole Turns kept literal may take",
    )
    sysprompt: str = Field(
        default="compact.txt",
        min_length=1,
        description="Sysprompt file in the sysprompts folder that guides the summary",
    )


class ToolSettings(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    timeout: float = Field(
        default=60.0,
        gt=0,
        description="seconds each call to a Function tool may run before Main takes it down",
    )


class ConversationLogs(BaseModel, frozen=True):
    """Whether each source's Notificaciones enter the Conversation; never the Log or the
    Chat (#109)."""

    model_config = ConfigDict(extra="forbid")

    every: bool = Field(
        default=True,
        description="whether Notificaciones enter the Conversation, for each source without its"
        " own (/conversation_logs)",
    )
    sources: dict[NotificationSource, bool] = Field(default_factory=dict[NotificationSource, bool])

    def enters(self, source: NotificationSource) -> bool:
        return self.sources.get(source, self.every)


class SessionSettings(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    assistant_name: str = Field(
        default="AllIn", min_length=1, description="the LLM's name in the Chat and Sysprompt"
    )
    username: str = Field(
        default_factory=getpass.getuser,
        min_length=1,
        description="the user's name in the Chat and Sysprompt",
    )
    sysprompt: str = Field(
        default="default.txt",
        min_length=1,
        description="Sysprompt file in the sysprompts folder; fixed once there is a Turn",
    )
    model: str = Field(default="allin", min_length=1, description="model name sent to the Server")
    title: str = Field(
        default="",
        description="its name in the Organizer (/windows); empty: its id and first Prompt",
    )
    # Its Enlace (ADR-0022): the Supervisor its Steps go to; empty: the Server at base_url.
    server: str | None = Field(
        default=None,
        pattern=SUPERVISOR_NAME_PATTERN,
        description="Supervisor its Steps go to (servers/<name>.toml); empty: Main's base_url",
    )
    sampling: Sampling = Sampling()
    # Each Notice type's Settings, by type and key, as far as they differ from what its module
    # declares (ADR-0009); their keys, ``notices.<type>.<key>``, come from the loaded modules.
    notices: dict[str, dict[str, JsonValue]] = Field(
        default_factory=dict[str, dict[str, JsonValue]]
    )
    exempt: bool = Field(
        default=True,
        description="its Confirmations go up to Main, which answers them under --devastation;"
        " only a human changes it",
    )
    compact: CompactSettings = CompactSettings()
    tools: ToolSettings = ToolSettings()
    conversation_logs: ConversationLogs = ConversationLogs()
    # Its Window's Atajos as they differ from the defaults (``ui.shortcuts``); only ``/key``
    # changes them, so only a human does (#57).
    keys: dict[str, str] = Field(default_factory=dict[str, str])
    # Its Window's Theme by name, and the loose keys that replace the Theme's own (#47).
    theme: ThemeName = Field(
        default=DEFAULT_THEME, description="Theme in the themes folder its Window shows"
    )
    font: FontKeys = FontKeys()
    colors: ColorKeys = ColorKeys()
    border: BorderKeys = BorderKeys()
    window: WindowKeys = WindowKeys()
    glyphs: GlyphKeys = GlyphKeys()

    def loose_keys(self) -> LooseKeys:
        """What it replaces of its Theme."""
        return LooseKeys(
            font=self.font,
            colors=self.colors,
            border=self.border,
            window=self.window,
            glyphs=self.glyphs,
        )


class StepSettings(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    stall: float = Field(
        default=120.0,
        gt=0,
        description="seconds a Step may go without progress before Main cancels it",
    )


class AttachedSettings(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    release: float = Field(
        default=5.0,
        gt=0,
        description="seconds a Proceso adherido gets to end a call past its timeout before Main"
        " kills it; for those attached from then on",
    )


class AdmissionSettings(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    patience: float = Field(
        default=120.0,
        ge=0,
        description="seconds a waiting Prompt may be overtaken by smaller ones that fit in the"
        " pool; after that nobody overtakes it",
    )


class SubscriptionSettings(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    repeats: int = Field(
        default=5,
        ge=1,
        description="times one Suscripción may wake its Session in one chain before Main cuts"
        " the chain with a Notificación",
    )
    events: int = Field(
        default=20,
        ge=1,
        description="events one Suscripción Prompt carries at most; the rest go in the next",
    )


class ErrandSettings(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    retries: int = Field(
        default=1,
        ge=0,
        le=10,
        description="times an Encargo tries a task again when its worker fails, unless it says"
        " (--retries=)",
    )
    deadline: float = Field(
        default=0.0,
        ge=0,
        description="seconds each worker of an Encargo has, unless it says (--deadline=); 0: none",
    )


WINDOWS_MAX_DEFAULT = 6


class WindowsSettings(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    max: int = Field(
        default=WINDOWS_MAX_DEFAULT,
        ge=0,
        description="Sessions in view at once; the rest wait in the background; 0: no limit",
    )


class MainSettings(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(
        default="http://127.0.0.1:9931/v1",
        pattern=r"^https?://\S+$",
        description="the Server's API; takes effect when Main restarts",
    )
    parallel: int | None = Field(
        default=None,
        ge=1,
        description="Turns at once; only lowers the Server's slots; empty: as many as slots",
    )
    step: StepSettings = StepSettings()
    admission: AdmissionSettings = AdmissionSettings()
    windows: WindowsSettings = WindowsSettings()  # the Máximo de Ventanas (#52, ADR-0020)
    attached: AttachedSettings = AttachedSettings()  # the Procesos adheridos (ADR-0012)
    subscription: SubscriptionSettings = SubscriptionSettings()  # the Suscripciones (#116)
    errand: ErrandSettings = ErrandSettings()  # the Encargos and Lotes (#117)
    # The Atajos of Main's own Chat as they differ from the defaults; only ``/key`` changes them.
    keys: dict[str, str] = Field(default_factory=dict[str, str])
    theme: ThemeName = Field(
        default=DEFAULT_THEME, description="Theme in the themes folder Main's Chat shows"
    )
    # The Session template: copied by /new, never used by Main itself.
    session: SessionSettings = Field(default_factory=SessionSettings)


@dataclass(frozen=True)
class SettingKey:
    """One Setting as ``/set`` names it."""

    name: SettingName
    scope: SettingScope
    parser: TypeAdapter[object]  # the field's type with its constraints
    optional: bool  # whether it may be left unset, which an empty value does
    default: JsonValue
    description: str


def setting_keys(
    model: type[BaseModel] = MainSettings, extra: Sequence[SettingKey] = ()
) -> list[SettingKey]:
    """Every key of ``model``'s Settings, in the order they are declared, then ``extra``: keys
    declared elsewhere, such as each Notice type's."""
    scope: SettingScope = "session" if model is SessionSettings else "main"
    return [*_keys(model, "", scope), *extra]


def apply_settings[M: BaseModel](
    settings: M, assignments: Mapping[SettingName, str], extra: Sequence[SettingKey] = ()
) -> M:
    """A copy of ``settings`` with each key set to its value as typed; nothing changes if any
    key is unknown or any value invalid."""
    keys = {key.name: key for key in setting_keys(type(settings), extra)}
    data = settings.model_dump()
    for name, raw in assignments.items():
        key = keys.get(name)
        if key is None:
            raise UnknownSetting(key=name)
        _set_path(data, name.split("."), _parse_value(key, raw))
    try:
        return type(settings).model_validate(data)
    except ValidationError as error:
        first = error.errors()[0]
        raise InvalidSetting(key=failing_key(first), reason=first["msg"]) from error


def parse_assignments(text: str) -> dict[SettingName, str]:
    """``key=value`` words; a value may be quoted to keep its spaces."""
    try:
        words = shlex.split(text)
    except ValueError as error:
        raise MalformedAssignment(text=text) from error
    assignments: dict[SettingName, str] = {}
    for word in words:
        name, equals, value = word.partition("=")
        if not equals or not name:
            raise MalformedAssignment(text=word)
        assignments[SettingName(name)] = value
    return assignments


def shown_settings(settings: BaseModel, extra: Sequence[SettingKey] = ()) -> list[str]:
    """Each key as ``key=value``, in the registry's order; an unset key shows nothing after ``=``."""
    data = settings.model_dump()
    lines: list[str] = []
    for key in setting_keys(type(settings), extra):
        value = _value_at(data, key)
        lines.append(f"{key.name}={'' if value is None else value}")
    return lines


def failing_key(failure: ErrorDetails) -> SettingName | None:
    """The key a pydantic failure is about; ``None`` when it is about the whole file."""
    return SettingName(".".join(str(part) for part in failure["loc"])) or None


def _keys(model: type[BaseModel], prefix: str, scope: SettingScope) -> Iterator[SettingKey]:
    for name, field in model.model_fields.items():
        annotation = field.annotation
        if get_origin(annotation) is dict:  # a group whose keys are declared elsewhere
            continue
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            inner: SettingScope = "session" if annotation is SessionSettings else scope
            yield from _keys(annotation, f"{prefix}{name}.", inner)
            continue
        default: JsonValue = field.get_default(call_default_factory=True)
        parser = TypeAdapter[object](field.rebuild_annotation())
        yield SettingKey(
            name=SettingName(f"{prefix}{name}"),
            scope=scope,
            parser=parser,
            optional=_accepts_none(parser),
            default=default,
            description=field.description or "",
        )


def _parse_value(key: SettingKey, raw: str) -> object:
    if raw == "" and key.optional:
        return None
    try:
        return key.parser.validate_strings(raw)
    except ValidationError as error:
        raise InvalidSetting(key=key.name, reason=error.errors()[0]["msg"]) from error


def _value_at(data: SettingsData, key: SettingKey) -> object:
    """The key's value in ``data``; its default while it, or its group, was never set."""
    value: object = data
    for part in key.name.split("."):
        if not isinstance(value, dict) or part not in value:
            return key.default
        group: SettingsData = value  # pyright: ignore[reportUnknownVariableType] - model_dump's
        value = group[part]
    return value


def _set_path(data: SettingsData, path: list[str], value: object) -> None:
    *groups, last = path
    for group in groups:
        data = data.setdefault(group, {})  # a key declared elsewhere may be its group's first
    data[last] = value


def _accepts_none(parser: TypeAdapter[object]) -> bool:
    try:
        parser.validate_python(None)
    except ValidationError:
        return False
    return True
