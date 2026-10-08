# Copyright 2026 JIALE LIU
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Drawing documents: sheets, views, and annotation.

Two different things get called "auto-dimension" in SOLIDWORKS, and they are
not interchangeable, so both are exposed:

* ``insert_model_annotations`` imports the dimensions the model already carries
  (Insert > Model Items).  Use this when the part was modelled with the
  dimensions you want to see on the drawing.
* ``auto_dimension_view`` runs the DimXpert scheme (baseline / ordinate /
  chain) over a view's geometry and invents dimensions.  Use this when the
  model has nothing worth importing.

Sheet coordinates are millimetres from the lower-left corner of the sheet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .sw_core import (
    active_document,
    as_list,
    byref_long,
    call_versioned,
    clear_selection,
    document_info,
    document_type,
    extension,
    feature_property,
    flag_methods,
    logger,
    rebuild,
    result,
    running_app,
    safe,
    sketch_manager,
    to_m,
    to_mm,
    tool,
    value,
    nothing,
)
from .sw_file import new_document


# swDwgPaperSizes_e
PAPER_SIZES = {
    "A": 0, "A_portrait": 1, "B": 2, "C": 3, "D": 4, "E": 5,
    "A4": 6, "A4_portrait": 7, "A3": 8, "A2": 9, "A1": 10, "A0": 11,
    "custom": 12,
}

# swDrawingViewTypes_e
VIEW_TYPES = {
    1: "sheet", 2: "section", 3: "detail", 4: "projected", 5: "auxiliary",
    6: "standard", 7: "named", 8: "relative", 9: "detached", 10: "alternate_position",
}

# swViewDisplayMode_e
DISPLAY_MODES = {
    "wireframe": 1, "hidden_lines_removed": 2, "hidden_lines_grey": 3,
    "shaded": 4, "shaded_with_edges": 5,
}

# swInsertAnnotation_e, the flags worth exposing on a mechanical drawing.
ANNOTATION_TYPES = {
    # swInsertDimensions on its own imports nothing: SOLIDWORKS only hands over
    # dimensions when one of the marked/not-marked flags is present. "dimensions"
    # therefore means all three, which is what asking for dimensions implies.
    "dimensions": 8 | 32768 | 524288,
    "marked_for_drawing": 32768,
    "not_marked_for_drawing": 524288,
    "hole_callouts": 1048576,
    "hole_wizard_profile": 65536,
    "hole_wizard_location": 131072,
    "notes": 64,
    "geometric_tolerances": 32,
    "datums": 2,
    "surface_finish": 128,
    "cosmetic_threads": 1,
    "axes": 512,
    "toleranced_dimensions": 16777216,
}

# swAutodimScheme_e / placement enums
AUTODIM_SCHEMES = {"baseline": 1, "ordinate": 2, "chain": 3, "centerline": 4}
AUTODIM_ENTITIES = {"all": 1, "selected": 2, "preselected": 0}
HORIZONTAL_PLACEMENT = {"below": -1, "above": 1}
VERTICAL_PLACEMENT = {"left": -1, "right": 1}

# swCenterMarkStyle_e
CENTER_MARK_STYLES = {"single": 2, "linear": 3, "circular": 4}

_DRAWING_METHODS = (
    "NewSheet3", "SetupSheet5", "SetupSheet4", "ActivateSheet", "ActivateView",
    "CreateDrawViewFromModelView3", "Create3rdAngleViews2", "Create1stAngleViews2",
    "CreateUnfoldedViewAt3", "CreateSectionViewAt5", "CreateDetailViewAt4", "CreateDetailViewAt3",
    "InsertModelAnnotations3", "AutoDimension", "InsertCenterMark3", "InsertCenterLine2",
    "AutoInsertCenterMarks2", "SelectEntity",
    "GetSheetNames", "GetFirstView", "GetCurrentSheet",
)


def require_drawing() -> tuple[Any, Any]:
    app, doc = active_document()
    if document_type(doc) != 3:
        raise RuntimeError("This tool requires an active drawing document.")
    return app, flag_methods(doc, *_DRAWING_METHODS)


def _ensure_model_open(app: Any, model: str) -> None:
    """Load the model if it is not already open.

    CreateDrawViewFromModelView3 needs it in memory, and the localized standard
    view names can only be read off an open document.
    """
    for doc in as_list(value(app, "GetDocuments")):
        try:
            if str(value(doc, "GetPathName") or "").lower() == model.lower():
                return
        except Exception:
            continue
    doc_type = {".sldprt": 1, ".sldasm": 2}.get(Path(model).suffix.lower(), 1)
    errors, warnings = byref_long(0), byref_long(0)
    # Opening a document activates it, which would leave the drawing tools
    # looking at a part, so put the drawing back in front afterwards.
    previous = ""
    try:
        current = app.ActiveDoc
        previous = str(value(current, "GetTitle")) if current is not None else ""
    except Exception:
        previous = ""
    try:
        # swOpenDocOptions_Silent = 1
        app.OpenDoc6(model, doc_type, 1, "", errors, warnings)
    except Exception:
        logger.info("Could not silently open %s for the drawing view.", model)
    if previous:
        try:
            flag_methods(app, "ActivateDoc3").ActivateDoc3(previous, True, 0, byref_long(0))
        except Exception:
            logger.info("Could not re-activate %s after loading the model.", previous)


