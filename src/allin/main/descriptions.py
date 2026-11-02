"""What Main knows of each function without importing it (ADR-0016): its description, made by
the encierro de descripción and kept with the hash of its package, so a changed package is
described again and each call is confined with the Alcance that was described."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, JsonValue, ValidationError

from allin.confinement.launcher import ToolLauncher
from allin.confinement.support import ConfinementSupport
from allin.core.errors import (
    DescriptionFailed,
    DescriptionRefused,
    InvalidReach,
    MalformedMessage,
    PackageChanged,
)
from allin.core.hashing import PackageHash, folder_hash
from allin.decorators import Reach


class ToolSchema(BaseModel, frozen=True):
    """One tool as ``tools=[...]`` takes it, with the marks of its own decorators."""

    name: str
    description: str
    parameters: dict[str, JsonValue]
    marks: dict[str, JsonValue]


class Description(BaseModel, frozen=True):
    """What the encierro de descripción made of a module, kept with its package's hash."""

    package_hash: PackageHash
    module: str
    kind: Literal["function", "fproc"]
    tools: tuple[ToolSchema, ...]
    reach: Reach  # what each call is confined with, whatever the module says once imported
    marks: dict[str, JsonValue]  # the class's or function's own, but the Alcance


class Descriptions:
    """The descriptions Main keeps in ``<data folder>/descriptions`` (ADR-0016)."""

    def __init__(
        self,
        data_dir: Path,
        describer: ToolLauncher,
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
        raised, ``MalformedMessage`` if what came back is not a description of it,
        ``PackageChanged`` if the package changed while it was described, and what
        ``ToolLauncher.describe`` raises (``ToolTimedOut`` for one that hangs)."""
        # Before the kept one too: one described on a kernel with support still does not run.
        if self._support.missing and not written_by_user:
            raise DescriptionRefused(module=module, missing=list(self._support.missing))
        package_hash = folder_hash(package)
        kept = self._folder / f"{package_hash}.{module}.json"
        if (known := _load(kept, package_hash, module)) is not None:
            return known
        outcome = self._describer.describe(package, module, self._timeout)
        if outcome.error is not None:
            raise DescriptionFailed(module=module, error=outcome.error)
        described = _parse(outcome.value, package_hash, module)
        if folder_hash(package) != package_hash:  # it would be kept under the wrong contents
            raise PackageChanged(module=module)
        self._folder.mkdir(parents=True, exist_ok=True)
        partial = kept.with_suffix(".partial")
        partial.write_text(described.model_dump_json(indent=2))
        partial.replace(kept)
        return described


def _parse(value: JsonValue, package_hash: PackageHash, module: str) -> Description:
    """What the confined process answered, which its module could have written: Main's own
    hash goes over any it claims, and an Alcance that is not one is refused here."""
    fields = value if isinstance(value, dict) else {}
    try:
        described = Description.model_validate({**fields, "package_hash": package_hash})
    except (ValidationError, InvalidReach) as error:
        raise MalformedMessage(channel="description", line=str(value)[:200]) from error
    if described.module != module:
        raise MalformedMessage(channel="description", line=str(value)[:200])
    return described


def _load(path: Path, package_hash: PackageHash, module: str) -> Description | None:
    try:
        kept = Description.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, InvalidReach):
        return None  # none kept, or one that is not a description: describe it again
    if kept.package_hash != package_hash or kept.module != module:
        return None
    return kept
