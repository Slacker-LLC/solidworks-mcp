# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

"""Native dome creation, geometry inspection and definition edits."""
import math
from .sw_core import (
    SELECTION_SCHEMA, apply_transform, as_list, clear_selection, dispatch_array,
    exit_active_sketch, find_feature, flag_methods, nothing, rebuild, require_part,
    require_selection, result, safe, tool, value, whats_wrong,
)
from .sw_feature import _feature_names, _feature_created_after
from .sw_multibody import _finish
from .sw_wrap import _geometry, _indices, _reference_id, _target_details

HEIGHT = {"type": "number", "exclusiveMinimum": 0}
FACES = {"type": "array", "items": {"type": "integer", "minimum": 0}, "minItems": 1, "uniqueItems": True}
CONSTRAINT = {"type": "object", "properties": {k: SELECTION_SCHEMA["properties"][k] for k in ("sketch_name", "sketch_points")},
              "required": ["sketch_name", "sketch_points"], "additionalProperties": False}


def _validate(args, creating=False):
    if creating or "height_mm" in args:
        height = float(args["height_mm"])
        if not math.isfinite(height) or height <= 0:
            raise RuntimeError("Dome height_mm must be finite and positive.")
    for k in ("reverse_direction", "elliptical", "clear_constraint", "clear_direction"):
        if k in args and not isinstance(args[k], bool):
            raise RuntimeError(f"{k} must be boolean.")
    if creating or "face_index" in args or "face_indices" in args:
        _indices(args)
    if "direction_edge_index" in args:
        index = args["direction_edge_index"]
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise RuntimeError("direction_edge_index must be a nonnegative solid-edge index.")
    if "constraint_selection" in args:
        spec = args["constraint_selection"]
        if (not isinstance(spec, dict) or set(spec) != {"sketch_name", "sketch_points"}
                or not isinstance(spec["sketch_name"], str) or not spec["sketch_name"].strip()
                or not isinstance(spec["sketch_points"], list) or len(spec["sketch_points"]) != 1
                or isinstance(spec["sketch_points"][0], bool) or not isinstance(spec["sketch_points"][0], int)
                or spec["sketch_points"][0] < 0):
            raise RuntimeError("Select exactly one point from a named sketch as the dome constraint.")
        if "height_mm" in args:
            raise RuntimeError("The constraint point controls height; do not supply height_mm with constraint_selection.")
    if "constraint_sketch_name" in args:
        name = args["constraint_sketch_name"]
        if not isinstance(name, str) or not name.strip():
            raise RuntimeError("constraint_sketch_name must name an existing sketch.")
        if "constraint_selection" in args or "height_mm" in args:
            raise RuntimeError("A whole-sketch constraint cannot be combined with a point constraint or height_mm.")
    if args.get("clear_constraint"):
        if "constraint_selection" in args or "constraint_sketch_name" in args:
            raise RuntimeError("Do not set and clear a dome constraint in the same edit.")
        if "height_mm" not in args:
            raise RuntimeError("Supply height_mm when clearing a dome constraint to restore height-driven geometry.")
    if args.get("clear_direction") and "direction_edge_index" in args:
        raise RuntimeError("Do not set and clear the dome direction in the same edit.")


def _selected(doc, spec):
    count = require_selection(doc, spec, mark=1)
    manager = flag_methods(value(doc, "SelectionManager"), "GetSelectedObject6")
    return [manager.GetSelectedObject6(i, 1) for i in range(1, count + 1)]


def _point_position(point):
    sketch = value(point, "GetSketch")
    inverse = value(value(sketch, "ModelToSketchTransform"), "Inverse")
    return apply_transform([float(value(point, k)) for k in ("X", "Y", "Z")], value(inverse, "ArrayData"))


def _constraint_samples(obj):
    if safe(obj, "GetSketch") is not None:
        return [_point_position(obj)], {"kind": "point", "sampling_complete": True}
    points = as_list(value(obj, "GetSketchPoints2"))
    # The native whole-sketch control uses explicit points, not the underlying
    # curves or their generated center/end points (swSketchPointType_Internal).
    samples = [_point_position(p) for p in points if int(value(p, "Type")) == 1]
    return samples, {"kind": "sketch", "sampling_complete": bool(samples), "user_point_count": len(samples),
                     "other_sketch_point_count": len(points) - len(samples)}


