"""Entry point script for PyInstaller builds.

PyInstaller treats the analyzed script as a standalone top-level module, so
importing `rmc.cli` directly here (rather than analyzing `src/rmc/cli.py` as
the script) avoids "attempted relative import with no known parent package"
errors caused by the relative imports inside the `rmc` package.
"""

from rmc.cli import cli

if __name__ == "__main__":
    cli()
