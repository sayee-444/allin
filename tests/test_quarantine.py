"""Quarantine and Approval of functions (ADR-0013, ADR-0016, #104). They describe for real on
this kernel."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from allin.confinement.landlock import abi_version
from allin.confinement.launcher import ToolLauncher
from allin.confinement.support import FULL_SUPPORT, ConfinementSupport
from allin.core.errors import ApprovalWidens, PieceNotShown, PieceRejected
from allin.core.hashing import folder_hash
from allin.decorators import Reach
from allin.main.descriptions import Descriptions
from allin.main.provenance import KITS_FILE
from allin.main.quarantine import Adjustments, PieceRef, Quarantine

pytestmark = pytest.mark.skipif(abi_version() < 6, reason="needs Landlock ABI 6 (kernel 6.12)")

FETCH = '''
from allin.decorators import reach


@reach(read=["home/Documents"], net=[443, 80])
def fetch(url: str) -> str:
    """Fetch a page."""
    return url
'''

PLAIN = '''
def shout(text: str) -> str:
    """Say it louder."""
    return text.upper()
'''


def tool(name: str, decorator: str) -> str:
    return f'''
from allin.decorators import reach


{decorator}
def {name}() -> None:
    """A tool."""
'''


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "var"


@pytest.fixture
def profile(data_dir: Path) -> Path:
    folder = data_dir / "profiles" / "default"
    (folder / "functions").mkdir(parents=True)
    return folder


@pytest.fixture
def launcher(data_dir: Path) -> Iterator[ToolLauncher]:
    launcher = ToolLauncher(data_dir)
    launcher.start()
    yield launcher
    launcher.stop()


def quarantine(
    profile: Path, launcher: ToolLauncher, support: ConfinementSupport = FULL_SUPPORT
) -> Quarantine:
    data_dir = profile.parents[1]
    return Quarantine(profile, Descriptions(data_dir, launcher, support, timeout=5.0))


def package(profile: Path, name: str, modules: dict[str, str]) -> Path:
    folder = profile / "functions" / name
    folder.mkdir(parents=True, exist_ok=True)
    for module, source in modules.items():
        (folder / f"{module}.py").write_text(source)
    return folder


def approve(pieces: Quarantine, name: str, adjustments: Adjustments | None = None) -> None:
    ref = PieceRef("function", name)
    pieces.approve(ref, pieces.piece(ref).hash, adjustments)


def offered(pieces: Quarantine) -> dict[str, Reach]:
    return {o.description.module: o.description.reach for o in pieces.offered()}


def test_a_new_function_is_in_quarantine_and_not_offered_until_approved(
    profile: Path, launcher: ToolLauncher
):
    package(profile, "web", {"fetch": FETCH})
    pieces = quarantine(profile, launcher)
    [piece] = pieces.pieces()
    assert (piece.ref.id, piece.state, piece.provenance.by) == (
        "function:web",
        "quarantined",
        "user",
    )
    assert pieces.offered() == []
    approve(pieces, "web")
    assert offered(pieces) == {"fetch": Reach(read=("home/Documents",), net=(443, 80))}


def test_changing_any_file_of_a_package_sends_all_its_functions_back_to_quarantine(
    profile: Path, launcher: ToolLauncher
):
    folder = package(profile, "kit", {"fetch": FETCH, "shout": PLAIN})
    pieces = quarantine(profile, launcher)
    approve(pieces, "kit")
    assert set(offered(pieces)) == {"fetch", "shout"}
    (folder / "README.md").write_text("not even code")
    assert pieces.piece(PieceRef("function", "kit")).state == "quarantined"
    assert offered(pieces) == {}


@pytest.mark.parametrize(
    ("planted", "data"),
    [("__pycache__/shout.cpython-314.pyc", b"\0bytecode"), ("logo.png", b"\x89PNG\0\0")],
)
def test_a_package_with_compiled_code_or_a_binary_is_rejected(
    profile: Path, launcher: ToolLauncher, planted: str, data: bytes
):
    folder = package(profile, "loud", {"shout": PLAIN})
    (folder / planted).parent.mkdir(exist_ok=True)
    (folder / planted).write_bytes(data)
    pieces = quarantine(profile, launcher)
    piece = pieces.piece(PieceRef("function", "loud"))
    assert piece.state == "rejected" and planted in piece.rejected
    with pytest.raises(PieceRejected):
        pieces.approve(piece.ref, piece.hash)


def test_approving_with_a_smaller_reach_or_a_confirmation_never_changes_the_code_or_hash(
    profile: Path, launcher: ToolLauncher
):
    folder = package(profile, "web", {"fetch": FETCH})
    before = folder_hash(folder)
    pieces = quarantine(profile, launcher)
    approve(pieces, "web", Adjustments(reach={"fetch": Reach(net=(443,))}, confirm=("fetch",)))
    [function] = pieces.offered()
    assert function.description.reach == Reach(net=(443,))
    assert function.confirm == {"fetch"}
    assert folder_hash(folder) == before
    assert (folder / "fetch.py").read_text() == FETCH
    assert pieces.piece(PieceRef("function", "web")).state == "approved"


@pytest.mark.parametrize(
    "adjustments",
    [
        Adjustments(reach={"fetch": Reach(net=(22,))}),  # a port it does not declare
        Adjustments(reach={"fetch": Reach(display=True)}),
        Adjustments(reach={"other": Reach()}),
        Adjustments(confirm=("rm",)),
    ],
)
def test_an_approval_can_only_reduce(
    profile: Path, launcher: ToolLauncher, adjustments: Adjustments
):
    package(profile, "web", {"fetch": FETCH})
    pieces = quarantine(profile, launcher)
    with pytest.raises(ApprovalWidens):
        approve(pieces, "web", adjustments)
    assert pieces.offered() == []


def test_it_approves_only_the_version_its_screen_showed(profile: Path, launcher: ToolLauncher):
    folder = package(profile, "loud", {"shout": PLAIN})
    pieces = quarantine(profile, launcher)
    ref = PieceRef("function", "loud")
    shown = pieces.piece(ref).hash
    (folder / "shout.py").write_text(PLAIN + "\nimport os\n")  # changed after it was shown
    with pytest.raises(PieceNotShown):
        pieces.approve(ref, shown)


def test_bulk_approval_leaves_out_what_acts_as_the_user_declares_grant_or_is_not_theirs(
    profile: Path, launcher: ToolLauncher
):
    package(profile, "loud", {"shout": PLAIN})
    package(profile, "web", {"fetch": FETCH})
    package(profile, "screen", {"draw": tool("draw", "@reach(display=True)")})
    package(profile, "dotfiles", {"tidy": tool("tidy", '@reach(write=["home/.config"])')})
    package(profile, "boss", {"give": tool("give", '@reach(commands=["grant"])')})
    kit = package(profile, "gift", {"wrap": PLAIN})
    origin = {"kit": "presents", "version": "1", "hash": folder_hash(kit)}
    (profile / KITS_FILE).write_text(json.dumps({"pieces": {"function:gift": origin}}))
    pieces = quarantine(profile, launcher)
    done = pieces.approve_own()
    assert sorted(done.approved) == ["function:loud", "function:web"]
    assert done.left_out == {
        "function:boss": "give declares Otorgar",
        "function:dotfiles": "tidy acts as you",
        "function:gift": "written by kit presents",
        "function:screen": "draw acts as you",
    }
    approve(pieces, "screen")  # alone, after its screen
    assert "draw" in offered(pieces)


def test_a_kits_piece_becomes_the_users_once_it_changes_on_disk(
    profile: Path, launcher: ToolLauncher
):
    kit = package(profile, "gift", {"wrap": PLAIN})
    origin = {"kit": "presents", "version": "1", "hash": folder_hash(kit)}
    (profile / KITS_FILE).write_text(json.dumps({"pieces": {"function:gift": origin}}))
    pieces = quarantine(profile, launcher)
    assert pieces.piece(PieceRef("function", "gift")).provenance.who == "presents"
    (kit / "wrap.py").write_text(PLAIN + "# mine now\n")
    assert pieces.piece(PieceRef("function", "gift")).provenance.by == "user"


def test_on_a_kernel_without_full_support_only_the_users_own_approved_pieces_run(
    profile: Path, launcher: ToolLauncher
):
    package(profile, "loud", {"shout": PLAIN})
    kit = package(profile, "gift", {"wrap": PLAIN})
    origin = {"kit": "presents", "version": "1", "hash": folder_hash(kit)}
    (profile / KITS_FILE).write_text(json.dumps({"pieces": {"function:gift": origin}}))
    approved = quarantine(profile, launcher)
    approve(approved, "loud")
    approve(approved, "gift")
    older_kernel = quarantine(profile, launcher, ConfinementSupport(abi=5))
    assert set(offered(older_kernel)) == {"shout"}