def _ellipsoid_queries(face, height, reverse, direction=None):
    edges = as_list(value(face, "GetEdges"))
    if len(edges) != 1:
        return None
    curve = value(edges[0], "GetCurve")
    normal = [float(c) for c in value(face, "Normal")]
    if direction is not None and abs(sum(a * b for a, b in zip(normal, direction))) < 1 - 1e-9:
        return None
    if bool(value(curve, "IsCircle")):
        params = value(curve, "CircleParams")
        center, major, minor = list(params[:3]), float(params[6]), float(params[6])
        seed = [1, 0, 0] if abs(normal[0]) < .9 else [0, 1, 0]
        a = [normal[1] * seed[2] - normal[2] * seed[1], normal[2] * seed[0] - normal[0] * seed[2], normal[0] * seed[1] - normal[1] * seed[0]]
        length = math.sqrt(sum(c * c for c in a)); a = [c / length for c in a]
        b = [normal[1] * a[2] - normal[2] * a[1], normal[2] * a[0] - normal[0] * a[2], normal[0] * a[1] - normal[1] * a[0]]
    elif bool(value(curve, "IsEllipse")):
        params = value(curve, "GetEllipseParams")
        center, major, minor = list(params[:3]), float(params[3]), float(params[7])
        a, b = list(params[4:7]), list(params[8:11])
    else:
        return None
    signed_height = height / 1000 * (-1 if reverse else 1)
    return [[center[i] + a[i] * major * rho * math.cos(angle) + b[i] * minor * rho * math.sin(angle)
             + (direction or normal)[i] * signed_height * math.sqrt(1 - rho * rho) for i in range(3)]
            for rho in (.25, .5, .75) for angle in [k * math.pi / 4 for k in range(8)]]


