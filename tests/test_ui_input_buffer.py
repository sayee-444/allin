import pytest

from allin.ui.input_buffer import InputBuffer, cursor_place


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


def test_a_restored_prompt_is_shown_and_what_was_typed_waits():
    buffer = typed("y otra")
    buffer.restore("hola\nadiós")
    assert buffer.text == "hola\nadiós"
    assert buffer.waiting == ["y otra"]
    assert buffer.blocks == ("hola\nadiós", "y otra")


def test_prompts_restored_one_after_another_stay_apart():
    buffer = typed("typing")
    buffer.restore("a1")
    buffer.restore("a2")
    assert buffer.blocks == ("a2", "a1", "typing")


def test_a_prompt_restored_twice_is_not_repeated():
    buffer = typed("typing")
    buffer.restore("a1")
    buffer.restore("a1")
    assert buffer.blocks == ("a1", "typing")


def test_take_sends_only_the_shown_block_and_shows_the_next():
    buffer = typed("typing")
    buffer.restore("a1")
    assert buffer.take() == "a1"
    assert buffer.text == "typing"
    assert buffer.waiting == []


def test_an_empty_shown_block_is_left_out_of_the_draft():
    buffer = typed("typing")
    buffer.restore("a1")
    for _ in "a1":
        buffer.backspace()
    assert buffer.blocks == ("typing",)
    assert buffer.take() == ""
    assert buffer.text == "typing"


# The cursor (#22)


def test_a_word_in_the_middle_is_corrected_without_touching_what_follows():
    buffer = typed("el gato negro")
    for _ in range(6):
        buffer.left()
    buffer.backspace()  # "gato" loses its "o"
    buffer.insert("a")
    assert buffer.text == "el gata negro"
    assert buffer.cursor == len("el gata")


def test_delete_removes_the_character_under_the_cursor():
    buffer = typed("hola")
    buffer.home()
    buffer.delete()
    assert buffer.text == "ola"
    buffer.end()
    buffer.delete()
    assert buffer.text == "ola"


def test_the_cursor_crosses_line_breaks_and_stops_at_both_ends():
    buffer = typed("ab\ncd")
    buffer.home()
    buffer.left()
    assert buffer.cursor == 0
    for _ in range(3):
        buffer.right()
    assert buffer.text[buffer.cursor] == "c"
    buffer.end()
    buffer.right()
    assert buffer.cursor == 5


def test_delete_word_works_back_from_the_cursor():
    buffer = typed("hola mundo cruel")
    for _ in range(6):
        buffer.left()
    buffer.delete_word()
    assert buffer.text == "hola  cruel"
    assert buffer.cursor == len("hola ")


@pytest.mark.parametrize(
    ("text", "cursor", "width", "place"),
    [
        ("hola", 4, 10, (0, 4)),
        ("abcdef", 4, 4, (1, 0)),  # wrapped: the 5th character starts the second row
        ("abcd", 4, 4, (1, 0)),  # a full row: the cursor's cell is on the next one
        ("ab\ncd", 3, 10, (1, 0)),
        ("ab\ncd", 2, 10, (0, 2)),
        ("日本", 1, 10, (0, 2)),
    ],
)
def test_the_cursor_is_placed_where_the_wrapped_text_draws_it(
    text: str, cursor: int, width: int, place: tuple[int, int]
):
    assert cursor_place(text, cursor, width) == place


def test_up_and_down_move_between_visual_rows_keeping_the_column():
    buffer = typed("primera linea\nsegunda\ntercera linea")
    assert buffer.up(width=40)
    assert buffer.cursor == len("primera linea\nsegunda")  # "tercera linea" is longer
    assert buffer.up(width=40)
    assert buffer.cursor == len("primera")
    assert not buffer.up(width=40)  # already on the first row
    assert buffer.down(width=40)
    assert buffer.cursor == len("primera linea\nsegunda")


def test_up_and_down_move_inside_a_wrapped_line():
    buffer = typed("abcdefghij")
    assert buffer.up(width=4)  # from row 2 ("ij") to row 1 ("efgh")
    assert buffer.cursor == len("abcdef")
    assert buffer.down(width=4)
    assert buffer.cursor == len("abcdefghij")
    assert not buffer.down(width=4)


# The history of Prompts (#22)


def test_older_brings_the_prompts_back_and_newer_returns_the_draft_intact():
    buffer = typed("a medio escribir")
    buffer.history = ["primero", "segundo"]
    buffer.older()
    assert buffer.text == "segundo"
    assert buffer.cursor == len("segundo")
    buffer.older()
    buffer.older()  # there is none older: it stays
    assert buffer.text == "primero"
    buffer.newer()
    assert buffer.text == "segundo"
    buffer.newer()
    assert buffer.text == "a medio escribir"
    buffer.newer()
    assert buffer.text == "a medio escribir"


def test_a_recalled_prompt_can_be_edited_and_sent():
    buffer = typed("")
    buffer.history = ["hola"]
    buffer.older()
    buffer.insert(" otra vez")
    assert buffer.take() == "hola otra vez"
    assert buffer.text == ""
    buffer.newer()  # browsing ended with the send
    assert buffer.text == ""


def test_the_draft_put_aside_while_browsing_is_still_part_of_the_draft():
    buffer = typed("borrador")
    buffer.history = ["viejo"]
    buffer.older()
    assert buffer.blocks == ("viejo", "borrador")


def test_a_prompt_coming_back_while_browsing_lands_on_the_draft():
    buffer = typed("borrador")
    buffer.history = ["viejo"]
    buffer.older()
    buffer.restore("devuelto")
    assert buffer.blocks == ("devuelto", "borrador")


def test_remember_adds_to_the_history_but_not_twice_in_a_row():
    buffer = InputBuffer()
    buffer.remember("uno")
    buffer.remember("uno")
    buffer.remember("dos")
    assert buffer.history == ["uno", "dos"]


def test_a_loaded_history_is_kept_as_the_pad_draws_it():
    buffer = InputBuffer()
    buffer.load_history(["uno\r\ndos", "tres", "tres"])
    assert buffer.history == ["uno\ndos", "tres"]


def test_sending_a_recalled_prompt_gives_back_the_draft_put_aside():
    buffer = typed("borrador")
    buffer.waiting = ["otro bloque"]
    buffer.history = ["viejo"]
    buffer.older()
    assert buffer.take() == "viejo"
    assert buffer.blocks == ("borrador", "otro bloque")
    assert buffer.cursor == len("borrador")
