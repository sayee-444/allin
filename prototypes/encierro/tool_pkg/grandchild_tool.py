"""#99: leave a grandchild that escapes the process group, to see whether the tool's end
takes it down."""

import subprocess
import time


def leave(seconds: str, wait: float) -> int:
    subprocess.Popen(["setsid", "sleep", seconds], start_new_session=True)
    time.sleep(wait)
    return 0
