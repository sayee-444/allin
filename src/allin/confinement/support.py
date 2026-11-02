"""What this kernel's Landlock can confine (ADR-0016): Main checks it when it starts."""

from dataclasses import dataclass

from allin.confinement.landlock import NETWORK_ABI, SCOPES_ABI, abi_version
from allin.ui.marks import UnconfinedArea


@dataclass(frozen=True)
class ConfinementSupport:
    abi: int  # the kernel's Landlock ABI; 0 without Landlock

    @property
    def missing(self) -> tuple[UnconfinedArea, ...]:
        """What tools run without on this kernel, as the Notice and the marks name it."""
        missing: list[UnconfinedArea] = []
        if self.abi < 1:
            missing.append("files")
        if self.abi < NETWORK_ABI:
            missing.append("network")
        if self.abi < SCOPES_ABI:
            missing.append("signals and abstract sockets")
        return tuple(missing)


def kernel_support() -> ConfinementSupport:
    return ConfinementSupport(abi=abi_version())


FULL_SUPPORT = ConfinementSupport(abi=SCOPES_ABI)
