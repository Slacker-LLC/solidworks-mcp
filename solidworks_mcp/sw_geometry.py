# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for details.

"""Direct editing, reference points, coordinate systems and native XYZ curves."""

import math

from .sw_core import (
    SELECTION_SCHEMA, active_document, as_list, document_type, double_array,
    clear_selection, exit_active_sketch, extension, feature_manager, feature_property,
    find_feature, flag_methods, iter_feature_objects, nothing, require_part,
    require_selection, result, to_m, to_mm, to_rad, to_deg, tool, value,
)
from .sw_feature import _feature_created_after, _feature_names
from .sw_multibody import _finish

NAME = {"type": "string"}
VECTOR = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
POINTS = {"type": "array", "items": VECTOR, "minItems": 2}
DELETE_OPTIONS = {"delete": 0, "patch": 1, "fill": 2, "fill_tangent": 3}
POINT_MODES = {"arc_center": 3, "edge_midpoint": 2, "face_center": 4, "along_distance": 2, "along_percentage": 2, "evenly": 2,
               "projection": 5, "intersection": 6, "sketch_point": 7}


def _vector(raw, label):
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise RuntimeError(f"{label} requires exactly three finite coordinates.")
    values = [float(number) for number in raw]
    if not all(math.isfinite(number) for number in values):
        raise RuntimeError(f"{label} requires exactly three finite coordinates.")
    return values


def _model():
    _, doc = active_document()
    if document_type(doc) not in (1, 2):
        raise RuntimeError("Reference geometry requires a part or assembly.")
    exit_active_sketch(doc)
    return doc


def _faces_only(selection):
    if not selection or set(selection) - {"faces", "feature_faces", "points"}:
        raise RuntimeError("Select faces from list_faces, feature_faces, or FACE point picks.")
    if any(p.get("type", "FACE") != "FACE" for p in selection.get("points", [])):
        raise RuntimeError("Only FACE point picks are accepted for direct editing.")


@tool("move_faces", "Create a native Move Face feature: offset faces, translate by XYZ mm, or rotate by XYZ degrees about an origin in mm. Re-list topology afterwards.",
      {"selection": SELECTION_SCHEMA, "mode": {"type": "string", "enum": ["offset", "translate", "rotate"]},
       "distance_mm": {"type": "number"}, "translation_mm": VECTOR, "rotation_deg": VECTOR, "origin_mm": VECTOR,
       "reverse": {"type": "boolean", "default": False}, "name": NAME}, ["selection", "mode"])
def move_faces(args):
    _faces_only(args["selection"])
    mode = args["mode"]
    translation, rotation = nothing(), nothing()
    distance = 0.0
    if mode == "offset":
        distance = float(args["distance_mm"])
        if not math.isfinite(distance) or distance == 0:
            raise RuntimeError("Offset distance must be finite and nonzero.")
    elif mode == "translate":
        delta = _vector(args["translation_mm"], "translation_mm")
        if not any(delta):
            raise RuntimeError("Translation must move the faces.")
        translation = double_array([to_m(n) for n in delta])
    elif mode == "rotate":
        angles = _vector(args["rotation_deg"], "rotation_deg")
        origin = _vector(args.get("origin_mm", [0, 0, 0]), "origin_mm")
        if not any(angles):
            raise RuntimeError("Rotation must move the faces.")
        rotation = double_array([to_m(n) for n in origin] + [to_rad(n) for n in angles])
    else:
        raise RuntimeError("Unknown Move Face mode.")
    _, doc = require_part()
    exit_active_sketch(doc)
    require_selection(doc, args["selection"], mark=1)
    manager = flag_methods(feature_manager(doc), "InsertMoveFace3")
    feature = manager.InsertMoveFace3({"offset": 0, "translate": 1, "rotate": 2}[mode], bool(args.get("reverse", False)),
                                     0.0, to_m(distance), translation, rotation, 0, 0.0)
    payload = _finish(doc, feature, args, "face movement")
    if payload["ok"]:
        definition = value(feature, "GetDefinition")
        payload["data"]["move_type"] = int(value(definition, "MoveType"))
        payload["data"]["distance_mm"] = to_mm(float(value(definition, "Distance")))
        payload["data"]["translation_mm"] = [to_mm(n) for n in as_list(value(definition, "TriadTranslationParameters"))]
        parameters = as_list(value(definition, "TriadRotationParameters"))
        payload["data"]["rotation_origin_mm"] = [to_mm(n) for n in parameters[:3]]
        payload["data"]["rotation_deg"] = [to_deg(n) for n in parameters[3:6]]
    return payload


