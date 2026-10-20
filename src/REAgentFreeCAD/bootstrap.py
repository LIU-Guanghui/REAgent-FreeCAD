"""MCP process entry point with actionable dependency diagnostics."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import NoReturn


def _recommended_launcher() -> Path | None:
    """Return the checkout launcher when this package is running from a source tree."""

    name = "REAgentFreeCAD.exe" if sys.platform == "win32" else "REAgentFreeCAD"
    package_file = Path(__file__).resolve()
    source_root = package_file.parents[2]
    if (source_root / "pyproject.toml").is_file():
        return source_root / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / name
    return None


def _failure_cause(error: BaseException) -> str:
    message = str(error)
    missing_name = getattr(error, "name", None)
    if missing_name == "mcp":
        return "the selected Python interpreter does not contain the mcp package"
    if "_pydantic_core" in message or "DLL load failed" in message:
        return (
            "the pydantic-core native extension was present but Windows denied its DLL load"
        )
    if isinstance(error, ModuleNotFoundError):
        return f"a required Python package is missing ({missing_name or message})"
    return f"a required MCP dependency could not be imported ({message})"


def _format_failure(error: BaseException) -> str:
    """Build a stderr-only startup message without touching FreeCAD."""

    launcher = _recommended_launcher()
    launcher_line = (
        f"Recommended repository launcher: {launcher}"
        if launcher is not None
        else "Use the REAgentFreeCAD launcher created by uv in the selected environment."
    )
    return "\n".join(
        (
            "REAgentFreeCAD MCP server could not start because its Python runtime is unavailable.",
            f"Interpreter: {Path(sys.executable).resolve()}",
            f"Environment prefix: {Path(sys.prefix).resolve()}",
            launcher_line,
            f"Cause: {_failure_cause(error)}.",
            "Repair: configure the MCP client with the project's absolute .venv launcher, "
            "then run `uv sync --locked` from that repository.",
            "Do not use system Python, PYTHONPATH fallback, pip, or a FreeCAD executable.",
        )
    )


def _report_and_exit(error: BaseException) -> NoReturn:
    print(_format_failure(error), file=sys.stderr)
    raise SystemExit(78)


def main() -> None:
    """Start the MCP server after reporting import failures clearly."""

    try:
        from .server import main as run_server
    except (ImportError, OSError) as error:
        _report_and_exit(error)
    run_server()


if __name__ == "__main__":
    main()

__all__ = ["main"]