def _open_model_path(app: Any, model_path: str | None) -> str:
    """Resolve which model a view should be built from.

    Defaults to the only open part or assembly, which is what the caller means
    almost every time.
    """
    if model_path:
        path = Path(str(model_path)).expanduser().resolve()
        if not path.is_file():
            raise RuntimeError(f"No such model file: {path}")
        _ensure_model_open(app, str(path))
        return str(path)

    candidates = []
    for doc in as_list(value(app, "GetDocuments")):
        if int(value(doc, "GetType")) in (1, 2):
            path = str(value(doc, "GetPathName") or "")
            if path:
                candidates.append(path)
    if not candidates:
        raise RuntimeError(
            "No saved part or assembly is open to draw. Save the model first, "
            "or pass model_path explicitly."
        )
    if len(candidates) > 1:
        raise RuntimeError(f"Several models are open; pass model_path. Candidates: {candidates}")
    return candidates[0]


def _iter_views(doc: Any) -> list[Any]:
    """Every view on every sheet, in SOLIDWORKS' own order.

    GetFirstView returns the sheet itself, and its 'next' chain walks the views
    on that sheet before moving to the next sheet.
    """
    views: list[Any] = []
    try:
        node = value(doc, "GetFirstView")
    except Exception:
        return views
    while node is not None:
        views.append(node)
        node = safe(node, "GetNextView")
    return views


def _view_entry(view: Any, index: int) -> dict[str, Any]:
    entry: dict[str, Any] = {"index": index, "name": str(safe(view, "GetName2", "") or "")}
    kind = safe(view, "Type")
    if kind is not None:
        entry["type"] = VIEW_TYPES.get(int(kind), f"type_{int(kind)}")
    position = safe(view, "Position")
    if position is not None and len(position) >= 2:
        entry["position_mm"] = [round(to_mm(position[0]), 4), round(to_mm(position[1]), 4)]
    scale = safe(view, "ScaleDecimal")
    if scale:
        entry["scale"] = round(float(scale), 6)
    for member in ("GetDimensionCount4", "GetDimensionCount3", "GetDimensionCount2"):
        count = safe(view, member)
        if count is not None:
            entry["dimensions"] = int(count)
            break
    referenced = str(safe(view, "GetReferencedModelName", "") or "")
    if referenced:
        entry["model"] = referenced
    return entry


# --------------------------------------------------------------------------
# Documents and sheets
# --------------------------------------------------------------------------


@tool(
    "create_drawing",
    "Create a new drawing document from the configured default template, optionally sized and "
    "scaled. Then use insert_standard_views or insert_model_view to place views.",
    {
        "paper_size": {"type": "string", "enum": sorted(PAPER_SIZES), "description": "Override the template's paper size."},
        "scale_numerator": {"type": "number", "default": 1},
        "scale_denominator": {"type": "number", "default": 1},
        "first_angle": {"type": "boolean", "description": "True for first-angle (ISO/GB) projection, false for third-angle."},
    },
)
def create_drawing(args: dict[str, Any]) -> dict[str, Any]:
    created = new_document("drawing")
    if not created.get("ok"):
        return created
    _, doc = require_drawing()

    if args.get("paper_size") or args.get("first_angle") is not None or args.get("scale_numerator"):
        sheet = value(doc, "GetCurrentSheet")
        name = str(safe(sheet, "GetName", "") or "")
        size = PAPER_SIZES.get(str(args.get("paper_size", "")), int(safe(sheet, "GetSize", 8) or 8))
        try:
            doc.SetupSheet5(
                name, size, 12 if size == 12 else size,
                float(args.get("scale_numerator", 1)), float(args.get("scale_denominator", 1)),
                bool(args.get("first_angle", True)), "", 0.0, 0.0, "", True,
            )
        except Exception as exc:
            logger.info("Sheet setup was declined: %s", exc)
    return result(True, "Created a new drawing.", document=document_info(doc), sheets=_sheet_names(doc))


def _sheet_names(doc: Any) -> list[str]:
    return [str(n) for n in as_list(safe(doc, "GetSheetNames"))]