@tool("delete_faces", "Create a native Delete Face feature, optionally patching or filling the removed faces. delete may convert a solid into a surface body.",
      {"selection": SELECTION_SCHEMA, "mode": {"type": "string", "enum": list(DELETE_OPTIONS), "default": "delete"}, "name": NAME}, ["selection"])
def delete_faces(args):
    _faces_only(args["selection"])
    mode = args.get("mode", "delete")
    if mode not in DELETE_OPTIONS:
        raise RuntimeError("Unknown Delete Face mode.")
    _, doc = require_part()
    exit_active_sketch(doc)
    require_selection(doc, args["selection"])
    before = _feature_names(doc)
    accepted = bool(flag_methods(extension(doc), "InsertDeleteFace").InsertDeleteFace(DELETE_OPTIONS[mode]))
    payload = _finish(doc, _feature_created_after(doc, before), args, "face deletion")
    if not accepted:
        payload.update(ok=False, message="Native Delete Face was not accepted.")
    return payload


@tool("replace_faces", "Replace selected model faces with selected replacement surfaces. faces_to_replace uses face indices; replacement can use surface_bodies or faces. Re-list topology afterwards.",
      {"faces_to_replace": SELECTION_SCHEMA, "replacement": SELECTION_SCHEMA, "name": NAME}, ["faces_to_replace", "replacement"])
def replace_faces(args):
    _faces_only(args["faces_to_replace"])
    if not args["replacement"] or set(args["replacement"]) - {"surface_bodies", "faces", "features", "points"}:
        raise RuntimeError("Select replacement surfaces or faces.")
    _, doc = require_part()
    exit_active_sketch(doc)
    require_selection(doc, args["faces_to_replace"], mark=1)
    require_selection(doc, args["replacement"], mark=2, append=True)
    before = _feature_names(doc)
    flag_methods(doc, "InsertFeatureReplaceFace").InsertFeatureReplaceFace()
    return _finish(doc, _feature_created_after(doc, before), args, "face replacement")


def _point_info(feature):
    point = value(value(feature, "GetSpecificFeature2"), "GetRefPoint")
    xyz = list(value(point, "ArrayData"))
    return {"name": str(value(feature, "Name")), "position_mm": [to_mm(n) for n in xyz[:3]]}


@tool("list_reference_points", "Read-only: list native reference points and their model coordinates in mm.")
def list_reference_points(args):
    _, doc = active_document()
    return result(True, "Read reference points.", points=[_point_info(f) for f in iter_feature_objects(doc)
                   if str(feature_property(f, "GetTypeName2", "")) == "RefPoint"])


@tool("create_reference_points", "Create native reference points at arc centers, edge midpoints, face centers, along a curve, by projection onto a model face/intersection, or on a sketch point. Percentage uses 0..100; distance is mm. Along-curve modes support count points. Reference-plane projection remains unavailable in this implementation.",
      {"selection": SELECTION_SCHEMA, "mode": {"type": "string", "enum": list(POINT_MODES)},
       "distance_mm": {"type": "number", "minimum": 0}, "percentage": {"type": "number", "minimum": 0, "maximum": 100},
       "count": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 1}, "name": NAME}, ["selection", "mode"])
