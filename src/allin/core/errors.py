"""AllIn error hierarchy.

Errors are raised where they happen and handled only at boundaries (dispatcher, executors,
FIFO readers, UI). Each error carries a stable ``code`` plus structured ``fields`` so the UI can
render it in any language without parsing the message.
"""

from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:  # only for the checker: a tool's process imports this and starts fast (#100)
    from pydantic import JsonValue


class AllInError(Exception):
    code: ClassVar[str] = "allin_error"

    def __init__(self, **fields: "JsonValue") -> None:
        self.fields: dict[str, JsonValue] = fields
        details = ", ".join(f"{key}={value!r}" for key, value in fields.items())
        super().__init__(f"{self.code}: {details}" if details else self.code)


class MalformedMessage(AllInError):
    """A line received through an IPC channel is not a valid message."""

    code: ClassVar[str] = "malformed_message"


class ChannelBroken(AllInError):
    """An end of an IPC channel could not be opened, read or written."""

    code: ClassVar[str] = "channel_broken"


class ScreenTooSmall(AllInError):
    """The terminal has no room left for the Chat or the input."""

    code: ClassVar[str] = "screen_too_small"


class SessionNotFound(AllInError):
    """No Session has this id on disk."""

    code: ClassVar[str] = "session_not_found"


class SessionUnreadable(AllInError):
    """A Session's folder exists but its metadata cannot be read."""

    code: ClassVar[str] = "session_unreadable"


class SessionStorageFailed(AllInError):
    """A Session's folder or metadata could not be written."""

    code: ClassVar[str] = "session_storage_failed"


class SessionNotOwned(AllInError):
    """The Session belongs to another Main, which is the only one that may open it."""

    code: ClassVar[str] = "session_not_owned"


class WindowAlreadyOpen(AllInError):
    """A Session has at most one Window."""

    code: ClassVar[str] = "window_already_open"


class SessionNotOpen(AllInError):
    """The Session has no Window, in view or in the background (#52)."""

    code: ClassVar[str] = "session_not_open"


class InvalidArguments(AllInError):
    """A Command main was called with arguments it does not take; ``usage`` shows the right ones."""

    code: ClassVar[str] = "invalid_arguments"


class WindowLaunchFailed(AllInError):
    """The Window's terminal could not be started."""

    code: ClassVar[str] = "window_launch_failed"


class MoldInvalid(AllInError):
    """A Molde (``mold``) is not one: ``reason`` says what is wrong in it (#112)."""

    code: ClassVar[str] = "mold_invalid"


class SyspromptInvalid(AllInError):
    """A Sysprompt file is missing, outside ``sysprompts/`` or not a valid template."""

    code: ClassVar[str] = "sysprompt_invalid"


class LlmRequestFailed(AllInError):
    """A Step's call to the LLM failed or its stream ended without a response."""

    code: ClassVar[str] = "llm_request_failed"


class LlmUnavailable(LlmRequestFailed):
    """The LLM's server cannot serve a Step: it is down, still loading its model (503), or it
    died while a Step streamed. ``reached`` says whether the Step got to the Server before it
    fell, which counts towards failing its Turn (ADR-0023)."""

    code: ClassVar[str] = "llm_unavailable"


class TurnCancelled(AllInError):
    """A Turn was stopped before it finished: its Window closed, or Main is shutting down."""

    code: ClassVar[str] = "turn_cancelled"


class StepStalled(AllInError):
    """A Step made no progress for ``step.stall`` seconds; Main cancelled it (ADR-0023)."""

    code: ClassVar[str] = "step_stalled"


class PromptTooBig(AllInError):
    """A Step needs more tokens (its measured input plus its Output cap) than its Server's slot
    holds even with nothing else in course: it fails without waiting (ADR-0004)."""

    code: ClassVar[str] = "prompt_too_big"


class CompactionFailed(AllInError):
    """A Compaction could not give a summary that serves: its Conversation stays as it was
    (ADR-0017)."""

    code: ClassVar[str] = "compaction_failed"


class TurnCrashed(AllInError):
    """A Turn hit an unexpected error; the executor survives it and reports it."""

    code: ClassVar[str] = "turn_crashed"


class UnknownCommand(AllInError):
    """No Command main has this name."""

    code: ClassVar[str] = "unknown_command"


