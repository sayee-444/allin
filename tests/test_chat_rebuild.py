from datetime import datetime
from typing import Any

import pytest

from allin.core.origin import Origin
from allin.core.settings import SessionSettings
from allin.ipc.messages import ChatAppend
from allin.main.chat_rebuild import compacted_entries, compaction_failed_entry, rebuild_chat
from allin.main.conversation import Conversation, Step, Turn
from allin.main.log import LogEntry

SETTINGS = SessionSettings(username="ana", assistant_name="AllIn")
TS = datetime.fromisoformat("2026-10-28T20:00:00+00:00")


def entry(event: str, **fields: object) -> LogEntry:
    return LogEntry.model_validate({"ts": TS, "event": event, **fields})


def turn(prompt: str, thinking: str, text: str) -> Turn:
    reasoning = {
        "type": "reasoning",
        "id": "rs_1",
        "summary": [],
        "content": [{"type": "reasoning_text", "text": thinking}],
    }
    message: dict[str, Any] = {
        "type": "message",
        "id": "msg_1",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }
    return Turn(prompt=prompt, steps=[Step(output=[reasoning, message])])


def test_a_finished_turn_shows_its_prompt_reasoning_and_answer_from_the_conversation():
    conversation = Conversation(sysprompt="s", turns=[turn("hola", "thinking", "Hello")])
    log = [entry("prompt", text="hola"), entry("turn_started"), entry("turn_finished", turn=0)]
    assert rebuild_chat(conversation, log, SETTINGS) == [
        ChatAppend(role="user", label="ana", text="hola"),
        ChatAppend(role="reasoning", label="reasoning", text="thinking"),
        ChatAppend(role="answer", label="AllIn", text="Hello"),
    ]


def test_a_failed_turn_shows_what_it_streamed_and_its_error():
    conversation = Conversation(sysprompt="s")
    log = [
        entry("prompt", text="hi"),
        entry("turn_failed", error="llm_request_failed: x", reasoning="", answer="half an ans"),
    ]
    assert rebuild_chat(conversation, log, SETTINGS) == [
        ChatAppend(role="user", label="ana", text="hi"),
        ChatAppend(role="answer", label="AllIn", text="half an ans"),
        ChatAppend(role="error", label="error", text="llm_request_failed: x"),
    ]


def test_a_turn_cancelled_by_closing_its_window_shows_what_it_streamed_and_why_it_stopped():
    log = [
        entry("prompt", text="a1"),
        entry("prompt", text="a2"),
        entry("window_closed", returncode=0),
        entry("prompts_dropped", reason="window closed", texts=["a2"]),
        entry(
            "turn_cancelled",
            error="turn_cancelled: reason='window closed'",
            reasoning="hm",
            answer="",
        ),
    ]
    assert rebuild_chat(Conversation(sysprompt="s"), log, SETTINGS) == [
        ChatAppend(role="user", label="ana", text="a1"),
        ChatAppend(role="user", label="ana", text="a2"),
        ChatAppend(role="info", label="dropped", text="1 queued prompt(s) dropped: window closed"),
        ChatAppend(role="reasoning", label="reasoning", text="hm"),
        ChatAppend(role="error", label="error", text="turn_cancelled: reason='window closed'"),
    ]


def test_prompts_queued_before_their_turns_ran_keep_the_order_they_were_shown_in():
    conversation = Conversation(
        sysprompt="s", turns=[turn("a1", "", "first"), turn("a2", "", "second")]
    )
    log = [
        entry("prompt", text="a1"),
        entry("prompt", text="a2"),
        entry("turn_finished", turn=0),
        entry("turn_finished", turn=1),
    ]
    assert rebuild_chat(conversation, log, SETTINGS) == [
        ChatAppend(role="user", label="ana", text="a1"),
        ChatAppend(role="user", label="ana", text="a2"),
        ChatAppend(role="answer", label="AllIn", text="first"),
        ChatAppend(role="answer", label="AllIn", text="second"),
    ]


def test_errors_are_shown_and_events_that_never_reach_the_chat_are_left_out():
    log = [
        entry("window_opened"),
        entry("error", error="malformed_message: line='x'"),
        entry("window_closed", returncode=0),
    ]
    assert rebuild_chat(Conversation(sysprompt="s"), log, SETTINGS) == [
        ChatAppend(role="error", label="error", text="malformed_message: line='x'"),
    ]


def test_a_finished_turn_missing_from_the_conversation_is_skipped():
    log = [entry("prompt", text="hi"), entry("turn_finished", turn=3)]
    assert rebuild_chat(Conversation(sysprompt="s"), log, SETTINGS) == [
        ChatAppend(role="user", label="ana", text="hi"),
    ]


def test_a_session_without_a_log_shows_its_conversations_turns():
    conversation = Conversation(sysprompt="s", turns=[turn("hola", "", "Hello")])
    assert rebuild_chat(conversation, [], SETTINGS) == [
        ChatAppend(role="user", label="ana", text="hola"),
        ChatAppend(role="answer", label="AllIn", text="Hello"),
    ]


