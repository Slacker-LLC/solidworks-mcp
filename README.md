# solidworks-mcp

An MCP server that drives a **running** SOLIDWORKS session over its COM API.

It does not launch SOLIDWORKS, register an add-in, execute arbitrary code, or
touch the network. It attaches to a session you already have open and calls the
documented API — so if a tool can't do something, neither could a macro.

180 tools: sketching with real relations and driving dimensions, the solid
features you actually reach for, reference geometry, assemblies and mates, and —
importantly — a feedback channel, including screenshots returned as images so
the model can see what it just built.

覆盖范围包含 SolidWorks 本体与官方自带、随附模块，排除嘉立创等第三方插件。
当前仍未全覆盖；功能域、完成判据和缺口记录见 [COVERAGE.md](COVERAGE.md)。

A sibling server, [`autocad-mcp`](https://github.com/limuzi013/autocad-mcp),
does the same for AutoCAD.

<!-- mcp-name: io.github.limuzi013/solidworks-mcp -->

---

## Requirements

- Windows, with SOLIDWORKS installed and **already running**
- Python 3.10+

## Install

```powershell
git clone https://github.com/limuzi013/solidworks-mcp.git
cd solidworks-mcp
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install .
```

That puts a `solidworks-mcp` command in the environment, which is what the MCP
host runs. `uv` works too, if you prefer it:

```powershell
uv tool install --from git+https://github.com/limuzi013/solidworks-mcp solidworks-mcp
```

### Claude Code / Claude Desktop

```json
{
  "mcpServers": {
    "solidworks-mcp": {
      "command": "C:\\path\\to\\.venv\\Scripts\\solidworks-mcp.exe"
    }
  }
}
```

### Codex CLI (`~/.codex/config.toml`)

```toml
[mcp_servers.solidworks-mcp]
command = 'C:\path\to\.venv\Scripts\solidworks-mcp.exe'
```

Use single-quoted TOML strings so backslashes survive. Restart the MCP host after
editing its configuration.

An MCP host can also be pointed straight at a checkout, with nothing installed
but the dependencies — `server.py` at the repository root exists for exactly
that:

```toml
[mcp_servers.solidworks-mcp]
command = 'C:\path\to\.venv\Scripts\python.exe'
args = ['C:\path\to\solidworks-mcp\server.py']
```

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `SW_MCP_OUTPUT_ROOT` | `~/Documents/solidworks-mcp` | Every file the server writes stays under here. Paths outside it are refused. |
| `SW_MCP_TEMPLATE_DIR` | auto-discovered | Where to look for `.prtdot` / `.asmdot` / `.drwdot` templates if SOLIDWORKS has no default configured. |
| `SW_MCP_DEMO_TOOLS` | unset | Set to `1` to register the basketball demo tools. Off by default: a `create_basketball` sitting next to `revolve` measurably degrades tool selection. |

---

## The two ideas that make it usable

### 1. Units are explicit, always

Every length parameter ends in `_mm` and every angle in `_deg`. The COM API
underneath is metres and radians; conversion happens once, at the boundary. No
tool has ever silently taken metres.

### 2. Selection is declarative

SOLIDWORKS features read from an implicit global selection set, with per-feature
"marks" deciding which selection means what. Exposing that to an agent is
hopeless. Instead every tool that needs geometry takes the same `selection`
object, and the server sets the marks:

```jsonc
// list_edges first, then:
{ "selection": { "edges": [4, 6, 9] } }
{ "selection": { "face_edges": [2] } }        // every edge of face 2
{ "selection": { "planes": ["front"] } }
{ "selection": { "sketch_segments": [0, 1] } }
{ "selection": { "points": [{ "x_mm": 30, "y_mm": 0, "z_mm": 20, "type": "FACE" }] } }
```

Indices come from `list_faces` / `list_edges` / `list_vertices` /
`list_sketch_segments` and describe the **current** model state — re-list after
any geometry change. Under the hood, selection resolves to a point that provably
lies on the entity and picks by 3D coordinate, which survives the fact that
late-bound Python cannot QueryInterface a `Face2` to `IEntity`.

---

## Tools

### Session and documents
| Tool | Purpose |
| --- | --- |
| `solidworks_status` | Verify the connection; report version and active document. |
| `get_active_document_info` | Title, path, type, unsaved state. |
| `create_new_document` | New part / assembly / drawing from the default template. |
| `open_document` | Open a `.sldprt` / `.sldasm` / `.slddrw`. |
| `save_document` / `save_active_document` | Save-as under the output root / save in place. |
| `export_document` | STEP, IGES, STL, Parasolid, 3MF, or an image. |
| `rebuild_document` | Rebuild and report failing features. |
| `set_appearance` / `set_material` | Display colour; real material (so mass properties mean something). |

### Reference geometry
`list_reference_planes`, `create_plane` (offset / angle / midplane / three points /
parallel-through-point), `create_axis`.

`create_reference_points` / `list_reference_points` 支持圆心、直线中点、面中心、
距离/百分比分布、均匀分布、交点、投影及草图点，读回模型坐标。
`create_coordinate_system` / `list_coordinate_systems` 创建与查询坐标系，
原点单位为 mm，输入角度为度；数值坐标系创建需要 SolidWorks 2022+。
参考点投影当前支持模型面，基准面投影仍是覆盖缺口。

`create_curve_through_points` / `get_curve_points` / `set_curve_points` 创建、
读取和修改通过 XYZ 点的原生参考曲线，输入和输出坐标均为 mm。
`composite_curve` 将相连的草图、边或参考曲线生成组合曲线。

### Sketching
`create_sketch` (on a plane **or a model face**), `create_3d_sketch`, `edit_sketch`, `close_sketch`,
`list_sketches`, `list_sketch_segments`, `list_sketch_points`, `get_sketch_status`.

`create_3d_sketch` 在零件或装配体中创建原生 3D 草图；`draw_line` 支持 `z1_mm/z2_mm`，
`draw_point` 和 `draw_spline` 支持 `z_mm`。3D 坐标以模型坐标为准，单位为 mm；
2D 草图拒绝非零 Z，避免原生接口忽略坐标。编辑与关闭根据原生 `Is3D` 选用对应接口，
编辑时核验持久引用；读取较早草图时不误报为最新草图名称。
`list_sketch_points` 包含内部点与用户点，返回选择索引、类型和草图/模型坐标。
`draw_centerline` 支持 XYZ 构造线，并核验端点与构造属性；`draw_3point_arc` 支持
三点 XYZ 圆弧，核验半径、弧长、端点和点到曲线距离，共线/重合输入在创建前拒绝。
重叠/共端点圆弧原生结果曾偏离输入，返回未确认状态。圆/椭圆可在 2D 平面草图创建后
用 `convert_entities` 转换成空间曲线；转换逐点比较源曲线经坐标变换后的采样点与输出，
并读取实际类型，椭圆在本机变为样条。`draw_ellipse` 核验理论点和周长，直接 3D 创建会
产生无有效曲线的原生对象，因此在修改前拒绝。模型边/面与部分圆弧等输入的完整对应
几何核验仍未覆盖；返回的 `geometry_correspondence_confirmed` 明确区分已核验与未核验。
其他 3D 曲线、工作平面、完整约束/尺寸与更多建模组合仍有缺口。
`add_3d_dimension` 用两个原生点索引创建 X/Y/Z 投影尺寸；重建求解后恢复原草图编辑，
核验线性尺寸、点的持久引用及实际轴向距离。零件 XYZ 和装配体 XY 实测通过；
本机装配体 Z 接口返回空，明确失败并保留缺口。
含 3D 圆/椭圆转换的零件中，后续 X 尺寸也出现原生拒绝，仍为缺口；原生点选择前
验证持久引用，避免把选择成功当成选中了正确点。`list_dimensions` 保留原生状态值：
0 未知、1 从动、2 驱动，并修正 `driven` 标记。

Geometry: `draw_line`, `draw_centerline`, `draw_circle`, `draw_rectangle`,
`draw_arc`, `draw_3point_arc`, `draw_ellipse`, `draw_polygon`, `draw_slot`,
`draw_point`, `draw_spline`.

Editing: `sketch_fillet`, `sketch_chamfer`, `sketch_trim`, `sketch_offset`,
`sketch_mirror`, `convert_entities`, `set_construction_geometry`.

Parametrics: `add_relation`, `add_dimension`, `set_dimension`, `list_dimensions`.

### Features
`boss_extrude`, `cut_extrude`, `revolve`, `fillet`, `chamfer`, `shell`, `draft`,
`rib`, `simple_hole`, `sweep`, `loft`, `linear_pattern`, `circular_pattern`,
`mirror_feature`, `delete_feature`, `rename_feature`, `set_feature_suppression`.

### Inspection — the feedback channel
| Tool | Purpose |
| --- | --- |
| `capture_screenshot` | Returns the view as an **image**, so the model can look at its own work. |
| `list_faces` / `list_edges` / `list_vertices` / `list_bodies` | Topology with types, sizes, and selection indices. Filterable by surface type, area, normal direction, curve type, length. |
| `get_mass_properties` / `get_bounding_box` / `measure` | Numbers to check the geometry against. |
| `check_errors` | What SOLIDWORKS thinks is wrong — see below. |
| `set_view` | Named view plus zoom-to-fit. |

### 直接编辑

| 工具 | 功能 |
| --- | --- |
| `move_faces` | 面偏移、按 XYZ 平移或绕指定原点旋转；长度为 mm、角度为度。 |
| `delete_faces` | 删除面，可选择修补、填充或相切填充；仅删除可能将实体变成曲面。 |
| `replace_faces` | 用指定曲面替换模型面，目标面与替换曲面分别使用原生选择标记。 |

拓扑改变后重新查询面和边。删除与修补、替换面已通过实机体积/面积检查；
删除面的填充及相切填充模式仍待专项验证。

### Assemblies
`list_components`, `insert_component`, `add_mate`, `list_mates`,
`set_component_fixed`, `set_component_visibility`, `set_component_suppression`,
`set_component_configuration`.

### 多实体、曲面与螺旋线

| 工具 | 功能 |
| --- | --- |
| `scale_bodies` | 选定实体等比例或按 X/Y/Z 缩放，支持质心与原点。 |
| `move_copy_bodies` | 实体平移、旋转或复制；平移和旋转分两次调用，长度为 mm、角度为度。 |
| `combine_bodies` | 多实体并集、差集与交集；差集的第一个实体为保留的主体。 |
| `delete_bodies` | 创建删除/保留实体特征。 |
| `mid_surface` / `get_mid_surface_data` | 原生自动中面与只读检查；读取面配对、厚度、面积、位置和缝合结果。非零位置未生效时返回失败。 |
| `preview_surface_trim` / `trim_surface` / `get_surface_trim_data` | 标准与相互曲面修剪；预览区域面积、边界与选择点，按区域索引保留或删除，并读取原生特征数据。 |
| `boundary_surface` / `get_boundary_feature_data` | 单/双方向有序曲线的边界曲面、按方向修剪、原生形成实体选项及只读检查；相切/曲率控制按边界采样核验几何，未确认时保留特征并返回失败。 |
| `wrap_sketch` / `get_wrap_data` / `set_wrap_parameters` | 单/多实体面的包覆三模式、解析/样条方法、三类方向及厚度/模式/方向/源草图修改；读回原生 `Face` 字段的前置几何。以体积、面分割、原生定义和持久引用快照验证。方法/网格不可读回；反向、清除方向及目标面修改未确认时返回失败。 |
| `dome` / `get_dome_data` / `set_dome_parameters` | 原生圆顶创建、定义与几何读取、高度/凹凸/椭球模式修改、目标面替换、单点/整体点草图约束和方向边。每个显式草图点转换到模型坐标，检查实际曲面距离；纯曲线草图不作为隐含点约束。清除约束/方向核验引用是否实际消失，本机请求被忽略时返回失败并保留特征。另以体积、中心高度、24 点椭球采样及持久引用核验。 |
| `surface_sweep` | 开口/闭合草图或圆截面扫描；导引线、法向控制、扭转、第一/第二/双向扫描，读回原生参数。要求 SW 2018+。 |
| `delete_surface_holes` | 删除选定曲面孔边界，保留未选孔；支持单孔与多孔。 |
| `surface_extrude` / `planar_surface` | 从草图生成拉伸曲面或平面曲面。 |
| `offset_surface` / `knit_surfaces` / `thicken_surface` | 偏移面、缝合曲面和曲面加厚；读取实际曲面或实体结果。 |
| `list_surface_bodies` | 查询曲面体及其面积，面积单位为 mm²。 |
| `create_helix` | 从圆草图按螺距和圈数生成螺旋线，读取实际螺距、圈数及高度。 |

实体操作沿用 `selection`，例如 `{"selection": {"bodies": [0, 1]}}`。
索引取自 `list_bodies`，几何变化后需重新查询。曲面体索引与实体索引独立，
曲面选择使用 `surface_bodies`，索引取自 `list_surface_bodies`。

修剪先调用 `preview_surface_trim`，再把返回的区域索引传给 `trim_surface`。
标准修剪指定一个 `trim_selection`；相互修剪设置 `mode=mutual`，只指定目标曲面体。
区域索引按目标顺序、包围盒和面积排序，几何变化后必须重新预览。
复杂区域可用 `picked_points_mm` 指定实际落在区域上的选择点。
相互修剪在本机即使 `knit=false` 仍会合并相接曲面；工具返回失败并保留特征信息。
实体形成选项、更多复杂曲面组合和旧版软件尚待覆盖或实测。

边界曲面的 `direction1`、`direction2` 是有序曲线列表，每项包含一个 `selection`。
曲线需先全部选择，再设置条件；创建后读取实际曲线数量、影响范围、相切类型和实体类型。
`create_solid=true` 使用原生 `InsertNetBlend2` 的形成实体选项，旧接口不支持该选项。
相切/曲率连续性检查使用输入面边界的几何采样和输出曲面的法向、第二基本形式，
参数读回正确而几何不符时仍返回失败。本机圆端面案例的法向差为 90°，该细项尚未完成。
方向向量、中心线、控制柄编辑和更多复杂边界条件仍待覆盖。

### 钣金与焊件

| 工具 | 功能 |
| --- | --- |
| `sheet_metal_base_flange` | 创建基体法兰，设置板厚、折弯半径与 K 因子。 |
| `list_sheet_metal_features` / `get_sheet_metal_parameters` / `set_sheet_metal_parameters` | 读取和修改钣金参数；默认操作原生模板参数，可指定实体钣金特征。 |
| `set_flat_pattern` / `export_flat_pattern` | 展开、折叠与 DXF 导出；导出要求零件已保存。 |
| `create_weldment` | 创建焊件特征。 |
| `list_weldment_profiles` / `get_weldment_profile_configurations` | 查询本机轮廓与规格配置。 |
| `insert_structural_member` | 按草图线段组、轮廓配置和角度创建结构构件。 |
| `list_cut_list` / `update_cut_list` | 读取和更新切割清单，返回实体与解析属性。 |

### 官方 Motion 算例

`list_motion_studies`、`create_motion_study`、`activate_motion_study`、
`duplicate_motion_study`、`delete_motion_study`、`set_motion_study_type`、
`set_motion_study_timing`。

时间单位为秒。零件与装配体均支持基础算例管理；工具读取实际支持的类型位掩码，
无法使用的 Basic Motion 或 Motion Analysis 会返回失败，不自动加载模块。
算例管理已验证，动力学求解、载荷、接触、结果曲线与动画输出仍待覆盖。

### 文档、配置、属性与方程

| 工具 | 功能 |
| --- | --- |
| `list_open_documents` / `activate_document` / `close_document` | 查询、切换和关闭已有文档；关闭有未保存修改的文档需显式传入 `discard_changes=true`。 |
| `list_solidworks_sessions` / `select_solidworks_session` | 查询已经运行的实例及其文档，按 `process_id` 明确选择后续 MCP 调用连接的实例。 |
| `list_configurations` / `create_configuration` / `activate_configuration` / `delete_configuration` | 查询、创建、激活和删除配置；不能删除活动配置。 |
| `list_custom_properties` / `get_custom_property` / `set_custom_property` / `delete_custom_property` | 查询和编辑文档级或配置级自定义属性，包括原始值、解析值与类型。 |
| `list_equations` / `add_equation` / `set_equation` / `delete_equation` / `evaluate_equations` | 查询和编辑方程与全局变量，使用 SOLIDWORKS 方程语法，例如 `"Width" = 25mm`。 |
| `list_materials` | 搜索实际配置的材质库，返回可传给 `set_material` 的准确名称与数据库路径。 |

`configuration` 为空时操作文档级属性，非空时必须是已存在的配置名称。
实例选择只作用于当前 MCP 服务进程；`process_id=0` 恢复默认 COM 注册。
可通过 `SW_MCP_SESSION_PID` 指定启动时连接的已有实例；显式工具选择优先。
实例退出后调用会失败，不会自动切换到另一个实例。查询可读不代表建模命令正常。
方程里的单位由 SOLIDWORKS 语法解释，应明确写出 `mm` 等单位；方程索引在删除后变化。

### Engineering drawings
`create_drawing`, `list_sheets`, `add_sheet`, `activate_sheet`,
`insert_standard_views`, `insert_model_view`, `insert_projected_view`,
`insert_section_view`, `insert_detail_view`, `list_drawing_views`,
`activate_drawing_view`, `create_drawing_sketch`, `set_drawing_view`,
`insert_model_annotations`, `auto_dimension_view`, `insert_center_marks`,
`insert_centerlines`, `add_note`.

For section and detail views, call `create_drawing_sketch` after activating the
parent view, then use the normal `draw_line` or `draw_circle` sketch tools.

---

## A worked example

```jsonc
create_new_document   { "kind": "part" }
create_sketch         { "plane": "front", "name": "Base" }
draw_rectangle        { "x1_mm": 10, "y1_mm": 10, "x2_mm": 70, "y2_mm": 50 }
add_dimension         { "selection": { "sketch_segments": [0] }, "kind": "horizontal",
                        "value_mm": 80, "place_x_mm": 40, "place_y_mm": -10 }
add_dimension         { "selection": { "sketch_segments": [1] }, "kind": "vertical",
                        "value_mm": 50, "place_x_mm": -10, "place_y_mm": 30 }
close_sketch          {}
boss_extrude          { "depth_mm": 20, "name": "BasePad" }

list_edges            { "curve_type": "line", "min_length_mm": 19 }   // find the four verticals
fillet                { "radius_mm": 5, "selection": { "edges": [0, 1, 2, 3] } }
shell                 { "thickness_mm": 2, "selection": { "faces": [4] } }

capture_screenshot    { "view": "isometric" }
get_mass_properties   {}
```

---

## Notes from the implementation

These are the things that cost real time. They are documented because anyone
automating SOLIDWORKS from Python will hit them.

**SOLIDWORKS members are inconsistently exposed to pywin32.** Most are declared
in the type library as `PROPGET` *with arguments*. Late-bound pywin32 may resolve
such a member on plain attribute access by invoking it with **no** arguments.
`IBody2.GetFaces` then returns a bound method that looks like a one-element
result — which is why an extruded box reported exactly one face. Worse,
`ModelDocExtension.AddDimension` invoked that way **crashes SOLIDWORKS outright**.
`sw_core.flag_methods()` calls `_FlagAsMethod` on every member the server invokes
with arguments, and `sw_core.value()` invokes zero-argument callables rather than
returning the method object.

**Modal dialogs deadlock the server.** `AddDimension2` pops the "Modify" box
whenever *Input dimension value* is enabled — which is the default. A modal
dialog blocks the COM call that opened it, and with it the whole server, until
somebody clicks. Dimension tools turn the preference off and restore it after.
If a tool ever hangs, look for a dialog behind the SOLIDWORKS window.

**Return values lie.** `SketchTrim` returns `False` on success. The `Create*`
sketch APIs return arrays whose truthiness is unreliable. `SketchAddConstraints`
takes a magic string and *silently ignores* one it does not recognise. So the
drawing tools judge success by the sketch's segment count, `sketch_trim` by
whether the sketch actually changed, and `add_relation` uses the typed
`ISketchRelationManager.AddRelation` and verifies via the relation count — and on
failure reports which relations that selection *would* accept.

**Most failures are silent.** SOLIDWORKS returns null far more often than it
raises. Every feature tool therefore rebuilds and reports
`ModelDocExtension.GetWhatsWrong`, and `check_errors` exposes it directly.

**`sketch_trim` needs its target selected.** Pass the segment in `selection`;
the point alone only tells SOLIDWORKS which piece to discard.

**Multi-reference features want distinct marks.** `InsertRefPlane` reads its
first, second, and third reference from *marks 0, 1, 2* — not from selection
order. Selecting all of them with mark 0 silently produces nothing, which is why
`create_plane` selects each reference with its own mark and documents that
references are consumed in written order.

**Two unit traps.** `IPartDoc.GetPartBox(False)` returns *document* units, not
metres, so it reads 1000x large if you treat it like the rest of the API; pass
`True`. And `swSketchLINE` is `0`, so folding a segment type through `or` turns
every line into "unknown".

## Known limitations

- One SOLIDWORKS session, one COM apartment: tool calls are serialised. Two
  clients driving the same session at once will deadlock it.
- Snapshot indices from `list_faces` / `list_edges` are invalidated by any
  geometry change. Re-list; do not cache.
- A modal dialog opened by SOLIDWORKS for any *other* reason will still block
  the server until dismissed.
- `rib` is picky about its profile. `InsertRib` returns void and simply builds
  nothing unless the open profile reaches material at both ends *and* the
  extrusion direction can get there. The tool defaults to `parallel_to_sketch`
  (right for a cross-section profile) and retries the other direction
  automatically, reporting which one worked. A profile that overlaps an existing
  rib still fails, and the tool says so rather than pretending.
- Only the **first** `insert_projected_view` off a given parent succeeds;
  subsequent projections from the same parent return nothing, through
  re-activation and rebuild alike. Use `insert_standard_views` for a full
  orthographic set.
- Detail-view scale is **absolute** (model to paper), not relative to the parent.
  On a 1:4 sheet, `2:1` makes the detail eight times the parent view and it
  overflows the sheet. Pick a scale near the sheet scale.
- Standard-plane and model-view display names are localized. Use `front`,
  `top`, and `right` for reference planes, and use names returned by
  `list_drawing_views` for drawing views; do not hard-code English UI labels.
- `through_all_both` is translated internally to a two-direction feature with
  `through_all` in both directions. Passing `swEndCondThroughAllBoth` as a
  single-ended feature silently cuts only one side in SOLIDWORKS 2026.
- `circular_pattern` cannot pattern a feature that built a *separate* body
  (`merge: false`) — SOLIDWORKS rejects the feature scope. Merge the body, or
  pattern the body instead.
- `save_document` cannot overwrite a file SOLIDWORKS currently has open, even
  with `overwrite: true`. The error says so when that is the cause.

## Testing

The useful tests are live SOLIDWORKS tests: the COM behaviour that matters here
cannot be mocked faithfully. With SOLIDWORKS already running, run:

```powershell
.\.venv\Scripts\python.exe tests\live_p0_regression.py
```

It verifies the P0 geometry/constraint regressions, including the full-volume
`through_all_both` cut.

Every tool has been exercised against SOLIDWORKS 2026 SP3.2 on a Simplified
Chinese install. Where a result could be checked numerically it was: the revolved
ring, swept rod and lofted cone match their closed-form volumes; a 5-degree
`draft` removes exactly the expected wedge; `rib` produces exactly the triangle
under its profile; `through_all_both` removes the full cylinder rather than half
of it. The limitations above are what survived that pass.

新增 28 个工具使用 `tests/live_expansion_regression.py` 验证。该脚本只修改新建测试文档，
结束时关闭它们并恢复原活动文档；装配体引用的测试零件保存在输出根目录下
`.expansion-tests/<uuid>/`。验证包括缩放体积、旋转包围盒、布尔运算体积、
曲面面积、螺旋线高度、配置、属性、方程以及组件状态的读取结果。

```powershell
python tests/live_expansion_regression.py
```

The original 92 tools were also exercised on SOLIDWORKS 2016 SP3 with a German
install. The 28 expansion tools have been checked on SOLIDWORKS 2026 SP3.2;
their behaviour on older releases has not yet been verified. Where a release lacks the newest
numbered method (`FeatureCut4`, `FeatureLinearPattern5`, `CreateDetailViewAt4`,
...) the tool falls back to the earlier variant with the arguments it takes; on
newer releases the newest name is always tried first, so nothing changes there.

## Layout

| Module | Contents |
| --- | --- |
| `sw_core.py` | COM attachment, units, feature tree, topology enumeration, selection engine, tool registry |
| `sw_file.py` | Session status, documents, saving, exporting, appearance, material |
| `sw_refgeom.py` | Reference planes and axes |
| `sw_sketch.py` | Sketches, geometry, editing, relations, dimensions |
| `sw_feature.py` | Solid features |
| `sw_inspect.py` | Topology listings, measurement, mass properties, screenshots |
| `sw_assembly.py` | Components and mates |
| `sw_drawing.py` | Sheets, views, model items, dimensions, center marks, notes |
| `sw_demo.py` | Basketball demo, opt-in via `SW_MCP_DEMO_TOOLS` |
| `sw_manage.py` | 文档切换、配置、自定义属性、方程、材质库搜索 |
| `sw_multibody.py` | 多实体操作、曲面、螺旋线和曲面面积查询 |
| `tests/live_expansion_regression.py` | 新增工具的实机回归与测试文档清理 |
| `tests/live_p0_regression.py` | Live regression checks for the confirmed P0 part/sketch defects |
| `tools/tlb_probe.py` | Reads signatures and enums straight off your installed type library |
| `server.py` | Registry assembly and stdio dispatch |

`tools/tlb_probe.py` is worth knowing about before you add a tool. The published
API reference disagrees with the shipped type library often enough to matter —
`FeatureFillet3` takes 14 arguments here, not the documented 7 — so check first:

```bash
python tools/tlb_probe.py methods '^IFeatureManager$' '^FeatureFillet3$'
python tools/tlb_probe.py enums '^swMateType_e$'
```

Adding a tool means writing one decorated function; the registry does the rest.

```python
@tool("my_tool", "What it does. Lengths are millimetres.",
      {"depth_mm": {"type": "number"}}, ["depth_mm"])
def my_tool(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_part()
    ...
    return result(True, "Did the thing.")
```

---

## License

Apache License 2.0 — see [LICENSE](LICENSE). Copyright 2026 JIALE LIU.

Use it, change it, ship it in a commercial product; that is all allowed. What
the license does require, if you redistribute this or anything derived from it,
is that you keep the copyright notices, pass on a copy of the [NOTICE](NOTICE)
file, and state which files you changed.
