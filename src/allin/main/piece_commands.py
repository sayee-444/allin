"""``/piece``: list the Profile's pieces, show one's approval screen and approve it (ADR-0013,
ADR-0016).

Only a human approves, on purpose: ``/piece`` is never delicate, so ``-y`` means nothing to it
and ``--devastation`` never answers for it. A piece is approved as its screen last showed it,
so what the human read is what runs.
"""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from pydantic import JsonValue

from allin.core.errors import AllInError, InvalidArguments, PieceNotShown
from allin.core.sources import is_text
from allin.decorators import MINIMUM, Reach
from allin.main.descriptions import Description
from allin.main.quarantine import (
    Adjustments,
    Piece,
    PieceRef,
    Quarantine,
    parse_piece_ref,
)
from allin.main.reporting import Reporter
from allin.ui.sanitize import approval_visible

USAGE = (
    "/piece [list] | /piece show <piece> | /piece approve <piece>"
    " [--reach <module>:<field>=<value>…] [--confirm <tool>…] | /piece approve --all"
)
_EXCESS = "!"  # marks, on the screen, what goes beyond the minimum Alcance
_STATES = {"approved": "approved", "quarantined": "in quarantine", "rejected": "rejected"}


class PieceCommands:
    def __init__(self, reporter: Reporter, quarantine: Callable[[], Quarantine]) -> None:
        """``quarantine`` gives the active Profile's, which changes with ``/profile switch``."""
        self._reporter = reporter
        self._quarantine = quarantine
        self._shown: dict[tuple[Path, str], str] = {}  # (Profile, piece) -> the hash shown

    def run(self, text: str = "") -> bool:
        match text.split():
            case [] | ["list"]:
                self._list()
            case ["show", piece]:
                self._show(_ref(piece))
            case ["approve", "--all"]:
                self._approve_own()
            case ["approve", piece, *rest]:
                self._approve(_ref(piece), rest)
            case _:
                raise InvalidArguments(usage=USAGE)
        return True

    def tell_quarantined(self) -> None:
        """What waits for an Approval in the active Profile, said as Main starts or switches
        to it: those pieces are shown but not used."""
        waiting = [p.ref.id for p in self._quarantine().pieces() if p.state != "approved"]
        if waiting:
            shown = f"{len(waiting)} piece(s) in quarantine or rejected: /piece to see them"
            self._reporter.report(None, "pieces_waiting", shown, pieces=list[JsonValue](waiting))

    def _list(self) -> None:
        pieces = self._quarantine().pieces()
        if not pieces:
            self._reporter.show(None, "no pieces")
        width = max((len(p.ref.id) for p in pieces), default=0)
        for piece in pieces:
            self._reporter.show(None, f"{piece.ref.id:<{width}}  {_about(piece)}")

    def _show(self, ref: PieceRef) -> None:
        quarantine = self._quarantine()
        piece = quarantine.piece(ref)
        lines = [f"{ref.id} · {_about(piece)} · hash {piece.hash[:12]}"]
        if piece.rejected:
            lines.append(f"rejected, not source a person can read: {', '.join(piece.rejected)}")
        if ref.kind == "function" and not piece.rejected:
            for module, described in quarantine.describe(piece).items():
                lines += _reach_screen(module, described)
        lines += _code_screen(piece)
        for line in lines:
            self._reporter.show(None, line)
        self._shown[(piece.path, ref.id)] = piece.hash

    def _approve(self, ref: PieceRef, rest: list[str]) -> None:
        quarantine = self._quarantine()
        piece = quarantine.piece(ref)
        shown = self._shown.get((piece.path, ref.id))
        if shown is None:
            raise PieceNotShown(piece=ref.id)
        adjustments = _adjustments(rest, quarantine, piece)
        approval = quarantine.approve(ref, shown, adjustments)
        shown_text = f"approved {ref.id}" + "".join(
            f" · {module} reduced to {_reach_summary(reach)}"
            for module, reach in approval.reach.items()
        )
        if approval.confirm:
            shown_text += f" · confirm {', '.join(approval.confirm)}"
        self._reporter.report(
            None,
            "piece_approved",
            shown_text,
            piece=ref.id,
            hash=approval.hash,
            confirm=list(approval.confirm),
        )

    def _approve_own(self) -> None:
        done = self._quarantine().approve_own()
        shown = f"approved {len(done.approved)}: {', '.join(done.approved) or 'none'}"
        self._reporter.report(None, "pieces_approved", shown, pieces=list(done.approved))
        for piece, why in done.left_out.items():
            self._reporter.show(None, f"  left out {piece}: {why}; /piece show {piece}")


def _ref(text: str) -> PieceRef:
    ref = parse_piece_ref(text)
    if ref is None:
        raise InvalidArguments(usage=USAGE)
    return ref


