"""`python -m mcp_server` entrypoint."""
import sys

from mcp_server.server import run


if __name__ == "__main__":
    sys.exit(run())
