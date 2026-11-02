"""The paths a call may touch: its Alcance resolved and trimmed around AllIn (ADR-0016, #101)."""

from dataclasses import replace
from pathlib import Path

import pytest

from allin.confinement.reach import Places, ReachPaths, allowed_paths
from allin.decorators import MINIMUM, Reach


def tree(root: Path, *dirs: str) -> None:
    for d in dirs:
        (root / d).mkdir(parents=True, exist_ok=True)


@pytest.fixture
def places(tmp_path: Path) -> Places:
    """AllIn installed in ~/Pictures/Neva with its data folder in ~/Pictures/Neva/var, the
    example of ADR-0016, in a test tree; and a /dev with terminals."""
    home = tmp_path / "home"
    install = home / "Pictures" / "Neva"
    data = install / "var"
    tree(home, "Documents", "Pictures/Holidays", ".config")
    tree(install, "src", "venv", "docs")
    tree(data, "state", "sessions", "workspaces/s1", "tmp/call-1", "profiles/p/functions/f")
    dev = tmp_path / "dev"
    tree(dev, "pts", "shm", "input")
    for name in ("null", "zero", "urandom", "random", "tty", "tty1", "ttyS0", "console", "sda"):
        (dev / name).touch()
    return Places(
        workspace=data / "workspaces" / "s1",
        tmp=data / "tmp" / "call-1",
        package=data / "profiles" / "p" / "functions" / "f",
        data_dir=data,
        installation=(install,),
        home=home,
        system=(tmp_path / "usr",),
        dev=dev,
        resolver=None,
        xauthority=None,
    )


def touchable(paths: tuple[Path, ...], target: Path) -> bool:
    """Whether Landlock would let ``target`` through rules on ``paths``: beneath any of them."""
    return any(target == p or p in target.parents for p in paths)


def test_without_the_decorator_a_function_has_exactly_the_minimum_reach(places: Places):
    reach = allowed_paths(MINIMUM, places)
    dev = places.dev
    assert reach == ReachPaths(
        read=(
            *places.system,
            places.data_dir.parent / "docs",
            places.data_dir.parent / "src",
            places.data_dir.parent / "venv",
            places.package,
            dev / "zero",
            dev / "urandom",
            dev / "random",
        ),
        write=(places.workspace, places.tmp, dev / "null"),
    )


def test_write_home_reaches_all_of_it_but_allins_data_and_installation(places: Places):
    reach = allowed_paths(Reach(write=("home",)), places)
    home, install, data = places.home, places.installation[0], places.data_dir
    assert touchable(reach.write, home / "Documents" / "new.txt")
    assert touchable(reach.write, home / "Pictures" / "Holidays" / "beach.jpg")
    assert not touchable(reach.write, home / "Pictures" / "Neva" / "src" / "x.py")
    assert not touchable(reach.write, data / "state" / "main.json")
    assert touchable(reach.write, places.workspace / "notes.md"), "its Carpeta de trabajo stays"
    assert set(reach.write) >= {
        home / "Documents",
        home / ".config",
        home / "Pictures" / "Holidays",
    }
    assert not touchable(reach.write, install), "the installation is only ever read"


def test_read_home_reads_the_installation_but_never_the_data_folder(places: Places):
    reach = allowed_paths(Reach(read=("home",)), places)
    install, data = places.installation[0], places.data_dir
    assert touchable(reach.read, install / "src" / "allin" / "x.py")
    assert touchable(reach.read, places.home / "Documents" / "a.txt")
    assert not touchable(reach.read, data / "sessions" / "s1" / "conversation.jsonl")
    assert not touchable(reach.read, data / "state" / "main.json")
    assert not touchable(reach.write, places.home / "Documents" / "a.txt")


def test_a_path_in_the_carpeta_de_trabajo_is_granted_although_it_is_in_the_data_folder(
    places: Places,
):
    reach = allowed_paths(Reach(read=("work/src",)), places)
    assert places.workspace / "src" in reach.read


def test_a_root_inside_allin_is_never_granted(places: Places):
    data, install = places.data_dir, places.installation[0]
    reach = allowed_paths(
        Reach(read=("home/Pictures/Neva/var/state",), write=("home/Pictures/Neva/src",)), places
    )
    assert not touchable(reach.read, data / "state" / "main.json")
    assert not touchable(reach.write, install / "src" / "x.py")


def test_no_reach_opens_a_terminal(places: Places):
    reach = allowed_paths(Reach(read=("/",), write=("/",)), places)
    dev = places.dev
    for terminal in ("pts/0", "tty", "tty1", "ttyS0", "console"):
        assert not touchable(reach.read, dev / terminal), terminal
        assert not touchable(reach.write, dev / terminal), terminal
    assert touchable(reach.read, dev / "sda") and touchable(reach.read, dev / "input" / "x")


def test_a_link_beside_allin_that_points_into_it_is_not_followed(places: Places):
    (places.home / "Pictures" / "shortcut").symlink_to(places.data_dir)
    reach = allowed_paths(Reach(read=("home",), write=("home",)), places)
    assert places.home / "Pictures" / "shortcut" not in reach.read + reach.write
    assert not any(p.resolve().is_relative_to(places.data_dir) for p in reach.write[3:])


def test_with_network_it_reads_where_the_resolver_configuration_really_is(
    places: Places, tmp_path: Path
):
    real = tmp_path / "run" / "resolve"
    tree(real.parent, "resolve")
    with_resolver = replace(places, resolver=real)
    assert real in allowed_paths(Reach(net=(443,)), with_resolver).read
    assert real in allowed_paths(Reach(net=True), with_resolver).read
    assert real not in allowed_paths(Reach(net=False), with_resolver).read


def test_with_display_it_reads_the_x11_cookie_and_nothing_else_of_the_session(
    places: Places, tmp_path: Path
):
    cookie = tmp_path / "run" / "Xauthority"
    tree(tmp_path, "run")
    cookie.touch()
    shown = allowed_paths(Reach(display=True), replace(places, xauthority=cookie))
    assert cookie in shown.read
    assert cookie not in allowed_paths(MINIMUM, replace(places, xauthority=cookie)).read
