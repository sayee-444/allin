import pytest

from allin.ui.input_buffer import InputBuffer


def typed(text: str) -> InputBuffer:
    buffer = InputBuffer()
    buffer.insert(text)
    return buffer


def test_backspace_deletes_one_character():
    buffer = typed("hola")
    buffer.backspace()
    assert buffer.text == "hol"


def test_backspace_on_an_empty_input_does_nothing():
    buffer = InputBuffer()
    buffer.backspace()
    assert buffer.text == ""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("hola mundo", "hola "),
        ("hola", ""),
        ("archivo.txt", "archivo."),
        ("f(x", "f("),
        ("a[b", "a["),
        ("a]b", "a]"),
        ("a{b", "a{"),
        ("a}b", "a}"),
        ("f(x)y", "f(x)"),
        ("uno\ndos", "uno\n"),
        ("uno\n", "uno"),  # a line break goes on its own, not with the line before it
        ("uno \n", "uno "),
        # Right after a delimiter, it goes one more step back instead of doing nothing.
        ("hola ", ""),
        ("hola mundo   ", "hola "),
        ("archivo.", "archivo"),
        ("f(x)", "f(x"),
        ("", ""),
    ],
)
def test_delete_word_stops_at_the_previous_delimiter(text: str, expected: str):
    buffer = typed(text)
    buffer.delete_word()
    assert buffer.text == expected


def test_inserted_text_is_kept_as_the_pad_draws_it():
    buffer = typed("uno\r\ndos\rtres\n\tcuatro\x1b[1m\x07")
    assert buffer.text == "uno\ndos\ntres\n    cuatro[1m"


def test_a_pasted_text_is_edited_like_typed_text():
    buffer = typed("ver ")
    buffer.insert("línea 1\nlínea 2")
    buffer.delete_word()
    buffer.backspace()
    assert buffer.text == "ver línea 1\nlínea"


def test_take_returns_the_text_and_empties_the_input():
    buffer = typed("hola\nmundo")
    assert buffer.take() == "hola\nmundo"
    assert buffer.text == ""
