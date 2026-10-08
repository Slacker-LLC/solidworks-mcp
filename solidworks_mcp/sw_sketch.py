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

"""Sketch creation, geometry, editing, relations, and dimensions.

Relations and dimensions are what turn a floating sketch into a driveable,
parametric one, so they are the centre of gravity of this module rather than an
afterthought.
"""

from __future__ import annotations

from typing import Any
import math

from .sw_core import (
    active_document,
    apply_transform,
    document_type,
    exit_active_sketch,
    sketch_features,
    sketch_name_for_object,
    persistent_reference_id,
    sketch_point_objects,
    dispatch_array,
    empty_variant,
    dimension_dialog_suppressed,
    as_list,
    apply_selection,
    clear_selection,
    enumerate_sketch_segments,
    extension,
    feature_property,
    find_feature,
    flag_methods,
    iter_feature_objects,
    latest_sketch,
    logger,
    rebuild,
    require_part,
    require_selection,
    resolve_plane_name,
    resolve_sketch,
    result,
    safe,
    SELECTION_SCHEMA,
    select_by_id,
    sketch_manager,
    sketch_names,
    toggle_sketch,
    sketch_segment_objects,
    tool,
    to_deg,
    to_m,
    to_mm,
    to_rad,
    TRIM_CHOICES,
    value,
)


# swConstraintType_e.  ISketchRelationManager takes these codes and can be
# verified afterwards, unlike SketchAddConstraints, which accepts a magic
# string and silently ignores anything it does not recognise.
RELATIONS = {
    "horizontal": 4,
    "vertical": 5,
    "tangent": 6,
    "parallel": 7,
    "perpendicular": 8,
    "coincident": 9,
    "concentric": 10,
    "symmetric": 11,
    "midpoint": 12,
    "intersection": 13,
    "equal": 14,
    "fixed": 17,
    "collinear": 27,
    "coradial": 28,
}
RELATION_NAMES = {code: name for name, code in RELATIONS.items()}

DIMENSION_DIRECTIONS = {"right": 0, "up": 1, "left": 2, "down": 3}


def _segment_count(doc: Any) -> int:
    """How many segments the open sketch holds right now."""
    sketch = doc.SketchManager.ActiveSketch
    if sketch is None:
        return 0
    return len(as_list(safe(sketch, "GetSketchSegments")))


def _drawn(doc: Any, before: int, what: str) -> dict[str, Any]:
    """Report a drawing tool by what the sketch actually gained.

    The Create* APIs return arrays whose truthiness is not a reliable success
    signal under late binding, so count segments instead.
    """
    added = _segment_count(doc) - before
    if added <= 0:
        return result(False, f"SOLIDWORKS did not create {what}.")
    return result(True, f"Added {what} ({added} segments).", segments_added=added)


def _active_sketch(doc: Any) -> Any:
    sketch = doc.SketchManager.ActiveSketch
    if sketch is None:
        raise RuntimeError("No sketch is open. Call create_sketch or edit_sketch first.")
    return sketch


def _require_open_sketch() -> tuple[Any, Any]:
    _, doc = active_document()
    _active_sketch(doc)
    return doc, sketch_manager(doc)


# --------------------------------------------------------------------------
# Sketch lifecycle
# --------------------------------------------------------------------------


