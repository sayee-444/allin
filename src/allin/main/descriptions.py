"""What Main knows of each function without importing it (ADR-0016): its description, made by
the encierro de descripción and kept with the hash of its package, so a changed package is
described again and each call is confined with the Alcance that was described."""

from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, JsonValue, ValidationError

from allin.confinement.launcher import ToolOutcome
from allin.confinement.support import ConfinementSupport
from allin.core.errors import DescriptionFailed, DescriptionRefused
from allin.core.hashing import folder_hash
from allin.decorators import Reach


class Describer(Protocol):
    def describe(self, package: Path, module: str, timeout: float) -> ToolOutcome: ...


class ToolSchema(BaseModel, frozen=True):
    """One tool as ``tools=[...]`` takes it, with the marks of its own decorators."""

    name: str
    description: str
    parameters: dict[str, JsonValue]
    marks: dict[str, JsonValue]


class DescribedReach(BaseModel, frozen=True):
    read: tuple[str, ...]
    write: tuple[str, ...]
    net: bool | tuple[int, ...]
    display: bool
    commands: tuple[str, ...]

    def value(self) -> Reach:
        return Reach(
            read=self.read,
            write=self.write,
            net=self.net,
            display=self.display,
            commands=self.commands,
        )


class Description(BaseModel, frozen=True):
    package_hash: str
    module: str
    kind: Literal["function", "fproc"]
    tools: tuple[ToolSchema, ...]
    reach: DescribedReach
    marks: dict[str, JsonValue]  # the class's or function's own, but the Alcance


class Descriptions:
    def __init__(
        self,
        data_dir: Path,
        describer: Describer,
        support: ConfinementSupport,
        timeout: float = 10.0,
    ) -> None:
        self._folder = data_dir / "descriptions"
        self._describer = describer
        self._support = support
        self._timeout = timeout

    def describe(self, package: Path, module: str, *, written_by_user: bool) -> Description:
        """The description of ``module`` in ``package``, kept from before if the package has
        not changed. Raises ``DescriptionRefused`` for a piece the user did not write on a
        kernel without full support, ``DescriptionFailed`` if its import or description
        raised, and what ``ToolLauncher.run`` raises (``ToolTimedOut`` for one that hangs)."""
        package_hash = folder_hash(package)
        kept = self._folder / f"{package_hash}.{module}.json"
        if (known := _load(kept)) is not None:
            return known
        if self._support.missing and not written_by_user:
            raise DescriptionRefused(module=module, missing=list(self._support.missing))
        outcome = self._describer.describe(package, module, self._timeout)
        if outcome.error is not None:
            raise DescriptionFailed(module=module, error=outcome.error)
        try:
            described = Description.model_validate(
                {"package_hash": package_hash, **_fields(outcome.value)}
            )
        except ValidationError as error:
            raise DescriptionFailed(module=module, error="not a description") from error
        self._folder.mkdir(parents=True, exist_ok=True)
        kept.write_text(described.model_dump_json(indent=2))
        return described


def _fields(value: JsonValue) -> dict[str, JsonValue]:
    return value if isinstance(value, dict) else {}


def _load(path: Path) -> Description | None:
    try:
        return Description.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        return None  # none kept, or one that is not a description: describe it again
