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
from typing import Any, cast

from pydantic import JsonValue, create_model

from allin.core.errors import InvalidReach, NotATool
from allin.decorators import REACH_MARK, reach_of

_MARK = "__allin_"


def describe(module: ModuleType) -> dict[str, JsonValue]:
    """Raises ``NotATool`` unless the module defines exactly one public function or class."""
    target = _the_tool(module)
    if inspect.isclass(target):
        kind, tools = "fproc", [_tool(name, method) for name, method in _public_methods(target)]
    else:
        kind, tools = "function", [_tool(target.__name__, target)]
    reach = {key: _json(value) for key, value in dataclasses.asdict(reach_of(target)).items()}
    return {
        "module": module.__name__,
        "kind": kind,
        "tools": tools,
        "reach": reach,
        "marks": _marks(target),
    }


def _the_tool(module: ModuleType) -> Callable[..., object]:
    own = sorted(
        (name, value)
        for name, value in vars(module).items()
        if not name.startswith("_")
        and (inspect.isfunction(value) or inspect.isclass(value))
        and getattr(value, "__module__", None) == module.__name__
    )
    if len(own) != 1:
        raise NotATool(module=module.__name__, found=[name for name, _ in own])
    return own[0][1]


def _public_methods(fproc: type) -> list[tuple[str, Callable[..., object]]]:
    """Its public methods. Raises ``InvalidReach`` for one with an Alcance of its own: the
    class's is the whole process's (ADR-0016), so it would not count."""
    methods = sorted(
        (name, method)
        for name, method in vars(fproc).items()
        if not name.startswith("_") and inspect.isfunction(method)
    )
    for name, method in methods:
        if hasattr(method, REACH_MARK):
            raise InvalidReach(field=f"{fproc.__name__}.{name}", value="put @reach on the class")
    return methods


def _tool(name: str, function: Callable[..., object]) -> JsonValue:
    """The function's name, its docstring and a JSON schema of its parameters (``self`` and
    ``*args``/``**kwargs`` left out), with the marks of its own decorators."""
    arguments = create_model(f"{name}_arguments", **cast("dict[str, Any]", _fields(function)))
    return {
        "name": name,
        "description": inspect.getdoc(function) or "",
        "parameters": _json(arguments.model_json_schema()),
        "marks": _marks(function),
    }


# A field for ``create_model``: the parameter's annotation, whatever type it names, and its
# default (``...`` when it has none).
type _Field = tuple[Any, Any]


def _fields(function: Callable[..., object]) -> dict[str, _Field]:
    """Its parameters but ``self`` and ``*args``/``**kwargs``, which a tool call never fills."""
    fields: dict[str, _Field] = {}
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
    return fields


def _marks(target: object) -> dict[str, JsonValue]:
    """Every mark a decorator of AllIn's left on ``target`` but the Alcance, which goes apart."""
    marks: dict[str, JsonValue] = {}
    for attribute in dir(target):
        if attribute.startswith(_MARK) and attribute.endswith("__") and attribute != REACH_MARK:
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
