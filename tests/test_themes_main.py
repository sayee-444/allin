"""Themes as Main uses them (#47, ADR-0018): what each Window is born with and sent, the
Settings that choose them, their Notices, their seeds and the Kits that carry them."""

import json
import os
from pathlib import Path

import pytest

from allin.core.settings import MainSettings
from allin.core.themes import BUILTIN, PALETTE, Theme, parse_theme
from allin.ipc.messages import BoxShow, ThemeSet, decode_main_message, encode
from allin.main.events import MainCommandEntered, ThemesChanged
from allin.main.portable import _hash  # pyright: ignore[reportPrivateUsage]
from allin.main.profiles import ProfileStore
from allin.main.settings_files import MainSettingsFiles
from allin.main.themes import FontProbe, Looks, ThemeBook, fontconfig_probe
from allin.ui.screen import ApplyTheme, ScreenUpdate, ShowBox
from allin.window.__main__ import MainListener, WindowRecord
from tests.test_dispatcher import MainHarness

OCEANO = """
[font]
family = "DejaVu Sans Mono"
size = 12

[colors]
background = "#002b36"
user = "#2aa198"

[border]
style = "rounded"
color = "#268bd2"
"""


def command(text: str) -> MainCommandEntered:
    return MainCommandEntered(text)


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    harness = MainHarness(tmp_path)
    write_theme(harness, "oceano", OCEANO)
    return harness


def write_theme(main: MainHarness, name: str, text: str) -> None:
    path = main.profile.themes / f"{name}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    before = path.stat().st_mtime_ns if path.exists() else 0
    path.write_text(text)
    # A new modification time even within the same tick of the clock, as an edit would have.
    now = max(before + 1_000_000, path.stat().st_mtime_ns)
    os.utime(path, ns=(now, now))


def notices(main: MainHarness, session_id: str) -> list[str]:
    [*_, window] = main.windows(session_id)
    return [m.text for m in window.sent if isinstance(m, BoxShow) and m.kind == "notice"]


def main_notices(main: MainHarness) -> list[str]:
    return [u.box.text for u in main.main_screen if isinstance(u, ShowBox)]


def test_a_window_is_born_with_its_theme_and_sent_it_before_its_chat(main: MainHarness):
    session = main.create_session(theme="oceano")
    main.run(command(f"/open {session.id}"))
    [(_, born)] = main.born_with
    [window] = main.windows(session.id)
    assert born.font.family == "DejaVu Sans Mono" and born.border.style == "rounded"
    assert window.themes == [born]


def test_without_a_theme_a_window_looks_as_before_but_for_its_own_border_colour(
    main: MainHarness,
):
    sessions = [main.create_session() for _ in range(4)]
    main.run(*(command(f"/open {session.id}") for session in sessions))
    borders = [theme.border.color for _, theme in main.born_with]
    assert len(set(borders)) == 4 and set(borders) <= set(PALETTE)
    for _, theme in main.born_with:
        unbordered = theme.model_copy(update={"border": BUILTIN.border})
        assert unbordered == BUILTIN


def test_a_session_keeps_its_border_colour_when_reopened_and_after_a_restart(
    main: MainHarness,
):
    session = main.create_session()
    main.run(command(f"/open {session.id}"), command(f"/close {session.id}"))
    main.run(command(f"/open {session.id}"))
    colors = {theme.border.color for _, theme in main.born_with}
    assert colors == {main.sessions.load(session.id).border_color}


def test_a_session_from_before_themes_gets_its_colour_the_first_time_it_shows(
    main: MainHarness,
):
    session = main.create_session()
    metadata = main.root / session.id / "session.json"
    data = json.loads(metadata.read_text())
    del data["border_color"]
    metadata.write_text(json.dumps(data))
    assert main.sessions.load(session.id).border_color is None
    main.run(command(f"/open {session.id}"))
    kept = main.sessions.load(session.id).border_color
    assert kept in PALETTE and main.born_with[0][1].border.color == kept


def test_setting_a_theme_with_the_window_open_sends_it_without_closing_it(main: MainHarness):
    session = main.create_session()
    main.run(command(f"/open {session.id}"), command(f"/session set {session.id} theme=oceano"))
    [window] = main.windows(session.id)  # the same Window, never closed and opened again
    assert len(window.themes) == 2
    assert window.themes[-1].colors.background == "#002b36"