def test_a_finished_turn_shows_what_it_cost_after_its_output():
    usage = {
        "input_tokens": 328,
        "input_tokens_details": {"cached_tokens": 324},
        "output_tokens": 39,
    }
    costly = Turn(prompt="hola", steps=[Step(output=[], usage=usage)])
    conversation = Conversation(sysprompt="s", turns=[costly])
    log = [entry("prompt", text="hola"), entry("turn_finished", turn=0)]
    assert rebuild_chat(conversation, log, SETTINGS) == [
        ChatAppend(role="user", label="ana", text="hola"),
        ChatAppend(role="info", label="tokens", text="43 tokens this turn"),
    ]


def test_after_a_compaction_the_chat_still_shows_every_turn_with_the_cut_line_and_summary():
    a, b, c = turn("p1", "", "A1"), turn("p2", "", "A2"), turn("p3", "", "A3")
    archived = Conversation(sysprompt="s", turns=[a, b])
    conversation = Conversation(
        sysprompt="s", summary="We chose mango.", summarized=1, turns=[b, c]
    )
    log = [
        entry("prompt", text="p1"),
        entry("turn_finished", turn=0),
        entry("prompt", text="p2"),
        entry("turn_finished", turn=1),
        entry("compacted", summarized=1, summary="We chose mango."),
        entry("prompt", text="p3"),
        entry("turn_finished", turn=2),
    ]
    assert rebuild_chat(conversation, log, SETTINGS, archived=[archived]) == [
        ChatAppend(role="user", label="ana", text="p1"),
        ChatAppend(role="answer", label="AllIn", text="A1"),
        ChatAppend(role="user", label="ana", text="p2"),
        ChatAppend(role="answer", label="AllIn", text="A2"),
        *compacted_entries(1, "We chose mango."),
        ChatAppend(role="user", label="ana", text="p3"),
        ChatAppend(role="answer", label="AllIn", text="A3"),
    ]
    [cut, summary] = compacted_entries(1, "We chose mango.")
    assert cut.text.startswith("──") and "Prompts 1\N{EN DASH}1" in cut.text
    assert summary == ChatAppend(role="info", label="summary", text="We chose mango.")


def test_a_failed_compaction_shows_why_and_that_nothing_changed():
    log = [entry("compaction_failed", error="compaction_failed: reason='x'")]
    assert rebuild_chat(Conversation(sysprompt="s"), log, SETTINGS) == [
        compaction_failed_entry("compaction_failed: reason='x'")
    ]


@pytest.mark.parametrize(
    ("origin", "label"),
    [
        (["human", "session:ab12"], "session:ab12"),
        (["human", "process:bot"], "process:bot"),
        (["human", "session:ab12", "subscription:s1"], "subscription:s1"),
        (["main"], "main"),
        (["human"], "ana"),
    ],
)
def test_a_prompt_is_labelled_with_the_last_link_of_its_origin_unless_a_human_sent_it(
    origin: list[str], label: str
):
    log = [entry("prompt", text="hola", origin=origin)]
    assert rebuild_chat(Conversation(sysprompt="s"), log, SETTINGS) == [
        ChatAppend(role="user", label=label, text="hola"),
    ]


def labels(log: list[LogEntry]) -> list[str]:
    return [line.label for line in rebuild_chat(Conversation(sysprompt="s"), log, SETTINGS)]


def test_a_prompt_logged_without_an_origin_it_can_read_is_the_users():
    log = [entry("prompt", text="hola"), entry("prompt", text="mal", origin="nadie")]
    assert labels(log) == ["ana", "ana"]


def test_a_queued_prompt_keeps_its_label_where_its_turn_starts():
    log = [
        entry("prompt", text="a1"),
        entry("prompt", text="b1", queued=True, origin=["human", "session:ab12"]),
        entry("turn_finished", turn=0),
        entry("prompt_started", text="b1", origin=["human", "session:ab12"]),
    ]
    assert rebuild_chat(Conversation(sysprompt="s"), log, SETTINGS) == [
        ChatAppend(role="user", label="ana", text="a1"),
        ChatAppend(role="user", label="session:ab12", text="b1"),
    ]


def test_a_queued_prompt_started_before_its_origin_was_logged_there_takes_it_from_the_queue():
    log = [
        entry("prompt", text="b1", queued=True, origin=["human", "process:bot"]),
        entry("prompt", text="a1", queued=True),
        entry("prompt_started", text="b1"),
        entry("prompt_started", text="a1"),
    ]
    assert labels(log) == ["process:bot", "ana"]


def test_a_session_without_a_log_labels_each_prompt_by_its_turns_origin():
    sent = turn("hola", "", "Hello").model_copy(
        update={"origin": Origin.model_validate(["human", "session:ab12"])}
    )
    assert rebuild_chat(Conversation(sysprompt="s", turns=[sent]), [], SETTINGS)[0] == ChatAppend(
        role="user", label="session:ab12", text="hola"
    )


def test_what_the_human_wrote_during_a_turn_is_the_users_whatever_the_turns_origin():
    interjected = Turn(
        prompt="b1",
        steps=[Step(output=[], interjected=["y esto"])],
        origin=Origin.model_validate(["human", "session:ab12"]),
    )
    log = [
        entry("prompt", text="b1", origin=["human", "session:ab12"]),
        entry("turn_finished", turn=0),
    ]
    assert rebuild_chat(Conversation(sysprompt="s", turns=[interjected]), log, SETTINGS) == [
        ChatAppend(role="user", label="session:ab12", text="b1"),
        ChatAppend(role="user", label="ana", text="y esto"),
    ]
