import argparse
from pathlib import Path

from allin.main.app import run


def main() -> None:
    parser = argparse.ArgumentParser(prog="allin")
    parser.add_argument(
        "--data-dir", type=Path, default=Path.cwd() / "var", help="runtime data (default: ./var)"
    )
    args = parser.parse_args()
    run(args.data_dir.resolve())


if __name__ == "__main__":
    main()
