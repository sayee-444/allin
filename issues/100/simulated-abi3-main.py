"""#100 evidence: Main as usual, but told the kernel's Landlock is ABI 3 (no network rules,
no scopes), to see the Notice and the border marks without another kernel."""
import sys

import allin.main.app as app
from allin.confinement.support import ConfinementSupport

app.kernel_support = lambda: ConfinementSupport(abi=3)

from allin.cli import main  # noqa: E402

sys.argv = ["allin", *sys.argv[1:]]
main()