def _about(piece: Piece) -> str:
    author = piece.provenance
    by = "you" if author.by == "user" else f"{author.by} {author.who or ''}".rstrip()
    return f"{_STATES[piece.state]} · written by {by}"


def _reach_screen(module: str, described: Description | AllInError) -> list[str]:
    """The Alcance comes first, and what goes beyond the minimum is marked (ADR-0016)."""
    if isinstance(described, AllInError):
        why = described.fields.get("error") or described.code
        return [f"{module}: not described ({why}), so it cannot be approved"]
    reach = described.reach
    tools = ", ".join(tool.name for tool in described.tools)
    lines = [f"{module} ({described.kind}) · tools: {tools}", "  reach:"]
    lines.append("    work folder, reading the system and its package")
    lines += [f"  {_EXCESS} read {path}" for path in reach.read]
    for path in reach.write:
        acts = Reach(write=(path,)).acts_as_user
        lines.append(f"  {_EXCESS} write {path}" + (": acts as you" if acts else ""))
    if reach.network:
        lines.append(f"  {_EXCESS} network {_network(reach)}")
    if reach.display:
        lines.append(f"  {_EXCESS} display: acts as you")
    if reach.commands:
        grant = ": declares Otorgar" if reach.grants else ""
        lines.append(f"  {_EXCESS} commands {', '.join(reach.commands)}{grant}")
    if described.marks:
        lines.append(f"  marks: {', '.join(sorted(described.marks))}")
    return lines


def _network(reach: Reach) -> str:
    if reach.net is True:
        return "every TCP port but AllIn's Servers' · UDP open"
    ports = ", ".join(str(port) for port in reach.tcp_ports(frozenset()) or ())
    return f"TCP {ports} · UDP open"


def _reach_summary(reach: Reach) -> str:
    if reach == MINIMUM:
        return "the minimum"
    parts = [*(f"read {p}" for p in reach.read), *(f"write {p}" for p in reach.write)]
    parts += [_network(reach)] if reach.network else []
    parts += ["display"] if reach.display else []
    parts += [f"commands {', '.join(reach.commands)}"] if reach.commands else []
    return "; ".join(parts)


def _code_screen(piece: Piece) -> list[str]:
    """Every file of the piece, its lines numbered, with control, direction and invisible
    characters marked (ADR-0013)."""
    files = [piece.path] if piece.path.is_file() else sorted(piece.path.rglob("*"))
    lines = ["code:"]
    for path in files:
        if not path.is_file():
            continue
        name = path.name if piece.path.is_file() else path.relative_to(piece.path).as_posix()
        lines.append(f"  {approval_visible(name)}")
        data = path.read_bytes()
        if not is_text(data):
            lines.append("    (not text)")
            continue
        text = data.decode("utf-8")
        lines += [
            f"    {number:>4}  {approval_visible(line)}"
            for number, line in enumerate(text.splitlines(), 1)
        ]
    return lines


def _adjustments(words: list[str], quarantine: Quarantine, piece: Piece) -> Adjustments:
    """``--reach <module>:<field>=<value>`` and ``--confirm <tool>``, each repeatable. A field
    replaces what the module declares: ``read``/``write``/``commands`` take a comma list (empty
    for none), ``net`` takes ``off``, ``on`` or ports and ``display`` takes ``off``."""
    if len(words) % 2:
        raise InvalidArguments(usage=USAGE)
    pairs = list(zip(words[::2], words[1::2], strict=True))
    if any(flag not in ("--reach", "--confirm") for flag, _ in pairs):
        raise InvalidArguments(usage=USAGE)
    confirm = tuple(value for flag, value in pairs if flag == "--confirm")
    changes = [value for flag, value in pairs if flag == "--reach"]
    if not changes:
        return Adjustments(confirm=confirm)
    declared = {
        module: described.reach
        for module, described in quarantine.describe(piece).items()
        if not isinstance(described, AllInError)
    }
    reaches: dict[str, Reach] = {}
    for change in changes:
        module, _, assignment = change.partition(":")
        field, _, value = assignment.partition("=")
        base = reaches.get(module, declared.get(module, MINIMUM))
        reaches[module] = _changed(base, field, value)
    return Adjustments(reach=reaches, confirm=confirm)


def _changed(reach: Reach, field: str, value: str) -> Reach:
    items = tuple(item for item in value.split(",") if item)
    try:
        match field:
            case "read":
                return replace(reach, read=items)
            case "write":
                return replace(reach, write=items)
            case "commands":
                return replace(reach, commands=items)
            case "net":
                net = {"off": False, "on": True}.get(value)
                return replace(reach, net=net if net is not None else tuple(map(int, items)))
            case "display" if value == "off":
                return replace(reach, display=False)
            case _:
                raise InvalidArguments(usage=USAGE)
    except (ValueError, AllInError) as error:
        raise InvalidArguments(usage=USAGE) from error