def _dome_info(doc, feature, expected=None):
    if feature is None or safe(feature, "GetTypeName2") != "Dome":
        raise RuntimeError("Specify an existing native dome feature.")
    data = flag_methods(value(feature, "GetDefinition"), "AccessSelections", "GetFaceCount")
    if not data.AccessSelections(doc, nothing()):
        raise RuntimeError("Cannot access dome selections.")
    probes, ellipsoid_queries = [], []
    try:
        faces = as_list(value(data, "Faces"))
        count = int(data.GetFaceCount())
        if len(faces) != count:
            raise RuntimeError("Native dome face count and references differ.")
        direction = value(data, "Direction")
        constraint = value(data, "ConstraintPointOrSketch")
        height, reverse = float(value(data, "Height")) * 1000, bool(value(data, "ReverseDir"))
        info = {"height_mm": height, "reverse_direction": reverse, "elliptical": bool(value(data, "Elliptical")),
                "input_face_count": count, "input_faces": [_target_details(f) for f in faces],
                "has_direction": direction is not None, "has_constraint": constraint is not None,
                "input_geometry": _geometry(doc)}
        direction_vector = None
        if direction is not None:
            params = safe(safe(direction, "GetCurve"), "LineParams")
            if params is not None and len(params) >= 6:
                vector = [float(c) for c in params[3:6]]
                length = math.sqrt(sum(c * c for c in vector))
                if length > 0 and math.isfinite(length):
                    direction_vector = [c / length for c in vector]
            info["direction_vector"] = direction_vector
            info["direction_geometry_verifiable"] = direction_vector is not None
        volume_signs = []
        for face, meta in zip(faces, info["input_faces"]):
            if meta["surface_type"] == "plane":
                normal = [float(c) for c in value(face, "Normal")]
                box = meta["box_mm"]
                center = [(box[i] + box[i + 3]) / 2000 for i in range(3)]
                vector = direction_vector or normal
                if direction_vector is not None:
                    alignment = sum(vector[i] * normal[i] for i in range(3)) * (-1 if reverse else 1)
                    volume_signs.append(1 if alignment > 1e-9 else -1 if alignment < -1e-9 else 0)
                probes.append((center, [center[i] + vector[i] * height / 1000 * (-1 if reverse else 1) for i in range(3)], normal, vector))
            if info["elliptical"] and constraint is None:
                ellipsoid_queries.append(_ellipsoid_queries(face, height, reverse, direction_vector) if meta["surface_type"] == "plane" else None)
        if direction is not None:
            info["expected_volume_sign"] = volume_signs[0] if volume_signs and all(s == volume_signs[0] for s in volume_signs) else None
        if expected:
            for key, obj in (("direction", direction), ("constraint", constraint)):
                if key in expected:
                    info[f"{key}_reference_confirmed"] = (obj is None if expected[key] is None else
                                                          obj is not None and bool(expected[key]) and _reference_id(doc, obj) == expected[key])
            if "faces" in expected:
                actual = [_reference_id(doc, f) for f in faces]
                info["face_references_confirmed"] = bool(actual) and len(actual) == len(expected["faces"]) and set(actual) == set(expected["faces"])
        constraint_queries, constraint_meta = [], None
        if constraint is not None:
            constraint_queries, constraint_meta = _constraint_samples(constraint)
            if constraint_meta["kind"] == "point":
                info["constraint_point_mm"] = [c * 1000 for c in constraint_queries[0]]
    finally:
        value(data, "ReleaseSelectionAccess")
    output = as_list(value(feature, "GetFaces"))
    info.update(_geometry(doc), feature_faces=[_target_details(f) for f in output])
    samples = []
    for center, query, normal, vector in probes:
        candidates = [list(flag_methods(f, "GetClosestPointOn").GetClosestPointOn(*query)[:3]) for f in output]
        if not candidates:
            continue
        closest = min(candidates, key=lambda q: sum((a - b) ** 2 for a, b in zip(q, query)))
        samples.append({"point_mm": [c * 1000 for c in closest],
                        "normal_displacement_mm": sum((closest[i] - center[i]) * normal[i] for i in range(3)) * 1000,
                        "direction_displacement_mm": sum((closest[i] - center[i]) * vector[i] for i in range(3)) * 1000,
                        "surface_gap_mm": math.dist(closest, query) * 1000})
    info["center_probes"] = samples
    if constraint_meta is not None:
        gaps = [min(math.dist(query, flag_methods(f, "GetClosestPointOn").GetClosestPointOn(*query)[:3]) * 1000
                    for f in output) for query in constraint_queries] if output else []
        maximum = max(gaps) if gaps else None
        info["constraint_surface_gap_mm"] = maximum
        info["constraint_check"] = {**constraint_meta, "sample_count": len(gaps),
                                    "samples_mm": [[c * 1000 for c in q] for q in constraint_queries],
                                    "maximum_surface_gap_mm": maximum,
                                    "confirmed": constraint_meta["sampling_complete"] and maximum is not None and maximum <= .01}
    if info["elliptical"] and not info["has_constraint"]:
        gaps = []
        supported = bool(ellipsoid_queries) and all(q is not None for q in ellipsoid_queries) and bool(output)
        if supported:
            for queries in ellipsoid_queries:
                for query in queries:
                    gaps.append(min(math.dist(query, flag_methods(f, "GetClosestPointOn").GetClosestPointOn(*query)[:3]) * 1000 for f in output))
        info["ellipsoid_check"] = {"confirmed": supported and max(gaps) <= .01,
                                   "maximum_surface_gap_mm": max(gaps) if gaps else None,
                                   "sample_count": len(gaps)}
    return info


def _geometry_confirmed(info):
    before = info["input_geometry"]["solid_volume_mm3"]
    delta = info["solid_volume_mm3"] - before
    tolerance = max(1e-6, abs(before) * 1e-9)
    if info.get("has_direction") and "expected_volume_sign" in info:
        expected_sign = info["expected_volume_sign"]
        signed = expected_sign is not None and expected_sign != 0 and delta * expected_sign > tolerance
    else:
        signed = delta < -tolerance if info["reverse_direction"] else delta > tolerance
    if info["has_constraint"]:
        # A constrained point drives the shape, and native Height becomes zero.
        height = (info.get("constraint_surface_gap_mm") is not None and info["constraint_surface_gap_mm"] <= .01
                  and info.get("constraint_check", {"confirmed": True})["confirmed"])
    else:
        sign = -1 if info["reverse_direction"] else 1
        directed = info.get("has_direction") and "direction_vector" in info
        height = bool(info["center_probes"]) and all(abs(p["direction_displacement_mm" if directed else "normal_displacement_mm"] - sign * info["height_mm"]) <= .01
                    and (not directed or p["surface_gap_mm"] <= .01) for p in info["center_probes"])
        height = height and info.get("direction_geometry_verifiable", True)
    return delta, signed and height and info.get("ellipsoid_check", {"confirmed": True})["confirmed"]


