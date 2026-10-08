# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

"""Native boundary surfaces, ordered curve groups and definition inspection."""
import math
from .sw_core import (
    SELECTION_SCHEMA, as_list, clear_selection, exit_active_sketch, feature_manager,
    find_feature, flag_methods, nothing, require_part, require_selection,
    result, safe, tool, value, call_versioned, BODY_SHEET, BODY_SOLID,
    get_bodies, iter_face_objects,
)
from .sw_feature import _feature_names, _feature_created_after
from .sw_multibody import _finish, _trim_body_info

TANGENCIES = {"none": 0, "normal_to_profile": 1, "tangent_to_face": 3, "curvature_to_face": 4, "default": 6}
INFLUENCES = {"to_next_curve": 0, "to_next_sharp": 16, "global": 32, "to_edge": 64, "linear": 144}
CURVE = {"type": "object", "properties": {
    "selection": SELECTION_SCHEMA,
    "tangency": {"type": "string", "enum": list(TANGENCIES), "default": "none"},
}, "required": ["selection"], "additionalProperties": False}
CURVES = {"type": "array", "items": CURVE}
INFLUENCE = {"type": "string", "enum": list(INFLUENCES), "default": "global"}


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _frame(surface, point, curvature=True):
    surface = flag_methods(surface, "GetClosestPointOn", "Evaluate")
    closest = surface.GetClosestPointOn(*point)
    order = 2 if curvature else 1
    values = surface.Evaluate(closest[3], closest[4], order, order)
    if curvature:
        du, duu, dv, duv, dvv = [list(values[i:i + 3]) for i in (3, 6, 9, 12, 18)]
    else:
        du, dv = list(values[3:6]), list(values[6:9])
        duu = duv = dvv = [0., 0., 0.]
    cross = [du[1] * dv[2] - du[2] * dv[1], du[2] * dv[0] - du[0] * dv[2], du[0] * dv[1] - du[1] * dv[0]]
    length = math.sqrt(_dot(cross, cross))
    if length <= 1e-20 or not all(math.isfinite(v) for v in values):
        raise RuntimeError("Cannot evaluate a regular surface frame for continuity verification.")
    normal = [v / length for v in cross]
    return {"du": du, "dv": dv, "normal": normal,
            "e": _dot(normal, duu), "f": _dot(normal, duv), "g": _dot(normal, dvv)}


def _curvature(frame, a, b, reference_normal):
    du, dv = frame["du"], frame["dv"]
    e, f, g = _dot(du, du), _dot(du, dv), _dot(dv, dv)
    det = e * g - f * f
    if det <= 1e-12 * e * g:
        raise RuntimeError("Degenerate parameterization in continuity verification.")
    def coefficients(t):
        u, v = _dot(du, t), _dot(dv, t)
        return ((g * u - f * v) / det, (e * v - f * u) / det)
    u1, v1 = coefficients(a)
    u2, v2 = coefficients(b)
    sign = 1 if _dot(frame["normal"], reference_normal) >= 0 else -1
    return sign * (u1 * u2 * frame["e"] + (u1 * v2 + v1 * u2) * frame["f"] + v1 * v2 * frame["g"]) / 1000


def _continuity_samples(doc, curve):
    if curve.get("tangency", "none") not in ("tangent_to_face", "curvature_to_face"):
        return []
    selection = curve["selection"]
    faces = []
    for key, kind in (("faces", BODY_SOLID), ("surface_faces", BODY_SHEET)):
        entries = iter_face_objects(doc, kind) if selection.get(key) else []
        faces.extend(entries[i][0] for i in selection.get(key, []))
    for index in selection.get("surface_bodies", []):
        faces.extend(as_list(value(get_bodies(doc, BODY_SHEET)[index], "GetFaces")))
    samples = []
    for face in faces:
        surface = value(face, "GetSurface")
        for edge in as_list(value(face, "GetEdges")):
            params = value(edge, "GetCurveParams3")
            edge_curve = flag_methods(value(edge, "GetCurve"), "Evaluate")
            for fraction in (.25, .5, .75):
                point = list(edge_curve.Evaluate(float(params.UMinValue) + fraction * (float(params.UMaxValue) - float(params.UMinValue)))[:3])
                samples.append({"point": point, "frame": _frame(surface, point, curve["tangency"] == "curvature_to_face")})
    return samples


