# solidworks-mcp

[English](README.md) | 简体中文

通过 COM API 驱动**正在运行**的 SOLIDWORKS 会话的 MCP 服务器。它连接你已经打开的
会话，不启动 SOLIDWORKS、不注册插件、不访问网络。

192 个工具。已覆盖与未覆盖清单：[COVERAGE.zh-CN.md](COVERAGE.zh-CN.md)。

## 环境要求

Windows、已安装且**正在运行**的 SOLIDWORKS、Python 3.10+。

## 安装

```powershell
git clone https://github.com/Slacker-LLC/solidworks-mcp.git
cd solidworks-mcp
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install .
```

命令是 `solidworks-mcp`，发行包是 `solidworks-mcp-server`。

Claude：

```json
{
  "mcpServers": {
    "solidworks-mcp": {
      "command": "C:\\path\\to\\.venv\\Scripts\\solidworks-mcp.exe"
    }
  }
}
```

Codex CLI（`~/.codex/config.toml`）：

```toml
[mcp_servers.solidworks-mcp]
command = 'C:\path\to\.venv\Scripts\solidworks-mcp.exe'
```

## 配置

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `SW_MCP_OUTPUT_ROOT` | `~/Documents/solidworks-mcp` | 服务器写出的所有文件都在此目录下。 |
| `SW_MCP_TEMPLATE_DIR` | 自动发现 | `.prtdot` / `.asmdot` / `.drwdot` 模板目录。 |
| `SW_MCP_DEMO_TOOLS` | 未设置 | 设为 `1` 会加入降低工具选择质量的演示工具。 |
| `SW_MCP_SESSION_PID` | 未设置 | 连接指定的已有实例。 |

长度以 `_mm` 结尾，角度以 `_deg` 结尾。几何通过 `selection` 对象传入
（`{"edges": [4, 6, 9]}`、`{"planes": ["front"]}`）；索引对应当前模型，几何变化后需
重新列举。

## 测试

```powershell
python -m unittest discover -s tests -p 'test_*.py'     # 离线
python tests/live_p0_regression.py                      # 实机，需 SOLIDWORKS 运行
```

贡献说明见 [CONTRIBUTING.md](CONTRIBUTING.md)，发布流程见 [RELEASING.md](RELEASING.md)。

## 许可证

Apache License 2.0 —— 见 [LICENSE](LICENSE) 与[简体中文说明](LICENSE.zh-CN.md)。版权 2026 JIALE LIU。