def create_reference_points(args):
    mode = args["mode"]
    if mode not in POINT_MODES:
        raise RuntimeError("Unknown reference point mode.")
    if mode == "projection" and args["selection"].get("planes"):
        raise RuntimeError("Reference-plane projection remains a coverage gap; native model-face projection is supported.")
    count = args.get("count", 1)
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 1000 or (mode not in {"evenly", "along_distance", "along_percentage"} and count != 1):
        raise RuntimeError("count must be 1, except along-curve modes allow 1..1000 points.")
    distance = float(args.get("distance_mm", 0))
    percent = float(args.get("percentage", 50))
    if not math.isfinite(distance) or distance < 0 or not math.isfinite(percent) or not 0 <= percent <= 100:
        raise RuntimeError("Point distance must be finite/nonnegative; percentage must be 0..100.")
    doc = _model()
    require_selection(doc, args["selection"])
    manager = flag_methods(feature_manager(doc), "InsertReferencePoint")
    created = as_list(manager.InsertReferencePoint(POINT_MODES[mode], {"along_distance": 0, "along_percentage": 1, "edge_midpoint": 1, "evenly": 2}.get(mode, 0),
                                                   50.0 if mode == "edge_midpoint" else percent if mode == "along_percentage" else to_m(distance), count))
    if not created:
        return result(False, "No reference points were created.")
    payloads = [_finish(doc, f, {"name": f"{args['name']}_{i+1}" if args.get("name") and count > 1 else args.get("name")}, "reference point")
                for i, f in enumerate(created)]
    points = [_point_info(f) for f in created]
    return result(len(points) == count and all(p["ok"] for p in payloads), "Read created reference points.", points=points, features=payloads)


def _coordinate_info(feature):
    transform = value(value(feature, "GetDefinition"), "Transform")
    matrix = list(value(transform, "ArrayData"))
    return {"name": str(value(feature, "Name")), "origin_mm": [to_mm(n) for n in matrix[9:12]],
            "rotation_matrix": matrix[:9], "transform": matrix}


@tool("list_coordinate_systems", "Read-only: list native coordinate systems with origin in mm and rotation matrices.")
def list_coordinate_systems(args):
    _, doc = active_document()
    return result(True, "Read coordinate systems.", coordinate_systems=[_coordinate_info(f) for f in iter_feature_objects(doc)
                  if str(feature_property(f, "GetTypeName2", "")) == "CoordSys"])


@tool("create_coordinate_system", "Create a coordinate system from XYZ location in mm and XYZ orientation in degrees relative to the global system. Requires SOLIDWORKS 2022+ numerical-coordinate API.",
      {"origin_mm": VECTOR, "rotation_deg": VECTOR, "name": NAME}, ["origin_mm"])
def create_coordinate_system(args):
    origin = _vector(args["origin_mm"], "origin_mm")
    rotation = _vector(args.get("rotation_deg", [0, 0, 0]), "rotation_deg")
    doc = _model()
    clear_selection(doc)
    manager = flag_methods(feature_manager(doc), "CreateCoordinateSystemUsingNumericalValues")
    feature = manager.CreateCoordinateSystemUsingNumericalValues(True, *[to_m(n) for n in origin], True, *[to_rad(n) for n in rotation])
    payload = _finish(doc, feature, args, "coordinate system")
    if payload["ok"]:
        payload["data"]["coordinate_system"] = _coordinate_info(feature)
    return payload


def _curve_points(raw):
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        raise RuntimeError("Provide at least two distinct curve points.")
    points = [_vector(point, "points_mm") for point in raw]
    if any(points[i] == points[i - 1] for i in range(1, len(points))):
        raise RuntimeError("Consecutive curve points must be distinct.")
    return points


def _curve_feature(doc, name):
    feature = find_feature(doc, name)
    if feature is None or str(feature_property(feature, "GetTypeName2", "")) != "CurveInFile":
        raise RuntimeError("Specify an existing XYZ through-points curve feature.")
    return feature


