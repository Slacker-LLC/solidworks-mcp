# solidworks-mcp

English | [简体中文](README.zh-CN.md)

MCP server that drives a **running** SOLIDWORKS session over its COM API. It
attaches to the session you already have open; it does not launch SOLIDWORKS,
register an add-in or touch the network.

192 tools. Covered vs not covered: [COVERAGE.md](COVERAGE.md).

<!-- mcp-name: io.github.Slacker-LLC/solidworks-mcp -->

## Requirements

Windows, SOLIDWORKS installed and **already running**, Python 3.10+.

## Install

```powershell
git clone https://github.com/Slacker-LLC/solidworks-mcp.git
cd solidworks-mcp
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install .
```

The command is `solidworks-mcp`; the distribution is `solidworks-mcp-server`.

Claude:

```json
{
  "mcpServers": {
    "solidworks-mcp": {
      "command": "C:\\path\\to\\.venv\\Scripts\\solidworks-mcp.exe"
    }
  }
}
```

Codex CLI (`~/.codex/config.toml`):

```toml
[mcp_servers.solidworks-mcp]
command = 'C:\path\to\.venv\Scripts\solidworks-mcp.exe'
```

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `SW_MCP_OUTPUT_ROOT` | `~/Documents/solidworks-mcp` | Everything the server writes stays under here. |
| `SW_MCP_TEMPLATE_DIR` | auto-discovered | `.prtdot` / `.asmdot` / `.drwdot` template directory. |
| `SW_MCP_DEMO_TOOLS` | unset | `1` adds demo tools that degrade tool selection. |
| `SW_MCP_SESSION_PID` | unset | Attach to a specific existing instance. |

Lengths are `_mm`, angles `_deg`. Geometry is passed as a `selection` object
(`{"edges": [4, 6, 9]}`, `{"planes": ["front"]}`); indices describe the current
model, so re-list them after a geometry change.

## Testing

```powershell
python -m unittest discover -s tests -p 'test_*.py'     # offline
python tests/live_p0_regression.py                      # live, SOLIDWORKS running
```

Contributor notes: [CONTRIBUTING.md](CONTRIBUTING.md) · release process:
[RELEASING.md](RELEASING.md).

## License

Apache License 2.0 — see [LICENSE](LICENSE) and the [Simplified Chinese note](LICENSE.zh-CN.md). Copyright 2026 JIALE LIU.
