"""The ``@`` directives a human types in a Prompt (Command prompts, #14)."""

import pytest

from allin.core.errors import ParamOverrideInvalid, SessionCommandInPrompt
from allin.core.settings import Sampling
from allin.main.prompt_commands import TurnOverrides, parse_prompt


def test_without_command_prompts_the_text_goes_exactly_as_typed():
    text = "\n    indented()\n\tdone  "
    assert parse_prompt(text).text_with({}) == text


@pytest.mark.parametrize(
    ("text", "sent"),
    [("@top_k=3 hola", "hola"), ("hola\n@top_k=3\nadios", "hola\nadios"), ("a  @seed=1", "a")],
)
def test_an_override_leaves_with_one_space_next_to_it(text: str, sent: str):
    assert parse_prompt(text).text_with({}) == sent


def test_an_override_applies_and_leaves_the_text():
    parsed = parse_prompt("explica @effort=medium esto @temperature=0.2")
    assert parsed.text_with({}) == "explica esto"
    assert parsed.overrides == TurnOverrides(effort="medium", temperature=0.2)
    assert parsed.references == ()


def test_overrides_reach_the_request_by_where_llama_reads_each():
    overrides = TurnOverrides(temperature=0.3, top_k=5, seed=7, effort="low")
    assert overrides.sampling(Sampling(top_p=0.9)) == Sampling(temperature=0.3, top_p=0.9)
    assert overrides.extra_params() == {
        "reasoning": {"effort": "low"},
        "extra_body": {"top_k": 5, "seed": 7},
    }
    assert TurnOverrides().extra_params() == {}


@pytest.mark.parametrize("token", ["@tools=all", "@sysprompt=x.txt", "@typical_p=0.9"])
def test_a_key_outside_the_closed_list_is_refused(token: str):
    with pytest.raises(ParamOverrideInvalid) as refused:
        parse_prompt(f"hola {token}")
    assert "temperature" in str(refused.value)  # it says which keys there are


@pytest.mark.parametrize("token", ["@temperature=hot", "@top_p=2", "@effort=maximum", "@seed="])
def test_a_value_the_key_does_not_take_is_refused(token: str):
    with pytest.raises(ParamOverrideInvalid):
        parse_prompt(f"hola {token}")


def test_a_file_reference_is_kept_where_it_was_typed_and_named_by_its_copy():
    parsed = parse_prompt("lee @./notas.txt y @~/a.png luego @/datos/b.md")
    assert parsed.references == ("./notas.txt", "~/a.png", "/datos/b.md")
    names = {"./notas.txt": ".allin/adjuntos/p/notas.txt", "/datos/b.md": "b.md"}
    assert parsed.text_with(names) == "lee .allin/adjuntos/p/notas.txt y @~/a.png luego b.md"


@pytest.mark.parametrize(
    "text",
    ["escribe a correo@dominio.com", "hola @juan, ¿vienes?", "precio: 3@4", "(@./x.txt)"],
)
def test_an_at_that_is_none_of_them_is_literal(text: str):
    parsed = parse_prompt(text)
    assert parsed.text_with({}) == text
    assert parsed.overrides == TurnOverrides()
    assert parsed.references == ()


@pytest.mark.parametrize("text", ["@clone", "@server wake gpu", "  @tools list"])
def test_a_command_session_at_the_start_is_recognised_and_not_sent(text: str):
    with pytest.raises(SessionCommandInPrompt) as refused:
        parse_prompt(text)
    assert refused.value.fields["command"] == text.split(maxsplit=1)[0].removeprefix("@")