def _verify_continuity(feature, references, require_curvature):
    if not references:
        return {"confirmed": False, "reason": "No explicit supporting face geometry was available."}
    faces = [flag_methods(face, "GetClosestPointOn") for face in as_list(value(feature, "GetFaces"))]
    maximum_angle, maximum_curvature, maximum_distance = 0., 0., 0.
    for reference in references:
        point = reference["point"]
        if not faces:
            return {"confirmed": False, "reason": "Created feature has no faces."}
        face, closest = min(((face, face.GetClosestPointOn(*point)) for face in faces), key=lambda item: math.dist(point, item[1][:3]))
        maximum_distance = max(maximum_distance, math.dist(point, closest[:3]) * 1000)
        target = _frame(value(face, "GetSurface"), closest[:3], require_curvature)
        source = reference["frame"]
        angle = math.degrees(math.acos(min(1., abs(_dot(source["normal"], target["normal"])))))
        maximum_angle = max(maximum_angle, angle)
        if require_curvature and angle <= .1:
            a = [v / math.sqrt(_dot(source["du"], source["du"])) for v in source["du"]]
            n = source["normal"]
            b = [n[1] * a[2] - n[2] * a[1], n[2] * a[0] - n[0] * a[2], n[0] * a[1] - n[1] * a[0]]
            for t1, t2 in ((a, a), (a, b), (b, b)):
                maximum_curvature = max(maximum_curvature, abs(_curvature(source, t1, t2, n) - _curvature(target, t1, t2, n)))
    return {"confirmed": maximum_distance <= .01 and maximum_angle <= .1 and (not require_curvature or maximum_curvature <= 1e-4),
            "sample_count": len(references), "maximum_gap_mm": maximum_distance, "maximum_normal_angle_deg": maximum_angle,
            "maximum_curvature_difference_per_mm": maximum_curvature if require_curvature and maximum_angle <= .1 else None}


def _boundary_arguments(args):
    groups = [args["direction1"], args.get("direction2", [])]
    if not groups[0] or sum(len(group) for group in groups) < 2:
        raise RuntimeError("A boundary needs direction1 and at least two curves in total.")
    for group in groups:
        for curve in group:
            selection = curve.get("selection", {})
            if not selection or set(selection) - {"sketches", "sketch_segments", "sketch_name", "edges", "surface_edges", "faces", "surface_faces", "surface_bodies"}:
                raise RuntimeError("Each boundary curve must select one sketch, segment, model edge, face or surface body.")
            if curve.get("tangency", "none") not in TANGENCIES:
                raise RuntimeError("Invalid boundary curve tangency.")
    influences = [args.get(f"direction{d + 1}_influence", "global") for d in range(2)]
    if any(influence not in INFLUENCES for influence in influences):
        raise RuntimeError("Invalid boundary direction influence.")
    return groups, influences


def _boundary_info(doc, feature):
    if feature is None or safe(feature, "GetTypeName2") != "NetBlend":
        raise RuntimeError("Specify an existing native boundary feature.")
    data = flag_methods(value(feature, "GetDefinition"), "AccessSelections", "GetCurvesCount",
                        "GetGuideTangencyType", "GetDraftAngle", "GetTangentLength")
    if not data.AccessSelections(doc, nothing()):
        raise RuntimeError("Cannot access boundary feature selections.")
    try:
        directions = []
        for d in range(2):
            count = int(data.GetCurvesCount(d))
            refs = as_list(value(data, f"D{d + 1}Curves"))
            if count != len(refs):
                raise RuntimeError("Native boundary curve count and references are inconsistent.")
            curves = []
            for i in range(count):
                tangency = int(data.GetGuideTangencyType(d, i))
                curve = {"index": i, "tangency": tangency}
                if tangency not in (0, 6):
                    curve.update(draft_angle_deg=math.degrees(float(data.GetDraftAngle(d, i))),
                                 tangent_handle_value=float(data.GetTangentLength(d, i)))
                curves.append(curve)
            directions.append({"curve_count": count, "influence": int(value(data, f"D{d + 1}CurveInfluence")), "curves": curves})
        info = {"directions": directions, "trim_direction1": bool(value(data, "TrimByD1")),
                "merge_result": bool(value(data, "MergeResult")),
                "merge_tangent_faces": bool(value(data, "MergeTangentFaces"))}
    finally:
        value(data, "ReleaseSelectionAccess")
    faces = as_list(value(feature, "GetFaces"))
    bodies = {}
    for face in faces:
        body = value(face, "GetBody")
        label = str(value(body, "Name"))
        if label not in bodies:
            kind = int(value(body, "GetType"))
            geometry = {**_trim_body_info(body), "name": label, "body_type": kind}
            if kind == 0:
                geometry["volume_mm3"] = float(flag_methods(body, "GetMassProperties").GetMassProperties(1.)[3]) * 1e9
            bodies[label] = geometry
    info.update(feature_face_count=len(faces), feature_face_area_mm2=sum(float(value(face, "GetArea")) for face in faces) * 1e6,
                bodies=list(bodies.values()))
    return info


@tool("get_boundary_feature_data", "Read native boundary-feature curve groups, influence values, tangencies, trim and merge state, owned faces and affected bodies. Area is mm2, volume mm3. tangent_handle_value is the native control value, not a model distance.",
      {"name": {"type": "string", "minLength": 1}}, ["name"])
