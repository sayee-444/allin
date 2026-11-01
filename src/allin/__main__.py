import argparse
from pathlib import Path

from allin.main.app import run


def main() -> None:
    parser = argparse.ArgumentParser(prog="allin")
    parser.add_argument(
        "--data-dir", type=Path, default=Path.cwd() / "var", help="runtime data (default: ./var)"
    )
    parser.add_argument(
        "--sysprompts-dir",
        type=Path,
        default=Path.cwd() / "sysprompts",
        help="the installation's Sysprompt templates, which the Profile default is born with"
        " (default: ./sysprompts)",
    )
    parser.add_argument(
        "--profile",
        help="the Profile to open (default: the last one used); one that does not exist is an"
        " error",
    )
    parser.add_argument(
        "--devastation",
        action="store_true",
        help="Main answers by itself the Confirmations of its Chat and of its exempt Sessions,"
        " for this start only",
    )
    args = parser.parse_args()
    profile: str | None = args.profile
    run(args.data_dir.resolve(), args.sysprompts_dir.resolve(), profile, args.devastation)


if __name__ == "__main__":
    main()
