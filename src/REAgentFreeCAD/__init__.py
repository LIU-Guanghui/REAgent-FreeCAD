"""REAgentFreeCAD package with a lazy MCP server entry point."""

__version__ = "0.0.1"


def main() -> None:
    """Load and run the MCP server only after the process entry point is ready."""

    from .server import main as run_server

    run_server()


__all__ = ["__version__", "main"]