@tool("get_dome_data", "Read native dome height (mm), convex/concave and elliptical state, input faces, constraint/direction presence, native direction vector, input/current solid geometry and closest-point center probes. Native B-spline boxes are approximate and do not prove height. Point and whole point-sketch constraints report native height zero, model-space point positions and the maximum actual point-to-dome gap; generated curve centers/endpoints are not implicit constraint points.",
      {"name": {"type": "string", "minLength": 1}}, ["name"])
def get_dome_data(args):
    _, doc = require_part()
    return result(True, "Read dome definition and geometry.", dome=_dome_info(doc, find_feature(doc, args["name"])))


@tool("dome", "Create a native dome on selected solid faces using mark 1. Height is mm; reverse_direction creates a concave dome, elliptical requests a half ellipsoid. Use face_index or face_indices. Checks native face count, parameters, actual signed volume, planar-face center height and 24-point half-ellipsoid surface gaps for conic boundaries. Native creation can reject disconnected multiple faces; no fallback drops faces. More complex/non-planar height verification remains pending and returns unconfirmed with the feature retained.",
      {"face_index": {"type": "integer", "minimum": 0}, "face_indices": FACES, "height_mm": HEIGHT,
       "reverse_direction": {"type": "boolean", "default": False}, "elliptical": {"type": "boolean", "default": False},
       "name": {"type": "string"}}, ["height_mm"])
def dome(args):
    _validate(args, creating=True)
    _, doc = require_part()
    exit_active_sketch(doc)
    indices = _indices(args)
    before = _feature_names(doc)
    try:
        require_selection(doc, {"faces": indices}, mark=1)
        flag_methods(doc, "InsertDome").InsertDome(float(args["height_mm"]) / 1000, args.get("reverse_direction", False), args.get("elliptical", False))
        feature = _feature_created_after(doc, before)
        payload = _finish(doc, feature, args, "dome")
        if feature is None or not payload["ok"]:
            return payload
        info = _dome_info(doc, feature)
        delta, geometry = _geometry_confirmed(info)
        correct = (info["input_face_count"] == len(indices) and math.isclose(info["height_mm"], float(args["height_mm"]), abs_tol=1e-7)
                   and info["reverse_direction"] == args.get("reverse_direction", False) and info["elliptical"] == args.get("elliptical", False))
        payload["data"].update(dome=info, volume_change_mm3=delta, geometry_confirmed=geometry)
        if not correct or not geometry:
            payload.update(ok=False, message="Dome created, but native parameters or geometric effect could not be confirmed. Feature retained.")
        return payload
    finally:
        clear_selection(doc)


@tool("set_dome_parameters", "Modify dome height, convex/concave state, elliptical state, target faces, a constraint point/whole point sketch or direction edge. Constraints and height are mutually exclusive. Whole-sketch constraints require explicit user sketch points; each point is transformed to model space and checked against the output dome. Generated curve-center/end points are not implicit constraints. clear_constraint requires height_mm; clear_direction cannot accompany a new direction. Clearing uses typed native null and checks actual reference absence. Native clear requests may be ignored or rejected and direction edges may flatten geometry; incomplete edits return failure with the feature retained.",
      {"name": {"type": "string", "minLength": 1}, "height_mm": HEIGHT, "reverse_direction": {"type": "boolean"},
       "elliptical": {"type": "boolean"}, "face_index": {"type": "integer", "minimum": 0}, "face_indices": FACES,
       "constraint_selection": CONSTRAINT, "constraint_sketch_name": {"type": "string", "minLength": 1},
       "clear_constraint": {"type": "boolean"}, "clear_direction": {"type": "boolean"},
       "direction_edge_index": {"type": "integer", "minimum": 0}}, ["name"])