@tool(
    "create_sketch",
    "Open a new 2D sketch on a reference plane or on a planar face of the model. "
    "Pass plane_name (exact localized name from list_reference_planes) or plane=front/top/right, "
    "or face_index from list_faces to sketch directly on a face.",
    {
        "plane": {"type": "string", "enum": ["front", "top", "right"], "default": "front"},
        "plane_name": {"type": "string", "description": "Exact localized plane name from list_reference_planes."},
        "face_index": {"type": "integer", "description": "Sketch on this face instead of a reference plane."},
        "name": {"type": "string", "description": "Rename the sketch once created."},
    },
)
def create_sketch(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_part()
    if doc.SketchManager.ActiveSketch is not None:
        return result(False, "A sketch is already open. Close it before creating another sketch.")
    face_index = args.get("face_index")
    if face_index is not None:
        require_selection(doc, {"faces": [int(face_index)]})
        target = f"face {face_index}"
    else:
        plane_name = str(args.get("plane_name") or args.get("plane") or "front")
        resolved = resolve_plane_name(doc, plane_name)
        clear_selection(doc)
        if not select_by_id(doc, resolved, "PLANE"):
            return result(False, f"Could not select reference plane '{resolved}'.")
        target = resolved

    sketch_manager(doc).InsertSketch(True)
    if doc.SketchManager.ActiveSketch is None:
        return result(False, f"SOLIDWORKS did not open a sketch on {target}.")

    name = args.get("name")
    created = ""
    try:
        created, feature = latest_sketch(doc)
        if name:
            feature.Name = str(name)
            created = str(name)
    except Exception:
        logger.info("Sketch opened but its feature could not be resolved for renaming.")
    return result(True, f"Opened a sketch on {target}.", sketch=created, target=target)


@tool("create_3d_sketch", "Create and open a new native 3D sketch in the active part or assembly. No plane is needed; geometry coordinates are model-space millimetres. Refuses to toggle an already-open sketch. Verifies native Is3D and the new feature before reporting success.",
      {"name": {"type": "string", "minLength": 1}})
def create_3d_sketch(args):
    _, doc = active_document()
    if document_type(doc) not in (1, 2):
        raise RuntimeError("3D sketches require a part or assembly document.")
    if doc.SketchManager.ActiveSketch is not None:
        return result(False, "A sketch is already open. Close it before creating another sketch.")
    before = set(sketch_names(doc))
    clear_selection(doc)
    toggle_sketch(doc, True)
    active = doc.SketchManager.ActiveSketch
    if active is None or not bool(value(active, "Is3D")):
        return result(False, "SOLIDWORKS did not open a native 3D sketch.")
    name, feature = latest_sketch(doc)
    if name in before or safe(feature, "GetTypeName2") != "3DProfileFeature":
        return result(False, "Could not confirm a new native 3D sketch feature.", sketch=name)
    if args.get("name"):
        feature.Name = args["name"]
        if str(value(feature, "Name")) != args["name"]:
            return result(False, "3D sketch created, but its requested name was not retained.", sketch=str(value(feature, "Name")))
        name = args["name"]
    return result(True, "Opened a native 3D sketch.", sketch=name, is_3d=True)


@tool(
    "edit_sketch",
    "Reopen an existing sketch for editing by name.",
    {"sketch_name": {"type": "string"}},
    ["sketch_name"],
)
def edit_sketch(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    if document_type(doc) not in (1, 2):
        raise RuntimeError("Named sketch editing currently requires a part or assembly.")
    if doc.SketchManager.ActiveSketch is not None:
        return result(False, "A sketch is already open. Close it before editing another sketch.")
    name, feature = resolve_sketch(doc, str(args["sketch_name"]))
    specific = value(feature, "GetSpecificFeature2")
    is_3d = bool(value(specific, "Is3D"))
    expected = persistent_reference_id(doc, specific)
    clear_selection(doc)
    if not select_by_id(doc, name, "SKETCH"):
        return result(False, f"Could not select sketch '{name}'.")
    toggle_sketch(doc, is_3d)
    active = doc.SketchManager.ActiveSketch
    confirmed = active is not None and bool(value(active, "Is3D")) == is_3d and bool(expected) and persistent_reference_id(doc, active) == expected
    return result(confirmed, f"Reopened sketch '{name}' for editing." if confirmed else "Could not confirm the requested sketch edit context.",
                  sketch=name, is_3d=is_3d, reference_confirmed=confirmed)


@tool("close_sketch", "Exit the open sketch without creating a feature.", {})
def close_sketch(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    if doc.SketchManager.ActiveSketch is None:
        return result(False, "No sketch is open.")
    name = ""
    try:
        name = sketch_name_for_object(doc, doc.SketchManager.ActiveSketch)
    except Exception:
        pass
    is_3d = bool(value(doc.SketchManager.ActiveSketch, "Is3D"))
    exit_active_sketch(doc)
    return result(True, "Closed the open sketch.", sketch=name, is_3d=is_3d)


@tool("list_sketches", "Read-only: list every sketch feature in the active document.", {})
def list_sketches(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    open_sketch = doc.SketchManager.ActiveSketch is not None
    details = [{"name": str(value(f, "Name")), "is_3d": bool(value(value(f, "GetSpecificFeature2"), "Is3D"))} for f in sketch_features(doc)]
    return result(True, "Read sketches.", sketches=sketch_names(doc), sketch_details=details, sketch_open=open_sketch)


@tool("list_sketch_points", "Read all native points of the open or named 2D/3D sketch with selection indices, native point types and sketch/model-space coordinates in mm. Includes generated endpoints/centers, so point indices match selection specs; user points have native type 1. Drawing-sketch enumeration remains pending.",
      {"sketch_name": {"type": "string"}})
def list_sketch_points(args):
    _, doc = active_document()
    if document_type(doc) not in (1, 2):
        raise RuntimeError("Point enumeration currently requires a part or assembly.")
    active = doc.SketchManager.ActiveSketch
    if args.get("sketch_name") or active is None:
        name, feature = resolve_sketch(doc, args.get("sketch_name"))
        sketch = value(feature, "GetSpecificFeature2")
    else:
        name = sketch_name_for_object(doc, active)
        sketch = active
    matrix = value(value(value(sketch, "ModelToSketchTransform"), "Inverse"), "ArrayData")
    points = []
    for index, point in enumerate(as_list(value(sketch, "GetSketchPoints2"))):
        coords = [float(value(point, k)) for k in ("X", "Y", "Z")]
        points.append({"index": index, "native_type": int(value(point, "Type")), "point_mm": [c * 1000 for c in coords],
                       "model_point_mm": [c * 1000 for c in apply_transform(coords, matrix)]})
    return result(True, "Read native sketch points.", sketch=name, is_3d=bool(value(sketch, "Is3D")), points=points)


@tool(
    "list_sketch_segments",
    "Read-only: list the segments of the open sketch (or a named one) with the indices used by "
    "add_relation, add_dimension, and selection specs.",
    {"sketch_name": {"type": "string", "description": "Defaults to the currently open sketch."}},
)
def list_sketch_segments(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    name, segments = enumerate_sketch_segments(doc, args.get("sketch_name") or None)
    return result(True, f"Read {len(segments)} sketch segments.", sketch=name, segments=segments)


# --------------------------------------------------------------------------
# Sketch geometry.  All coordinates are millimetres in sketch space.
# --------------------------------------------------------------------------

_XY = {"x_mm": {"type": "number"}, "y_mm": {"type": "number"}}


def _xyz(args, x="x_mm", y="y_mm", z="z_mm"):
    coords = [float(args[x]), float(args[y]), float(args.get(z, 0))]
    if not all(math.isfinite(c) for c in coords):
        raise RuntimeError("Sketch coordinates must be finite.")
    return [c / 1000 for c in coords]


def _check_z(doc, points):
    if any(p[2] != 0 for p in points) and not bool(value(_active_sketch(doc), "Is3D")):
        raise RuntimeError("Nonzero Z requires an open 3D sketch; 2D sketch coordinates lie in its XY plane.")


def _create_without_inference(manager, method, *coords):
    previous = manager.AddToDB
    try:
        manager.AddToDB = True
        return getattr(manager, method)(*coords)
    finally:
        manager.AddToDB = previous


@tool(
    "draw_line",
    "Add a line to the open 2D/3D sketch. Coordinates are mm in sketch space (model space for 3D). Nonzero z1_mm/z2_mm requires a 3D sketch.",
    {
        "x1_mm": {"type": "number"}, "y1_mm": {"type": "number"},
        "x2_mm": {"type": "number"}, "y2_mm": {"type": "number"},
        "z1_mm": {"type": "number", "default": 0}, "z2_mm": {"type": "number", "default": 0},
        "construction": {"type": "boolean", "default": False},
    },
    ["x1_mm", "y1_mm", "x2_mm", "y2_mm"],
)
def draw_line(args: dict[str, Any]) -> dict[str, Any]:
    a, b = _xyz(args, "x1_mm", "y1_mm", "z1_mm"), _xyz(args, "x2_mm", "y2_mm", "z2_mm")
    doc, manager = _require_open_sketch()
    _check_z(doc, [a, b])
    before = _segment_count(doc)
    segment = _create_without_inference(manager, "CreateLine", *a, *b)
    if segment is not None and bool(args.get("construction", False)):
        segment.ConstructionGeometry = True
    payload = _drawn(doc, before, "a line")
    if payload["ok"]:
        if segment is None:
            return result(False, "Line geometry appeared, but its native reference could not be confirmed.")
        endpoints = [[float(value(value(segment, method), k)) for k in ("X", "Y", "Z")]
                     for method in ("GetStartPoint2", "GetEndPoint2")]
        confirmed = min(max(math.dist(endpoints[0], a), math.dist(endpoints[1], b)),
                        max(math.dist(endpoints[0], b), math.dist(endpoints[1], a))) <= 1e-5
        if args.get("construction", False):
            confirmed = confirmed and bool(value(segment, "ConstructionGeometry"))
        payload.update(ok=confirmed)
        payload["data"].update(start_mm=[c * 1000 for c in endpoints[0]], end_mm=[c * 1000 for c in endpoints[1]])
        if not confirmed:
            payload["message"] = "Line created, but requested endpoints or construction state could not be confirmed."
    return payload


@tool(
    "draw_centerline",
    "Add a verified construction line to the open 2D/3D sketch. XYZ coordinates are millimetres; nonzero Z requires 3D. Uses native CreateLine because CreateCenterLine may flatten Z.",
    {
        "x1_mm": {"type": "number"}, "y1_mm": {"type": "number"},
        "x2_mm": {"type": "number"}, "y2_mm": {"type": "number"},
        "z1_mm": {"type": "number", "default": 0}, "z2_mm": {"type": "number", "default": 0},
    },
    ["x1_mm", "y1_mm", "x2_mm", "y2_mm"],
)
def draw_centerline(args: dict[str, Any]) -> dict[str, Any]:
    return draw_line({**args, "construction": True})


@tool(
    "draw_circle",
    "Add a circle to the open sketch. Centre and radius are millimetres.",
    {
        "x_mm": {"type": "number"}, "y_mm": {"type": "number"},
        "radius_mm": {"type": "number", "exclusiveMinimum": 0},
        "construction": {"type": "boolean", "default": False},
    },
    ["x_mm", "y_mm", "radius_mm"],
)
def draw_circle(args: dict[str, Any]) -> dict[str, Any]:
    doc, manager = _require_open_sketch()
    before = _segment_count(doc)
    segment = _create_without_inference(manager, "CreateCircleByRadius",
        to_m(args["x_mm"]), to_m(args["y_mm"]), 0.0, to_m(args["radius_mm"]))
    if segment is not None and bool(args.get("construction", False)):
        segment.ConstructionGeometry = True
    payload = _drawn(doc, before, "a circle")
    if payload["ok"]:
        if segment is None:
            return result(False, "Circle geometry appeared, but its native reference could not be confirmed.")
        center = value(segment, "GetCenterPoint2")
        coords = [float(value(center, k)) * 1000 for k in ("X", "Y", "Z")]
        radius = float(value(segment, "GetRadius")) * 1000
        confirmed = math.dist(coords, [float(args["x_mm"]), float(args["y_mm"]), 0]) <= .01 and math.isclose(radius, float(args["radius_mm"]), abs_tol=.01, rel_tol=0)
        payload.update(ok=confirmed)
        payload["data"].update(center_mm=coords, radius_mm=radius)
        if not confirmed:
            payload["message"] = "Circle created, but requested center or radius could not be confirmed."
    return payload


@tool(
    "draw_rectangle",
    "Add a corner rectangle to the open sketch. Both corners are millimetres.",
    {
        "x1_mm": {"type": "number"}, "y1_mm": {"type": "number"},
        "x2_mm": {"type": "number"}, "y2_mm": {"type": "number"},
    },
    ["x1_mm", "y1_mm", "x2_mm", "y2_mm"],
)
def draw_rectangle(args: dict[str, Any]) -> dict[str, Any]:
    doc, manager = _require_open_sketch()
    before = _segment_count(doc)
    manager.CreateCornerRectangle(
        to_m(args["x1_mm"]), to_m(args["y1_mm"]), 0.0,
        to_m(args["x2_mm"]), to_m(args["y2_mm"]), 0.0,
    )
    return _drawn(doc, before, "a rectangle")


@tool(
    "draw_arc",
    "Add an arc defined by centre, start point, and end point. direction 1 is counter-clockwise, "
    "-1 is clockwise. The start point sets the radius; the end point is projected onto it. Millimetres.",
    {
        "center_x_mm": {"type": "number"}, "center_y_mm": {"type": "number"},
        "start_x_mm": {"type": "number"}, "start_y_mm": {"type": "number"},
        "end_x_mm": {"type": "number"}, "end_y_mm": {"type": "number"},
        "direction": {"type": "integer", "enum": [1, -1], "default": 1},
    },
    ["center_x_mm", "center_y_mm", "start_x_mm", "start_y_mm", "end_x_mm", "end_y_mm"],
)
def draw_arc(args: dict[str, Any]) -> dict[str, Any]:
    doc, manager = _require_open_sketch()
    before = _segment_count(doc)
    manager.CreateArc(
        to_m(args["center_x_mm"]), to_m(args["center_y_mm"]), 0.0,
        to_m(args["start_x_mm"]), to_m(args["start_y_mm"]), 0.0,
        to_m(args["end_x_mm"]), to_m(args["end_y_mm"]), 0.0,
        int(args.get("direction", 1)),
    )
    return _drawn(doc, before, "an arc")


@tool(
    "draw_3point_arc",
    "Add a verified 2D/3D arc through start, end and a point on the arc, in mm. Nonzero Z requires 3D. Rejects coincident or collinear input before creation and checks native endpoints, radius, length and curve distance.",
    {
        "x1_mm": {"type": "number"}, "y1_mm": {"type": "number"},
        "x2_mm": {"type": "number"}, "y2_mm": {"type": "number"},
        "x3_mm": {"type": "number"}, "y3_mm": {"type": "number"},
        "z1_mm": {"type": "number", "default": 0}, "z2_mm": {"type": "number", "default": 0}, "z3_mm": {"type": "number", "default": 0},
    },
    ["x1_mm", "y1_mm", "x2_mm", "y2_mm", "x3_mm", "y3_mm"],
)
def draw_3point_arc(args: dict[str, Any]) -> dict[str, Any]:
    points = [_xyz(args, f"x{i}_mm", f"y{i}_mm", f"z{i}_mm") for i in (1, 2, 3)]
    a, b, c = points
    u, v = [[q - p for p, q in zip(a, point)] for point in (b, c)]
    def cross(x, y):
        return [x[1]*y[2]-x[2]*y[1], x[2]*y[0]-x[0]*y[2], x[0]*y[1]-x[1]*y[0]]
    def dot(x, y):
        return sum(p*q for p, q in zip(x, y))
    w = cross(u, v)
    area_squared = dot(w, w)
    if min(math.dist(a, b), math.dist(a, c), math.dist(b, c)) <= 1e-9 or area_squared <= dot(u,u)*dot(v,v)*1e-16:
        raise RuntimeError("Three-point arcs require distinct, non-collinear points.")
    center = [a[i] + (dot(u,u)*cross(v,w)[i] + dot(v,v)*cross(w,u)[i])/(2*area_squared) for i in range(3)]
    radial = [[p[i]-center[i] for i in range(3)] for p in points]
    radius = math.sqrt(dot(radial[0], radial[0]))
    normal = [x/math.sqrt(area_squared) for x in w]
    def angle(target):
        return math.atan2(dot(normal, cross(radial[0], target)), dot(radial[0], target)) % (2*math.pi)
    end_angle, through_angle = angle(radial[1]), angle(radial[2])
    expected_length = radius*(end_angle if through_angle <= end_angle else 2*math.pi-end_angle)
    doc, manager = _require_open_sketch()
    _check_z(doc, points)
    before = _segment_count(doc)
    segment = _create_without_inference(manager, "Create3PointArc", *(q for p in points for q in p))
    payload = _drawn(doc, before, "a 3-point arc")
    if not payload["ok"]:
        return payload
    if segment is None:
        return result(False, "Arc appeared, but its native reference could not be confirmed.")
    curve = value(segment, "GetCurve")
    if curve is None:
        return result(False, "Arc appeared, but its native curve could not be confirmed.")
    flag_methods(curve, "GetClosestPointOn")
    gaps = [math.dist(p, [float(q) for q in as_list(curve.GetClosestPointOn(*p))[:3]]) for p in points]
    endpoints = [[float(value(value(segment, method), k)) for k in ("X", "Y", "Z")] for method in ("GetStartPoint2", "GetEndPoint2")]
    endpoint_gap = min(max(math.dist(endpoints[0],a), math.dist(endpoints[1],b)), max(math.dist(endpoints[0],b), math.dist(endpoints[1],a)))
    actual_radius, actual_length = float(value(segment,"GetRadius")), float(value(segment,"GetLength"))
    confirmed = max(gaps+[endpoint_gap]) <= 1e-5 and abs(actual_radius-radius) <= 1e-5 and abs(actual_length-expected_length) <= 1e-5
    return result(confirmed, "Created and verified a 3-point arc." if confirmed else "Arc created, but requested geometry could not be confirmed.",
                  radius_mm=actual_radius*1000, length_mm=actual_length*1000, max_point_gap_mm=max(gaps+[endpoint_gap])*1000)


@tool(
    "draw_ellipse",
    "Add a verified full ellipse in an open 2D sketch. Center and perpendicular major/minor axis points are mm. Verifies 24 theoretical points and perimeter; direct native 3D creation is unsupported, so use a plane sketch and convert_entities for spatial geometry (native spline representation).",
    {
        "center_x_mm": {"type": "number"}, "center_y_mm": {"type": "number"},
        "major_x_mm": {"type": "number"}, "major_y_mm": {"type": "number"},
        "minor_x_mm": {"type": "number"}, "minor_y_mm": {"type": "number"},
    },
    ["center_x_mm", "center_y_mm", "major_x_mm", "major_y_mm", "minor_x_mm", "minor_y_mm"],
)
def draw_ellipse(args: dict[str, Any]) -> dict[str, Any]:
    points = [_xyz(args, f"{p}_x_mm", f"{p}_y_mm") for p in ("center", "major", "minor")]
    center, major, minor = points
    u, v = [[q-p for p,q in zip(center,point)] for point in (major,minor)]
    a, b = math.dist(center, major), math.dist(center, minor)
    if min(a,b) <= 1e-9 or a < b or abs(sum(x*y for x,y in zip(u,v))) > a*b*1e-8:
        raise RuntimeError("Ellipse axes must be nonzero, perpendicular, with major radius at least the minor radius.")
    doc, manager = _require_open_sketch()
    if bool(value(_active_sketch(doc), "Is3D")):
        raise RuntimeError("Native CreateEllipse requires a 2D sketch. Create it on a plane and convert the segment into a 3D sketch instead.")
    before = _segment_count(doc)
    segment = _create_without_inference(manager,"CreateEllipse",*(q for p in points for q in p))
    payload = _drawn(doc, before, "an ellipse")
    if not payload["ok"]:
        return payload
    curve = value(segment,"GetCurve") if segment is not None else None
    if curve is None:
        return result(False,"Ellipse appeared, but its native curve could not be confirmed.")
    flag_methods(curve,"GetClosestPointOn")
    gaps=[]
    for i in range(24):
        angle=2*math.pi*i/24
        p=[center[k]+u[k]*math.cos(angle)+v[k]*math.sin(angle) for k in range(3)]
        gaps.append(math.dist(p,[float(q) for q in as_list(curve.GetClosestPointOn(*p))[:3]]))
    # Simpson integration of the independent theoretical perimeter.
    n=256
    h=2*math.pi/n
    speeds=[math.sqrt(a*a*math.sin(i*h)**2+b*b*math.cos(i*h)**2) for i in range(n+1)]
    expected=h/3*(speeds[0]+speeds[-1]+4*sum(speeds[1:-1:2])+2*sum(speeds[2:-1:2]))
    length=float(value(segment,"GetLength"))
    confirmed=max(gaps)<=1e-5 and abs(length-expected)<=1e-5
    return result(confirmed,"Created and verified an ellipse." if confirmed else "Ellipse created, but requested geometry could not be confirmed.",
                  length_mm=length*1000,max_point_gap_mm=max(gaps)*1000)


@tool(
    "draw_polygon",
    "Add a regular polygon from its centre and one corner (inscribed) or edge midpoint (circumscribed). Millimetres.",
    {
        "center_x_mm": {"type": "number"}, "center_y_mm": {"type": "number"},
        "point_x_mm": {"type": "number"}, "point_y_mm": {"type": "number"},
        "sides": {"type": "integer", "minimum": 3, "maximum": 100, "default": 6},
        "inscribed": {"type": "boolean", "default": True},
    },
    ["center_x_mm", "center_y_mm", "point_x_mm", "point_y_mm"],
)
def draw_polygon(args: dict[str, Any]) -> dict[str, Any]:
    doc, manager = _require_open_sketch()
    before = _segment_count(doc)
    manager.CreatePolygon(
        to_m(args["center_x_mm"]), to_m(args["center_y_mm"]), 0.0,
        to_m(args["point_x_mm"]), to_m(args["point_y_mm"]), 0.0,
        int(args.get("sides", 6)), bool(args.get("inscribed", True)),
    )
    return _drawn(doc, before, "a polygon")


@tool(
    "draw_slot",
    "Add a straight slot from two centre points and a width. length_type center_center measures "
    "between arc centres; full_length measures overall. Millimetres.",
    {
        "x1_mm": {"type": "number"}, "y1_mm": {"type": "number"},
        "x2_mm": {"type": "number"}, "y2_mm": {"type": "number"},
        "width_mm": {"type": "number", "exclusiveMinimum": 0},
        "length_type": {"type": "string", "enum": ["center_center", "full_length"], "default": "center_center"},
        "add_dimensions": {"type": "boolean", "default": False},
    },
    ["x1_mm", "y1_mm", "x2_mm", "y2_mm", "width_mm"],
)
def draw_slot(args: dict[str, Any]) -> dict[str, Any]:
    doc, manager = _require_open_sketch()
    length_type = 0 if str(args.get("length_type", "center_center")) == "center_center" else 1
    before = _segment_count(doc)
    manager.CreateSketchSlot(
        0,  # swSketchSlotCreationType_line
        length_type,
        to_m(args["width_mm"]),
        to_m(args["x1_mm"]), to_m(args["y1_mm"]), 0.0,
        to_m(args["x2_mm"]), to_m(args["y2_mm"]), 0.0,
        0.0, 0.0, 0.0,
        1,  # arc direction, unused for a straight slot
        bool(args.get("add_dimensions", False)),
    )
    return _drawn(doc, before, "a slot")


@tool(
    "draw_point",
    "Add a point to the open 2D/3D sketch. Coordinates are mm in sketch space (model space for 3D). Nonzero z_mm requires a 3D sketch; reports actual coordinates.",
    {**_XY, "z_mm": {"type": "number", "default": 0}},
    ["x_mm", "y_mm"],
)
def draw_point(args: dict[str, Any]) -> dict[str, Any]:
    coords = _xyz(args)
    doc, manager = _require_open_sketch()
    _check_z(doc, [coords])
    point = manager.CreatePoint(*coords)
    actual = [float(value(point, k)) for k in ("X", "Y", "Z")] if point is not None else None
    confirmed = actual is not None and math.dist(actual, coords) <= 1e-5
    return result(confirmed, "Added a sketch point." if confirmed else "Sketch point creation or coordinates could not be confirmed.",
                  point_mm=[c * 1000 for c in actual] if actual is not None else None)


@tool(
    "draw_spline",
    "Add a spline through an ordered list of points. Millimetres.",
    {
        "points": {
            "type": "array",
            "minItems": 2,
            "items": {
                "type": "object",
                "properties": {"x_mm": {"type": "number"}, "y_mm": {"type": "number"}, "z_mm": {"type": "number", "default": 0}},
                "required": ["x_mm", "y_mm"],
            },
        }
    },
    ["points"],
)
def draw_spline(args: dict[str, Any]) -> dict[str, Any]:
    points = [_xyz(p) for p in args["points"]]
    doc, manager = _require_open_sketch()
    _check_z(doc, points)
    flat: list[float] = []
    for point in points:
        flat.extend(point)
    from .sw_core import double_array

    before = _segment_count(doc)
    manager.CreateSpline(double_array(flat))
    return _drawn(doc, before, "a spline")


# --------------------------------------------------------------------------
# Sketch editing
# --------------------------------------------------------------------------


@tool(
    "sketch_fillet",
    "Round the corners between the selected sketch segments. Select two segments (or a shared "
    "endpoint) via selection, then give the radius in millimetres.",
    {
        "radius_mm": {"type": "number", "exclusiveMinimum": 0},
        "selection": SELECTION_SCHEMA,
        "keep_constraints": {"type": "boolean", "default": True},
    },
    ["radius_mm", "selection"],
)
def sketch_fillet(args: dict[str, Any]) -> dict[str, Any]:
    doc, _ = _require_open_sketch()
    require_selection(doc, args["selection"])
    ok = bool(doc.SketchFillet2(to_m(args["radius_mm"]), 1 if args.get("keep_constraints", True) else 0))
    clear_selection(doc)
    return result(ok, "Applied a sketch fillet." if ok else "SOLIDWORKS did not apply the sketch fillet.")


@tool(
    "sketch_chamfer",
    "Chamfer the corner between two selected sketch segments. Millimetres and degrees.",
    {
        "distance_mm": {"type": "number", "exclusiveMinimum": 0},
        "angle_deg": {"type": "number", "default": 45},
        "mode": {"type": "string", "enum": ["distance_angle", "distance_distance", "equal_distance"], "default": "equal_distance"},
        "second_distance_mm": {"type": "number", "description": "Only used by distance_distance."},
        "selection": SELECTION_SCHEMA,
    },
    ["distance_mm", "selection"],
)
def sketch_chamfer(args: dict[str, Any]) -> dict[str, Any]:
    doc, _ = _require_open_sketch()
    require_selection(doc, args["selection"])
    mode = str(args.get("mode", "equal_distance"))
    option = {"distance_angle": 0, "distance_distance": 1, "equal_distance": 2}[mode]
    if mode == "distance_angle":
        first, second = to_rad(args.get("angle_deg", 45)), to_m(args["distance_mm"])
    elif mode == "distance_distance":
        first = to_m(args.get("second_distance_mm", args["distance_mm"]))
        second = to_m(args["distance_mm"])
    else:
        first, second = to_m(args["distance_mm"]), to_m(args["distance_mm"])
    doc.SketchChamfer(first, second, option)
    clear_selection(doc)
    return result(True, "Applied a sketch chamfer.")


def _sketch_shape(doc: Any) -> tuple[int, float]:
    """A cheap fingerprint of the open sketch: segment count and total length."""
    sketch = doc.SketchManager.ActiveSketch
    if sketch is None:
        return (0, 0.0)
    segments = as_list(safe(sketch, "GetSketchSegments"))
    total = 0.0
    for segment in segments:
        try:
            total += float(value(segment, "GetLength"))
        except Exception:
            pass
    return (len(segments), round(total, 9))


@tool(
    "sketch_trim",
    "Trim sketch geometry. Put the segment to trim in selection, then give a point on the piece "
    "you want removed; SOLIDWORKS cuts it back to the nearest intersection. Millimetres.",
    {
        "x_mm": {"type": "number"}, "y_mm": {"type": "number"},
        "selection": SELECTION_SCHEMA,
        "mode": {"type": "string", "enum": list(TRIM_CHOICES), "default": "closest"},
    },
    ["x_mm", "y_mm", "selection"],
)
def sketch_trim(args: dict[str, Any]) -> dict[str, Any]:
    doc, manager = _require_open_sketch()
    option = TRIM_CHOICES[str(args.get("mode", "closest"))]
    # SketchTrim acts on the current selection. With none it does nothing, and
    # with a stale one left by an earlier tool it silently trims the wrong
    # entity, so the selection is established here every time.
    clear_selection(doc)
    if not args.get("selection"):
        return result(
            False,
            "sketch_trim needs the segment to trim in selection; without one SOLIDWORKS "
            "either does nothing or trims whatever happened to be selected before.",
        )
    require_selection(doc, args["selection"])
    before = _sketch_shape(doc)
    # SketchTrim reports False even when it succeeds, so judge by the sketch.
    manager.SketchTrim(option, to_m(args["x_mm"]), to_m(args["y_mm"]), 0.0)
    after = _sketch_shape(doc)
    clear_selection(doc)
    if after == before:
        return result(
            False,
            "Nothing was trimmed. Give a point that lies on the piece you want gone, on the "
            "segment named in selection.",
        )
    return result(
        True,
        f"Trimmed the sketch ({before[0]} segments to {after[0]}).",
        segments_before=before[0],
        segments_after=after[0],
    )


@tool(
    "sketch_offset",
    "Offset the selected sketch entities by a distance in millimetres.",
    {
        "distance_mm": {"type": "number"},
        "selection": SELECTION_SCHEMA,
        "both_directions": {"type": "boolean", "default": False},
        "chain": {"type": "boolean", "default": True},
    },
    ["distance_mm", "selection"],
)
def sketch_offset(args: dict[str, Any]) -> dict[str, Any]:
    doc, _ = _require_open_sketch()
    require_selection(doc, args["selection"])
    ok = bool(
        doc.SketchOffset2(
            to_m(args["distance_mm"]),
            bool(args.get("both_directions", False)),
            bool(args.get("chain", True)),
        )
    )
    clear_selection(doc)
    return result(ok, "Offset the sketch entities." if ok else "SOLIDWORKS did not offset the selection.")


@tool(
    "sketch_mirror",
    "Mirror sketch entities about a centerline. Put the entities to mirror in selection and the "
    "centerline index in mirror_segment.",
    {
        "selection": SELECTION_SCHEMA,
        "mirror_segment": {"type": "integer", "description": "Index of the centerline segment from list_sketch_segments."},
    },
    ["selection", "mirror_segment"],
)
def sketch_mirror(args: dict[str, Any]) -> dict[str, Any]:
    doc, _ = _require_open_sketch()
    require_selection(doc, args["selection"], mark=0)
    segments = sketch_segment_objects(doc, None)
    index = int(args["mirror_segment"])
    if not 0 <= index < len(segments):
        return result(False, f"mirror_segment {index} is out of range (0..{len(segments) - 1}).")
    from .sw_core import select_object

    if not select_object(doc, segments[index], mark=0, append=True):
        return result(False, "Could not add the mirror centerline to the selection.")
    doc.SketchMirror()
    clear_selection(doc)
    return result(True, "Mirrored the sketch entities.")


def _curve_samples(curve):
    import pythoncom
    import win32com.client
    flag_methods(curve,"GetEndParams","Evaluate2")
    refs=[win32com.client.VARIANT(pythoncom.VT_BYREF|t,v) for t,v in
          ((pythoncom.VT_R8,0.),(pythoncom.VT_R8,0.),(pythoncom.VT_BOOL,False),(pythoncom.VT_BOOL,False))]
    if not curve.GetEndParams(*refs):
        raise RuntimeError("Cannot read native curve parameter bounds.")
    start,end=float(refs[0].value),float(refs[1].value)
    if not all(math.isfinite(x) for x in (start,end)) or end<=start:
        raise RuntimeError("Native curve bounds are invalid.")
    return [[float(q) for q in as_list(curve.Evaluate2(start+(end-start)*i/24,0))[:3]] for i in range(25)]


@tool("convert_entities", "Convert selected edges, face loops or source sketch segments into the open 2D/3D sketch. Verifies new native curves; supported sketch-segment inputs additionally compare 25 transformed source samples and total length with chain=false. Reports ellipse-to-spline native representation and unverified correspondence explicitly.",
      {"selection": SELECTION_SCHEMA,"chain": {"type":"boolean","default":True},"inner_loops":{"type":"boolean","default":False}},["selection"])
def convert_entities(args: dict[str, Any]) -> dict[str, Any]:
    doc, manager = _require_open_sketch()
    existing=sketch_segment_objects(doc)
    before={persistent_reference_id(doc,s) for s in existing}
    if b"" in before:
        raise RuntimeError("Cannot identify existing target curves before conversion.")
    spec=args["selection"]
    expected=[]
    expected_length=0.
    supported=False
    if spec.get("sketch_segments") and set(spec)<= {"sketch_name","sketch_segments"}:
        source_segments=sketch_segment_objects(doc,spec.get("sketch_name"))
        source_sketch = value(resolve_sketch(doc,spec["sketch_name"])[1],"GetSpecificFeature2") if spec.get("sketch_name") else manager.ActiveSketch
        source_to_model=value(value(value(source_sketch,"ModelToSketchTransform"),"Inverse"),"ArrayData")
        model_to_target=value(value(manager.ActiveSketch,"ModelToSketchTransform"),"ArrayData")
        supported=True
        for index in dict.fromkeys(spec["sketch_segments"]):
            if not 0<=int(index)<len(source_segments):
                raise RuntimeError("Source sketch segment index is out of range.")
            segment=source_segments[int(index)]
            length=float(value(segment,"GetLength"))
            curve=value(segment,"GetCurve")
            kind=int(value(segment,"GetType"))
            if curve is None or kind not in (0,1,2,3) or (kind==1 and not math.isclose(length,2*math.pi*float(value(segment,"GetRadius")),abs_tol=1e-8)):
                supported=False
                break
            expected.extend(apply_transform(apply_transform(p,source_to_model),model_to_target) for p in _curve_samples(curve))
            expected_length+=length
    require_selection(doc, args["selection"])
    ok = bool(manager.SketchUseEdge3(bool(args.get("chain", True)), bool(args.get("inner_loops", False))))
    segments=[s for s in sketch_segment_objects(doc) if not before or persistent_reference_id(doc,s) not in before]
    valid=all(value(s,"GetCurve") is not None and float(value(s,"GetLength")) > 0 for s in segments)
    confirmed=ok and bool(segments) and valid
    correspondence=None
    gap_mm=None
    if confirmed and supported and expected:
        curves=[flag_methods(value(s,"GetCurve"),"GetClosestPointOn") for s in segments]
        gap_mm=1000*max(min(math.dist(p,[float(q) for q in as_list(c.GetClosestPointOn(*p))[:3]]) for c in curves) for p in expected)
        actual_length=sum(float(value(s,"GetLength")) for s in segments)
        length_matches=actual_length>=expected_length-1e-5 if args.get("chain",True) else abs(actual_length-expected_length)<=1e-5
        correspondence=gap_mm<=.01 and length_matches
        confirmed=confirmed and correspondence
    return result(confirmed, "Converted and verified native output curves." if confirmed else "Conversion or native output curves could not be confirmed.",
                  native_accepted=ok,segments_added=len(segments),native_types=[int(value(s,"GetType")) for s in segments],
                  geometry_correspondence_confirmed=correspondence,max_source_point_gap_mm=gap_mm)


@tool(
    "set_construction_geometry",
    "Toggle the selected sketch segments between normal and construction geometry.",
    {"selection": SELECTION_SCHEMA, "construction": {"type": "boolean", "default": True}},
    ["selection"],
)
def set_construction_geometry(args: dict[str, Any]) -> dict[str, Any]:
    doc, _ = _require_open_sketch()
    spec = dict(args["selection"])
    indices = [int(i) for i in spec.get("sketch_segments", [])]
    if not indices:
        return result(False, "Pass sketch_segments indices in the selection.")
    segments = sketch_segment_objects(doc, spec.get("sketch_name"))
    flag = bool(args.get("construction", True))
    changed = 0
    for index in indices:
        if not 0 <= index < len(segments):
            return result(False, f"Sketch segment index {index} is out of range (0..{len(segments) - 1}).")
        segments[index].ConstructionGeometry = flag
        changed += 1
    return result(True, f"Set construction={flag} on {changed} segments.", changed=changed)


# --------------------------------------------------------------------------
# Relations and dimensions
# --------------------------------------------------------------------------


def _relation_entities(doc: Any, spec: dict[str, Any]) -> list[Any]:
    """Resolve a selection spec to the sketch objects AddRelation wants."""
    sketch_name = spec.get("sketch_name")
    entities: list[Any] = []
    if spec.get("sketch_segments"):
        segments = sketch_segment_objects(doc, sketch_name)
        for raw in spec["sketch_segments"]:
            index = int(raw)
            if not 0 <= index < len(segments):
                raise RuntimeError(f"Sketch segment index {index} is out of range (0..{len(segments) - 1}).")
            entities.append(segments[index])
    if spec.get("sketch_points"):
        points = sketch_point_objects(doc, sketch_name)
        for raw in spec["sketch_points"]:
            index = int(raw)
            if not 0 <= index < len(points):
                raise RuntimeError(f"Sketch point index {index} is out of range (0..{len(points) - 1}).")
            entities.append(points[index])
    return entities


@tool(
    "add_relation",
    "Add a geometric relation between the selected sketch entities. This is what makes a sketch "
    "fully defined and driveable, so add relations before dimensions. Select the entities via "
    "selection (sketch_segments / sketch_points), then name the relation.",
    {
        "relation": {"type": "string", "enum": sorted(RELATIONS), "description": "Geometric relation to add."},
        "selection": SELECTION_SCHEMA,
    },
    ["relation", "selection"],
)
def add_relation(args: dict[str, Any]) -> dict[str, Any]:
    doc, _ = _require_open_sketch()
    relation = str(args["relation"])
    code = RELATIONS[relation]
    sketch = doc.SketchManager.ActiveSketch
    manager = value(sketch, "RelationManager")
    flag_methods(manager, "AddRelation", "GetRelationsCount", "GetAllowedRelations")

    entities = _relation_entities(doc, args["selection"])
    if not entities:
        raise RuntimeError(
            "add_relation needs sketch_segments or sketch_points in the selection. "
            "Call list_sketch_segments to see the indices."
        )

    before = int(manager.GetRelationsCount(0))
    status_before = _sketch_status(doc)
    payload = dispatch_array(entities)
    try:
        manager.AddRelation(payload, code)
    except Exception as exc:
        clear_selection(doc)
        return result(False, f"SOLIDWORKS rejected the '{relation}' relation: {exc}", entities=len(entities))

    added = int(manager.GetRelationsCount(0)) - before
    clear_selection(doc)
    if added <= 0:
        # SOLIDWORKS adds nothing rather than complaining when a relation does
        # not apply, so say which ones would have.
        allowed: list[str] = []
        try:
            allowed = sorted(
                RELATION_NAMES.get(int(c), f"code_{int(c)}")
                for c in as_list(manager.GetAllowedRelations(payload))
            )
        except Exception:
            pass
        return result(
            False,
            f"SOLIDWORKS did not add a {relation} relation to that selection.",
            entities=len(entities),
            allowed_relations=allowed,
        )
    status = _sketch_status(doc)
    if status in {"over_defined", "no_solution"} and status != status_before:
        # The relation went in, but the sketch can no longer solve, so the
        # geometry will not have moved. Say so instead of reporting a clean win.
        return result(
            False,
            f"The {relation} relation was added but left the sketch {status}, so the geometry did "
            "not move. Remove a conflicting relation or dimension first.",
            entities=len(entities),
            sketch_status=status,
            sketch_status_before=status_before,
        )
    return result(
        True,
        f"Added a {relation} relation across {len(entities)} entities.",
        entities=len(entities),
        sketch_status=status,
    )


def _sketch_status(doc: Any) -> str:
    """Report whether the open sketch is under/fully/over defined."""
    try:
        sketch = doc.SketchManager.ActiveSketch
        if sketch is None:
            return "closed"
        code = int(value(sketch, "GetConstrainedStatus"))
    except Exception:
        return "unknown"
    # swConstrainedStatus_e is 1-based: 1 unknown, 2 under, 3 fully, 4 over.
    return {
        1: "unknown", 2: "under_defined", 3: "fully_defined",
        4: "over_defined", 5: "no_solution", 6: "invalid_solution",
        7: "autosolve_off",
    }.get(code, f"status_{code}")


_DIMENSION_METHODS = {
    "auto": "AddDimension2",
    "horizontal": "AddHorizontalDimension2",
    "vertical": "AddVerticalDimension2",
    "radius": "AddRadialDimension2",
    "diameter": "AddDiameterDimension2",
}


@tool(
    "add_dimension",
    "Add a driving dimension to the selected sketch entities and set its value. Select one entity "
    "(length/radius) or two (distance/angle) via selection. value_mm drives linear dimensions; "
    "value_deg drives angular ones. place_*_mm is where the dimension text sits (millimetres). "
    "Use kind to force a horizontal, vertical, radius, or diameter dimension instead of letting "
    "SOLIDWORKS infer one.",
    {
        "selection": SELECTION_SCHEMA,
        "value_mm": {"type": "number", "description": "Target value for a linear dimension, in millimetres."},
        "value_deg": {"type": "number", "description": "Target value for an angular dimension, in degrees."},
        "kind": {"type": "string", "enum": sorted(_DIMENSION_METHODS), "default": "auto"},
        "place_x_mm": {"type": "number", "default": 0},
        "place_y_mm": {"type": "number", "default": 0},
        "place_z_mm": {"type": "number", "default": 0},
        "name": {"type": "string", "description": "Rename the dimension so equations can reference it."},
    },
    ["selection"],
)
def add_dimension(args: dict[str, Any]) -> dict[str, Any]:
    app, doc = active_document()
    count = require_selection(doc, args["selection"])
    member = _DIMENSION_METHODS[str(args.get("kind", "auto"))]
    flag_methods(doc, member)

    # ModelDoc2.AddDimension2 rather than ModelDocExtension.AddDimension: the
    # latter reproducibly crashes SOLIDWORKS 2026 on a sketch selection.
    with dimension_dialog_suppressed(app):
        display = getattr(doc, member)(
            to_m(args.get("place_x_mm", 0)),
            to_m(args.get("place_y_mm", 0)),
            to_m(args.get("place_z_mm", 0)),
        )
    if display is None:
        clear_selection(doc)
        return result(
            False,
            "SOLIDWORKS did not create a dimension for that selection. "
            "Check that the entities can actually carry one dimension between them.",
            entities=count,
        )

    dimension = None
    try:
        dimension = flag_methods(display, "GetDimension2").GetDimension2(0)
    except Exception:
        dimension = safe(display, "GetDimension")

    applied: dict[str, Any] = {}
    if dimension is not None:
        target = None
        if args.get("value_mm") is not None:
            target = to_m(args["value_mm"])
            applied["value_mm"] = float(args["value_mm"])
        elif args.get("value_deg") is not None:
            target = to_rad(args["value_deg"])
            applied["value_deg"] = float(args["value_deg"])
        if target is not None:
            # swSetValueInConfiguration_e.swSetValue_InAllConfigurations = 2
            flag_methods(dimension, "SetSystemValue3")
            code = int(dimension.SetSystemValue3(target, 2, empty_variant()))
            actual = float(safe(dimension, "SystemValue", target) or 0.0)
            applied["applied"] = abs(actual - target) < 1e-9
            if code:
                applied["set_value_status"] = code
        if args.get("name"):
            try:
                dimension.Name = str(args["name"])
            except Exception:
                logger.info("Could not rename the new dimension.")
        applied["full_name"] = str(safe(dimension, "FullName", ""))

    clear_selection(doc)
    # EditRebuild3 exits sketch mode, which would strand the caller mid-sketch;
    # the sketch solver already applied the value, so only rebuild outside one.
    if doc.SketchManager.ActiveSketch is None:
        rebuild(doc)
    status = _sketch_status(doc)
    return result(True, "Added a dimension.", sketch_status=status, **applied)


@tool("add_3d_dimension", "Create a native X/Y/Z projected linear dimension between two points of the open 3D sketch. Indices come from list_sketch_points. Values/placement are mm; an optional value is applied in all configurations, while geometry is verified in the active one. Rebuilds to solve deferred geometry, restores the same edit context, and verifies native dimension plus actual projected point distance. Native assembly Z may be refused and is reported as failure.",
      {"axis":{"type":"string","enum":["x","y","z"]},"point_indices":{"type":"array","items":{"type":"integer","minimum":0},"minItems":2,"maxItems":2,"uniqueItems":True},
       "value_mm":{"type":"number","minimum":0},"place_x_mm":{"type":"number","default":0},"place_y_mm":{"type":"number","default":0},"place_z_mm":{"type":"number","default":0}},["axis","point_indices"])
def add_3d_dimension(args):
    app,doc=active_document()
    sketch=_active_sketch(doc)
    if not bool(value(sketch,"Is3D")):
        raise RuntimeError("Axis dimensions require an open 3D sketch.")
    axis=str(args["axis"])
    if axis not in ("x","y","z"):
        raise RuntimeError("Axis must be x, y or z.")
    indices=[int(i) for i in args["point_indices"]]
    points=sketch_point_objects(doc)
    if len(indices)!=2 or indices[0]==indices[1] or any(i<0 or i>=len(points) for i in indices):
        raise RuntimeError("Select two distinct valid native sketch point indices.")
    selected=[points[i] for i in indices]
    references=[persistent_reference_id(doc,p) for p in selected]
    if not all(references) or references[0]==references[1]:
        raise RuntimeError("Cannot confirm two distinct native point references.")
    coordinate=axis.upper()
    before_distance=abs(float(value(selected[1],coordinate))-float(value(selected[0],coordinate)))
    expected=before_distance
    if args.get("value_mm") is not None:
        expected=float(args["value_mm"])/1000
    placement=[float(args.get(f"place_{k}_mm",0))/1000 for k in "xyz"]
    if expected<0 or not all(math.isfinite(v) for v in [expected,*placement]):
        raise RuntimeError("Dimension values and placement must be finite; the distance cannot be negative.")
    name=sketch_name_for_object(doc,sketch)
    original_reference=persistent_reference_id(doc,sketch)
    require_selection(doc,{"sketch_points":indices})
    method=f"AddAlong{coordinate}Dimension"
    try:
        selection=flag_methods(doc.SelectionManager,"GetSelectedObjectCount2","GetSelectedObject6")
        selected_references=[persistent_reference_id(doc,selection.GetSelectedObject6(i,-1)) for i in range(1,selection.GetSelectedObjectCount2(-1)+1)]
        if selected_references!=references:
            return result(False,"Native selection did not retain the requested point references.",axis=axis,selected_references_confirmed=False)
        with dimension_dialog_suppressed(app):
            display=getattr(flag_methods(sketch_manager(doc),method),method)(*placement)
        if display is None:
            return result(False,"SOLIDWORKS did not create the projected 3D dimension.",axis=axis,projected_before_mm=before_distance*1000,
                          selected_references_confirmed=True,sketch_status=_sketch_status(doc))
        dimension=flag_methods(display,"GetDimension2").GetDimension2(0)
        if dimension is None:
            return result(False,"Display dimension created, but its native dimension is unavailable.",axis=axis)
        full_name=str(value(dimension,"FullName"))
        display_type=int(value(display,"Type2"))
        code=0
        if args.get("value_mm") is not None:
            code=int(flag_methods(dimension,"SetSystemValue3").SetSystemValue3(expected,2,empty_variant()))
    finally:
        clear_selection(doc)
    rebuilt=rebuild(doc)
    context=doc.SketchManager.ActiveSketch
    if context is None:
        reopened=edit_sketch({"sketch_name":name})
        context=doc.SketchManager.ActiveSketch
        restored=bool(reopened["ok"]) and context is not None and persistent_reference_id(doc,context)==original_reference
    else:
        restored=bool(value(context,"Is3D")) and persistent_reference_id(doc,context)==original_reference
    actual=float(value(dimension,"SystemValue"))
    after={persistent_reference_id(doc,p):p for p in sketch_point_objects(doc,name)}
    same_points=all(r in after for r in references)
    distance=abs(float(value(after[references[1]],coordinate))-float(value(after[references[0]],coordinate))) if same_points else None
    confirmed=rebuilt and restored and display_type==2 and code==0 and abs(actual-expected)<=1e-9 and distance is not None and abs(distance-expected)<=1e-5
    return result(confirmed,"Created and verified a projected 3D dimension." if confirmed else "Dimension created, but its value, geometry or edit context could not be confirmed.",
                  axis=axis,full_name=full_name,value_mm=actual*1000,projected_distance_mm=distance*1000 if distance is not None else None,
                  display_type=display_type,set_value_status=code,rebuild_ok=rebuilt,edit_context_restored=restored,point_references_confirmed=same_points)


@tool(
    "set_dimension",
    "Change an existing dimension by its full name, for example 'D1@草图1'. Use list_dimensions to "
    "find names. Linear values are millimetres, angular values are degrees.",
    {
        "full_name": {"type": "string"},
        "value_mm": {"type": "number"},
        "value_deg": {"type": "number"},
    },
    ["full_name"],
)
def set_dimension(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    name = str(args["full_name"])
    dimension = doc.Parameter(name)
    if dimension is None:
        return result(False, f"No dimension named '{name}' exists. Call list_dimensions to see valid names.")
    if args.get("value_mm") is not None:
        target, applied = to_m(args["value_mm"]), {"value_mm": float(args["value_mm"])}
    elif args.get("value_deg") is not None:
        target, applied = to_rad(args["value_deg"]), {"value_deg": float(args["value_deg"])}
    else:
        return result(False, "Pass value_mm or value_deg.")

    flag_methods(dimension, "SetSystemValue3")
    code = int(dimension.SetSystemValue3(target, 2, empty_variant()))
    actual = float(safe(dimension, "SystemValue", target) or 0.0)
    if abs(actual - target) > 1e-9:
        return result(
            False,
            f"SOLIDWORKS did not accept the new value for {name}; the sketch may be over defined.",
            status=code,
            **applied,
        )
    if doc.SketchManager.ActiveSketch is not None:
        return result(True, f"Set {name} in the open sketch.", **applied)
    rebuilt = rebuild(doc)
    return result(rebuilt, f"Set {name}." if rebuilt else f"Set {name}, but the rebuild reported a problem.", **applied)


@tool(
    "list_dimensions",
    "Read-only: list native driving/driven display dimensions in the document, or only those of one feature/sketch. Reports driven_state 0=unknown, 1=driven, 2=driving, "
    "with the full names that set_dimension takes.",
    {"feature_name": {"type": "string", "description": "Restrict to one feature or sketch."}},
)
def list_dimensions(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    target = args.get("feature_name")
    features = [find_feature(doc, str(target))] if target else iter_feature_objects(doc)
    if target and features[0] is None:
        return result(False, f"No feature named '{target}' exists in this document.")

    dimensions: list[dict[str, Any]] = []
    for feature in features:
        if feature is None:
            continue
        owner = str(feature_property(feature, "Name", ""))
        try:
            display = value(feature, "GetFirstDisplayDimension")
        except Exception:
            continue
        while display is not None:
            try:
                dimension = flag_methods(display, 'GetDimension2').GetDimension2(0)
                full_name = str(safe(dimension, "FullName", ""))
                system_value = float(safe(dimension, "SystemValue", 0.0) or 0.0)
                entry: dict[str, Any] = {
                    "owner": owner,
                    "full_name": full_name,
                    "name": str(safe(dimension, "Name", "")),
                    "driven": bool(safe(dimension, "DrivenState", 0) == 1),
                    "driven_state": int(safe(dimension, "DrivenState", 0)),
                }
                # Dimension type 3 is angular in swDimensionType_e; everything
                # else we surface here is a length.
                if int(safe(dimension, "GetType", 0) or 0) == 3:
                    entry["value_deg"] = round(to_deg(system_value), 6)
                else:
                    entry["value_mm"] = round(to_mm(system_value), 6)
                dimensions.append(entry)
            except Exception:
                logger.info("Skipped an unreadable display dimension on %s", owner)
            display = _next_display(feature, display)
    return result(True, f"Read {len(dimensions)} dimensions.", dimensions=dimensions)


def _next_display(feature: Any, current: Any) -> Any:
    try:
        return flag_methods(feature, 'GetNextDisplayDimension').GetNextDisplayDimension(current)
    except Exception:
        return None


@tool(
    "get_sketch_status",
    "Read-only: report whether the open sketch is under defined, fully defined, or over defined.",
    {},
)
def get_sketch_status(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    status = _sketch_status(doc)
    return result(True, f"The open sketch is {status}.", sketch_status=status)