class LogWriteFailed(AllInError):
    """An event could not be appended to a Log."""

    code: ClassVar[str] = "log_write_failed"


class LogUnreadable(AllInError):
    """A Log exists but cannot be read."""

    code: ClassVar[str] = "log_unreadable"


class MainStateUnreadable(AllInError):
    """Main's saved state is there but cannot be read; Main starts without it."""

    code: ClassVar[str] = "main_state_unreadable"


class MainStateStorageFailed(AllInError):
    """Main's state could not be saved."""

    code: ClassVar[str] = "main_state_storage_failed"


class MainAlreadyRunning(AllInError):
    """Another Main with the same id and data dir is running; only one may (ADR-0007)."""

    code: ClassVar[str] = "main_already_running"


class UnknownSetting(AllInError):
    """No Setting has this key."""

    code: ClassVar[str] = "unknown_setting"


class InvalidSetting(AllInError):
    """A Setting was given a value its key does not take."""

    code: ClassVar[str] = "invalid_setting"


class MalformedAssignment(AllInError):
    """Settings are given as ``key=value`` words; ``text`` is not one."""

    code: ClassVar[str] = "malformed_assignment"


class SettingsFileInvalid(AllInError):
    """A Settings file cannot be read or holds a bad key; Main uses the defaults instead."""

    code: ClassVar[str] = "settings_file_invalid"


class SettingsStorageFailed(AllInError):
    """A Settings file could not be written."""

    code: ClassVar[str] = "settings_storage_failed"


class SyspromptLocked(AllInError):
    """A Session's Sysprompt is fixed once its Conversation has a Turn."""

    code: ClassVar[str] = "sysprompt_locked"


class HumanOnly(AllInError):
    """Only a human may do this (``-y``, ``--devastation``, the Exemption: ADR-0013)."""

    code: ClassVar[str] = "human_only"


class NoticeFailed(AllInError):
    """A Notice module failed to load, or its check failed; the other Notices go on."""

    code: ClassVar[str] = "notice_failed"


class ServerSettingsInvalid(AllInError):
    """A Supervisor's ``servers/<name>.toml`` is missing or holds a bad key."""

    code: ClassVar[str] = "server_settings_invalid"


class SupervisorAlreadyRunning(AllInError):
    """Another process already is this Supervisor; only one may be (ADR-0008)."""

    code: ClassVar[str] = "supervisor_already_running"


class SupervisorNotRunning(AllInError):
    """A Supervisor asked to restart is asleep or failed: its next wake uses its pending
    Settings anyway."""

    code: ClassVar[str] = "supervisor_not_running"


class ServerNotAdoptable(AllInError):
    """``/server adopt`` cannot take this ``llama serve`` under a Supervisor, and says why."""

    code: ClassVar[str] = "server_not_adoptable"


class SupervisorExists(AllInError):
    """``/server new`` or ``/server adopt`` named a Supervisor that already exists."""

    code: ClassVar[str] = "supervisor_exists"


class SupervisorNotFound(AllInError):
    """No ``servers/<name>.toml``: no Supervisor has this name."""

    code: ClassVar[str] = "supervisor_not_found"


class ProfileNotFound(AllInError):
    """No ``profiles/<name>/``: naming a Profile that does not exist never creates it."""

    code: ClassVar[str] = "profile_not_found"


class ProfileNameInvalid(AllInError):
    """A Profile's name takes letters, digits and hyphens, from 3 to 32 of them."""

    code: ClassVar[str] = "profile_name_invalid"


class ProfileExists(AllInError):
    """``/profile new`` named a Profile that already exists."""

    code: ClassVar[str] = "profile_exists"


class ProfileStorageFailed(AllInError):
    """A Profile's folder or one of its files could not be read or written."""

    code: ClassVar[str] = "profile_storage_failed"


class ExportFolderNotEmpty(AllInError):
    """A copy of a Profile or a Kit goes into a folder that does not exist yet or is empty."""

    code: ClassVar[str] = "export_folder_not_empty"


class PortableInvalid(AllInError):
    """A Profile's copy or a Kit is not one AllIn can read: no manifest, a broken one, or a
    file that does not match it; nothing of it is used."""

    code: ClassVar[str] = "portable_invalid"


