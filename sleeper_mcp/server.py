"""Entry point.

Importing the tool modules is what registers their @mcp.tool() functions on
the shared FastMCP instance in client.py. The imports look unused; they are not.
"""

import sys

from .client import mcp
from . import discovery as _discovery  # noqa: F401
from . import keepers as _keepers      # noqa: F401
from . import lineups as _lineups      # noqa: F401
from . import playoffs as _playoffs    # noqa: F401
from . import reads as _reads          # noqa: F401
from . import signals as _signals      # noqa: F401  (inert without a file)
from . import usage as _usage          # noqa: F401
from . import writes as _writes        # noqa: F401


def main() -> None:
    if len(sys.argv) > 1:
        from .setup import dispatch
        code = dispatch(sys.argv[1:])
        if code is not None:
            sys.exit(code)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
