import curses
import sys
from curses.textpad import rectangle


def main(stdscr):
    s = "o◡o"
    print(
        "helloooo  this is the sys.path "
        + s
        + " "
        + f"{sys.path[0]}"
        + "\n\n"
        + f"argumentos del sistema {sys.argv}"
    )

    height, width = stdscr.getmaxyx()

    divisor = 3

    while True:
        key = stdscr.getch()

        if key == curses.KEY_F1:  ## esc
            break

    return 0


if __name__ == "__main__":
    x = curses.wrapper(main)
    print(x)
