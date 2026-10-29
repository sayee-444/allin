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
        help="Sysprompt templates (default: ./sysprompts)",
    )
    args = parser.parse_args()
    run(args.data_dir.resolve(), args.sysprompts_dir.resolve())


if __name__ == "__main__":
    main()