def test_a_theme_that_does_not_exist_is_refused_and_the_error_says_which_there_are(
    main: MainHarness,
):
    session = main.create_session()
    chat = main.run(command(f"/session set {session.id} theme=nada"))
    assert any("theme_not_found" in line and "oceano" in line for line in chat)
    assert main.sessions.load(session.id).settings.theme == "default"
    chat = main.run(command("/set session.theme=nada"))
    assert any("theme_not_found" in line for line in chat)


def test_a_loose_key_changes_its_session_only_and_a_bad_one_is_refused(main: MainHarness):
    mine, other = main.create_session(theme="oceano"), main.create_session(theme="oceano")
    chat = main.run(
        command(f"/open {mine.id}"),
        command(f"/open {other.id}"),
        command(f"/session set {mine.id} font.size=13"),
        command(f"/session set {mine.id} font.family=Mono:weight=200"),
    )
    [mine_window], [other_window] = main.windows(mine.id), main.windows(other.id)
    assert mine_window.themes[-1].font.size == 13
    assert mine_window.themes[-1].font.family == "DejaVu Sans Mono"
    assert (
        other_window.themes == [other_window.themes[0]] and other_window.themes[0].font.size == 12
    )
    assert any("font.family" in line for line in chat)
    assert main.sessions.load(mine.id).settings.font.family is None


def test_editing_a_theme_changes_every_window_that_uses_it(main: MainHarness):
    first, second = main.create_session(theme="oceano"), main.create_session(theme="oceano")
    plain = main.create_session()

    def edit() -> None:
        write_theme(main, "oceano", OCEANO.replace("#002b36", "#00008b"))

    main.run(
        command(f"/open {first.id}"),
        command(f"/open {second.id}"),
        command(f"/open {plain.id}"),
        edit,
        ThemesChanged(),
        settle=True,
    )
    for session in (first, second):
        [window] = main.windows(session.id)
        assert window.themes[-1].colors.background == "#00008b"
    [plain_window] = main.windows(plain.id)
    assert len(plain_window.themes) == 1


def test_a_theme_left_invalid_keeps_its_last_valid_version_and_says_what_failed(
    main: MainHarness,
):
    session = main.create_session(theme="oceano")
    main.run(
        command(f"/open {session.id}"),
        lambda: write_theme(main, "oceano", '[colors]\nuser = "rojo"'),
        ThemesChanged(),
        settle=True,
    )
    [window] = main.windows(session.id)
    assert window.themes == [window.themes[0]]
    assert window.themes[0].colors.background == "#002b36"
    [notice] = notices(main, session.id)
    assert "theme_invalid" in notice and "colors.user" in notice and "last valid" in notice


@pytest.mark.parametrize(
    "line",
    ['[colors]\nuser = "#12"', '[font]\nfamily = "Mono,Serif"', '[font]\nfamily = "A:b"'],
)
def test_a_theme_without_its_shape_does_not_load_and_tells_it(main: MainHarness, line: str):
    write_theme(main, "malo", line)
    session = main.create_session(theme="malo")
    main.run(command(f"/open {session.id}"))
    assert main.born_with[0][1].font.family == "Monospace"  # default, its shape kept
    [notice] = notices(main, session.id)
    assert "theme_invalid" in notice


def test_a_theme_that_went_away_shows_default_with_a_notice(main: MainHarness):
    session = main.create_session(theme="oceano")
    (main.profile.themes / "oceano.toml").unlink()
    main.run(command(f"/open {session.id}"))
    assert main.born_with[0][1].font == BUILTIN.font
    assert any("oceano not found" in notice for notice in notices(main, session.id))


def test_the_theme_notice_turns_off_per_session(main: MainHarness):
    session = main.create_session(theme="ido")
    main.run(
        command(f"/session set {session.id} notices.theme.enabled=false"),
        command(f"/open {session.id}"),
    )
    assert notices(main, session.id) == []


