# 发布

[English](RELEASING.md) | 简体中文

MCP Registry 只存元数据，发布顺序是先发包到 PyPI，再发布指向该包的 Registry 条目。Registry 会先验证 PyPI 包。

## PyPI 包名

PyPI 上的 `solidworks-mcp` 属于另一个项目（`hjbaard/SolidWorks-MCP`，版本 0.16.0），本仓库使用 `solidworks-mcp-server`：

| 位置 | 值 |
| --- | --- |
| `pyproject.toml` → `project.name` | `solidworks-mcp-server` |
| `server.json` → `packages[0].identifier` | `solidworks-mcp-server` |
| `pyproject.toml` → `project.scripts` | `solidworks-mcp`（安装后的命令） |
| `server.json` → `name` | `io.github.Slacker-LLC/solidworks-mcp` |

首次发布前确认名称仍未被占用（404 表示当前没有该包）：

```powershell
curl "https://pypi.org/pypi/solidworks-mcp-server/json"
```

GitHub 账号与仓库已从 `limuzi013` 改名为 `Slacker-LLC`。PyPI 和 MCP Registry 按当前名称匹配，发布配置不要再写旧名称。

## 通过 tag 发布

推送 `v*` tag 会触发 `.github/workflows/publish-mcp.yml`：检查版本、所有权标记、构建，然后依次发布 PyPI 与 MCP Registry；认证使用 OIDC，不在仓库保存令牌。

首次发布前，在 PyPI 项目的 Publishing 设置中添加 GitHub pending publisher：owner `Slacker-LLC`、repository `solidworks-mcp`、workflow `publish-mcp.yml`。

```bash
git tag v0.1.0 && git push origin v0.1.0
```

## 发布前检查

以下版本号必须相同：

| 文件 | 字段 |
| --- | --- |
| `pyproject.toml` | `project.version` |
| `server.json` | `version` |
| `server.json` | `packages[0].version` |
| `solidworks_mcp/__init__.py` | `__version__` |

Registry 通过 PyPI 包 README 中的 `mcp-name:` 标记验证所有权。`README.md` 中的标记必须与 `server.json` 的 `name` 和发布账号命名空间一致：

```markdown
<!-- mcp-name: io.github.Slacker-LLC/solidworks-mcp -->
```

GitHub OIDC 命名空间保留账号大小写；这里必须写 `Slacker-LLC`。移除该标记会导致 Registry 包验证失败。

## 构建与发布

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m pip install build
.\.venv\Scripts\python.exe -m build
```

wheel 名称为 `solidworks_mcp_server-<version>-py3-none-any.whl`，安装命令仍为 `solidworks-mcp`。

手动发到 PyPI 时：

```powershell
.\.venv\Scripts\python.exe -m pip install twine
.\.venv\Scripts\python.exe -m twine upload dist/*
```

PyPI 版本号发布后无法复用，即使删除发布也一样。上传前先检查版本。

## 发布到 MCP Registry

安装官方 `mcp-publisher` CLI：

```powershell
$arch = if ([System.Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture -eq "Arm64") { "arm64" } else { "amd64" }; Invoke-WebRequest -Uri "https://github.com/modelcontextprotocol/registry/releases/latest/download/mcp-publisher_windows_$arch.tar.gz" -OutFile "mcp-publisher.tar.gz"; tar xf mcp-publisher.tar.gz mcp-publisher.exe; rm mcp-publisher.tar.gz
```

登录并发布：

```powershell
.\mcp-publisher.exe login github
.\mcp-publisher.exe publish
```

验证条目：

```powershell
curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.Slacker-LLC/solidworks-mcp"
```

## 首次发布后的安装方式

PyPI 发布前，`uvx solidworks-mcp-server` 尚不可用。发布成功后可使用：

```json
{
  "mcpServers": {
    "solidworks-mcp": {
      "command": "uvx",
      "args": ["solidworks-mcp-server"]
    }
  }
}
```
