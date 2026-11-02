"""What the encierro de descripción makes of a function's module (ADR-0016). It runs in the
confined process that imported the module, never in Main: importing a function runs its code.

A module is one tool, its one public function; or an fproc, its one public class, with a tool
per public method. Each comes with the schema ``tools=[...]`` takes, and every mark its
decorators left (``__allin_<name>__``), the Alcance first.
"""

import dataclasses
import inspect
from collections.abc import Callable
from types import ModuleType
from typing import Any

from pydantic import JsonValue, create_model

from allin.core.errors import NotATool
from allin.decorators import reach_of

_MARK = "__allin_"


def describe(module: ModuleType) -> dict[str, JsonValue]:
    """Raises ``NotATool`` unless the module defines exactly one public function or class."""
    own = sorted(
        (name, value)
        for name, value in vars(module).items()
        if not name.startswith("_")
        and (inspect.isfunction(value) or inspect.isclass(value))
        and getattr(value, "__module__", None) == module.__name__
    )
    if len(own) != 1:
        raise NotATool(module=module.__name__, found=[name for name, _ in own])
    name, target = own[0]
    if inspect.isclass(target):
        methods = sorted(
            (method_name, method)
            for method_name, method in vars(target).items()
            if not method_name.startswith("_") and inspect.isfunction(method)
        )
        kind, tools = "fproc", [_tool(n, m) for n, m in methods]
    else:
        kind, tools = "function", [_tool(name, target)]
    reach = {key: _json(value) for key, value in dataclasses.asdict(reach_of(target)).items()}
    return {
        "module": module.__name__,
        "kind": kind,
        "tools": tools,
        "reach": reach,
        "marks": _marks(target),
    }


def _tool(name: str, function: Callable[..., object]) -> JsonValue:
    """The function's name, its docstring and a JSON schema of its parameters (``self`` and
    ``*args``/``**kwargs`` left out), with the marks of its own decorators."""
    fields: dict[str, Any] = {}
    for parameter in inspect.signature(function).parameters.values():
        if parameter.name == "self" or parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        annotation = (
            Any if parameter.annotation is inspect.Parameter.empty else parameter.annotation
        )
        default = ... if parameter.default is inspect.Parameter.empty else parameter.default
        fields[parameter.name] = (annotation, default)
    arguments = create_model(f"{name}_arguments", **fields)
    return {
        "name": name,
        "description": inspect.getdoc(function) or "",
        "parameters": _json(arguments.model_json_schema()),
        "marks": _marks(function),
    }


def _marks(target: object) -> dict[str, JsonValue]:
    """Every mark a decorator of AllIn's left on ``target`` but the Alcance, which goes apart."""
    marks: dict[str, JsonValue] = {}
    for attribute in dir(target):
        if (
            attribute.startswith(_MARK)
            and attribute.endswith("__")
            and attribute != "__allin_reach__"
        ):
            marks[attribute[len(_MARK) : -2]] = _json(getattr(target, attribute))
    return marks


def _json(value: object) -> JsonValue:
    """``value`` as JSON: a dataclass as its fields, tuples as lists, anything else as text."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _json(dataclasses.asdict(value))
    if isinstance(value, dict):
        items: dict[object, object] = value  # pyright: ignore[reportUnknownVariableType] - any mark
        return {str(key): _json(item) for key, item in items.items()}
    if isinstance(value, (list, tuple)):
        sequence: list[object] | tuple[object, ...] = value  # pyright: ignore[reportUnknownVariableType] - any mark
        return [_json(item) for item in sequence]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return repr(value)