class PortableFormatUnknown(AllInError):
    """The manifest says a format version this AllIn does not know."""

    code: ClassVar[str] = "portable_format_unknown"


class KitRejected(AllInError):
    """A Kit that brings what no Kit may bring (compiled code, binaries, Atajos, a Server
    Setting that names a file or opens the network) or a piece this AllIn cannot load yet; the
    whole Kit is refused (ADR-0014)."""

    code: ClassVar[str] = "kit_rejected"


class KitReferenceMissing(AllInError):
    """A piece of a Kit names one that is neither in the Kit nor in the Profile (ADR-0014)."""

    code: ClassVar[str] = "kit_reference_missing"


class KitPieceUnknown(AllInError):
    """``/kit export`` or ``/kit load`` named a piece there is not."""

    code: ClassVar[str] = "kit_piece_unknown"


class ClipboardUnavailable(AllInError):
    """No tool here reaches the system clipboard (``tools`` says which would)."""

    code: ClassVar[str] = "clipboard_unavailable"


class ClipboardFailed(AllInError):
    """The clipboard tool could not run, hung or failed (``tool``, ``reason``)."""

    code: ClassVar[str] = "clipboard_failed"


class SessionCommandInPrompt(AllInError):
    """A Prompt sent with ``/send`` that starts with ``@<command>``: a Command session is typed
    alone in its Session's Window, so it is recognised and not sent (ADR-0025)."""

    code: ClassVar[str] = "session_command_in_prompt"


class GrantNotGivable(AllInError):
    """Otorgar gives neither Otorgar nor a piece that declares it (``what``): the permission
    never passes from one Session to another (ADR-0025)."""

    code: ClassVar[str] = "grant_not_givable"


class AttachmentNotApproved(AllInError):
    """A Session attached a file from outside its Carpeta de trabajo and the human did not
    approve copying it: the Prompt was not sent (ADR-0013)."""

    code: ClassVar[str] = "attachment_not_approved"


class OutsideProfile(AllInError):
    """A Session sent to one that is not of the active Profile: a Profile is private, and only
    a human crosses it (ADR-0010)."""

    code: ClassVar[str] = "outside_profile"


class SessionCommandFailed(AllInError):
    """A Command session module failed to load; the other Command sessions go on."""

    code: ClassVar[str] = "session_command_failed"


class UnknownSessionCommand(AllInError):
    """No Command session has this name on this machine."""

    code: ClassVar[str] = "unknown_session_command"


class InvalidSessionCommand(AllInError):
    """A Command session's arguments do not make its Command main: ``reason`` says why."""

    code: ClassVar[str] = "invalid_session_command"


class SessionCommandNotListed(AllInError):
    """A Session's LLM asked for a Command session that the Sessions ``not_in``, on its chain,
    do not have in their lists (ADR-0025)."""

    code: ClassVar[str] = "session_command_not_listed"


class SessionCommandNotDeclared(AllInError):
    """A function asked for a Command session its ``@reach(commands=[…])`` does not declare."""

    code: ClassVar[str] = "session_command_not_declared"


class ParamOverrideInvalid(AllInError):
    """``@key=value`` with a key outside the closed list, or a value the key does not take;
    the Prompt is not sent."""

    code: ClassVar[str] = "param_override_invalid"


class FileReferenceInvalid(AllInError):
    """``@<path>`` names no file Main can read; the Prompt is not sent."""

    code: ClassVar[str] = "file_reference_invalid"


class KeyNameInvalid(AllInError):
    """A key named as ``ctrl+b``, ``shift+enter``, ``alt+x`` or ``f5`` that is not one, or one
    a Window cannot tell apart (``reason``)."""

    code: ClassVar[str] = "key_name_invalid"


class ShortcutTaken(AllInError):
    """The key already has an Atajo (``bound``); ``--replace`` changes it."""

    code: ClassVar[str] = "shortcut_taken"


class ShortcutMissing(AllInError):
    """``off`` on a key that has no Atajo."""

    code: ClassVar[str] = "shortcut_missing"


class LastSendKey(AllInError):
    """Some key must always send: the last one cannot go or change."""

    code: ClassVar[str] = "last_send_key"


