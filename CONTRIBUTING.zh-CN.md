# 贡献指南

[English](CONTRIBUTING.md) | 简体中文

## 离线测试能与不能验证什么

离线套件 `python -m unittest discover -s tests -p 'test_*.py'` 共 225 项，覆盖单位换算、选择标记、几何辅助、各模块工具契约和导入护栏。CI 在 Windows 上运行 Python 3.10–3.13，并单独导入 `solidworks_mcp.server`。

离线测试无法证明 SOLIDWORKS 是否接受某次调用。COM 行为无法被准确 mock；涉及几何的改动必须在运行中的实例上验证：

```powershell
.\.venv\Scripts\python.exe tests\live_p0_regression.py
```

特征或草图工具必须在真实 SOLIDWORKS 上运行，并通过读回几何、与解析结果对照来验证，不能只相信工具的成功消息。SOLIDWORKS 经常返回 null 而不是抛出异常。

## 编写工具前先查类型库

公开 API 参考与随软件发布的类型库可能不一致。例如本机的 `FeatureFillet3` 接受 14 个参数，而文档写的是 7 个。先从本机读取签名：

```bash
python tools/tlb_probe.py methods '^IFeatureManager$' '^FeatureFillet3$'
python tools/tlb_probe.py enums '^swMateType_e$'
```

## 添加工具

写一个带装饰器的函数，注册表会处理其余部分：

```python
@tool("my_tool", "What it does. Lengths are millimetres.",
      {"depth_mm": {"type": "number"}}, ["depth_mm"])
def my_tool(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_part()
    ...
    return result(True, "Did the thing.")
```

以下约定必须遵守，违反会让服务器向模型报告错误结果：

- **在边界处理单位。** 长度参数以 `_mm` 结尾，角度以 `_deg` 结尾；只在 `sw_core` 换算一次，不让工具接收米。
- **用 `selection` 对象传几何。** 不传原始 COM 选择状态；由 `sw_core` 设置标记。
- **模型改变后重建并报告 `GetWhatsWrong`。** 不检查就返回成功不可接受。
- **根据模型判断成功，不能只看返回值。** `SketchTrim` 成功时返回 `False`；`SketchAddConstraints` 会静默忽略不认识的字符串。
- **不要打开模态对话框。** 它会阻塞打开它的 COM 调用并卡住整个服务器。

## 常见耗时陷阱

**pywin32 对 SOLIDWORKS 成员的暴露方式不一致。** 类型库中的多数成员声明为带参数的 `PROPGET`；晚绑定 pywin32 可能在直接访问属性时用零参数调用它。`IBody2.GetFaces` 因而会返回看似单元素结果的绑定方法；这样调用 `ModelDocExtension.AddDimension` 会直接导致 SOLIDWORKS 崩溃。服务器对带参数成员调用 `sw_core.flag_methods()` 的 `_FlagAsMethod`；`sw_core.value()` 会调用零参数可调用对象，而不是返回方法对象。

**模态对话框会卡住服务器。** 默认启用的 *Input dimension value* 会使 `AddDimension2` 弹出 “Modify” 对话框。尺寸工具会暂时关闭该选项并在完成后恢复。工具挂住时，检查 SOLIDWORKS 窗口后面是否有对话框。

**返回值可能不可靠。** `SketchTrim` 成功时返回 `False`；`Create*` 草图 API 的数组真值不可靠；`SketchAddConstraints` 会忽略不认识的字符串。应检查模型，例如线段数、`GetWhatsWrong` 和关系数量。

**多参考特征需要独立标记。** `InsertRefPlane` 从标记 0、1、2 读取三个参考，而不是按选择顺序读取。全部使用标记 0 会静默失败；`create_plane` 因此为每个参考使用不同标记。

**两个单位陷阱。** `IPartDoc.GetPartBox(False)` 返回文档单位，误当成米会大 1000 倍，应传 `True`。`swSketchLINE` 的值为 `0`，用 `or` 处理段类型会把所有直线变成 “unknown”。

## 为什么 `mcp` 固定在 2.0 以下

`mcp` 2.0 移除了服务器依赖的低层 `Server.list_tools()` / `Server.call_tool()` 装饰器 API，并将 `Tool.inputSchema` 改名为 `input_schema`（旧名仅保留为序列化别名）。在 2.0 下导入 `solidworks_mcp.server` 会报 `AttributeError: 'Server' object has no attribute 'list_tools'`，因此依赖固定为 `<2`，等待迁移完成。

CI 单独导入服务器模块。单元测试只覆盖 `sw_core`，之前入口损坏时测试仍然通过。

## 范围

服务器连接用户已经打开的 SOLIDWORKS 会话并调用有文档记录的 API。它不启动 SOLIDWORKS、不注册插件、不执行任意代码，也不访问网络；不接受会向 MCP 客户端开放 CAD 会话任意代码执行能力的改动。

## 语言

仓库文档以英文为主，并维护简体中文版本。README、覆盖清单、贡献指南、发布指南和许可证说明需同步；`AGENTS.md` 同时包含两种语言。代码、标识符、命令和报错文本保持原样。

## 分支保护与合并

`main` 受保护：所有改动经过 PR，四个 `offline` CI 作业（Windows、Python 3.10–3.13）为必需状态检查；管理员也不能强推或删除分支；合并前必须解决所有会话。

自动化可以创建分支、推送并开 PR，**合并由仓库所有者完成**。自动化不得合并、直接推送 `main` 或修改保护规则。具体 agent 指令见 `AGENTS.md`。

当前仓库只有一个账号，因此 `required_approving_review_count` 设为 `0`：GitHub 不允许作者批准自己的 PR，设为 1 会阻塞所有合并。增加第二个协作者后，可将其改为 1，让“只有所有者合并”成为技术限制。
