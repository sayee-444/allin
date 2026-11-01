# Coding standards

How code in AllIn is written: read before implementing and when reviewing (`code-review`). Tooling already enforces the mechanical rules (basedpyright `strict`, and the ruff families chosen in `pyproject.toml`, each with its reason), so this file holds only the judgement calls.

## Types

Every value has a type that says what it means in the domain, and data from outside the program becomes a typed value at the **boundary**.

- **Boundaries parse.** Data that crosses a boundary (files on disk, IPC lines, HTTP replies, Settings) is parsed into a pydantic `BaseModel` where it enters, and only the model travels inward: _parse, don't validate_. `slots.parse_props` is the pattern: a private model for the part of `/props` Main reads, one `model_validate`, `None` on `ValidationError`.
- **Pick the container by where the value lives:**
  - pydantic `BaseModel`, `frozen=True`: anything serialized (Conversation, Logs, state, IPC messages).
  - `@dataclass(frozen=True)`: values that stay in the process (Main's events, `ServerProps`, `Prompt`).
  - `dict[str, Any]` behind a named `type` alias (`Item`, `Step.usage`): only for JSON kept verbatim from an outside API. Read its keys into annotated locals (`cached: int = details.get(...)`), so `Any` stops at that line.
- **Domain concepts get their own type**: `Literal` for a closed set of strings (`ChatRole`, `PauseReason`), `NewType` for ids (`MainId`, `SessionId`), a `type` alias to name a union or a callback (`SubmitTurn`). A bare `str` or `int` standing in for a concept is a review finding (Primitive Obsession).
- **Messages are discriminated unions**: each model has a `type: Literal[...]` field, and code branches on it with `match`.
- **A `Protocol` where there are two adapters**: the real one and a test fake count (`Llm`, `LlmStream`, `WindowLink`, `ChannelListener`). With a single implementation, depend on the class itself.
- **Unknown input is `object`**, narrowed with `isinstance` or parsed by a model. What is written as JSON is `JsonValue` (Log fields, error fields).
- **Escapes are named**: `cast` and `# pyright: ignore[<rule>]` carry the rule and a comment saying why the checker cannot see it.

## Errors

- **Every error is an `AllInError`** subclass with a stable `code` and its facts as keyword fields (`SessionNotFound(session_id=...)`), never a message string: Windows and Logs render it from the fields. Re-raise with `from error`.
- **A broad `except Exception` that swallows the error** lives only where a thread must survive anything (a Turn's executor), with a `noqa: BLE001` comment saying why. One that only adds context and re-raises (`Cancellation.watching`) needs no exemption.

## Concurrency

- **Threads with blocking I/O** (ADR-0002 prefers them over `select`; the same reasoning keeps `asyncio` out): each long-lived job, such as a FIFO end, the Server watch, a Turn or the Launcher's watcher, has its own thread and talks to Main's single Dispatcher thread through a `queue.Queue`. Only Main writes state (ADR-0001).
- **Code that tools run goes in a spawned process** (ADR-0005), never a thread or a persistent pool.

## Output

- **Nothing is printed**: what happens goes to the Log, and what the user should see goes to a Chat (`Reporter`). ruff's `T20` enforces the first half.

## Readability

- **Names come from `GLOSSARY.md`.** A new domain term enters the glossary in the same change, and code uses it with the same meaning.
- **Docstrings say what in domain words**, one line when it fits; comments say why, next to the line they explain.
- **Code, comments and the Log are in English**; docs and GitHub text in Spanish.
- **One function, one level of abstraction**: a function reads as steps in the domain's words, and the details sit in helpers named after what they do.