class ShortcutInvalid(AllInError):
    """An Atajo runs an action of the input or, in Main's Chat, a Command main (``allowed``)."""

    code: ClassVar[str] = "shortcut_invalid"


class TurnActive(AllInError):
    """The Session has a Turn, a queued Prompt or a Compaction under way: interrupt it
    (Shift+Enter) or wait for it to end first."""

    code: ClassVar[str] = "turn_active"


class ArchiveMissing(AllInError):
    """A Rewind to before a Compaction needs what it archived, and it is not there."""

    code: ClassVar[str] = "archive_missing"


class SessionIdInvalid(AllInError):
    """A Session id a human chose is not 4 to 12 characters of ``[a-z0-9]``."""

    code: ClassVar[str] = "session_id_invalid"


class SessionIdTaken(AllInError):
    """Another Session already has this id, as its id or as an alias."""

    code: ClassVar[str] = "session_id_taken"


class ThemeInvalid(AllInError):
    """A Theme's file is no valid TOML, or a value has not the shape it must."""

    code: ClassVar[str] = "theme_invalid"


class ThemeNotFound(AllInError):
    """No Theme has this name in the Profile's ``themes/`` (``available`` says which do)."""

    code: ClassVar[str] = "theme_not_found"


class ConfinementFailed(AllInError):
    """A tool's process could not confine itself: ``call`` is the system call the kernel
    refused, with its ``errno``. Nothing of the function ran (ADR-0016)."""

    code: ClassVar[str] = "confinement_failed"


class ToolTimedOut(AllInError):
    """A Function tool ran past its ``seconds`` and was taken down with its descendants."""

    code: ClassVar[str] = "tool_timed_out"


class ToolCancelled(AllInError):
    """A Function tool was taken down with its descendants before it answered, because its
    Turn was cancelled."""

    code: ClassVar[str] = "tool_cancelled"


class ToolDied(AllInError):
    """A Function tool's process ended without giving its result: ``exit_code`` (negative for a
    signal) and the end of its ``stderr``."""

    code: ClassVar[str] = "tool_died"


class ToolNotStarted(AllInError):
    """A Function tool's process could not be started: ``cause`` is the type of the error that
    starting it raised, or ``stopped`` when Main's tool launcher had already stopped."""

    code: ClassVar[str] = "tool_not_started"


class AttachFailed(AllInError):
    """A Proceso adherido could not start: making its fproc's class raised ``error``."""

    code: ClassVar[str] = "attach_failed"


class AttachedEnded(AllInError):
    """A Proceso adherido's process ended, so its methods fail until a human attaches it
    again: ``exit_code``, negative for a signal, or ``None`` if not known (ADR-0012)."""

    code: ClassVar[str] = "attached_ended"


class AttachedNotFound(AllInError):
    """No Proceso adherido of the active Profile has this ``name``."""

    code: ClassVar[str] = "attached_not_found"


class AttachedCallCancelled(AllInError):
    """A Command session a Proceso adherido's method asked for, during a call that was
    cancelled: it does not run."""

    code: ClassVar[str] = "attached_call_cancelled"


class AttachedChanged(AllInError):
    """An ``@writes`` call to a Proceso adherido was not run: it changed since its Session last
    read it, and is at ``revision`` now (ADR-0012). Read again, then write."""

    code: ClassVar[str] = "attached_changed"


class AttachedCallsItself(AllInError):
    """A method of the Proceso adherido ``name`` calls ``name`` again: taking one call at a
    time, it would wait for itself for good, so no method calls its own process."""

    code: ClassVar[str] = "attached_calls_itself"


class AttachedMethodFailed(AllInError):
    """The method ``method`` of the Proceso adherido ``name`` raised ``error``."""

    code: ClassVar[str] = "attached_method_failed"


class CommandSessionWithoutSession(AllInError):
    """``command`` runs in a Session, and the call it came from went through none: one called
    from Main's Chat."""

    code: ClassVar[str] = "command_session_without_session"


class AttachedMethodNotFound(AllInError):
    """The Proceso adherido ``name`` has no public method ``method``."""

    code: ClassVar[str] = "attached_method_not_found"