def _curve_info(feature):
    definition = value(feature, "GetDefinition")
    flat = as_list(value(definition, "PointArray"))
    # Despite its name, GetPointCount returns the number of coordinate doubles.
    coordinate_count = int(value(definition, "GetPointCount"))
    if len(flat) != coordinate_count or coordinate_count < 6 or coordinate_count % 3:
        raise RuntimeError("Native curve point data has an unexpected shape.")
    return {"name": str(value(feature, "Name")), "point_count": coordinate_count // 3,
            "points_mm": [[to_mm(n) for n in flat[i:i+3]] for i in range(0, len(flat), 3)]}


@tool("get_curve_points", "Read-only: read actual ordered through-points of an XYZ reference curve feature in mm.", {"name": NAME}, ["name"])
def get_curve_points(args):
    _, doc = require_part()
    return result(True, "Read native curve points.", curve=_curve_info(_curve_feature(doc, args["name"])))


@tool("create_curve_through_points", "Create a native XYZ reference curve through ordered model-space points in mm without an intermediate file.",
      {"points_mm": POINTS, "name": NAME}, ["points_mm"])
def create_curve_through_points(args):
    points = _curve_points(args["points_mm"])
    _, doc = require_part()
    exit_active_sketch(doc)
    clear_selection(doc)
    before = _feature_names(doc)
    flag_methods(doc, "InsertCurveFileBegin", "InsertCurveFilePoint", "InsertCurveFileEnd")
    doc.InsertCurveFileBegin()
    accepted = True
    try:
        for point in points:
            accepted = bool(doc.InsertCurveFilePoint(*[to_m(n) for n in point])) and accepted
    finally:
        accepted = bool(doc.InsertCurveFileEnd()) and accepted
    feature = _feature_created_after(doc, before)
    payload = _finish(doc, feature, args, "XYZ through-points curve")
    if payload["ok"]:
        payload["data"]["curve"] = _curve_info(feature)
        actual = payload["data"]["curve"]["points_mm"]
        matches = len(actual) == len(points) and all(math.isclose(a, b, abs_tol=1e-6) for p, q in zip(actual, points) for a, b in zip(p, q))
        if not accepted or not matches:
            payload.update(ok=False, message="Native curve creation or point readback failed.")
    return payload


@tool("set_curve_points", "Replace the ordered points of an existing XYZ curve. Coordinates are model-space mm; dependent features rebuild.",
      {"name": NAME, "points_mm": POINTS}, ["name", "points_mm"])
def set_curve_points(args):
    points = _curve_points(args["points_mm"])
    _, doc = require_part()
    exit_active_sketch(doc)
    feature = _curve_feature(doc, args["name"])
    definition = value(feature, "GetDefinition")
    definition.PointArray = double_array([to_m(n) for point in points for n in point])
    accepted = bool(flag_methods(feature, "ModifyDefinition").ModifyDefinition(definition, doc, nothing()))
    payload = _finish(doc, feature, {}, "XYZ curve edit")
    actual = _curve_info(feature)
    payload["data"]["curve"] = actual
    if not accepted or len(actual["points_mm"]) != len(points) or not all(math.isclose(a, b, abs_tol=1e-6) for p, q in zip(actual["points_mm"], points) for a, b in zip(p, q)):
        payload.update(ok=False, message="Native curve edit or point readback failed.")
    return payload


@tool("composite_curve", "Create a native composite reference curve from connected edges, sketches or reference curves. Select an ordered connected chain.",
      {"selection": SELECTION_SCHEMA, "name": NAME}, ["selection"])
def composite_curve(args):
    _, doc = require_part()
    exit_active_sketch(doc)
    require_selection(doc, args["selection"], mark=1)
    before = _feature_names(doc)
    accepted = bool(flag_methods(doc, "InsertCompositeCurve").InsertCompositeCurve())
    payload = _finish(doc, _feature_created_after(doc, before), args, "composite curve")
    if not accepted:
        payload.update(ok=False, message="Native composite curve creation failed.")
    return payload
