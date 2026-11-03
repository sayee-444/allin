"""``/subprofile`` (#123, ADR-0026): a human lists the Subperfiles of the active Profile and
gives one the Exención or a cap on its Turns.

Only a human: the Exención is one of their decisions, as a Session's is (ADR-0013), and the cap
shares their GPU.
"""

from collections.abc import Callable

from allin.core.errors import InvalidArguments, SubprofileNotFound
from allin.core.ids import AttachedName, SessionId, parse_attached_name
from allin.main.reporting import Reporter
from allin.main.subprofiles import SubprofileRecord, Subprofiles

SUBPROFILE_USAGE = "/subprofile [<name> [exempt=on|off] [turns=<n>]]"
_SWITCH = {"on": True, "off": False}


def subprofile_line(name: AttachedName, record: SubprofileRecord) -> str:
    """One Subperfil as ``/subprofile`` lists it: ``guide: 2 sessions · exempt · turns 1``."""
    count = len(record.sessions)
    parts = [f"{count} session{'' if count == 1 else 's'}"]
    if record.exempt:
        parts.append("exempt")
    parts.append(f"turns {record.turns}")
    return f"{name}: {' · '.join(parts)}"


class SubprofileCommands:
    def __init__(
        self,
        subprofiles: Subprofiles,
        reporter: Reporter,
        remark: Callable[[SessionId], None],
    ) -> None:
        """``remark``: tell a Session's Window again what ``--devastation`` answers for it."""
        self._subprofiles = subprofiles
        self._reporter = reporter
        self._remark = remark

    def run(self, name: str | None = None, changes: str | None = None) -> bool:
        if name is None:
            names = self._subprofiles.names()
            self._reporter.show(None, f"subprofiles: {len(names)}")
            for each in names:
                self._reporter.show(
                    None, f"  {subprofile_line(each, self._subprofiles.record(each))}"
                )
            return True
        instance = parse_attached_name(name)
        if instance is None or instance not in self._subprofiles.names():
            raise SubprofileNotFound(name=name)
        if changes is None:
            self._reporter.show(None, subprofile_line(instance, self._subprofiles.record(instance)))
            return True
        exempt, turns = _parse(changes)
        before = self._subprofiles.record(instance)
        if exempt is not None:
            self._subprofiles.set_exempt(instance, exempt)
        if turns is not None:
            self._subprofiles.set_turns(instance, turns)
        record = self._subprofiles.record(instance)
        shown = f"subprofile {subprofile_line(instance, record)}"
        self._reporter.report(
            None,
            "subprofile_changed",
            shown,
            name=instance,
            exempt=record.exempt,
            turns=record.turns,
        )
        if record.exempt != before.exempt:
            for session_id in record.sessions:
                self._remark(session_id)
        return True


def _parse(changes: str) -> tuple[bool | None, int | None]:
    """``exempt=on|off`` and ``turns=<n>``, each at most once; raises ``InvalidArguments``."""
    exempt: bool | None = None
    turns: int | None = None
    for word in changes.split():
        key, _, value = word.partition("=")
        if key == "exempt" and exempt is None and value in _SWITCH:
            exempt = _SWITCH[value]
        elif key == "turns" and turns is None and value.isdigit() and int(value) >= 1:
            turns = int(value)
        else:
            raise InvalidArguments(usage=SUBPROFILE_USAGE)
    return exempt, turns