def test_mains_chat_takes_its_theme_and_tells_its_problems_only_when_chosen(
    main: MainHarness,
):
    write_theme(main, "malo", '[colors]\nuser = "rojo"')
    settings = main.settings
    main.settings = settings.model_copy(update={"theme": "oceano"})
    main.run()
    applied = [u for u in main.main_screen if isinstance(u, ApplyTheme)]
    assert applied[-1].terminal == "main" and applied[-1].theme.colors.background == "#002b36"
    main.main_screen.clear()
    main.settings = settings.model_copy(update={"theme": "malo"})
    main.run()  # on start: told nothing
    assert main_notices(main) == []
    main.run(command("/set theme=oceano"), command("/set theme=malo"))
    assert any("theme_invalid" in text for text in main_notices(main))


def test_a_missing_font_is_told_and_the_next_window_asks_again():
    installed: set[str] = set()
    asked: list[str] = []

    def match(family: str) -> str:
        asked.append(family)
        return family if family in installed else "DejaVu Sans Mono"

    looks = Looks(ThemeBook(Path("/nonexistent")), FontProbe(match, lambda _: True))
    theme = BUILTIN.model_copy(
        update={"font": BUILTIN.font.model_copy(update={"family": "Fira Code"})}
    )
    look = looks.with_fonts(theme, [])
    assert look.problems == ["font Fira Code is not installed; DejaVu Sans Mono is used instead"]
    installed.add("Fira Code")
    assert looks.with_fonts(theme, []).problems
    looks.forget_fonts()  # as a Window opens
    assert looks.with_fonts(theme, []).problems == []
    assert looks.with_fonts(BUILTIN, []).problems == []  # Monospace: whichever fontconfig picks


def test_a_glyph_set_no_font_has_steps_down_and_is_told():
    looks = Looks(ThemeBook(Path("/nonexistent")), FontProbe(lambda _: None, lambda _: False))
    nerd = BUILTIN.model_copy(update={"glyphs": BUILTIN.glyphs.model_copy(update={"set": "nerd"})})
    look = looks.with_fonts(nerd, [])
    assert look.theme.glyphs.set == "ascii" and len(look.problems) == 2


def test_fontconfig_is_asked_for_the_escaped_family_with_monospace_after_it(
    monkeypatch: pytest.MonkeyPatch,
):
    commands: list[list[str]] = []

    class Done:
        returncode = 0
        stdout = "DejaVu Sans Mono,DejaVu Sans Mono Book"

    def run(command: list[str], **_: object) -> Done:
        commands.append(command)
        return Done()

    def which(_: str) -> str:
        return "/bin/x"

    monkeypatch.setattr("allin.main.themes.shutil.which", which)
    monkeypatch.setattr("allin.main.themes.subprocess.run", run)
    probe = fontconfig_probe()
    assert probe.match("Iosevka-Term") == "DejaVu Sans Mono"
    assert commands[0][-1] == "Iosevka\\-Term,monospace"
    assert probe.has_glyphs("nerd") and commands[1][1] == ":charset=e0b0"


def test_the_installations_themes_seed_a_profile_and_one_from_before_gets_them(tmp_path: Path):
    seed = tmp_path / "install" / "themes"
    seed.mkdir(parents=True)
    (seed / "default.toml").write_text("")
    (seed / "oceano.toml").write_text(OCEANO)
    store = ProfileStore(tmp_path / "var", tmp_path / "install" / "sysprompts", seed)
    store.migrate()
    profile = store.open(None)
    assert sorted(p.name for p in profile.themes.iterdir()) == ["default.toml", "oceano.toml"]
    copy = store.create("otro", profile)
    assert (copy.themes / "oceano.toml").read_text() == OCEANO
    old = tmp_path / "var" / "profiles" / "viejo"
    old.mkdir()
    assert (store.open("viejo").themes / "oceano.toml").is_file()


def test_the_seed_default_theme_is_the_built_in_look():
    seed = Path(__file__).parent.parent / "themes"
    book = ThemeBook(seed)
    assert book.theme("default") == (BUILTIN, [])
    for name in book.names():
        assert book.theme(name)[1] == [], name


def test_a_profile_switch_uses_the_other_profiles_themes(main: MainHarness):
    other = main.profiles.create("otro", main.profile)
    (other.themes / "oceano.toml").unlink()
    session = main.create_session(theme="oceano")
    main.run(command("/profile switch otro"), command(f"/open {session.id}"))
    assert main.born_with[-1][1].font == BUILTIN.font


