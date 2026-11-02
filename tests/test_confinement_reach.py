from pathlib import Path

from allin.confinement.reach import minimum_reach


def tree(root: Path, *dirs: str) -> None:
    for d in dirs:
        (root / d).mkdir(parents=True)


def test_the_installation_is_read_without_the_data_folder_inside_it(tmp_path: Path):
    # AllIn installed in ~/Pictures/Neva with its data folder in ~/Pictures/Neva/var (ADR-0016).
    install = tmp_path / "Neva"
    tree(install, "src", "venv", "docs", "var/sessions", "var/workspaces/s1", "var/tmp/c1")
    data = install / "var"
    reach = minimum_reach(
        workspace=data / "workspaces" / "s1",
        tmp=data / "tmp" / "c1",
        package=data / "profiles" / "default" / "functions" / "f",
        data_dir=data,
        installation=(install,),
        system=(Path("/usr"),),
    )
    assert set(reach.read) >= {install / "src", install / "venv", install / "docs", Path("/usr")}
    assert install not in reach.read
    assert not any(p == data or data in p.parents for p in reach.read if p.name != "f")


def test_a_tool_writes_only_its_workspace_its_tmpdir_and_dev_null(tmp_path: Path):
    data = tmp_path / "var"
    tree(data, "workspaces/s1", "tmp/c1")
    reach = minimum_reach(
        workspace=data / "workspaces" / "s1",
        tmp=data / "tmp" / "c1",
        package=tmp_path / "pkg",
        data_dir=data,
        installation=(tmp_path / "install",),
        system=(Path("/usr"),),
    )
    assert reach.write == (data / "workspaces" / "s1", data / "tmp" / "c1", Path("/dev/null"))


def test_its_own_package_and_the_harmless_devices_are_read(tmp_path: Path):
    data = tmp_path / "var"
    package = data / "profiles" / "default" / "functions" / "f"
    reach = minimum_reach(
        workspace=data / "workspaces" / "s1",
        tmp=data / "tmp" / "c1",
        package=package,
        data_dir=data,
        installation=(tmp_path / "install",),
        system=(Path("/usr"),),
    )
    assert package in reach.read
    assert {Path("/dev/zero"), Path("/dev/urandom"), Path("/dev/random")} <= set(reach.read)
    assert Path("/proc") not in reach.read and Path("/dev", "shm") not in reach.read


def test_trimming_goes_down_every_level_to_the_data_folder(tmp_path: Path):
    home = tmp_path / "home"
    tree(home, "Documents", "Pictures/Holidays", "Pictures/Neva/src", "Pictures/Neva/var")
    data = home / "Pictures" / "Neva" / "var"
    reach = minimum_reach(
        workspace=data / "workspaces" / "s1",
        tmp=data / "tmp" / "c1",
        package=tmp_path / "pkg",
        data_dir=data,
        installation=(),
        system=(home,),
    )
    broad = {p for p in reach.read if p.is_relative_to(home)}
    assert broad == {
        home / "Documents",
        home / "Pictures" / "Holidays",
        home / "Pictures" / "Neva" / "src",
    }


def test_a_root_inside_the_data_folder_is_never_granted_as_a_broad_root(tmp_path: Path):
    data = tmp_path / "var"
    venv_in_data = data / "venv"
    tree(tmp_path, "var/venv")
    reach = minimum_reach(
        workspace=data / "workspaces" / "s1",
        tmp=data / "tmp" / "c1",
        package=tmp_path / "pkg",
        data_dir=data,
        installation=(venv_in_data,),
        system=(),
    )
    assert venv_in_data not in reach.read
