from datetime import datetime

from allin.core.settings import SessionSettings
from allin.ipc.messages import ChatAppend
from allin.main.chat_rebuild import rebuild_chat
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
    message = {
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
