"""The MCP servers the host's own Claude configuration declares, by name.

With the operator's ``dangerously_allow_host_mcp`` opt-in on, a session
loads the servers the host's user-level Claude configuration declares.
Whether that includes a tracker server decides whether a board session
still needs the deployment's own one, so the names are read here, once, at
boot.  Only names are read: a definition can carry a credential, and
nothing in this process needs one.

The file is the one Claude Code keeps its user-level servers in:
``.claude.json`` under ``CLAUDE_CONFIG_DIR`` when that is set, otherwise
under the user's home directory.  A missing or unreadable file declares
nothing.
"""

import json
import os
from pathlib import Path

_CONFIG_FILE = ".claude.json"
_CONFIG_DIR_VARIABLE = "CLAUDE_CONFIG_DIR"
_SERVERS_KEY = "mcpServers"


def user_claude_config_file() -> Path:
    """Where Claude Code keeps the host's user-level configuration."""
    return Path(os.environ.get(_CONFIG_DIR_VARIABLE) or Path.home()) / _CONFIG_FILE


def host_mcp_server_names(config_file: Path) -> frozenset[str]:
    """The names of the user-level MCP servers *config_file* declares."""
    try:
        document = json.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    if not isinstance(document, dict):
        return frozenset()
    servers = document.get(_SERVERS_KEY)
    if not isinstance(servers, dict):
        return frozenset()
    return frozenset(str(name) for name in servers)
