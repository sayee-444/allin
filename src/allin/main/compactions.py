"""Running each Session's Compaction (ADR-0017), all or nothing.

A Compaction is a queue entry of its Session (``Prompt`` of kind ``compact`` or
``auto_compact``), so it waits its turn there and no other Turn of the Session starts while it
lasts. It first measures, on the Server, each Turn and the rest of its request; then it plans
what stays literal and runs one summary Step per part, each a Step like any other (Admission, a
slot lock, repeated after a fall, ADR-0023), which ``TurnQueue`` runs with the input given here.
Last it measures the summary: only one that saves at least half of what it replaces is saved,
after the Conversation as it was is archived. Until then nothing changes.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from allin.core.cut import CutReason
from allin.core.errors import AllInError, CompactionFailed
from allin.core.ids import PromptId, ServerKey, SessionId
from allin.core.origin import Origin
from allin.ipc.messages import ChatAppend
from allin.main.boxes import MainBoxes
from allin.main.chat_rebuild import compacted_entries, compaction_failed_entry
from allin.main.compaction import (
    DEFAULT_SYSPROMPT,
    compacted,
    kept_turns,
    next_part,
    part_room,
    saves_half,
    summary_request,
    summary_text,
)
from allin.main.conversation import Conversation, Item, Step, summary_item, turn_items
from allin.main.open_windows import OpenWindows
from allin.main.prompts import Prompt
from allin.main.reporting import Reporter
from allin.main.sessions import Session, SessionStore
from allin.main.sysprompt import render_sysprompt

# Asks the Server to measure ``items`` for a Session; the answer comes with the ticket.
type Measure = Callable[[ServerKey, SessionId, int, list[Item]], None]
# What a measure was for: a Turn's size as its LLM reads it now, by its index; as it would
# read pruned; or another part of the work.
type _Asked = (
    int | tuple[Literal["pruned"], int] | Literal["overhead", "summary", "request", "check"]
)


@dataclass
class _Work:
    entry: Prompt  # its place in its Session's queue
    session: Session
    server: ServerKey
    limit: int  # the slot's
    cap: int  # what each of its summary Steps may generate
    conversation: Conversation  # as it was when it started: what it replaces part of
    sysprompt: str  # the compaction Sysprompt, rendered
    asked: dict[int, _Asked] = field(default_factory=dict[int, _Asked])  # pending, by ticket
    sizes: dict[int, int] = field(default_factory=dict[int, int])  # each Turn's tokens
    pruned_sizes: dict[int, int] = field(default_factory=dict[int, int])  # each Turn's, pruned
    # What the Prompt it makes room for needs, for an automatic one: if pruning alone brings
    # that under ``compact.threshold``, nothing is summarized (ADR-0017).
    prompt_need: int | None = None
    overhead: int = 0  # the compaction Sysprompt and the closing request
    old_summary: int = 0  # the earlier summary's tokens
    summarize: int | None = None  # how many first Turns it replaces, once planned
    done: int = 0  # how many of them are summarized so far
    summary: str | None = None  # the summary so far
    summary_size: int = 0  # an upper bound of its tokens
    part: int = 0  # how many Turns the next summary Step reads
    request: list[Item] | None = None  # the next summary Step's input
    need: int | None = None  # what that Step needs, once measured
    checking: bool = False  # every part is summarized: its summary is being measured


@dataclass(frozen=True)
class CompactionDone:
    """A Compactación saved: its Session, the numbers (from 1) and ids of the Prompts it
    summarized, or only pruned, this time, and who asked it."""

    session_id: SessionId
    first: int
    last: int
    ids: tuple[PromptId, ...]
    origin: Origin
    pruned_only: bool  # their tool outputs pruned, with no summary


type Compacted = Callable[[CompactionDone], None]


def _heard_by_nobody(done: CompactionDone) -> None:
    pass


class Compactions:
    def __init__(
        self,
        reporter: Reporter,
        windows: OpenWindows,
        boxes: MainBoxes,
        sessions: SessionStore,
        sysprompts: Path,
        measure: Measure,
        tickets: Iterator[int],
        conversation: Callable[[Session], Conversation],
    ) -> None:
        """``sysprompts``: the active Profile's; ``conversation`` gives a Session's
        Conversation; ``tickets``, shared with the measures of Prompts, tells answers apart."""
        self._reporter = reporter
        self._windows = windows
        self._boxes = boxes
        self._sessions = sessions
        self._sysprompts = sysprompts
        self._measure = measure
        self._tickets = tickets
        self._conversation = conversation
        self._works: dict[SessionId, _Work] = {}
        # Sessions whose next Prompt already had its automatic Compaction.
        self._tried: set[SessionId] = set()
        # What the Prompt an automatic Compaction makes room for needs, by its Session.
        self._wanted: dict[SessionId, int] = {}
        # Hears each Compactación done: the Session, how many first Prompts the summary has
        # and who asked it, for its Suscripciones (#116).
        self.on_compacted: Compacted = _heard_by_nobody

    def use_sysprompts(self, sysprompts: Path) -> None:
        """The active Profile's, once Main switched to another."""
        self._sysprompts = sysprompts

    def wanted(self, session_id: SessionId, need: int) -> None:
        """The Session's next Prompt needs ``need``: its automatic Compaction is for that."""
        self._wanted[session_id] = need

    def tried(self, session_id: SessionId) -> bool:
        return session_id in self._tried

    def forget(self, session_id: SessionId) -> None:
        """The Session's next Prompt started or left the queue."""
        self._tried.discard(session_id)

    def busy(self, session_id: SessionId) -> bool:
        return session_id in self._works

    def entry(self, session_id: SessionId) -> Prompt:
        return self._works[session_id].entry

    def continuing(self, session_id: SessionId) -> bool:
        """Whether its next summary Step goes on after one already done: a Continuation."""
        work = self._works.get(session_id)
        return work is not None and work.done > 0

    def owns(self, session_id: SessionId, ticket: int) -> bool:
        work = self._works.get(session_id)
        return work is not None and ticket in work.asked

    def drop(self, session_id: SessionId) -> None:
        """Its Session's queue was dropped: the Compaction stops, changing nothing."""
        self._works.pop(session_id, None)
        self._tried.discard(session_id)

    def need(
        self, entry: Prompt, session: Session, server: ServerKey, limit: int, cap: int
    ) -> int | None:
        """What the Compaction's next summary Step needs, as measured; ``None`` while
        something is measured, which this asks for. Raises ``CompactionFailed`` if it cannot
        go on."""
        work = self._works.get(session.id)
        if work is None or (work.entry, work.server, work.limit) != (entry, server, limit):
            self._start(entry, session, server, limit, cap)
            return None
        if work.asked or work.checking:
            return None
        if work.summarize is None:
            self._plan(work)
        if work.need is None:  # its next part is not chosen yet
            self._ask_part(work, None)
            return None
        if work.need > limit:  # more than its sizes said: fewer Turns in this part
            work.need = None
            self._ask_part(work, work.part // 2)
            return None
        return work.need

    def request(self, session_id: SessionId) -> list[Item]:
        """The input of its summary Step, which ``need`` measured."""
        request = self._works[session_id].request
        assert request is not None
        return request

    def step_done(self, session_id: SessionId, steps: list[Step], cut: CutReason | None) -> None:
        """A summary Step ended; with every part done, its summary is measured."""
        work = self._works[session_id]
        text = summary_text(steps)
        if not text:
            raise CompactionFailed(reason="the summary is empty")
        if cut is not None:
            raise CompactionFailed(reason=f"the summary was cut ({cut})")
        usage = steps[-1].usage or {}
        work.summary, work.summary_size = text, int(usage.get("output_tokens") or 0)
        work.done += work.part
        work.request, work.need, work.part = None, None, 0
        assert work.summarize is not None
        if work.done >= work.summarize:
            work.checking = True
            prompts = work.conversation.summarized + work.done
            self._ask(work, "check", [summary_item(text, prompts)])

    def measured(self, session_id: SessionId, ticket: int, tokens: int | None) -> bool:
        """The Server measured something the Compaction asked; whether it is over and saved.
        Raises ``CompactionFailed`` if it cannot go on or its summary does not serve."""
        work = self._works[session_id]
        asked = work.asked.pop(ticket)
        if tokens is None:
            raise CompactionFailed(reason="the Server could not measure the Conversation")
        match asked:
            case "overhead":
                work.overhead = tokens
            case "summary":
                work.old_summary = tokens
            case "request":
                work.need = tokens + work.cap
            case "check":
                self._finish(work, tokens)
                return True
            case ("pruned", index):
                work.pruned_sizes[index] = tokens
            case int() as index:
                work.sizes[index] = tokens
        if work.asked or work.summarize is not None:
            return False
        self._plan(work)  # every Turn is measured
        return work.summarize is None  # pruning alone was enough

    def failed(self, entry: Prompt, error: AllInError) -> None:
        """Tell why the Compaction failed; its Conversation is as it was."""
        session_id = entry.session_id
        self._works.pop(session_id, None)
        shown = f"compaction failed, nothing changed: {error}"
        self._reporter.record(session_id, "compaction_failed", shown, error=str(error))
        self._windows.send(session_id, compaction_failed_entry(str(error)))
        if entry.kind == "auto_compact":
            self._tried.add(session_id)
            text = f"{error}; the Turn goes on if it still fits"
            self._boxes.notify(session_id, "Compaction failed", text, "compaction_failed")

    def _start(
        self, entry: Prompt, session: Session, server: ServerKey, limit: int, cap: int
    ) -> None:
        """Measure each Turn and the rest of its request, as the Server counts them."""
        self._works.pop(session.id, None)
        settings = session.settings
        try:  # a Profile from before Compactions existed has no file of its own
            sysprompt = render_sysprompt(
                self._sysprompts, settings, settings.compact.sysprompt, DEFAULT_SYSPROMPT
            )
        except AllInError as error:
            raise CompactionFailed(reason=str(error)) from error
        conversation = self._conversation(session)
        work = _Work(entry, session, server, limit, cap, conversation, sysprompt)
        if entry.kind == "auto_compact":
            work.prompt_need = self._wanted.pop(session.id, None)
        self._works[session.id] = work
        self._reporter.record(session.id, "compaction_started", "compacting…", kind=entry.kind)
        self._ask(work, "overhead", summary_request(sysprompt, None, 0, [], entry.text))
        if conversation.summary is not None:
            summary = summary_item(conversation.summary, conversation.summarized)
            self._ask(work, "summary", [summary])
        for index, turn in enumerate(conversation.turns):
            read = turn_items(turn, pruned=index < conversation.pruned)
            self._ask(work, index, read)
            if turn_items(turn, pruned=True) != read:  # else its pruned size is the same
                self._ask(work, ("pruned", index), turn_items(turn, pruned=True))

    def _plan(self, work: _Work) -> None:
        """Which first Turns it replaces: all but the last whole ones that fit in
        ``compact.keep`` of the slot. They are read pruned; for an automatic one, if that alone
        makes room for its Prompt, they are only pruned and nothing is summarized."""
        count = len(work.conversation.turns)
        sizes = [work.sizes[index] for index in range(count)]
        for index in range(count):
            work.pruned_sizes.setdefault(index, work.sizes[index])
        settings = work.session.settings.compact
        summarize = count - kept_turns(sizes, int(settings.keep * work.limit))
        if summarize == 0:
            raise CompactionFailed(reason="nothing to compact: its last Turns fit in compact.keep")
        saved = sum(sizes[i] - work.pruned_sizes[i] for i in range(summarize))
        need = work.prompt_need
        if need is not None and saved > 0 and need - saved <= settings.threshold * work.limit:
            self._prune(work, summarize, saved)
            return
        work.summarize = summarize
        work.summary, work.summary_size = work.conversation.summary, work.old_summary

    def _ask_part(self, work: _Work, part: int | None) -> None:
        """Measure the request of the next summary Step: ``part`` Turns, or else the oldest
        ones left that fit in the slot along with the summary so far."""
        assert work.summarize is not None
        if part is None:  # its Turns are read pruned
            left = [work.pruned_sizes[index] for index in range(work.done, work.summarize)]
            room = part_room(work.limit, work.cap, work.overhead, work.summary_size)
            part = next_part(left, room)
        if part == 0:
            raise CompactionFailed(reason="a Turn is too big to summarize in one Step")
        work.part = part
        turns = work.conversation.turns[work.done : work.done + part]
        prompts = work.conversation.summarized + work.done
        work.request = summary_request(
            work.sysprompt, work.summary, prompts, turns, work.entry.text
        )  # which reads its Turns pruned
        self._ask(work, "request", work.request)

    def _ask(self, work: _Work, asked: _Asked, items: list[Item]) -> None:
        ticket = next(self._tickets)
        work.asked[ticket] = asked
        self._measure(work.server, work.session.id, ticket, items)

    def _finish(self, work: _Work, tokens: int) -> None:
        """Save the summary if it saves at least half of what it replaces, archiving the
        Conversation as it was first; else nothing changes."""
        session_id = work.session.id
        assert work.summarize is not None and work.summary is not None
        replaced = work.old_summary + sum(work.sizes[i] for i in range(work.summarize))
        if not saves_half(replaced, tokens):
            raise CompactionFailed(
                reason="the summary does not save half", summary=tokens, replaced=replaced
            )
        if self._conversation(work.session) != work.conversation:
            raise CompactionFailed(reason="the Conversation changed meanwhile")
        archive = self._sessions.archive_conversation(work.session, work.conversation)
        after = compacted(work.conversation, work.summary, work.summarize)
        self._sessions.save_conversation(work.session, after)
        del self._works[session_id]
        if work.entry.kind == "auto_compact":
            self._tried.add(session_id)
        shown = f"compacted: Prompts 1\N{EN DASH}{after.summarized} summarized, {replaced} → {tokens} tokens"
        self._reporter.record(
            session_id,
            "compacted",
            shown,
            summarized=after.summarized,
            summary=work.summary,
            archive=archive,
            replaced=replaced,
            tokens=tokens,
        )
        for entry in compacted_entries(after.summarized, work.summary):
            self._windows.send(session_id, entry)
        self._done(work, work.summarize, pruned_only=False)

    def _prune(self, work: _Work, count: int, saved: int) -> None:
        """Only prune the tool outputs of its first ``count`` Turns: they stay whole in the
        Conversation, for the Window and a Rewind, and its LLM reads one line for each."""
        session_id = work.session.id
        if self._conversation(work.session) != work.conversation:
            raise CompactionFailed(reason="the Conversation changed meanwhile")
        conversation = work.conversation
        after = conversation.model_copy(update={"pruned": max(conversation.pruned, count)})
        self._sessions.save_conversation(work.session, after)
        del self._works[session_id]
        self._tried.add(session_id)
        first, last = conversation.summarized + 1, conversation.summarized + count
        shown = (
            f"compacted: tool outputs of Prompts {first}\N{EN DASH}{last} pruned, {saved} tokens"
            " saved; nothing summarized"
        )
        self._reporter.record(session_id, "pruned", shown, first=first, last=last, saved=saved)
        self._windows.send(session_id, ChatAppend(role="info", label="context", text=shown))
        self._done(work, count, pruned_only=True)

    def _done(self, work: _Work, count: int, *, pruned_only: bool) -> None:
        conversation = work.conversation
        ids = tuple(turn.prompt_id for turn in conversation.turns[:count])
        first, last = conversation.summarized + 1, conversation.summarized + count
        self.on_compacted(
            CompactionDone(work.session.id, first, last, ids, work.entry.origin, pruned_only)
        )
