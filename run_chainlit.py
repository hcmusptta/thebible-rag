"""Start this Chainlit app without nest_asyncio on Python 3.14.

Chainlit 2.11 applies nest_asyncio unconditionally in its CLI. The current
nest_asyncio release is incompatible with Python 3.14's task bookkeeping and
causes AnyIO failures while static UI files are served.
"""

import os
import sys

import nest_asyncio


def main() -> None:
    # Uvicorn already owns the event loop, so nested-loop support is unnecessary.
    nest_asyncio.apply = lambda *args, **kwargs: None

    # Chainlit maps the generic DEBUG variable to a boolean CLI option. Ignore a
    # non-boolean value such as the system-wide DEBUG=release setting.
    debug = os.environ.get("DEBUG", "").strip().lower()
    if debug not in {"", "0", "1", "false", "true", "no", "yes", "off", "on"}:
        os.environ.pop("DEBUG", None)

    from chainlit.cli import cli

    sys.argv = [sys.argv[0], "run", "main.py", *sys.argv[1:]]
    cli()


if __name__ == "__main__":
    main()
