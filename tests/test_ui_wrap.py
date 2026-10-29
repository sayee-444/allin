import pytest

from allin.ui.wrap import wrap


@pytest.mark.parametrize(
    ("text", "width", "expected"),
    [
        ("hola", 10, ["hola"]),
        ("abcd", 4, ["abcd"]),
        ("abcdefghij", 4, ["abcd", "efgh", "ij"]),
        ("a\n\nb", 10, ["a", "", "b"]),
        ("a\n", 10, ["a", ""]),
        ("", 5, [""]),
        ("hola mundo", 5, ["hola ", "mundo"]),
        ("日本語", 4, ["日本", "語"]),
        ("o◡o", 2, ["o◡", "o"]),
        ("a\tb", 10, ["a    b"]),  # tabs become spaces so cells can be counted
        ("a\rb\x07c", 10, ["abc"]),  # other control characters are dropped
        ("ééé", 2, ["éé", "é"]),  # combining marks take 0 cells
    ],
)
def test_wrap_splits_text_into_visual_lines(text: str, width: int, expected: list[str]):
    assert wrap(text, width) == expected


def test_rewrapping_at_a_new_width_keeps_every_character():
    text = "primera línea\nsegunda"
    assert wrap(text, 6) == ["primer", "a líne", "a", "segund", "a"]
    assert wrap(text, 20) == ["primera línea", "segunda"]
