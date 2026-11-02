"""Approving pieces in tests, as a human does on the approval screen (ADR-0013)."""

from pathlib import Path

from allin.confinement.launcher import ToolLauncher
from allin.confinement.support import FULL_SUPPORT
from allin.main.descriptions import Descriptions
from allin.main.quarantine import PieceRef, Quarantine


def approve_sysprompt(sysprompts: Path, name: str) -> None:
    """Approve the Sysprompt ``name`` of the Profile whose folder holds ``sysprompts``, as it
    is now."""
    data_dir = sysprompts.parent  # a Sysprompt is never described: no process is started
    quarantine = Quarantine(
        sysprompts.parent, Descriptions(data_dir, ToolLauncher(data_dir), FULL_SUPPORT)
    )
    ref = PieceRef("sysprompt", name)
    quarantine.approve(ref, quarantine.piece(ref).hash)
