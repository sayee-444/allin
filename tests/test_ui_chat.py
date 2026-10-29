from allin.ui.chat import Chat, Row


def test_streamed_text_grows_one_labelled_entry_per_role_in_its_colour():
    chat = Chat()
    chat.add("hola", role="user", label="ana")
    chat.stream("let me ", role="reasoning", label="reasoning")
    chat.stream("think", role="reasoning", label="reasoning")
    chat.stream("Hi", role="answer", label="AllIn")
    chat.stream(" there", role="answer", label="AllIn")
    assert chat.rows(40) == [
        Row("user", "[ana] hola"),
        Row("reasoning", "[reasoning] let me think"),
        Row("answer", "[AllIn] Hi there"),
    ]


def test_after_the_stream_ends_the_same_role_starts_a_new_entry():
    chat = Chat()
    chat.stream("one", role="answer", label="AllIn")
    chat.end_stream()
    chat.stream("two", role="answer", label="AllIn")
    assert [row.text for row in chat.rows(40)] == ["[AllIn] one", "[AllIn] two"]


def test_rows_follow_the_stream_and_the_width():
    chat = Chat()
    chat.add("info line")
    chat.stream("abcdef", role="answer", label="A")
    assert [row.text for row in chat.rows(6)] == ["info l", "ine", "[A] ab", "cdef"]
    chat.stream("gh", role="answer", label="A")
    assert [row.text for row in chat.rows(6)] == ["info l", "ine", "[A] ab", "cdefgh"]
    assert [row.text for row in chat.rows(20)] == ["info line", "[A] abcdefgh"]