def get_boundary_feature_data(args):
    _, doc = require_part()
    return result(True, "Read boundary feature definition and geometry.", boundary=_boundary_info(doc, find_feature(doc, args["name"])))


@tool("boundary_surface", "Create a native boundary surface from ordered curves in one or two directions. Each curve selects exactly one entity. Selection marks include both direction and curve order. create_solid=true requests the native solid-forming option (requires InsertNetBlend2). Native curve counts, influence, tangency, trim/merge state and actual body types are checked. Centerlines, direction vectors and arbitrary per-curve handle edits are not implemented yet.",
      {"direction1": {**CURVES, "minItems": 1}, "direction2": CURVES,
       "direction1_influence": INFLUENCE, "direction2_influence": INFLUENCE,
       "trim_direction1": {"type": "boolean", "default": False}, "trim_direction2": {"type": "boolean", "default": False},
       "close_direction1": {"type": "boolean", "default": False}, "close_direction2": {"type": "boolean", "default": False},
       "merge_result": {"type": "boolean", "default": False}, "create_solid": {"type": "boolean", "default": False},
       "force_non_rational": {"type": "boolean", "default": True}, "name": {"type": "string"}}, ["direction1"])
def boundary_surface(args):
    groups, influences = _boundary_arguments(args)
    _, doc = require_part()
    exit_active_sketch(doc)
    clear_selection(doc)
    fm = flag_methods(feature_manager(doc), "SetNetBlendCurveData", "SetNetBlendDirectionData", "InsertNetBlend2", "InsertNetBlend")
    create_solid = bool(args.get("create_solid", False))
    if create_solid and not hasattr(fm, "InsertNetBlend2"):
        raise RuntimeError("Solid-forming boundary surfaces require InsertNetBlend2; the older method cannot preserve this option.")
    before = _feature_names(doc)
    try:
        continuity = {(d, i): _continuity_samples(doc, curve) for d, group in enumerate(groups) for i, curve in enumerate(group)
                      if curve.get("tangency", "none") in ("tangent_to_face", "curvature_to_face")}
        # Complete both selection groups before configuring any curve conditions.
        for d, group in enumerate(groups):
            for i, curve in enumerate(group):
                if require_selection(doc, curve["selection"], mark=8192 * (i + 1) + d + 1, append=True) != 1:
                    raise RuntimeError("Each boundary curve must resolve to exactly one entity.")
        for d, group in enumerate(groups):
            for i, curve in enumerate(group):
                fm.SetNetBlendCurveData(d, i, TANGENCIES[curve.get("tangency", "none")], 0., 1., True)
            fm.SetNetBlendDirectionData(d, INFLUENCES[influences[d]], int(bool(args.get(f"trim_direction{d + 1}", False))),
                                       bool(args.get(f"close_direction{d + 1}", False)), False)
        arguments = (2, len(groups[0]), len(groups[1]), False, .0001, False, bool(args.get("merge_result", False)),
                     False, True, False, -1., -1., False, -1, False, False, -1., False, -1., bool(args.get("force_non_rational", True)))
        feature = call_versioned(fm, ("InsertNetBlend2", (*arguments, create_solid)), ("InsertNetBlend", arguments))
        if feature is None:
            feature = _feature_created_after(doc, before)
        payload = _finish(doc, feature, args, "boundary surface")
        if payload["ok"]:
            try:
                info = _boundary_info(doc, feature)
                payload["data"]["boundary"] = info
                issues = []
                for d, group in enumerate(groups):
                    actual = info["directions"][d]
                    if actual["curve_count"] != len(group) or actual["influence"] != INFLUENCES[influences[d]]:
                        issues.append(f"direction {d + 1} curves or influence")
                    for i, curve in enumerate(group):
                        requested = TANGENCIES[curve.get("tangency", "none")]
                        if i >= len(actual["curves"]) or (requested != 6 and actual["curves"][i]["tangency"] != requested):
                            issues.append(f"direction {d + 1} curve {i} tangency")
                        if (d, i) in continuity:
                            check = _verify_continuity(feature, continuity[d, i], curve["tangency"] == "curvature_to_face")
                            if i < len(actual["curves"]):
                                actual["curves"][i]["continuity"] = check
                            if not check["confirmed"]:
                                issues.append(f"direction {d + 1} curve {i} geometric continuity unconfirmed")
                if info["trim_direction1"] != bool(args.get("trim_direction1", False)) or info["merge_result"] != bool(args.get("merge_result", False)):
                    issues.append("trim or merge state")
                expected_type = 0 if create_solid else 1
                if not info["bodies"] or any(body["body_type"] != expected_type for body in info["bodies"]):
                    issues.append("resulting body type")
                if issues:
                    payload.update(ok=False, message="Boundary feature differs from requested settings: " + ", ".join(issues))
            except Exception as exc:
                payload.update(ok=False, message=f"Created boundary feature but could not verify its native result: {exc}")
        return payload
    finally:
        clear_selection(doc)
