"""The functions AllIn's installation brings (``functions/``) run only the Command sessions they
declare in ``@reach(commands=[…])`` (ADR-0025): read from their code, not run, so a new one that
asks for another fails here before Main ever refuses it."""

import ast
from pathlib import Path

import pytest

INSTALLED = Path(__file__).parents[1] / "functions"


def modules() -> list[Path]:
    return sorted(INSTALLED.rglob("*.py"))


def declared(tree: ast.Module) -> set[str]:
    """The Command sessions its ``@reach(commands=[…])`` names."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _called(node) == "reach"):
            continue
        for keyword in node.keywords:
            if keyword.arg == "commands" and isinstance(keyword.value, ast.List | ast.Tuple):
                names |= {
                    e.value
                    for e in keyword.value.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                }
    return names


def asked(tree: ast.Module) -> list[str | None]:
    """The Command session each ``command(…)`` call asks for; ``None`` where its name is not
    written out, which counts as one it does not declare."""
    return [
        _name(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _called(node) == "command"
    ]


def _called(call: ast.Call) -> str | None:
    match call.func:
        case ast.Name(id=name) | ast.Attribute(attr=name):
            return name
        case _:
            return None


def _name(call: ast.Call) -> str | None:
    match call.args:
        case [ast.Constant(value=str(text)), *_]:
            first = text
        case [ast.JoinedStr(values=[ast.Constant(value=str(text)), *_]), *_]:
            first = text
        case _:
            return None
    word = first.split(maxsplit=1)[0] if first.strip() else ""
    return word.removeprefix("@") if word.startswith("@") and len(word) > 1 else None


def test_the_installation_brings_functions():
    assert modules(), "functions/ is where the installation's functions live"


@pytest.mark.parametrize("module", modules(), ids=lambda path: str(path.relative_to(INSTALLED)))
def test_an_installed_function_runs_only_the_command_sessions_it_declares(module: Path):
    tree = ast.parse(module.read_text())
    undeclared = [name for name in asked(tree) if name is None or name not in declared(tree)]
    assert undeclared == []


def test_the_check_sees_one_that_is_not_declared():
    tree = ast.parse(
        "@reach(commands=['send'])\n"
        "def f():\n"
        "    command('@send a b')\n"
        "    command(f'@server restart')\n"
        "    command(text)\n"
    )
    assert asked(tree) == ["send", "server", None]
    assert declared(tree) == {"send"}