class AttachedMethodsNotGiven(AllInError):
    """The Session ``session_id`` does not have the methods of the Proceso adherido ``name``."""

    code: ClassVar[str] = "attached_methods_not_given"


class AttachedNameTaken(AllInError):
    """A Proceso adherido of the active Profile already runs with this ``name``."""

    code: ClassVar[str] = "attached_name_taken"


class AttachedNameInvalid(AllInError):
    """``name`` is no Proceso adherido's name: a letter, then letters, digits or _, up to 24."""

    code: ClassVar[str] = "attached_name_invalid"


class FprocNotOffered(AllInError):
    """No approved fproc of the active Profile is the module ``fproc``."""

    code: ClassVar[str] = "fproc_not_offered"


class SharedFileRefused(AllInError):
    """``path`` cannot be a Proceso adherido's shared file: ``reason`` ("not a file", which
    covers none there, or "AllIn's own")."""

    code: ClassVar[str] = "shared_file_refused"


class SharedFileNotApproved(AllInError):
    """A Session asked for a shared file from outside its Carpeta de trabajo and the human did
    not approve it: the Proceso adherido was not attached (ADR-0013)."""

    code: ClassVar[str] = "shared_file_not_approved"


class InvalidConcurrent(AllInError):
    """``@concurrent`` got ``value``, which is no number of calls of at least 1:
    ``@concurrent()`` takes any."""

    code: ClassVar[str] = "invalid_concurrent"


class InvalidReach(AllInError):
    """``@reach`` got something that is not an Alcance: ``field`` and what it held (``value``),
    such as a path with no symbolic root (``work``, ``home``, ``/``) or a port out of range."""

    code: ClassVar[str] = "invalid_reach"


class NotATool(AllInError):
    """A function's ``module`` does not define exactly one public function or class, the tool
    it is: ``found`` names the ones it does define (ADR-0016)."""

    code: ClassVar[str] = "not_a_tool"


class DescriptionFailed(AllInError):
    """The encierro de descripción gave no description of ``module``: ``error`` is what its
    import or description raised."""

    code: ClassVar[str] = "description_failed"


class DescriptionRefused(AllInError):
    """On a kernel that cannot confine in full (``missing``), a piece the user did not write is
    not even described: it stays in quarantine showing only its code (ADR-0016)."""

    code: ClassVar[str] = "description_refused"


class PackageChanged(AllInError):
    """A function's package changed while ``module`` was being described, so its description
    is not kept: whoever asked describes it again."""

    code: ClassVar[str] = "package_changed"


class PackageNotSource(AllInError):
    """A function's ``package`` holds compiled code (``files``: ``__pycache__``, a ``.pyc`` or
    an extension module), which no call runs: what was approved is its source (ADR-0013)."""

    code: ClassVar[str] = "package_not_source"


class PieceNotFound(AllInError):
    """No piece ``piece`` (``function:<package>`` or ``sysprompt:<file>``) in the Profile."""

    code: ClassVar[str] = "piece_not_found"


class PieceInQuarantine(AllInError):
    """``piece`` is new or changed since a human approved it: Main shows it but does not use it
    until it is approved again (ADR-0013)."""

    code: ClassVar[str] = "piece_in_quarantine"


class PieceRejected(AllInError):
    """``piece`` holds what is not source a person can read (``files``): it is never approved
    (ADR-0013)."""

    code: ClassVar[str] = "piece_rejected"


class PieceNotShown(AllInError):
    """``piece`` is approved only as the approval screen last showed it: it was not shown, or it
    changed since (ADR-0013)."""

    code: ClassVar[str] = "piece_not_shown"


class PieceNotDescribed(AllInError):
    """A function of ``piece`` could not be described (``module``, ``error``): without its
    Alcance it cannot be approved (ADR-0016)."""

    code: ClassVar[str] = "piece_not_described"


class ApprovalWidens(AllInError):
    """An Approval may only reduce what ``piece`` declares: ``module``'s Alcance, or a tool to
    confirm that it does not have (``detail``)."""

    code: ClassVar[str] = "approval_widens"


class WorkspaceRefused(AllInError):
    """A Carpeta de trabajo cannot be ``path``: ``reason`` is ``inside AllIn`` or ``not a
    folder`` (ADR-0016)."""

    code: ClassVar[str] = "workspace_refused"