# Kits carry Themes (#47).


def test_a_kit_carries_its_themes_and_loading_it_copies_them(main: MainHarness, tmp_path: Path):
    kit = tmp_path / "azul"
    main.settings = main.settings.model_copy(
        update={"session": main.settings.session.model_copy(update={"theme": "oceano"})}
    )
    main.run(command(f"/kit export {kit} settings"))
    manifest = json.loads((kit / "manifest.json").read_text())
    assert {"kind": "theme", "name": "oceano"} in [
        {"kind": p["kind"], "name": p["name"]} for p in manifest["pieces"]
    ]
    other = main.profiles.create("otro", main.profile)
    (other.themes / "oceano.toml").unlink()
    MainSettingsFiles(other.settings).save_current(MainSettings())
    chat = main.run(command("/profile switch otro"), command(f"/kit load {kit} --take settings"))
    assert any("kit azul 1 loaded" in line for line in chat)
    assert (other.themes / "oceano.toml").read_text() == OCEANO


def test_a_kit_whose_settings_name_a_theme_it_lacks_is_refused(main: MainHarness, tmp_path: Path):
    kit = tmp_path / "azul"
    main.settings = main.settings.model_copy(
        update={"session": main.settings.session.model_copy(update={"theme": "oceano"})}
    )
    main.run(command(f"/kit export {kit} settings"))
    manifest = json.loads((kit / "manifest.json").read_text())
    manifest["pieces"] = [p for p in manifest["pieces"] if p["kind"] != "theme"]
    (kit / "manifest.json").write_text(json.dumps(manifest))
    (kit / "themes" / "oceano.toml").unlink()
    other = main.profiles.create("otro", main.profile)
    (other.themes / "oceano.toml").unlink()
    chat = main.run(command("/profile switch otro"), command(f"/kit load {kit} --take settings"))
    assert any("kit_reference_missing" in line and "theme:oceano" in line for line in chat)


def test_a_kit_with_an_invalid_theme_is_refused(main: MainHarness, tmp_path: Path):
    kit = tmp_path / "azul"
    main.run(command(f"/kit export {kit} theme:oceano"))
    bad = '[font]\nfamily = "Mono:weight=200"\n'
    manifest = json.loads((kit / "manifest.json").read_text())
    for piece in manifest["pieces"]:
        if piece["kind"] == "theme":
            piece["sha256"] = _hash({"themes/oceano.toml": bad.encode()})
    (kit / "themes" / "oceano.toml").write_text(bad)
    (kit / "manifest.json").write_text(json.dumps(manifest))
    main.profiles.create("otro", main.profile)
    chat = main.run(command("/profile switch otro"), command(f"/kit load {kit}"))
    assert any("kit_rejected" in line and "theme:oceano" in line for line in chat)


def test_a_theme_set_reaches_the_window_as_json_and_back():
    message = ThemeSet(theme=Theme())
    assert decode_main_message(encode(message)) == message


def test_the_window_applies_the_theme_main_sent_as_it_is(tmp_path: Path):
    shown: list[ScreenUpdate] = []
    listener = MainListener(shown.append, WindowRecord(tmp_path / "window.jsonl"))
    theme = parse_theme("oceano", OCEANO)
    listener.on_message(ThemeSet(theme=theme))
    assert shown == [ApplyTheme(theme, "window")]


def test_a_problem_is_told_once_and_again_when_the_look_changes(main: MainHarness):
    write_theme(main, "lejos", '[font]\nfamily = "Hack"')
    write_theme(main, "otra", '[font]\nfamily = "Hack"\n[border]\nstyle = "double"')
    main.fonts = FontProbe(lambda _: "DejaVu Sans Mono", lambda _: True)
    session = main.create_session(theme="lejos")
    main.run(
        command(f"/open {session.id}"),
        command(f"/session set {session.id} assistant_name=Otro"),  # same look: not again
        command(f"/session set {session.id} theme=otra"),  # another look: told again
    )
    told = [text for text in notices(main, session.id) if "Hack" in text]
    assert len(told) == 2