def set_dome_parameters(args):
    if not any(k in args for k in ("height_mm", "reverse_direction", "elliptical", "face_index", "face_indices", "constraint_selection", "constraint_sketch_name", "direction_edge_index")) and not any(args.get(k) for k in ("clear_constraint", "clear_direction")):
        raise RuntimeError("Specify at least one dome parameter to change.")
    _validate(args)
    _, doc = require_part()
    exit_active_sketch(doc)
    feature = find_feature(doc, args["name"])
    previous = _dome_info(doc, feature)
    references, expected = {}, {}
    try:
        if "face_index" in args or "face_indices" in args:
            faces = _selected(doc, {"faces": _indices(args)})
            references["Faces"] = dispatch_array(faces)
            expected["faces"] = [_reference_id(doc, f) for f in faces]
        if "constraint_selection" in args:
            point = _selected(doc, args["constraint_selection"])[0]
            references["ConstraintPointOrSketch"] = point
            expected["constraint"] = _reference_id(doc, point)
        if "constraint_sketch_name" in args:
            selected = find_feature(doc, args["constraint_sketch_name"])
            if safe(selected, "GetTypeName2") not in ("ProfileFeature", "3DProfileFeature"):
                raise RuntimeError("constraint_sketch_name must identify a native sketch feature.")
            sketch = value(selected, "GetSpecificFeature2")
            samples, _ = _constraint_samples(sketch)
            if not samples:
                raise RuntimeError("constraint_sketch_name requires at least one explicit user sketch point.")
            references["ConstraintPointOrSketch"] = sketch
            expected["constraint"] = _reference_id(doc, sketch)
        for key, member, control in (("clear_constraint", "ConstraintPointOrSketch", "constraint"),
                                      ("clear_direction", "Direction", "direction")):
            if args.get(key):
                if previous[f"has_{control}"]:
                    references[member] = nothing()
                expected[control] = None
        if "direction_edge_index" in args:
            edge = _selected(doc, {"edges": [args["direction_edge_index"]]})[0]
            if not bool(value(value(edge, "GetCurve"), "IsLine")):
                raise RuntimeError("The dome direction edge must be linear.")
            references["Direction"] = edge
            expected["direction"] = _reference_id(doc, edge)
        if any(not ref for refs in expected.values() if refs is not None for ref in (refs if isinstance(refs, list) else [refs])):
            raise RuntimeError("Cannot snapshot dome reference identities.")
    finally:
        clear_selection(doc)
    data = flag_methods(value(feature, "GetDefinition"), "AccessSelections")
    if not data.AccessSelections(doc, nothing()):
        return result(False, "Cannot access dome selections.")
    accepted, native_error = False, None
    try:
        for member, obj in references.items():
            try:
                setattr(data, member, obj)
            except Exception as exc:
                clearing = ((member == "ConstraintPointOrSketch" and args.get("clear_constraint")) or
                            (member == "Direction" and args.get("clear_direction")))
                if not clearing:
                    raise
                native_error = str(exc)
                break
        if native_error is None:
            for key, member in (("height_mm", "Height"), ("reverse_direction", "ReverseDir"), ("elliptical", "Elliptical")):
                if key in args:
                    setattr(data, member, float(args[key]) / 1000 if key == "height_mm" else args[key])
            accepted = bool(flag_methods(feature, "ModifyDefinition").ModifyDefinition(data, doc, nothing()))
    finally:
        if not accepted:
            value(data, "ReleaseSelectionAccess")
        clear_selection(doc)
    rebuild(doc)
    info = _dome_info(doc, feature, expected)
    delta, geometry = _geometry_confirmed(info)
    correct = all((math.isclose(info[k], float(args[k]), abs_tol=1e-7) if k == "height_mm" else info[k] == args[k])
                  for k in ("height_mm", "reverse_direction", "elliptical") if k in args)
    correct = correct and all(info["face_references_confirmed" if k == "faces" else f"{k}_reference_confirmed"] for k in expected)
    problems = whats_wrong(doc)
    return result(accepted and correct and geometry and not problems,
                  "Updated dome parameters and verified geometry." if accepted and correct and geometry and not problems else
                  "Dome modification or geometric effect could not be confirmed. Feature retained.",
                  feature=args["name"], dome=info, parameters_confirmed=correct, geometry_confirmed=geometry,
                  volume_change_from_input_mm3=delta, problems=problems, native_clear_error=native_error)
