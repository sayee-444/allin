"""The Notice types Main loaded from ``<data-dir>/notices/`` and when each one shows.

Modules load once, when Main starts, without hot reload (ADR-0016). One that fails to load is
told and left out; the others still load.
"""

import importlib.util
import string
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter

from allin import notices
from allin.core.errors import AllInError, NoticeFailed
from allin.core.settings import SessionSettings, SettingKey, SettingName
from allin.notices import NoticeEvent, NoticeType

type Notify = Callable[[str, str], None]  # shows a Notice's title and text


class NoticeTypes:
    def __init__(self, types: list[NoticeType]) -> None:
        self._types = {notice_type.name: notice_type for notice_type in types}

    def setting_keys(self, prefix: str = "") -> list[SettingKey]:
        """Each type's Settings as keys; ``prefix`` is ``session.`` for Main's template."""
        return [
            SettingKey(
                name=SettingName(f"{prefix}notices.{name}.{key}"),
                scope="session",
                parser=TypeAdapter[object](type(default)),
                optional=False,
                default=default,
                description=f"the {name} Notice's {key}",
            )
            for name, notice_type in self._types.items()
            for key, default in notice_type.defaults().items()
        ]

    def happened(
        self, event: NoticeEvent, settings: SessionSettings, notify: Notify
    ) -> list[NoticeFailed]:
        """Show the Session the Notices ``event`` sets off, as its Settings say; return the
        types whose check failed, after the others ran."""
        failed: list[NoticeFailed] = []
        for notice_type in self._types.values():
            params = {**notice_type.defaults(), **settings.notices.get(notice_type.name, {})}
            if params["enabled"] is not True:
                continue
            try:
                variables = notice_type.check(event, params)
            except Exception as error:  # noqa: BLE001 -- a human's module must not stop Main
                failed.append(NoticeFailed(notice=notice_type.name, reason=repr(error)))
                continue
            if variables is not None:
                notify(str(params["title"]), _fill(str(params["text"]), variables))
        return failed


@dataclass(frozen=True)
class LoadedNotices:
    types: NoticeTypes
    problems: list[AllInError]  # why a module failed to load, for Main to tell on start


def load_notice_types(folder: Path) -> LoadedNotices:
    """The types the modules in ``folder`` declare, and why any module failed to load."""
    problems: list[AllInError] = []
    loaded: list[NoticeType] = []
    for path in sorted(folder.glob("*.py")) if folder.is_dir() else []:
        notices.declared.clear()
        try:
            _import(path)
        except Exception as error:  # noqa: BLE001 -- a broken module must not stop Main
            problems.append(NoticeFailed(notice=path.name, reason=repr(error)))
            continue
        loaded += notices.declared
    notices.declared.clear()
    return LoadedNotices(NoticeTypes(loaded), problems)


def _import(path: Path) -> None:
    spec = importlib.util.spec_from_file_location(f"allin_notices_{path.stem}", path)
    if spec is None or spec.loader is None:  # a ``.py`` file always has a loader
        raise NoticeFailed(notice=path.name, reason="not a module")
    spec.loader.exec_module(importlib.util.module_from_spec(spec))


def _fill(text: str, variables: Mapping[str, object]) -> str:
    """``text`` with its ``{name}`` variables; one it does not have stays as written, and a
    text that is not a valid template shows as it is."""
    try:
        return string.Formatter().vformat(text, (), _Kept(variables))
    except (ValueError, IndexError, KeyError, AttributeError):
        return text


class _Kept(dict[str, object]):
    def __init__(self, variables: Mapping[str, object]) -> None:
        super().__init__(variables)

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"