@tool("list_sheets", "Read-only: list the sheets of the active drawing, with the active one marked.", {})
def list_sheets(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_drawing()
    current = value(doc, "GetCurrentSheet")
    active = str(safe(current, "GetName", "") or "")
    sheets = []
    for name in _sheet_names(doc):
        sheets.append({"name": name, "active": name == active})
    properties = safe(current, "GetProperties2")
    payload: dict[str, Any] = {"sheets": sheets, "active_sheet": active}
    if properties is not None and len(properties) >= 6:
        # [paper size, template, scale1, scale2, first angle, width, height]
        payload["scale"] = [float(properties[2]), float(properties[3])]
        payload["first_angle"] = bool(properties[4])
        if len(properties) >= 7:
            payload["sheet_size_mm"] = [round(to_mm(properties[5]), 2), round(to_mm(properties[6]), 2)]
    return result(True, f"Read {len(sheets)} sheets.", **payload)


@tool(
    "add_sheet",
    "Add a sheet to the active drawing.",
    {
        "name": {"type": "string"},
        "paper_size": {"type": "string", "enum": sorted(PAPER_SIZES), "default": "A3"},
        "scale_numerator": {"type": "number", "default": 1},
        "scale_denominator": {"type": "number", "default": 1},
        "first_angle": {"type": "boolean", "default": True},
    },
)
def add_sheet(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_drawing()
    name = str(args.get("name") or f"Sheet{len(_sheet_names(doc)) + 1}")
    size = PAPER_SIZES[str(args.get("paper_size", "A3"))]
    ok = bool(
        doc.NewSheet3(
            name, size, size,
            float(args.get("scale_numerator", 1)), float(args.get("scale_denominator", 1)),
            bool(args.get("first_angle", True)), "", 0.0, 0.0, "",
        )
    )
    return result(ok, f"Added sheet '{name}'." if ok else "SOLIDWORKS did not add the sheet.", sheets=_sheet_names(doc))


@tool(
    "activate_sheet",
    "Make a sheet the active one.",
    {"name": {"type": "string"}},
    ["name"],
)
def activate_sheet(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_drawing()
    name = str(args["name"])
    ok = bool(doc.ActivateSheet(name))
    return result(ok, f"Activated sheet '{name}'." if ok else f"No sheet named '{name}'.", sheets=_sheet_names(doc))


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------


@tool(
    "insert_standard_views",
    "Place the standard set of views (front, top, side, isometric) for a model in one call, "
    "laid out by projection angle. first_angle follows ISO/GB convention.",
    {
        "model_path": {"type": "string", "description": "Defaults to the only open saved part or assembly."},
        "first_angle": {"type": "boolean", "default": True},
    },
)
def insert_standard_views(args: dict[str, Any]) -> dict[str, Any]:
    app, doc = require_drawing()
    model = _open_model_path(app, args.get("model_path"))
    first_angle = bool(args.get("first_angle", True))
    ok = bool(doc.Create1stAngleViews2(model) if first_angle else doc.Create3rdAngleViews2(model))
    rebuild(doc)
    views = [_view_entry(v, i) for i, v in enumerate(_iter_views(doc))]
    return result(
        ok,
        f"Placed the standard views for {Path(model).name}." if ok
        else "SOLIDWORKS did not place the standard views.",
        model=model,
        views=views,
    )


@tool(
    "insert_model_view",
    "Place one named orientation of a model at a point on the sheet. Position is millimetres from "
    "the sheet's lower-left corner.",
    {
        "view": {
            "type": "string",
            "enum": ["front", "back", "left", "right", "top", "bottom",
                     "isometric", "trimetric", "dimetric", "normal_to", "current"],
            "default": "front",
        },
        "x_mm": {"type": "number"},
        "y_mm": {"type": "number"},
        "model_path": {"type": "string", "description": "Defaults to the only open saved part or assembly."},
        "scale": {"type": "number", "description": "Override the sheet scale for this view, e.g. 0.25 for 1:4."},
        "display_mode": {"type": "string", "enum": sorted(DISPLAY_MODES), "description": "Override the view's display style."},
    },
    ["x_mm", "y_mm"],
)
def insert_model_view(args: dict[str, Any]) -> dict[str, Any]:
    app, doc = require_drawing()
    model = _open_model_path(app, args.get("model_path"))
    wanted = str(args.get("view", "front"))
    resolved = _model_view_name(app, model, wanted)
    view = doc.CreateDrawViewFromModelView3(
        model, resolved, to_m(args["x_mm"]), to_m(args["y_mm"]), 0.0
    )
    if view is None:
        return result(
            False,
            f"SOLIDWORKS did not place the {wanted} view (resolved to '{resolved}'). "
            f"Check that the model is open and the point is on the sheet.",
            model=model,
            resolved_view_name=resolved,
        )
    _apply_view_options(view, args)
    rebuild(doc)
    return result(True, f"Placed the {wanted} view.", view=_view_entry(view, -1), model=model)


def _model_view_name(app: Any, model: str, wanted: str) -> str:
    """Resolve a standard orientation to the name this install actually uses.

    A localized SOLIDWORKS translates them — "*Isometric" is "*等轴测" on a
    Chinese install — and CreateDrawViewFromModelView3 silently returns nothing
    for a name it does not recognise.  GetModelViewNames reports them in a
    fixed order, so ask the model rather than hard-coding English.
    """
    english = {
        "normal_to": "*Normal To", "front": "*Front", "back": "*Back",
        "left": "*Left", "right": "*Right", "top": "*Top", "bottom": "*Bottom",
        "isometric": "*Isometric", "trimetric": "*Trimetric", "dimetric": "*Dimetric",
        "current": "*Current",
    }
    order = ["normal_to", "front", "back", "left", "right", "top", "bottom",
             "isometric", "trimetric", "dimetric"]
    if wanted not in order:
        return english.get(wanted, wanted)

    for doc in as_list(value(app, "GetDocuments")):
        try:
            if str(value(doc, "GetPathName") or "").lower() != model.lower():
                continue
            names = [str(n) for n in as_list(value(doc, "GetModelViewNames"))]
        except Exception:
            continue
        index = order.index(wanted)
        if len(names) > index:
            return names[index]
    return english[wanted]


def _apply_view_options(view: Any, args: dict[str, Any]) -> None:
    if args.get("scale"):
        try:
            view.ScaleDecimal = float(args["scale"])
        except Exception:
            logger.info("Could not override the view scale.")
    mode = args.get("display_mode")
    if mode:
        try:
            flag_methods(view, "SetDisplayMode3").SetDisplayMode3(
                False, DISPLAY_MODES[str(mode)], False, False
            )
        except Exception:
            logger.info("Could not set the view display mode.")


@tool(
    "insert_projected_view",
    "Project a new view off an existing one. Name the parent and a direction and the placement is "
    "worked out for you; pass x_mm/y_mm instead to place it exactly. Millimetres.",
    {
        "parent_view": {"type": "string", "description": "View to project from. Defaults to the last view placed."},
        "direction": {"type": "string", "enum": ["right", "left", "up", "down"], "default": "right"},
        "offset_mm": {"type": "number", "default": 80, "description": "How far from the parent to place it."},
        "x_mm": {"type": "number", "description": "Exact placement, overriding direction/offset."},
        "y_mm": {"type": "number"},
        "not_aligned": {"type": "boolean", "default": False, "description": "Break alignment with the parent view."},
    },
)
def insert_projected_view(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_drawing()
    parent_name = args.get("parent_view")
    parent = None
    for view in _iter_views(doc):
        if int(safe(view, "Type", 1) or 1) == 1:
            continue
        if parent_name:
            if str(safe(view, "GetName2", "")) == str(parent_name):
                parent = view
                break
        else:
            parent = view
    if parent is None:
        return result(False, "No parent drawing view to project from. Place a view first.")

    parent_name = str(safe(parent, "GetName2", ""))
    position = safe(parent, "Position") or (0.0, 0.0)
    if args.get("x_mm") is not None and args.get("y_mm") is not None:
        x, y = to_m(args["x_mm"]), to_m(args["y_mm"])
    else:
        offset = to_m(args.get("offset_mm", 80))
        dx, dy = {
            "right": (offset, 0.0), "left": (-offset, 0.0),
            "up": (0.0, offset), "down": (0.0, -offset),
        }[str(args.get("direction", "right"))]
        x, y = float(position[0]) + dx, float(position[1]) + dy

    # The parent has to be the active view, and creating a view activates the
    # new one, so re-activate before every projection.
    doc.ActivateView(parent_name)
    view = doc.CreateUnfoldedViewAt3(x, y, 0.0, bool(args.get("not_aligned", False)))
    if view is None:
        rebuild(doc)
        doc.ActivateView(parent_name)
        view = doc.CreateUnfoldedViewAt3(x, y, 0.0, bool(args.get("not_aligned", False)))
    if view is None:
        return result(
            False,
            f"SOLIDWORKS did not project a view from '{parent_name}'. In practice only the first "
            f"projection off a given parent succeeds through this API; for a full set of "
            f"orthographic views use insert_standard_views, which places them in one call.",
            parent=parent_name,
            point_mm=[round(to_mm(x), 2), round(to_mm(y), 2)],
        )
    rebuild(doc)
    return result(True, f"Projected a view from '{parent_name}'.", parent=parent_name, view=_view_entry(view, -1))


def _draw_in_view(doc: Any, parent_view: str | None, draw: Any) -> tuple[str, Any]:
    """Activate a view, draw one entity inside it, and leave it selected.

    Sketch geometry in a drawing belongs to whichever view is active, so the
    activate/draw/select dance has to happen together or the entity lands on
    the sheet instead of in the view.
    """
    parent = None
    for view in _iter_views(doc):
        if int(safe(view, "Type", 1) or 1) == 1:
            continue
        if parent_view:
            if str(safe(view, "GetName2", "")) == str(parent_view):
                parent = view
                break
        else:
            parent = view
    if parent is None:
        raise RuntimeError("No parent drawing view. Place a view before sectioning or detailing it.")

    name = str(safe(parent, "GetName2", ""))
    if not bool(doc.ActivateView(name)):
        raise RuntimeError(f"Could not activate view '{name}'.")
    entity = draw(sketch_manager(doc))
    if entity is None:
        raise RuntimeError("SOLIDWORKS did not draw the reference geometry in the view.")
    clear_selection(doc)
    from .sw_core import select_object

    if not select_object(doc, entity, mark=0, append=False):
        raise RuntimeError("Could not select the reference geometry just drawn.")
    return name, entity


@tool(
    "insert_section_view",
    "Create a section view: give the cutting line's two endpoints and where the section should "
    "sit, and the line is drawn in the parent view for you. Coordinates are millimetres on the "
    "sheet, so read the parent's position from list_drawing_views first.",
    {
        "x1_mm": {"type": "number", "description": "Cutting line start, on the sheet."},
        "y1_mm": {"type": "number"},
        "x2_mm": {"type": "number", "description": "Cutting line end, on the sheet."},
        "y2_mm": {"type": "number"},
        "place_x_mm": {"type": "number", "description": "Where the section view goes."},
        "place_y_mm": {"type": "number"},
        "parent_view": {"type": "string", "description": "View to cut. Defaults to the last one placed."},
        "label": {"type": "string", "default": "A"},
        "depth_mm": {"type": "number", "default": 0, "description": "Partial-section depth; 0 cuts all the way."},
        "exclude_fasteners": {"type": "boolean", "default": False},
        "use_selection": {"type": "boolean", "default": False, "description": "Use the already-selected line instead of drawing one."},
    },
    ["place_x_mm", "place_y_mm"],
)
def insert_section_view(args: dict[str, Any]) -> dict[str, Any]:
    from .sw_core import empty_variant

    _, doc = require_drawing()
    parent = args.get("parent_view")
    if not bool(args.get("use_selection", False)):
        for key in ("x1_mm", "y1_mm", "x2_mm", "y2_mm"):
            if args.get(key) is None:
                return result(False, f"Pass the cutting line endpoints, or set use_selection. Missing {key}.")
        parent, _ = _draw_in_view(
            doc, parent,
            lambda sm: sm.CreateLine(
                to_m(args["x1_mm"]), to_m(args["y1_mm"]), 0.0,
                to_m(args["x2_mm"]), to_m(args["y2_mm"]), 0.0,
            ),
        )

    # swCreateSectionViewAtOptions_e: 1 = section, 2 = exclude fasteners.
    options = 1 | (2 if bool(args.get("exclude_fasteners", False)) else 0)
    view = doc.CreateSectionViewAt5(
        to_m(args["place_x_mm"]), to_m(args["place_y_mm"]), 0.0,
        str(args.get("label", "A")), options, empty_variant(),
        to_m(args.get("depth_mm", 0)),
    )
    if view is None:
        return result(
            False,
            "SOLIDWORKS did not create the section view. Check that the cutting line actually "
            "crosses the parent view's geometry.",
            parent=parent,
        )
    rebuild(doc)
    return result(
        True, f"Created section view {args.get('label', 'A')}.",
        parent=parent, view=_view_entry(view, -1),
    )


@tool(
    "insert_detail_view",
    "Create a detail view: give the detail circle's centre and radius and where the enlarged view "
    "should sit, and the circle is drawn in the parent view for you. Coordinates are millimetres "
    "on the sheet, so read the parent's position from list_drawing_views first. The scale is "
    "absolute (model to paper), NOT relative to the parent: on a 1:4 sheet, asking for 2:1 makes "
    "the detail eight times the parent view and it will overflow the sheet. Pick a scale near the "
    "sheet scale — 1:2 on a 1:4 sheet gives a sensible 2x enlargement.",
    {
        "center_x_mm": {"type": "number", "description": "Detail circle centre, on the sheet."},
        "center_y_mm": {"type": "number"},
        "radius_mm": {"type": "number", "description": "Detail circle radius, on the sheet."},
        "place_x_mm": {"type": "number", "description": "Where the enlarged view goes."},
        "place_y_mm": {"type": "number"},
        "parent_view": {"type": "string", "description": "View to detail. Defaults to the last one placed."},
        "label": {"type": "string", "default": "I"},
        "scale_numerator": {"type": "number", "default": 2},
        "scale_denominator": {"type": "number", "default": 1},
        "full_outline": {"type": "boolean", "default": False},
        "jagged_outline": {"type": "boolean", "default": False},
        "use_selection": {"type": "boolean", "default": False, "description": "Use the already-selected circle instead of drawing one."},
    },
    ["place_x_mm", "place_y_mm"],
)
def insert_detail_view(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_drawing()
    parent = args.get("parent_view")
    if not bool(args.get("use_selection", False)):
        for key in ("center_x_mm", "center_y_mm", "radius_mm"):
            if args.get(key) is None:
                return result(False, f"Pass the detail circle, or set use_selection. Missing {key}.")
        parent, _ = _draw_in_view(
            doc, parent,
            lambda sm: sm.CreateCircleByRadius(
                to_m(args["center_x_mm"]), to_m(args["center_y_mm"]), 0.0, to_m(args["radius_mm"])
            ),
        )

    detail_args = (
        to_m(args["place_x_mm"]), to_m(args["place_y_mm"]), 0.0,
        1,  # swDetailCircleStyle_e: 1 = circle profile
        float(args.get("scale_numerator", 2)), float(args.get("scale_denominator", 1)),
        str(args.get("label", "I")),
        2,  # swDetViewStyle_e: 2 = label with a leader
        bool(args.get("full_outline", False)),
        bool(args.get("jagged_outline", False)),
        False, 0,
    )
    # CreateDetailViewAt3 (2016) stops after FullOutline: no jagged outline,
    # no fixed-size circle, no border style.
    view = call_versioned(
        doc,
        ("CreateDetailViewAt4", detail_args),
        ("CreateDetailViewAt3", detail_args[:9]),
    )
    if view is None:
        return result(
            False,
            "SOLIDWORKS did not create the detail view. Check that the detail circle lies over the "
            "parent view's geometry.",
            parent=parent,
        )
    rebuild(doc)
    return result(
        True, f"Created detail view {args.get('label', 'I')}.",
        parent=parent, view=_view_entry(view, -1),
    )


@tool(
    "list_drawing_views",
    "Read-only: list the views on the active drawing with their names, types, positions, scales, "
    "and how many dimensions each carries. The first entry is the sheet itself.",
    {},
)
def list_drawing_views(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_drawing()
    views = [_view_entry(v, i) for i, v in enumerate(_iter_views(doc))]
    return result(True, f"Read {len(views)} drawing views.", views=views)


@tool(
    "activate_drawing_view",
    "Make a drawing view the active one, which is what section, detail, projected views and "
    "sketching inside a view all operate on.",
    {"name": {"type": "string", "description": "View name from list_drawing_views."}},
    ["name"],
)
def activate_drawing_view(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_drawing()
    name = str(args["name"])
    ok = bool(doc.ActivateView(name))
    return result(ok, f"Activated view '{name}'." if ok else f"No view named '{name}'.")


@tool(
    "create_drawing_sketch",
    "Open a 2D sketch in a drawing view. Use draw_line or draw_circle next to define a section or "
    "detail boundary; the sketch remains open and its geometry selected for insert_section_view or "
    "insert_detail_view.",
    {"view_name": {"type": "string", "description": "Parent view to sketch in. Defaults to the active drawing view."}},
)
def create_drawing_sketch(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_drawing()
    view = _activate(doc, args.get("view_name"))
    name = str(safe(view, "GetName2", "") or "")
    if not name or not bool(doc.ActivateView(name)):
        return result(False, "SOLIDWORKS could not activate a drawing view for sketching.")
    clear_selection(doc)
    sketch_manager(doc).InsertSketch(True)
    if doc.SketchManager.ActiveSketch is None:
        return result(False, f"SOLIDWORKS did not open a sketch in drawing view '{name}'.")
    return result(True, f"Opened a sketch in drawing view '{name}'.", view_name=name)


@tool(
    "set_drawing_view",
    "Move, rescale, or restyle an existing drawing view. Position is millimetres from the sheet's "
    "lower-left corner.",
    {
        "name": {"type": "string"},
        "x_mm": {"type": "number"},
        "y_mm": {"type": "number"},
        "scale": {"type": "number", "description": "Decimal scale, e.g. 0.25 for 1:4."},
        "display_mode": {"type": "string", "enum": sorted(DISPLAY_MODES)},
        "tangent_edges": {
            "type": "string",
            "enum": ["visible", "hidden", "phantom"],
            "description": "How tangent edges are drawn.",
        },
    },
    ["name"],
)
def set_drawing_view(args: dict[str, Any]) -> dict[str, Any]:
    from .sw_core import double_array

    _, doc = require_drawing()
    name = str(args["name"])
    target = next((v for v in _iter_views(doc) if str(safe(v, "GetName2", "")) == name), None)
    if target is None:
        return result(False, f"No view named '{name}'. Call list_drawing_views for the names.")

    if args.get("x_mm") is not None and args.get("y_mm") is not None:
        target.Position = double_array([to_m(args["x_mm"]), to_m(args["y_mm"])])
    _apply_view_options(target, args)
    if args.get("tangent_edges"):
        mode = {"visible": 1, "hidden": 2, "phantom": 3}[str(args["tangent_edges"])]
        try:
            target.TangentEdgeDisplay = mode
        except Exception:
            logger.info("Could not set tangent edge display.")
    rebuild(doc)
    return result(True, f"Updated view '{name}'.", view=_view_entry(target, -1))


# --------------------------------------------------------------------------
# Annotation
# --------------------------------------------------------------------------


@tool(
    "insert_model_annotations",
    "Import the model's own dimensions and annotations onto the drawing (Insert > Model Items). "
    "This is the right tool when the part was modelled with the dimensions you want shown. "
    "For dimensions invented from the view geometry instead, use auto_dimension_view.",
    {
        "types": {
            "type": "array",
            "items": {"type": "string", "enum": sorted(ANNOTATION_TYPES)},
            "default": ["marked_for_drawing", "not_marked_for_drawing"],
            "description": "Which annotation kinds to import.",
        },
        "view_name": {
            "type": "string",
            "description": "Target drawing view when all_views is false. Defaults to the first model view.",
        },
        "all_views": {"type": "boolean", "default": True, "description": "Import into every view, not just the active one."},
        "duplicate_dimensions": {"type": "boolean", "default": False, "description": "Allow the same dimension in more than one view."},
        "hidden_feature_dimensions": {"type": "boolean", "default": False},
        "source": {
            "type": "string",
            "enum": ["entire_model", "selected_feature", "selected_component", "assembly_only"],
            "default": "entire_model",
        },
    },
)
def insert_model_annotations(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_drawing()
    wanted = [str(t) for t in (args.get("types") or ["marked_for_drawing", "not_marked_for_drawing"])]
    mask = 0
    for name in wanted:
        mask |= ANNOTATION_TYPES[name]
    source = {
        "entire_model": 0, "selected_feature": 1,
        "selected_component": 2, "assembly_only": 3,
    }[str(args.get("source", "entire_model"))]

    before = sum(int(v.get("dimensions", 0)) for v in
                 (_view_entry(x, i) for i, x in enumerate(_iter_views(doc))))

    # InsertModelAnnotations3 works on the *selected* drawing view, not merely
    # the active one.  Activating a view alone was why a part containing seven
    # dimensions consistently imported zero annotations.  Select a concrete
    # model view by its localized name before invoking the API.
    view = _activate(doc, args.get("view_name"))
    view_name = str(safe(view, "GetName2", "") or "")
    if not view_name:
        return result(False, "Could not resolve a model drawing view for model-item import.")
    clear_selection(doc)
    selected = bool(
        extension(doc).SelectByID2(view_name, "DRAWINGVIEW", 0.0, 0.0, 0.0, False, 0, nothing(), 0)
    )
    if not selected:
        return result(False, f"SOLIDWORKS could not select drawing view '{view_name}'.")
    if not bool(doc.ActivateView(view_name)):
        return result(False, f"SOLIDWORKS could not activate drawing view '{view_name}'.")

    inserted = doc.InsertModelAnnotations3(
        source, mask,
        bool(args.get("all_views", True)),
        bool(args.get("duplicate_dimensions", False)),
        bool(args.get("hidden_feature_dimensions", False)),
        False,
    )
    rebuild(doc)
    clear_selection(doc)
    views = [_view_entry(v, i) for i, v in enumerate(_iter_views(doc))]
    after = sum(int(v.get("dimensions", 0)) for v in views)
    added = after - before
    # The return is an array of the annotations placed, but it comes back empty
    # in plenty of valid cases, so report the dimension-count delta instead.
    return result(
        added > 0 or bool(as_list(inserted)),
        f"Imported {added} dimensions." if added > 0
        else "No model items were imported. The model may have no dimensions marked for drawing; "
             "try types including not_marked_for_drawing, or use auto_dimension_view.",
        types=wanted,
        view_name=view_name,
        dimensions_added=added,
        views=views,
    )


@tool(
    "auto_dimension_view",
    "Run DimXpert over a drawing view and generate dimensions from its geometry. Use this when the "
    "model has no dimensions worth importing; to reuse the model's own dimensions call "
    "insert_model_annotations instead. The view must be active first.",
    {
        "view_name": {"type": "string", "description": "View to dimension. Defaults to the active view."},
        "entities": {"type": "string", "enum": sorted(AUTODIM_ENTITIES), "default": "all"},
        "horizontal_scheme": {"type": "string", "enum": sorted(AUTODIM_SCHEMES), "default": "baseline"},
        "horizontal_placement": {"type": "string", "enum": sorted(HORIZONTAL_PLACEMENT), "default": "below"},
        "vertical_scheme": {"type": "string", "enum": sorted(AUTODIM_SCHEMES), "default": "baseline"},
        "vertical_placement": {"type": "string", "enum": sorted(VERTICAL_PLACEMENT), "default": "left"},
    },
)
def auto_dimension_view(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_drawing()
    if args.get("view_name"):
        if not bool(doc.ActivateView(str(args["view_name"]))):
            return result(False, f"No view named '{args['view_name']}'.")

    before = [_view_entry(v, i) for i, v in enumerate(_iter_views(doc))]
    status = int(
        doc.AutoDimension(
            AUTODIM_ENTITIES[str(args.get("entities", "all"))],
            AUTODIM_SCHEMES[str(args.get("horizontal_scheme", "baseline"))],
            HORIZONTAL_PLACEMENT[str(args.get("horizontal_placement", "below"))],
            AUTODIM_SCHEMES[str(args.get("vertical_scheme", "baseline"))],
            VERTICAL_PLACEMENT[str(args.get("vertical_placement", "left"))],
        )
    )
    rebuild(doc)
    after = [_view_entry(v, i) for i, v in enumerate(_iter_views(doc))]
    added = sum(int(v.get("dimensions", 0)) for v in after) - sum(int(v.get("dimensions", 0)) for v in before)
    return result(
        added > 0,
        f"Auto-dimensioning added {added} dimensions."
        if added > 0 else f"Auto-dimensioning added nothing (status {status}).",
        status=status,
        dimensions_added=added,
        views=after,
    )


def _activate(doc: Any, view_name: str | None) -> Any:
    """Activate the named view (or use the active one) and return the view object."""
    if view_name and not bool(doc.ActivateView(str(view_name))):
        raise RuntimeError(f"No view named '{view_name}'.")
    # The API requires the object returned by ActiveDrawingView after activation
    # for operations such as AutoInsertCenterMarks2.  A view object retained
    # while walking the sheet is not always accepted as that active view.
    active = safe(doc, "ActiveDrawingView")
    if active is not None:
        return flag_methods(active, "GetVisibleEntities2", "GetVisibleEntities", "SelectEntity", "AutoInsertCenterMarks2")

    target = None
    for view in _iter_views(doc):
        if view_name:
            if str(safe(view, "GetName2", "")) == str(view_name):
                target = view
                break
        elif safe(view, "Type") is not None and int(safe(view, "Type", 1) or 1) != 1:
            target = view
    if target is None:
        raise RuntimeError("No drawing view is available. Place a view first.")
    return target


def _select_circular_edges(doc: Any, view: Any) -> int:
    """Select every circular edge visible in the view.

    InsertCenterMark3 annotates the current selection rather than the whole
    view, so a tool that means "mark every hole here" has to build that
    selection itself.
    """
    from .sw_core import nothing

    clear_selection(doc)
    count = 0
    entities: list[Any] = []
    flag_methods(view, "GetVisibleEntities2", "GetVisibleEntities", "SelectEntity")
    # swViewEntityType_Edge = 1. The component argument must be a typed null for
    # a part view, which a bare Python None does not marshal to.
    for member in ("GetVisibleEntities2", "GetVisibleEntities"):
        for component in (nothing(), None):
            try:
                entities = as_list(getattr(view, member)(component, 1))
            except Exception:
                continue
            if entities:
                break
        if entities:
            break
    if not entities:
        logger.info("Could not enumerate the view's visible edges.")
        return 0
    for entity in entities:
        try:
            curve = value(entity, "GetCurve")
            if curve is None or int(value(curve, "Identity")) != 3002:  # circle
                continue
        except Exception:
            continue
        # IView::SelectEntity is the drawing-aware selection path.  Calling
        # Edge::Select4 here selects the corresponding model edge (or silently
        # selects nothing), so InsertCenterMark3/InsertCenterLine2 see no
        # drawing geometry even though GetVisibleEntities2 found it.
        if view.SelectEntity(entity, True):
            count += 1
    return count


def _center_mark_count(view: Any) -> int | None:
    """Read legacy center-mark features when the drawing exposes them.

    Modern SOLIDWORKS creates annotation center marks, for which this count is
    often zero.  It is therefore informational only; a successful insertion
    result remains authoritative.
    """
    try:
        size = byref_long(0)
        flag_methods(view, "GetCenterMarkCount2")
        return int(view.GetCenterMarkCount2(size))
    except Exception:
        return None


@tool(
    "insert_center_marks",
    "Add center marks to the circular edges of a drawing view. By default every circle in the view "
    "gets one; pass selected_only if you have already made a selection.",
    {
        "view_name": {"type": "string", "description": "Defaults to the active view."},
        "style": {"type": "string", "enum": sorted(CENTER_MARK_STYLES), "default": "single"},
        "propagate": {"type": "boolean", "default": True},
        "slots": {"type": "boolean", "default": False},
        "selected_only": {"type": "boolean", "default": False, "description": "Annotate the existing selection instead of every circle."},
    },
)
def insert_center_marks(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_drawing()
    view = _activate(doc, args.get("view_name"))
    if not bool(args.get("selected_only", False)):
        # GetVisibleEntities2 does not enumerate part-view edges reliably in
        # late-bound Python.  The API's own view-scoped automatic command does,
        # and covers holes, fillets, and slots (the three supported classes).
        before = _center_mark_count(view)
        ok = bool(
            flag_methods(view, "AutoInsertCenterMarks2").AutoInsertCenterMarks2(
                1 | 2 | 4,  # holes, fillets, slots
                1 | 2 | 8,  # linear, circular, and base connection lines
                bool(args.get("slots", False)), bool(args.get("slots", False)),
                True, 0.0, 0.0, False, False, 0.0,
            )
        )
        rebuild(doc)
        total = _center_mark_count(view)
        return result(
            ok,
            "Inserted automatic center marks." if ok else "SOLIDWORKS added no center marks.",
            method="AutoInsertCenterMarks2",
            legacy_center_mark_features_before=before,
            legacy_center_mark_features=total,
        )

    picked = _select_circular_edges(doc, view)
    marks = doc.InsertCenterMark3(
        CENTER_MARK_STYLES[str(args.get("style", "single"))],
        bool(args.get("propagate", True)),
        bool(args.get("slots", False)),
    )
    rebuild(doc)
    clear_selection(doc)
    return result(
        bool(as_list(marks)),
        "Inserted center marks for the selected entities." if as_list(marks)
        else "SOLIDWORKS added no center marks to the selected entities.",
        circles_selected=picked,
        method="InsertCenterMark3",
    )


@tool(
    "insert_centerlines",
    "Add centerlines to a drawing view. By default every circular edge in the view is used, which "
    "is what gives bolt circles and holes their axes.",
    {
        "view_name": {"type": "string", "description": "Defaults to the active view."},
        "selected_only": {"type": "boolean", "default": False},
    },
)
def insert_centerlines(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_drawing()
    view = _activate(doc, args.get("view_name"))
    if not bool(args.get("selected_only", False)):
        # In a drawing, the axes of circular holes are represented by automatic
        # center *marks* (the visible orthogonal centerlines), not standalone
        # ICenterLine objects.  Asking InsertCenterLine2 to infer them from
        # raw model edges silently does nothing in a part view.  Use the
        # drawing-view command that SOLIDWORKS itself uses for hole axes.
        ok = bool(
            flag_methods(view, "AutoInsertCenterMarks2").AutoInsertCenterMarks2(
                1,  # hole centers
                1 | 2 | 8,
                False, False, True, 0.0, 0.0, False, False, 0.0,
            )
        )
        rebuild(doc)
        return result(
            ok,
            "Inserted hole axes as automatic center marks." if ok
            else "SOLIDWORKS added no hole axes to this view.",
            method="AutoInsertCenterMarks2",
            centerline_kind="center_marks",
        )

    lines = doc.InsertCenterLine2()
    rebuild(doc)
    clear_selection(doc)
    count = len(as_list(lines))
    return result(
        count > 0,
        f"Added {count} centerlines." if count else
        "SOLIDWORKS added no centerlines to the selected entities.",
        count=count,
        method="InsertCenterLine2",
    )


@tool(
    "add_note",
    "Place a text note on the sheet, for things like a 技术要求 block. Position is millimetres from "
    "the sheet's lower-left corner. Use \\n for line breaks.",
    {
        "text": {"type": "string"},
        "x_mm": {"type": "number"},
        "y_mm": {"type": "number"},
        "height_mm": {"type": "number", "description": "Text height; omit to use the document default."},
    },
    ["text", "x_mm", "y_mm"],
)
def add_note(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_drawing()
    clear_selection(doc)
    note = flag_methods(doc, "InsertNote").InsertNote(str(args["text"]))
    if note is None:
        return result(False, "SOLIDWORKS did not create the note.")
    try:
        annotation = value(note, "GetAnnotation")
        annotation.SetPosition(to_m(args["x_mm"]), to_m(args["y_mm"]), 0.0)
        if args.get("height_mm"):
            text_format = value(annotation, "GetTextFormat")
            text_format.CharHeight = to_m(args["height_mm"])
            annotation.SetTextFormat(0, False, text_format)
    except Exception:
        logger.info("The note was created but could not be positioned or restyled.")
    rebuild(doc)
    clear_selection(doc)
    return result(True, "Added a note.", text=str(args["text"]))
