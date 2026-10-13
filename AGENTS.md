# Agent instructions / 仓库约定

## Language / 语言

- Repository documents are English-first with Simplified Chinese counterparts. Keep the `README`, `COVERAGE`, `CONTRIBUTING`, `RELEASING` and license-note pairs in sync; this file carries both languages. Commit messages and pull requests are English-first.
- User conversations may be in Simplified Chinese. Keep code, identifiers, paths, commands, configuration keys and tool names unchanged. Quote errors before explaining them.

- 仓库文档以英文为主，并提供简体中文版。同步维护 `README`、`COVERAGE`、`CONTRIBUTING`、`RELEASING` 和许可证说明的中英文版本；本文件包含两种语言。提交信息和 PR 以英文为主。
- 与用户的对话可以使用简体中文。代码、标识符、路径、命令、配置键名和工具名称保持原样；先引用报错原文再解释。

## Merge permissions / 合并权限

- `main` is protected. Changes require a pull request and all four `offline` checks (Python 3.10–3.13); conversations must be resolved. Force pushes and branch deletion are blocked, including for administrators.
- Automation may create branches, commit, push branches, open pull requests and wait for CI. **Do not merge pull requests, push directly to `main`, or change branch protection.** The repository owner (@Slacker-LLC) merges.

- `main` 已启用保护。所有改动必须经过 PR，并通过四个 `offline` 检查（Python 3.10–3.13）；必须解决全部会话。管理员也不能强推或删除分支。
- 自动化可以创建分支、提交、推送分支、开 PR 并等待 CI。**不得合并 PR、直接推送 `main` 或更改分支保护。** PR 由仓库所有者（@Slacker-LLC）合并。

## Verification / 验证要求

- Before code changes, run `python -m unittest discover -s tests -p 'test_*.py'`.
- For COM, geometry or SOLIDWORKS-state changes, also run the relevant `tests/live_*_regression.py` and read back actual geometry, area, volume or native definitions. A return code alone is not proof of success.
- Pull request descriptions state what changed, why, how it was verified and any remaining gaps. Report failures as failures.

- 代码改动前运行 `python -m unittest discover -s tests -p 'test_*.py'`。
- 涉及 COM、几何或 SOLIDWORKS 状态的改动，还要运行对应的 `tests/live_*_regression.py`，并读回真实几何、面积、体积或原生定义。不能只凭返回码判定成功。
- PR 正文说明改动内容、原因、验证方式和剩余缺口；失败项如实记录为失败。
