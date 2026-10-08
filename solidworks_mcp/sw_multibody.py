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

"""Multibody editing, surface construction and helix curves."""

import math
from .sw_core import (
    BODY_SHEET, BODY_SOLID, SELECTION_SCHEMA, dispatch_array, integer_array, byref_long, byref_variant, clear_selection, exit_active_sketch, call_versioned,
    feature_manager, feature_result, flag_methods, get_bodies, nothing,
    rename_feature, require_part, require_selection, result,
    select_sketch_for_feature, to_m, to_mm, to_rad, tool, as_list, value, safe,
    byref_double, byref_dispatch, find_feature, _face_point, _surface_details, mm_point,
)
from .sw_feature import _feature_created_after, _feature_names

BODY_SELECTION = {"type": "object", "properties": {"bodies": {"type": "array", "items": {"type": "integer", "minimum": 0}, "minItems": 1, "uniqueItems": True}}, "required": ["bodies"], "additionalProperties": False}
NAME = {"type": "string"}
NUM = {"type": "number", "default": 0}

TRIM_PROPERTIES = {
    "surface_body_indices": {"type": "array", "items": {"type": "integer", "minimum": 0}, "minItems": 1, "uniqueItems": True},
    "mode": {"type": "string", "enum": ["standard", "mutual"], "default": "standard"},
    "trim_selection": SELECTION_SCHEMA,
    "split_system": {"type": "boolean", "default": True},
    "linear_extension": {"type": "boolean", "default": False},
    "remove_picked": {"type": "boolean", "default": False},
}


def _trim_body_info(body):
    faces = as_list(value(body, "GetFaces"))
    point = next((point for face in faces if (point := _face_point(face)) is not None), None)
    return {"area_mm2": sum(float(value(face, "GetArea")) for face in faces) * 1e6,
            "face_count": len(faces), "box_mm": [to_mm(v) for v in value(body, "GetBodyBox")],
            "selection_point_mm": mm_point(point) if point is not None else None}


def _prepare_surface_trim(doc, args):
    indices = args["surface_body_indices"]
    mode = args.get("mode", "standard")
    if mode not in ("standard", "mutual"):
        raise RuntimeError("mode must be standard or mutual.")
    bodies = get_bodies(doc, BODY_SHEET)
    if not indices or len(set(indices)) != len(indices) or any(isinstance(i, bool) or not isinstance(i, int) or i < 0 or i >= len(bodies) for i in indices):
        raise RuntimeError("Specify distinct surface body indices from list_surface_bodies.")
    selection = args.get("trim_selection", {})
    if mode == "mutual":
        if len(indices) < 2 or selection:
            raise RuntimeError("Mutual trimming needs at least two surface bodies and no separate trim_selection.")
        clear_selection(doc)
        ext = flag_methods(value(doc, "Extension"), "SelectByID2")
        for index in indices:
            # Re-resolve originals by name: Body2.Select2 can block after a mutual preview.
            point = _trim_body_info(bodies[index])["selection_point_mm"]
            if point is None or not ext.SelectByID2(str(value(bodies[index], "Name")), "SURFACEBODY", *[to_m(v) for v in point], True, 0, nothing(), 0):
                raise RuntimeError("Cannot select an original surface for mutual trimming.")
    else:
        if not selection or set(selection) - {"planes", "sketches", "surface_bodies", "surface_faces"}:
            raise RuntimeError("Standard trimming needs one plane, sketch, surface body or surface face as trim_selection.")
        if set(selection.get("surface_bodies", [])) & set(indices):
            raise RuntimeError("A trimming surface cannot also be a target surface.")
        if require_selection(doc, selection) != 1:
            raise RuntimeError("Standard trimming needs exactly one trimming tool.")
    fm = flag_methods(feature_manager(doc), "PreTrimSurface", "GetPreTrimmedBodies", "PostTrimSurface")
    if not fm.PreTrimSurface(mode == "mutual", bool(args.get("split_system", True)),
                             bool(args.get("linear_extension", False)), bool(args.get("remove_picked", False))):
        raise RuntimeError("SolidWorks rejected the surface trimming setup.")
    if safe(fm, "SolidForTrim") is not None:
        fm.SolidForTrim = False
    pieces = []
    for index in indices:
        regions = as_list(fm.GetPreTrimmedBodies(bodies[index]))
        if not regions:
            raise RuntimeError(f"No trim regions were generated for surface body {index}.")
        ordered = sorted(((body, _trim_body_info(body)) for body in regions),
                         key=lambda item: (item[1]["box_mm"], item[1]["area_mm2"]))
        for body, info in ordered:
            pieces.append((body, {**info, "index": len(pieces), "surface_body_index": index}))
    return fm, bodies, pieces


@tool("preview_surface_trim", "Generate temporary trim regions without creating a feature. Standard mode needs one trim_selection; mutual mode uses only surface_body_indices. Region indices are sorted by target order and geometric bounds. Regenerate the preview after changing geometry. Distances are mm and areas mm2.",
      TRIM_PROPERTIES, ["surface_body_indices"])
def preview_surface_trim(args):
    _, doc = require_part()
    exit_active_sketch(doc)
    try:
        _, _, pieces = _prepare_surface_trim(doc, args)
        return result(True, "Read temporary surface trimming regions.", regions=[info for _, info in pieces])
    finally:
        clear_selection(doc)


def _surface_trim_info(doc, feature):
    if feature is None or safe(feature, "GetTypeName2") != "TrimRefSurface":
        raise RuntimeError("Specify an existing surface trim feature.")
    definition = flag_methods(value(feature, "GetDefinition"), "AccessSelections")
    if not definition.AccessSelections(doc, nothing()):
        raise RuntimeError("Cannot access surface trim feature selections.")
    try:
        return {"type": int(value(definition, "GetType")),
                "trim_tool_count": int(value(definition, "GetTrimToolsCount")),
                "pieces_to_keep_count": int(value(definition, "GetPiecesToKeepCount"))}
    finally:
        value(definition, "ReleaseSelectionAccess")


@tool("get_surface_trim_data", "Read native type, trimming tool count and kept-piece count of an existing surface trim feature.",
      {"name": NAME}, ["name"])
def get_surface_trim_data(args):
    _, doc = require_part()
    return result(True, "Read surface trim feature data.", trim=_surface_trim_info(doc, find_feature(doc, args["name"])))


@tool("trim_surface", "Create a standard or mutual surface trim from preview region indices. remove_picked=false keeps selected regions; true removes them. Selection points are sent to native ISelectData, not only body pointers. Optional picked_points_mm overrides preview points in region_indices order. Rebuild, native definition and total remaining sheet area are verified; failed verification retains the created feature for inspection.",
      {**TRIM_PROPERTIES, "region_indices": {"type": "array", "items": {"type": "integer", "minimum": 0}, "minItems": 1, "uniqueItems": True},
       "picked_points_mm": {"type": "array", "items": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}},
       "knit": {"type": "boolean", "default": False}, "name": NAME}, ["surface_body_indices", "region_indices"])
def trim_surface(args):
    selected = args["region_indices"]
    if not selected or len(set(selected)) != len(selected) or any(isinstance(i, bool) or not isinstance(i, int) or i < 0 for i in selected):
        raise RuntimeError("Specify distinct non-negative region indices from preview_surface_trim.")
    points = args.get("picked_points_mm")
    if points is not None and (len(points) != len(selected) or any(len(p) != 3 or not all(math.isfinite(float(v)) for v in p) for p in points)):
        raise RuntimeError("picked_points_mm needs one finite XYZ point per selected region.")
    _, doc = require_part()
    exit_active_sketch(doc)
    try:
        fm, bodies, pieces = _prepare_surface_trim(doc, args)
        if any(i >= len(pieces) for i in selected):
            raise RuntimeError("A region index is out of range. Regenerate preview_surface_trim.")
        selected_set = set(selected)
        kept = [info for _, info in pieces if (info["index"] not in selected_set) == bool(args.get("remove_picked", False))]
        if not kept:
            raise RuntimeError("The trimming selection would remove every target region.")
        sm = flag_methods(value(doc, "SelectionManager"), "CreateSelectData")
        for order, index in enumerate(selected):
            body, info = pieces[index]
            point = points[order] if points is not None else info["selection_point_mm"]
            if point is None:
                raise RuntimeError("Cannot find a selection point for this region; provide picked_points_mm.")
            point_m = [to_m(v) for v in point]
            faces = as_list(value(body, "GetFaces"))
            if not any(math.dist(point_m, flag_methods(face, "GetClosestPointOn").GetClosestPointOn(*point_m)[:3]) < 1e-8 for face in faces):
                raise RuntimeError("The selected point does not lie on its requested trim region.")
            data = value(sm, "CreateSelectData")
            data.Mark = 0
            data.X, data.Y, data.Z = point_m
            if not flag_methods(body, "Select2").Select2(True, data):
                raise RuntimeError("Cannot select the requested temporary trim region.")
        expected_regions = kept + [_trim_body_info(body) for i, body in enumerate(bodies) if i not in args["surface_body_indices"]]
        expected_area = sum(info["area_mm2"] for info in expected_regions)
        expected_box = [min(info["box_mm"][i] for info in expected_regions) if i < 3 else max(info["box_mm"][i] for info in expected_regions) for i in range(6)]
        feature = fm.PostTrimSurface(bool(args.get("knit", False)))
        payload = _finish(doc, feature, args, "surface trim", regions=[info for _, info in pieces], kept_region_indices=[info["index"] for info in kept])
        if payload["ok"]:
            try:
                info = _surface_trim_info(doc, feature)
                actual_regions = [_trim_body_info(body) for body in get_bodies(doc, BODY_SHEET)]
                actual_area = sum(region["area_mm2"] for region in actual_regions)
                actual_box = [min(region["box_mm"][i] for region in actual_regions) if i < 3 else max(region["box_mm"][i] for region in actual_regions) for i in range(6)] if actual_regions else None
                payload["data"].update(trim=info, expected_sheet_area_mm2=expected_area, actual_sheet_area_mm2=actual_area,
                                      expected_sheet_box_mm=expected_box, actual_sheet_box_mm=actual_box,
                                      sheet_body_count=len(actual_regions), knit_requested=bool(args.get("knit", False)))
                if info["type"] != (1 if args.get("mode", "standard") == "mutual" else 0) or not math.isclose(actual_area, expected_area, rel_tol=1e-6, abs_tol=1e-5) or actual_box is None or not all(math.isclose(a, b, abs_tol=1e-5) for a, b in zip(actual_box, expected_box)):
                    payload.update(ok=False, message="Surface trim geometry or native type differs from the requested regions.")
                minimum_separate = len({region["surface_body_index"] for region in kept}) + len(bodies) - len(args["surface_body_indices"])
                if not args.get("knit", False) and len(actual_regions) < minimum_separate:
                    payload.update(ok=False, message="SolidWorks joined distinct source sheets despite knit=false; the created feature is retained for inspection.")
            except Exception as exc:
                payload.update(ok=False, message=f"Created surface trim feature but could not verify its native result: {exc}")
        return payload
    finally:
        clear_selection(doc)


def _positive(raw, label):
    number = float(raw)
    if not math.isfinite(number) or number <= 0:
        raise RuntimeError(f"{label} must be finite and positive.")
    return number


def _selected_bodies(doc, selection, minimum=1, mark=0):
    indices = selection.get("bodies", [])
    if len(indices) < minimum or len(set(indices)) != len(indices):
        raise RuntimeError(f"Select at least {minimum} distinct solid bodies from list_bodies.")
    if set(selection) != {"bodies"}:
        raise RuntimeError("This tool accepts only selection.bodies.")
    bodies = get_bodies(doc)
    if any(not isinstance(i, int) or i < 0 or i >= len(bodies) for i in indices):
        raise RuntimeError("Body index is out of range. Use list_bodies again.")
    require_selection(doc, selection, mark=mark)
    return [bodies[i] for i in indices]


def _finish(doc, feature, args, action, **data):
    rename_feature(feature, args.get("name"))
    return feature_result(doc, feature, action, **data)


def _mid_face_info(face):
    info = {"area_mm2": float(value(face, "GetArea")) * 1e6}
    point = _face_point(face)
    if point is not None:
        info["point_mm"] = mm_point(point)
    normal = safe(face, "Normal")
    if normal is not None:
        info["normal"] = [float(v) for v in normal[:3]]
    surface = safe(face, "GetSurface")
    if surface is not None:
        info.update(_surface_details(surface))
    return info


def _mid_surface_info(feature):
    if feature is None or safe(feature, "GetTypeName2") != "MidRefSurface":
        raise RuntimeError("Specify an existing midsurface feature.")
    mid = flag_methods(value(feature, "GetSpecificFeature2"), "GetFirstFacePair", "GetNextFacePair", "GetFirstFace", "GetNextFace")
    pair_count = int(value(mid, "GetFacePairCount"))
    face_count = int(value(mid, "GetFaceCount"))
    faces = as_list(value(mid, "GetFaces"))
    if pair_count <= 0 or face_count <= 0 or len(faces) != face_count:
        raise RuntimeError("Native midsurface topology is empty or inconsistent.")
    pairs = []
    for i in range(pair_count):
        thickness, partner = byref_double(), byref_dispatch()
        first = (mid.GetFirstFacePair if i == 0 else mid.GetNextFacePair)(thickness, partner)
        if first is None or partner.value is None or not math.isfinite(thickness.value) or thickness.value <= 0:
            raise RuntimeError("Native midsurface face-pair traversal is incomplete.")
        pairs.append({"index": i, "thickness_mm": to_mm(thickness.value),
                      "source_faces": [_mid_face_info(first), _mid_face_info(partner.value)]})
    neutral = []
    for i in range(face_count):
        first, partner, thickness = byref_dispatch(), byref_dispatch(), byref_double()
        face = (mid.GetFirstFace if i == 0 else mid.GetNextFace)(first, partner, thickness)
        if face is None or first.value is None or partner.value is None or thickness.value <= 0:
            raise RuntimeError("Native neutral-face traversal is incomplete.")
        info = _mid_face_info(face)
        info["thickness_mm"] = to_mm(thickness.value)
        info["measured_placement"] = _mid_placement(face, first.value, partner.value, thickness.value)
        neutral.append(info)
    return {"name": str(value(feature, "Name")), "face_pair_count": pair_count,
            "face_count": face_count, "sheet_count": int(value(mid, "GetNeutralSheetCount")),
            "area_mm2": sum(face["area_mm2"] for face in neutral), "face_pairs": pairs, "faces": neutral}


def _mid_placement(face, first, partner, thickness):
    point = _face_point(face)
    if point is None:
        return None
    try:
        distances = []
        for source in (first, partner):
            closest = flag_methods(source, "GetClosestPointOn").GetClosestPointOn(*point)
            distances.append(math.sqrt(sum((point[i] - closest[i]) ** 2 for i in range(3))))
        if not math.isclose(sum(distances), thickness, rel_tol=1e-5, abs_tol=1e-7):
            return None
        return (distances[1] - distances[0]) / thickness
    except Exception:
        return None


@tool("mid_surface", "Create an automatic midsurface in the active part using native face-pair detection. Placement -1..1 requests the neutral surface position; 0 is halfway. Nonzero placement is forwarded but returns failure if actual geometry cannot confirm it. Knit requests one sewn surface body. Reports actual face pairs, thicknesses, surface counts and geometry.",
      {"placement": {"type": "number", "minimum": -1, "maximum": 1, "default": 0},
       "knit": {"type": "boolean", "default": True}, "name": NAME})
def mid_surface(args):
    placement = float(args.get("placement", 0))
    if not math.isfinite(placement) or not -1 <= placement <= 1:
        raise RuntimeError("Midsurface placement must be finite and between -1 and 1.")
    _, doc = require_part()
    exit_active_sketch(doc)
    clear_selection(doc)
    before = _feature_names(doc)
    manager = flag_methods(feature_manager(doc), "InsertMidSurface")
    try:
        create = manager.InsertMidSurface
    except AttributeError:
        returned = flag_methods(doc, "InsertMidSurfaceExt").InsertMidSurfaceExt(placement, bool(args.get("knit", True)))
    else:
        # Body/document arguments apply to assembly context only; null in a part.
        returned = create(nothing(), nothing(), placement, bool(args.get("knit", True)))
    feature = _feature_created_after(doc, before) if returned is not None else None
    payload = _finish(doc, feature, args, "midsurface")
    if payload["ok"]:
        info = _mid_surface_info(feature)
        payload["data"]["midsurface"] = info
        payload["data"]["requested_placement"] = placement
        if any(face["measured_placement"] is None or not math.isclose(face["measured_placement"], placement, abs_tol=1e-5) for face in info["faces"]):
            payload.update(ok=False, message="Midsurface was created, but its actual geometry does not confirm the requested placement.")
        if args.get("knit", True) and info["sheet_count"] != 1:
            payload.update(ok=False, message="Midsurface was created, but native knitting did not produce one surface body.")
    return payload


@tool("get_mid_surface_data", "Read-only: inspect a named midsurface feature. Reports actual original face pairs and thicknesses in mm, neutral surface faces and total area in mm2; does not regenerate the feature.",
      {"name": NAME}, ["name"])
def get_mid_surface_data(args):
    _, doc = require_part()
    return result(True, "Read native midsurface geometry.", midsurface=_mid_surface_info(find_feature(doc, args["name"])))


@tool("scale_bodies", "Scale selected solid bodies uniformly or along X/Y/Z about their centroid or the model origin. Re-list topology afterwards.",
      {"selection": BODY_SELECTION, "factor": {"type": "number", "exclusiveMinimum": 0, "default": 1},
       "uniform": {"type": "boolean", "default": True}, "y_factor": {"type": "number", "exclusiveMinimum": 0, "default": 1},
       "z_factor": {"type": "number", "exclusiveMinimum": 0, "default": 1}, "about": {"type": "string", "enum": ["centroid", "origin"], "default": "centroid"}, "name": NAME}, ["selection"])
def scale_bodies(args):
    x = _positive(args.get("factor", 1), "factor")
    uniform = bool(args.get("uniform", True))
    y = x if uniform else _positive(args.get("y_factor", 1), "y_factor")
    z = x if uniform else _positive(args.get("z_factor", 1), "z_factor")
    about = {"centroid": 0, "origin": 1}[args.get("about", "centroid")]
    _, doc = require_part()
    _selected_bodies(doc, args["selection"])
    manager = flag_methods(feature_manager(doc), "InsertScale")
    return _finish(doc, manager.InsertScale(about, uniform, x, y, z), args, "body scale", factors=[x, y, z])


@tool("move_copy_bodies", "Translate OR rotate selected solid bodies, optionally copying them. Use separate calls for translation and rotation. Lengths in mm, rotations in degrees around the supplied point. Copies must be >=1 when copy=true.",
      {"selection": BODY_SELECTION, "x_mm": NUM, "y_mm": NUM, "z_mm": NUM,
       "rotation_x_deg": NUM, "rotation_y_deg": NUM, "rotation_z_deg": NUM,
       "rotation_point_x_mm": NUM, "rotation_point_y_mm": NUM, "rotation_point_z_mm": NUM,
       "copy": {"type": "boolean", "default": False}, "copies": {"type": "integer", "minimum": 1, "maximum": 100, "default": 1}, "name": NAME}, ["selection"])
def move_copy_bodies(args):
    copies = int(args.get("copies", 1))
    if not 1 <= copies <= 100:
        raise RuntimeError("copies must be between 1 and 100.")
    translations = [float(args.get(k, 0)) for k in ("x_mm", "y_mm", "z_mm")]
    angles = [float(args.get(k, 0)) for k in ("rotation_x_deg", "rotation_y_deg", "rotation_z_deg")]
    point = [float(args.get(k, 0)) for k in ("rotation_point_x_mm", "rotation_point_y_mm", "rotation_point_z_mm")]
    if not all(math.isfinite(v) for v in translations + angles + point):
        raise RuntimeError("Translations, rotation angles and rotation points must be finite.")
    if any(translations) and any(angles):
        raise RuntimeError("Use separate calls for translation and rotation; SOLIDWORKS ignores rotation when translation is also specified.")
    _, doc = require_part()
    selected = _selected_bodies(doc, args["selection"], mark=1)
    before = len(get_bodies(doc))
    manager = flag_methods(feature_manager(doc), "InsertMoveCopyBody2")
    feature = manager.InsertMoveCopyBody2(*(to_m(v) for v in translations), 0.0,
                                        *(to_m(v) for v in point), *(to_rad(v) for v in angles), bool(args.get("copy", False)), copies)
    if feature is not None and any(angles):
        # SW2026 maps the InsertMoveCopyBody2 angle arguments to different
        # axes than their typelib names. Set the definition's explicit XYZ
        # properties instead of relying on the positional angle mapping.
        definition = flag_methods(value(feature, "GetDefinition"), "AccessSelections", "ReleaseSelectionAccess")
        if not definition.AccessSelections(doc, nothing()):
            return result(False, "Cannot access move/copy definition to verify rotation.")
        modified = False
        try:
            definition.TransformX, definition.TransformY, definition.TransformZ = [to_rad(v) for v in angles]
            definition.RotationOriginX, definition.RotationOriginY, definition.RotationOriginZ = [to_m(v) for v in point]
            modified = bool(flag_methods(feature, "ModifyDefinition").ModifyDefinition(definition, doc, nothing()))
        finally:
            if not modified:
                value(definition, "ReleaseSelectionAccess")
        if not modified:
            return result(False, "SOLIDWORKS did not accept the requested rotation definition.")
        actual = value(feature, "GetDefinition")
        readback = [float(value(actual, name)) for name in ("TransformX", "TransformY", "TransformZ")]
        if not all(math.isclose(a, to_rad(b), abs_tol=1e-9) for a, b in zip(readback, angles)):
            return result(False, "Rotation definition readback differs from the requested axes.")
    payload = _finish(doc, feature, args, "body move/copy")
    after = len(get_bodies(doc))
    payload.setdefault("data", {}).update(body_count=after)
    expected = before + len(selected) * copies if args.get("copy", False) else before
    if payload["ok"] and after != expected:
        payload.update(ok=False, message="Body move/copy feature exists, but body count differs from the requested result.")
    return payload


@tool("combine_bodies", "Boolean add, subtract or intersect at least two solid bodies. For subtract, the first body is the main body and the rest are tools.",
      {"selection": BODY_SELECTION, "operation": {"type": "string", "enum": ["add", "subtract", "intersect"]}, "name": NAME}, ["selection", "operation"])
def combine_bodies(args):
    _, doc = require_part()
    bodies = _selected_bodies(doc, args["selection"], 2)
    operation = str(args["operation"])
    code = {"add": 15903, "subtract": 15902, "intersect": 15901}[operation]
    main = bodies[0] if operation == "subtract" else nothing()
    tools = bodies[1:] if operation == "subtract" else bodies
    manager = flag_methods(feature_manager(doc), "InsertCombineFeature")
    return _finish(doc, manager.InsertCombineFeature(code, main, dispatch_array(tools)), args, "body combination", operation=operation)


@tool("delete_bodies", "Create a Delete/Keep Body feature using solid body indices. keep_selected=true retains only selected bodies; otherwise deletes them.",
      {"selection": BODY_SELECTION, "keep_selected": {"type": "boolean", "default": False}, "name": NAME}, ["selection"])
def delete_bodies(args):
    _, doc = require_part()
    selected = _selected_bodies(doc, args["selection"])
    before = len(get_bodies(doc))
    keep = bool(args.get("keep_selected", False))
    manager = flag_methods(feature_manager(doc), "InsertDeleteBody2")
    payload = _finish(doc, manager.InsertDeleteBody2(keep), args, "delete/keep body")
    expected = len(selected) if keep else before - len(selected)
    if payload["ok"] and len(get_bodies(doc)) != expected:
        payload.update(ok=False, message="Delete/keep body count differs from the requested result.")
    return payload


@tool("surface_extrude", "Extrude an open or closed sketch into a surface body (no solid volume). Blind depth in mm; reverse flips direction.",
      {"depth_mm": {"type": "number", "exclusiveMinimum": 0}, "sketch_name": NAME, "reverse": {"type": "boolean", "default": False}, "name": NAME}, ["depth_mm"])
def surface_extrude(args):
    depth = to_m(_positive(args["depth_mm"], "depth_mm"))
    _, doc = require_part()
    sketch = select_sketch_for_feature(doc, args.get("sketch_name"))
    before = len(get_bodies(doc, BODY_SHEET))
    previous_features = _feature_names(doc)
    manager = flag_methods(feature_manager(doc), "FeatureExtruRefSurface3")
    manager.FeatureExtruRefSurface3(True, bool(args.get("reverse", False)), 0, 0.0,
        0, 0, depth, depth, False, False, False, False, 0.0, 0.0,
        False, False, False, False, False, False, False, False)
    feature = _feature_created_after(doc, previous_features)
    payload = _finish(doc, feature, args, "extruded surface", sketch=sketch)
    if payload["ok"] and len(get_bodies(doc, BODY_SHEET)) <= before:
        payload.update(ok=False, message="Extruded surface feature did not add a surface body.")
    return payload


@tool("list_surface_bodies", "Read-only: list surface bodies of the active part with names, face counts and areas in square millimetres. Solid-body indices are separate.")
def list_surface_bodies(args):
    _, doc = require_part()
    entries = []
    for index, body in enumerate(get_bodies(doc, BODY_SHEET)):
        faces = as_list(value(body, "GetFaces"))
        entries.append({"index": index, "name": str(value(body, "Name")), "face_count": len(faces),
                        "area_mm2": sum(float(value(face, "GetArea")) for face in faces) * 1e6})
    return result(True, "Read surface bodies.", surface_bodies=entries)


@tool("planar_surface", "Create a planar surface from a closed planar sketch. Produces a surface body rather than a solid.",
      {"sketch_name": NAME, "name": NAME})
def planar_surface(args):
    _, doc = require_part()
    sketch = select_sketch_for_feature(doc, args.get("sketch_name"))
    before = _feature_names(doc)
    count = len(get_bodies(doc, BODY_SHEET))
    flag_methods(doc, "InsertPlanarRefSurface").InsertPlanarRefSurface()
    feature = _feature_created_after(doc, before)
    payload = _finish(doc, feature, args, "planar surface", sketch=sketch)
    if payload["ok"] and len(get_bodies(doc, BODY_SHEET)) <= count:
        payload.update(ok=False, message="Planar surface feature did not add a surface body.")
    return payload


@tool("create_helix", "Create a constant-pitch helix from a sketch containing one circle. Defined by pitch in mm and revolutions; angles are degrees.",
      {"sketch_name": NAME, "pitch_mm": {"type": "number", "exclusiveMinimum": 0}, "revolutions": {"type": "number", "exclusiveMinimum": 0},
       "start_angle_deg": NUM, "clockwise": {"type": "boolean", "default": True}, "reverse": {"type": "boolean", "default": False}, "name": NAME}, ["pitch_mm", "revolutions"])
def create_helix(args):
    pitch = _positive(args["pitch_mm"], "pitch_mm")
    turns = _positive(args["revolutions"], "revolutions")
    _, doc = require_part()
    select_sketch_for_feature(doc, args.get("sketch_name"))
    before = _feature_names(doc)
    flag_methods(doc, "InsertHelix").InsertHelix(bool(args.get("reverse", False)), bool(args.get("clockwise", True)), False, False,
        0, to_m(pitch * turns), to_m(pitch), turns, 0.0, to_rad(args.get("start_angle_deg", 0)))
    feature = _feature_created_after(doc, before)
    payload = _finish(doc, feature, args, "helix")
    if payload["ok"]:
        definition = value(feature, "GetDefinition")
        actual_pitch = float(value(definition, "Pitch")) * 1000
        actual_turns = float(value(definition, "Revolution"))
        height = float(value(definition, "Height")) * 1000
        payload["data"].update(pitch_mm=actual_pitch, revolutions=actual_turns, height_mm=height)
        if not (math.isclose(actual_pitch, pitch, rel_tol=1e-6) and math.isclose(actual_turns, turns, rel_tol=1e-6)):
            payload.update(ok=False, message="Helix feature exists, but pitch/revolution readback differs.")
    return payload


@tool("offset_surface", "Create an offset surface from selected solid-model faces; distance=0 copies the selected faces. Distance is mm.",
      {"selection": SELECTION_SCHEMA, "distance_mm": {"type": "number", "minimum": 0}, "reverse": {"type": "boolean", "default": False}, "name": NAME}, ["selection", "distance_mm"])
def offset_surface(args):
    distance = float(args["distance_mm"])
    if not math.isfinite(distance) or distance < 0:
        raise RuntimeError("distance_mm must be finite and nonnegative.")
    _, doc = require_part()
    require_selection(doc, args["selection"])
    before = _feature_names(doc)
    sheets = len(get_bodies(doc, BODY_SHEET))
    flag_methods(doc, "InsertOffsetSurface").InsertOffsetSurface(to_m(distance), bool(args.get("reverse", False)))
    payload = _finish(doc, _feature_created_after(doc, before), args, "offset surface")
    if payload["ok"] and len(get_bodies(doc, BODY_SHEET)) <= sheets:
        payload.update(ok=False, message="Offset surface feature did not produce a surface body.")
    return payload


@tool("thicken_surface", "Thicken one surface into a solid. Select surface_bodies from list_surface_bodies or its feature; lengths are mm. both applies thickness on each side.",
      {"selection": SELECTION_SCHEMA, "thickness_mm": {"type": "number", "exclusiveMinimum": 0},
       "direction": {"type": "string", "enum": ["side_one", "side_two", "both"], "default": "side_one"},
       "merge": {"type": "boolean", "default": True}, "name": NAME}, ["selection", "thickness_mm"])
def thicken_surface(args):
    thickness = _positive(args["thickness_mm"], "thickness_mm")
    _, doc = require_part()
    count = require_selection(doc, args["selection"], mark=1)
    if count != 1:
        raise RuntimeError("Select exactly one surface body or surface feature.")
    before = len(get_bodies(doc))
    manager = flag_methods(feature_manager(doc), "FeatureBossThicken")
    feature = manager.FeatureBossThicken(to_m(thickness), {"side_one": 0, "side_two": 1, "both": 2}[args.get("direction", "side_one")],
                                        0, False, bool(args.get("merge", True)), True, True)
    payload = _finish(doc, feature, args, "surface thickening")
    if payload["ok"] and len(get_bodies(doc)) <= before and not args.get("merge", True):
        payload.update(ok=False, message="Thickening did not create a separate solid body.")
    return payload


@tool("knit_surfaces", "Knit at least two selected surface bodies/features, optionally forming a closed solid. Tolerance is mm; re-list surfaces after changes.",
      {"selection": SELECTION_SCHEMA, "form_solid": {"type": "boolean", "default": False}, "merge_entities": {"type": "boolean", "default": True},
       "tolerance_mm": {"type": "number", "minimum": 0.0001, "maximum": 0.1, "default": 0.001}, "name": NAME}, ["selection"])
def knit_surfaces(args):
    tolerance = float(args.get("tolerance_mm", 0.001))
    if not math.isfinite(tolerance) or not 0.0001 <= tolerance <= 0.1:
        raise RuntimeError("Knit tolerance must be between 0.0001 and 0.1 mm.")
    _, doc = require_part()
    count = require_selection(doc, args["selection"], mark=1)
    if count < 2:
        raise RuntimeError("Select at least two surface bodies or surface features.")
    before = len(get_bodies(doc))
    manager = flag_methods(feature_manager(doc), "InsertSewRefSurface")
    feature = manager.InsertSewRefSurface(False, bool(args.get("form_solid", False)), bool(args.get("merge_entities", True)), to_m(tolerance), to_m(tolerance))
    payload = _finish(doc, feature, args, "surface knit")
    if payload["ok"] and args.get("form_solid", False) and len(get_bodies(doc)) <= before:
        payload.update(ok=False, message="Knit feature did not form a closed solid.")
    return payload


@tool("surface_revolve", "Revolve an open 2D sketch into a reference surface. A sketch centerline or axis_selection supplies the axis. Angles are degrees; supports one direction, midplane or two directions.",
      {"sketch_name": NAME, "axis_selection": SELECTION_SCHEMA, "angle_deg": {"type": "number", "exclusiveMinimum": 0, "maximum": 360, "default": 360},
       "angle2_deg": {"type": "number", "minimum": 0, "maximum": 360, "default": 0},
       "mode": {"type": "string", "enum": ["one_direction", "midplane", "two_directions"], "default": "one_direction"},
       "reverse": {"type": "boolean", "default": False}, "name": NAME})
def surface_revolve(args):
    angle = _positive(args.get("angle_deg", 360), "angle_deg")
    second = float(args.get("angle2_deg", 0))
    mode = args.get("mode", "one_direction")
    if mode not in {"one_direction", "midplane", "two_directions"} or angle > 360 or not math.isfinite(second) or not 0 <= second <= 360:
        raise RuntimeError("Invalid surface revolve mode or angle.")
    if (mode == "two_directions" and (second <= 0 or angle + second > 360)) or (mode != "two_directions" and second != 0):
        raise RuntimeError("Two directions requires positive angle2_deg and a total angle <=360; other modes require angle2_deg=0.")
    _, doc = require_part()
    sketch = select_sketch_for_feature(doc, args.get("sketch_name"))
    if args.get("axis_selection"):
        require_selection(doc, args["axis_selection"], mark=4, append=True)
    before = len(get_bodies(doc, BODY_SHEET))
    manager = flag_methods(feature_manager(doc), "InsertRevolvedRefSurface")
    feature = manager.InsertRevolvedRefSurface(to_rad(angle), bool(args.get("reverse", False)), to_rad(second),
                                              {"one_direction": 0, "midplane": 1, "two_directions": 2}[mode])
    payload = _finish(doc, feature, args, "revolved surface", sketch=sketch)
    if payload["ok"] and len(get_bodies(doc, BODY_SHEET)) <= before:
        payload.update(ok=False, message="Surface revolve did not produce a surface body.")
    return payload


@tool("surface_loft", "Loft an ordered set of open or closed sketches into a surface. Supports guide curves, centerline and none/normal/vector/adjacent-face end tangency. A closed loft requires at least three profiles.",
      {"profile_sketches": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 2, "uniqueItems": True},
       "guide_selection": SELECTION_SCHEMA, "centerline_selection": SELECTION_SCHEMA,
       "start_tangency": {"type": "string", "enum": ["none", "normal", "vector", "adjacent_faces"], "default": "none"},
       "end_tangency": {"type": "string", "enum": ["none", "normal", "vector", "adjacent_faces"], "default": "none"},
       "start_vector_selection": SELECTION_SCHEMA, "end_vector_selection": SELECTION_SCHEMA,
       "closed": {"type": "boolean", "default": False}, "keep_tangency": {"type": "boolean", "default": True},
       "force_non_rational": {"type": "boolean", "default": False}, "tolerance_factor": {"type": "number", "exclusiveMinimum": 0, "default": 1}, "name": NAME}, ["profile_sketches"])
def surface_loft(args):
    profiles = args["profile_sketches"]
    closed = bool(args.get("closed", False))
    if not isinstance(profiles, list) or len(profiles) < (3 if closed else 2) or any(not isinstance(p, str) or not p.strip() for p in profiles) or len(set(profiles)) != len(profiles):
        raise RuntimeError("Provide distinct ordered profile sketches; closed lofts require at least three.")
    tolerance = _positive(args.get("tolerance_factor", 1), "tolerance_factor")
    tangencies = {"none": 0, "normal": 1, "vector": 2, "adjacent_faces": 3}
    start, end = args.get("start_tangency", "none"), args.get("end_tangency", "none")
    if start not in tangencies or end not in tangencies:
        raise RuntimeError("Invalid loft tangency mode.")
    for side, kind in (("start", start), ("end", end)):
        if kind == "vector" and not args.get(f"{side}_vector_selection"):
            raise RuntimeError(f"{side} vector tangency requires {side}_vector_selection.")
    _, doc = require_part()
    exit_active_sketch(doc)
    clear_selection(doc)
    for index, sketch in enumerate(profiles):
        require_selection(doc, {"sketches": [sketch]}, mark=1, append=index > 0)
    for key, mark in (("guide_selection", 2), ("centerline_selection", 4), ("start_vector_selection", 8), ("end_vector_selection", 32)):
        if args.get(key):
            require_selection(doc, args[key], mark=mark, append=True)
    before = _feature_names(doc)
    count = len(get_bodies(doc, BODY_SHEET))
    flag_methods(doc, "InsertLoftRefSurface2").InsertLoftRefSurface2(closed, bool(args.get("keep_tangency", True)),
        bool(args.get("force_non_rational", False)), tolerance, tangencies[start], tangencies[end])
    payload = _finish(doc, _feature_created_after(doc, before), args, "lofted surface", profiles=profiles)
    if payload["ok"] and len(get_bodies(doc, BODY_SHEET)) <= count:
        payload.update(ok=False, message="Surface loft did not produce a surface body.")
    return payload


@tool("cut_with_surface", "Cut solid bodies with one selected reference plane or surface. Optional body_indices limits scope to existing solid bodies; flip changes the removed side. Re-list topology afterwards.",
      {"selection": SELECTION_SCHEMA, "flip": {"type": "boolean", "default": False},
       "body_indices": {"type": "array", "items": {"type": "integer", "minimum": 0}, "minItems": 1, "uniqueItems": True},
       "keep_piece_index": {"type": "integer", "minimum": -1, "default": -1}, "name": NAME}, ["selection"])
def cut_with_surface(args):
    indices = args.get("body_indices")
    if indices is not None and (not isinstance(indices, list) or not indices or any(not isinstance(i, int) or isinstance(i, bool) or i < 0 for i in indices) or len(set(indices)) != len(indices)):
        raise RuntimeError("body_indices must contain distinct nonnegative solid-body indices.")
    piece = args.get("keep_piece_index", -1)
    if not isinstance(piece, int) or isinstance(piece, bool) or piece < -1:
        raise RuntimeError("keep_piece_index must be an integer >=-1.")
    _, doc = require_part()
    bodies = get_bodies(doc)
    if not bodies or (indices is not None and any(i >= len(bodies) for i in indices)):
        raise RuntimeError("No matching solid bodies exist. Re-list bodies before selecting cut scope.")
    exit_active_sketch(doc)
    if require_selection(doc, args["selection"]) != 1:
        raise RuntimeError("Select exactly one cutting plane or surface.")
    errors = byref_long()
    manager = flag_methods(feature_manager(doc), "InsertCutSurface")
    scope = dispatch_array([bodies[i] for i in indices]) if indices is not None else nothing()
    feature = manager.InsertCutSurface(bool(args.get("flip", False)), piece, True, indices is None, scope, errors)
    payload = _finish(doc, feature, args, "surface cut", error_code=int(errors.value))
    if int(errors.value):
        payload.update(ok=False, message="Native surface cut reported an error.")
    return payload


def _surface_boundary_selection(selection):
    if not isinstance(selection, dict) or not selection or set(selection) - {"surface_faces", "surface_edges"}:
        raise RuntimeError("Select surface_faces or surface_edges from topology listings with body_type=surface.")
    if not any(selection.values()):
        raise RuntimeError("Select at least one surface face or edge.")
    for indices in selection.values():
        if not isinstance(indices, list) or any(not isinstance(i, int) or isinstance(i, bool) or i < 0 for i in indices) or len(set(indices)) != len(indices):
            raise RuntimeError("Surface topology indices must be distinct nonnegative integers.")


@tool("delete_surface_holes", "Remove selected hole boundaries from surface bodies. Select surface_edges from list_edges(body_type=surface); unselected holes are preserved. Re-list topology afterwards.",
      {"selection": SELECTION_SCHEMA, "name": NAME}, ["selection"])
def delete_surface_holes(args):
    selection = args["selection"]
    _surface_boundary_selection(selection)
    if set(selection) != {"surface_edges"}:
        raise RuntimeError("Surface hole removal accepts only surface_edges.")
    _, doc = require_part()
    exit_active_sketch(doc)
    require_selection(doc, selection)
    manager = flag_methods(feature_manager(doc), "InsertDeleteHoleForSurface")
    try:
        create = manager.InsertDeleteHoleForSurface
    except AttributeError:
        before = _feature_names(doc)
        flag_methods(doc, "InsertDeleteHole").InsertDeleteHole()
        feature = _feature_created_after(doc, before)
    else:
        feature = create()
    return _finish(doc, feature, args, "surface hole removal")


@tool("extend_surface", "Extend selected surface faces (all boundary edges) or individual surface edges. Distance is mm; up_to_point requires a selected point and up_to_surface requires a solid face. Re-list surface topology afterwards.",
      {"selection": SELECTION_SCHEMA, "linear": {"type": "boolean", "default": False},
       "end_condition": {"type": "string", "enum": ["distance", "up_to_point", "up_to_surface"], "default": "distance"},
       "distance_mm": {"type": "number", "exclusiveMinimum": 0}, "target_selection": SELECTION_SCHEMA, "name": NAME}, ["selection"])
def extend_surface(args):
    _surface_boundary_selection(args["selection"])
    mode = args.get("end_condition", "distance")
    if mode not in {"distance", "up_to_point", "up_to_surface"}:
        raise RuntimeError("Invalid surface extension end condition.")
    distance = _positive(args.get("distance_mm", 0), "distance_mm") if mode == "distance" else 0
    target = args.get("target_selection")
    if mode != "distance" and not target:
        raise RuntimeError("A target_selection is required for this end condition.")
    if mode != "distance" and float(args.get("distance_mm", 0)) != 0:
        raise RuntimeError("distance_mm applies only to distance extension.")
    if mode == "distance" and target:
        raise RuntimeError("Distance extension does not accept target_selection.")
    _, doc = require_part()
    exit_active_sketch(doc)
    require_selection(doc, args["selection"])
    if target and require_selection(doc, target, append=True) != 1:
        raise RuntimeError("Select exactly one target point or solid face.")
    before = _feature_names(doc)
    flag_methods(doc, "InsertExtendSurface").InsertExtendSurface(bool(args.get("linear", False)),
        {"distance": 0, "up_to_point": 1, "up_to_surface": 2}[mode], to_m(distance))
    return _finish(doc, _feature_created_after(doc, before), args, "surface extension")


@tool("untrim_surface", "Restore trimmed surface faces or selected edges. Faces support all/internal/external boundaries; selected edges support extending by a percentage of their natural boundary or connecting endpoints. trim_opposite_side requires merge=false and SolidWorks 2024+.",
      {"selection": SELECTION_SCHEMA,
       "face_mode": {"type": "string", "enum": ["all", "internal", "external"], "default": "all"},
       "edge_mode": {"type": "string", "enum": ["extend", "connect_endpoints"], "default": "extend"},
       "extend_percent": {"type": "number", "minimum": 0, "default": 0, "description": "Percentage of the natural boundary; only for surface_edges with edge_mode=extend."},
       "merge": {"type": "boolean", "default": True}, "trim_opposite_side": {"type": "boolean", "default": False}, "name": NAME}, ["selection"])
def untrim_surface(args):
    _surface_boundary_selection(args["selection"])
    faces = {"all": 0, "internal": 1, "external": 2}
    edges = {"extend": 2, "connect_endpoints": 1}
    face_mode, edge_mode = args.get("face_mode", "all"), args.get("edge_mode", "extend")
    distance = float(args.get("extend_percent", 0))
    if face_mode not in faces or edge_mode not in edges or not math.isfinite(distance) or distance < 0:
        raise RuntimeError("Invalid untrim mode or extension percentage.")
    if distance and (not args["selection"].get("surface_edges") or edge_mode != "extend"):
        raise RuntimeError("extend_percent applies only to selected edges in extend mode.")
    if edge_mode == "connect_endpoints" and len(args["selection"].get("surface_edges", [])) < 2:
        raise RuntimeError("Connect endpoints requires at least two selected surface edges.")
    merge, opposite = bool(args.get("merge", True)), bool(args.get("trim_opposite_side", False))
    if merge and opposite:
        raise RuntimeError("trim_opposite_side requires merge=false.")
    _, doc = require_part()
    exit_active_sketch(doc)
    require_selection(doc, args["selection"])
    manager = flag_methods(feature_manager(doc), "InsertUntrimSurface2", "InsertUntrimSurface")
    parameters = (faces[face_mode], edges[edge_mode], distance, merge)
    candidates = [("InsertUntrimSurface2", parameters + (opposite,))]
    if not opposite:
        candidates.append(("InsertUntrimSurface", parameters))
    return _finish(doc, call_versioned(manager, *candidates), args, "surface untrim")


def _selected_objects(doc, selection, mark=0):
    require_selection(doc, selection, mark=mark)
    manager = flag_methods(doc.SelectionManager, "GetSelectedObjectCount2", "GetSelectedObject6")
    return [manager.GetSelectedObject6(i, mark) for i in range(1, manager.GetSelectedObjectCount2(mark) + 1)]


@tool("surface_sweep", "Sweep an open/closed sketch profile or a numeric circular profile along a path sketch. Supports guide sketches, follow-path/keep-normal/constant-twist control and one/both directions. Angles are degrees and diameter is mm. Requires the modern surface-sweep definition API (SOLIDWORKS 2018+). Native settings are read back before confirming success.",
      {"profile_sketch": NAME, "path_sketch": NAME,
       "circular_diameter_mm": {"type": "number", "exclusiveMinimum": 0},
       "guide_sketches": {"type": "array", "items": NAME, "uniqueItems": True},
       "twist_control": {"type": "string", "enum": ["follow_path", "keep_normal", "constant_twist", "first_guide", "two_guides"], "default": "follow_path"},
       "twist_angle_deg": NUM, "second_twist_angle_deg": NUM,
       "reverse_twist": {"type": "boolean", "default": False}, "reverse_second_twist": {"type": "boolean", "default": False},
       "direction": {"type": "string", "enum": ["first", "both", "second"], "default": "first"},
       "keep_tangency": {"type": "boolean", "default": False},
       "advanced_smoothing": {"type": "boolean", "default": False},
       "merge_smooth_faces": {"type": "boolean", "default": False}, "name": NAME}, ["path_sketch"])
def surface_sweep(args):
    profile, path = args.get("profile_sketch"), args["path_sketch"]
    circular = "circular_diameter_mm" in args
    diameter = _positive(args["circular_diameter_mm"], "circular_diameter_mm") if circular else 0
    if not isinstance(path, str) or not path.strip() or (circular and profile) or (not circular and (not isinstance(profile, str) or not profile.strip())):
        raise RuntimeError("Provide a path sketch and exactly one sketch or circular profile.")
    guides = args.get("guide_sketches", [])
    if not isinstance(guides, list) or any(not isinstance(g, str) or not g.strip() for g in guides) or len(set(guides)) != len(guides):
        raise RuntimeError("Guide sketches must be distinct nonempty names.")
    if profile == path or any(g in (profile, path) for g in guides):
        raise RuntimeError("Profile, path and guide sketches must be distinct.")
    controls = {"follow_path": 0, "keep_normal": 1, "constant_twist": 8, "first_guide": 2, "two_guides": 3}
    directions = {"first": 0, "both": 1, "second": 2}
    mode, direction = args.get("twist_control", "follow_path"), args.get("direction", "first")
    angle, second = float(args.get("twist_angle_deg", 0)), float(args.get("second_twist_angle_deg", 0))
    reverse, reverse_second = bool(args.get("reverse_twist", False)), bool(args.get("reverse_second_twist", False))
    if mode not in controls or direction not in directions or not all(math.isfinite(a) and a >= 0 for a in (angle, second)):
        raise RuntimeError("Invalid sweep control, direction or nonnegative twist angle.")
    if (angle or second or reverse or reverse_second) and mode != "constant_twist":
        raise RuntimeError("Twist angles and reversals require constant_twist control.")
    if (second or reverse_second) and direction != "both":
        raise RuntimeError("Second twist settings require bidirectional sweeping.")
    if (direction == "both" and guides) or (mode == "first_guide" and len(guides) < 1) or (mode == "two_guides" and len(guides) < 2):
        raise RuntimeError("Guide control requires at least one/two guide sketches; bidirectional sweeps do not support guides.")
    if circular and (guides or mode != "follow_path" or direction != "first"):
        raise RuntimeError("Circular profiles support follow_path control without guides or bidirectional options.")
    _, doc = require_part()
    exit_active_sketch(doc)
    clear_selection(doc)
    if profile:
        require_selection(doc, {"sketches": [profile]}, mark=1, append=True)
    require_selection(doc, {"sketches": [path]}, mark=4, append=True)
    if guides:
        require_selection(doc, {"sketches": guides}, mark=2, append=True)
    manager = flag_methods(feature_manager(doc), "CreateDefinition", "CreateFeature")
    data = manager.CreateDefinition(62)  # swFmRefSurface initializes ISweepFeatureData.
    if data is None:
        raise RuntimeError("This SOLIDWORKS build cannot create a modern surface-sweep definition.")
    data = flag_methods(data, "SetTwistAngle", "SetD2TwistAngle")
    settings = {"TwistControlType": controls[mode], "CircularProfile": circular,
                "Direction": directions[direction], "MaintainTangency": bool(args.get("keep_tangency", False)),
                "AdvancedSmoothing": bool(args.get("advanced_smoothing", False)),
                "MergeSmoothFaces": bool(args.get("merge_smooth_faces", False))}
    if circular:
        settings["CircularProfileDiameter"] = to_m(diameter)
    if mode == "constant_twist":
        settings["AlignWithEndFaces"] = False
        if direction in {"both", "second"}:
            settings["D2ReverseTwistDir"] = reverse_second if direction == "both" else reverse
    for key, expected in settings.items():
        setattr(data, key, expected)
    def set_twist(definition):
        definition.SetTwistAngle(0 if direction == "second" else to_rad(-angle if reverse else angle))
        if direction in {"both", "second"}:
            definition.SetD2TwistAngle(to_rad(second if direction == "both" else angle))
            definition.D2ReverseTwistDir = reverse_second if direction == "both" else reverse
    if mode == "constant_twist":
        set_twist(data)
    feature = manager.CreateFeature(data)
    edit_applied = True
    if feature is not None and (reverse or reverse_second):
        # Negative twist is stored by CreateFeature but initially yields positive
        # geometry on the tested build. ModifyDefinition rebuilds the signed twist.
        edit = flag_methods(value(feature, "GetDefinition"), "AccessSelections", "SetTwistAngle", "SetD2TwistAngle", "ReleaseSelectionAccess")
        if edit.AccessSelections(doc, nothing()):
            try:
                set_twist(edit)
                edit_applied = bool(flag_methods(feature, "ModifyDefinition").ModifyDefinition(edit, doc, nothing()))
            finally:
                edit.ReleaseSelectionAccess()
        else:
            edit_applied = False
    payload = _finish(doc, feature, args, "surface sweep")
    if not edit_applied:
        payload.update(ok=False, message="Surface sweep was created, but signed twist geometry could not be rebuilt.")
    if payload["ok"]:
        actual = value(feature, "GetDefinition")
        readback = {key: value(actual, key) for key in settings}
        readback["guide_count"] = int(value(actual, "GetGuideCurvesCount"))
        settings["guide_count"] = len(guides)
        if mode == "constant_twist":
            readback["twist_angle_deg"] = math.degrees(float(value(actual, "GetD2TwistAngle" if direction == "second" else "GetTwistAngle")))
            settings["twist_angle_deg"] = angle if direction == "second" else -angle if reverse else angle
            if direction == "both":
                readback["second_twist_angle_deg"] = math.degrees(float(value(actual, "GetD2TwistAngle")))
                settings["second_twist_angle_deg"] = second
        payload["data"]["native_settings"] = readback
        # Direction is inapplicable (-1) for circular profiles and endpoint profiles.
        # Only the default first direction can accept that native sentinel.
        if direction == "first" and readback["Direction"] == -1:
            settings.pop("Direction")
        mismatched = [key for key, expected in settings.items()
                      if not math.isclose(float(readback[key]), float(expected), abs_tol=1e-8)]
        if mismatched:
            payload.update(ok=False, message=f"Surface sweep was created, but native settings differ: {', '.join(mismatched)}.")
        elif not as_list(safe(feature, "GetFaces")) or any(safe(safe(face, "GetBody"), "GetType") != BODY_SHEET for face in as_list(safe(feature, "GetFaces"))):
            payload.update(ok=False, message="Surface sweep did not produce faces on a surface body.")
    return payload


def _curve_selection(selection):
    if not isinstance(selection, dict) or not selection or set(selection) - {"edges", "surface_edges", "sketches"} or not any(selection.values()):
        raise RuntimeError("Select boundary edges or whole sketches.")
    for key, entries in selection.items():
        if not isinstance(entries, list) or len(set(entries)) != len(entries):
            raise RuntimeError("Boundary selection lists must contain distinct entries.")
        if key != "sketches" and any(not isinstance(i, int) or isinstance(i, bool) or i < 0 for i in entries):
            raise RuntimeError("Boundary edge indices must be nonnegative integers.")
        if key == "sketches" and any(not isinstance(i, str) or not i.strip() for i in entries):
            raise RuntimeError("Boundary sketches require nonempty names.")


@tool("fill_surface", "Create a filled surface from edge/sketch boundary groups with requested continuity and native control readback. Sketches support contact only. Curvature is forwarded but returns failure if the native API cannot confirm it. Supports direction faces, internal constraints, optimize, merge and try-to-form-solid.",
      {"boundaries": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
          "selection": SELECTION_SCHEMA, "continuity": {"type": "string", "enum": ["contact", "tangent", "curvature"], "default": "contact"},
          "direction_face_selection": SELECTION_SCHEMA}, "required": ["selection"]}},
       "constraint_selection": SELECTION_SCHEMA, "resolution": {"type": "integer", "minimum": 1, "maximum": 3, "default": 2},
       "optimize": {"type": "boolean", "default": True}, "merge": {"type": "boolean", "default": False},
       "try_form_solid": {"type": "boolean", "default": False}, "reverse_direction": {"type": "boolean", "default": False},
       "reverse_surface": {"type": "boolean", "default": False}, "name": NAME}, ["boundaries"])
def fill_surface(args):
    groups = args["boundaries"]
    resolution = args.get("resolution", 2)
    controls = {"contact": 0, "tangent": 1, "curvature": 2}
    if not isinstance(groups, list) or not groups or not isinstance(resolution, int) or isinstance(resolution, bool) or not 1 <= resolution <= 3:
        raise RuntimeError("Provide boundary groups and resolution 1, 2 or 3.")
    seen = set()
    for group in groups:
        if not isinstance(group, dict) or "selection" not in group:
            raise RuntimeError("Each boundary group needs a selection.")
        _curve_selection(group["selection"])
        for key, entries in group["selection"].items():
            for entry in entries:
                identity = (key, entry)
                if identity in seen:
                    raise RuntimeError("A boundary entity cannot appear in multiple groups.")
                seen.add(identity)
        continuity = group.get("continuity", "contact")
        if continuity not in controls or (continuity != "contact" and group["selection"].get("sketches")):
            raise RuntimeError("Invalid continuity; sketch boundaries support contact only.")
    if args.get("constraint_selection"):
        _curve_selection(args["constraint_selection"])
    _, doc = require_part()
    exit_active_sketch(doc)
    boundaries, types, faces = [], [], []
    for group in groups:
        objects = _selected_objects(doc, group["selection"])
        direction = None
        if group.get("direction_face_selection"):
            selected = _selected_objects(doc, group["direction_face_selection"])
            if len(selected) != 1:
                raise RuntimeError("Select exactly one direction face per boundary group.")
            direction = selected[0]
        boundaries.extend(objects)
        types.extend([controls[group.get("continuity", "contact")]] * len(objects))
        faces.extend([direction] * len(objects))
    constraints = _selected_objects(doc, args["constraint_selection"]) if args.get("constraint_selection") else []
    clear_selection(doc)
    for group in groups:
        mark = {"contact": 257, "tangent": 513, "curvature": 1}[group.get("continuity", "contact")]
        require_selection(doc, group["selection"], mark=mark, append=True)
    if constraints:
        require_selection(doc, args["constraint_selection"], mark=4, append=True)
    options = sum(flag for key, flag, default in (("optimize", 1, True), ("try_form_solid", 2, False),
        ("merge", 4, False), ("reverse_direction", 8, False), ("reverse_surface", 16, False)) if args.get(key, default))
    manager = flag_methods(feature_manager(doc), "InsertFillSurface2")
    feature = manager.InsertFillSurface2(resolution, options, dispatch_array(boundaries), integer_array(types),
        dispatch_array(faces) if any(face is not None for face in faces) else nothing(),
        dispatch_array(constraints) if constraints else nothing())
    payload = _finish(doc, feature, args, "filled surface")
    if payload["ok"]:
        data = flag_methods(value(feature, "GetDefinition"), "AccessSelections", "ReleaseSelectionAccess", "GetPatchBoundary", "GetCurvatureControl")
        if not data.AccessSelections(doc, nothing()):
            payload.update(ok=False, message="Filled surface was created, but native continuity could not be read.")
        else:
            try:
                entities = as_list(data.GetPatchBoundary(byref_variant()))
                actual = [int(data.GetCurvatureControl(entity)) for entity in entities]
                payload["data"]["native_continuity_controls"] = actual
                if sorted(actual) != sorted(types):
                    payload.update(ok=False, message="Filled surface was created, but native continuity does not confirm the requested controls.")
            finally:
                data.ReleaseSelectionAccess()
    if payload["ok"] and args.get("try_form_solid", False):
        filled_faces = as_list(safe(feature, "GetFaces"))
        if not any(safe(safe(face, "GetBody"), "GetType") == BODY_SOLID for face in filled_faces):
            payload.update(ok=False, message="Filled surface did not produce a face on a solid body.")
    return payload


@tool("ruled_surface", "Create a ruled surface along solid or surface edges. Supports tangent, normal, tapered-to-vector, perpendicular-to-vector and sweep modes. Length is mm, taper angle is degrees; sweep can use a numeric direction vector. Alternate face uses native selection mark 6. Normal-mode pull reversal remains unconfirmed and returns failure after creation.",
      {"selection": SELECTION_SCHEMA, "mode": {"type": "string", "enum": ["tangent", "normal", "tapered", "perpendicular", "sweep"], "default": "tangent"},
       "length_mm": {"type": "number", "exclusiveMinimum": 0}, "angle_deg": {"type": "number", "default": 0},
       "direction_selection": SELECTION_SCHEMA, "direction_vector": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
       "alternate_face": {"type": "boolean", "default": False}, "flip_pull_direction": {"type": "boolean", "default": False},
       "flip_direction": {"type": "boolean", "default": False}, "trim_and_knit": {"type": "boolean", "default": False},
       "remove_connecting_surfaces": {"type": "boolean", "default": False}, "name": NAME}, ["selection", "length_mm"])
def ruled_surface(args):
    _curve_selection(args["selection"])
    if args["selection"].get("sketches"):
        raise RuntimeError("Ruled surfaces require model edges, not sketch boundaries.")
    modes = {"tangent": 0, "normal": 1, "tapered": 2, "perpendicular": 3, "sweep": 4}
    mode = args.get("mode", "tangent")
    length = _positive(args["length_mm"], "length_mm")
    angle = float(args.get("angle_deg", 0))
    if mode not in modes or not math.isfinite(angle) or abs(angle) >= 90 or (mode != "tapered" and angle != 0):
        raise RuntimeError("Invalid ruled mode or taper angle; taper angle must be between -90 and 90 degrees.")
    direction = args.get("direction_selection")
    vector = args.get("direction_vector")
    if vector is not None:
        if mode != "sweep" or direction or not isinstance(vector, list) or len(vector) != 3:
            raise RuntimeError("Numeric direction_vector is supported only by sweep and cannot be combined with direction_selection.")
        vector = [float(v) for v in vector]
        magnitude = math.sqrt(sum(v * v for v in vector))
        if not math.isfinite(magnitude) or magnitude == 0:
            raise RuntimeError("Direction vector must be finite and nonzero.")
        vector = [v / magnitude for v in vector]
    if mode in {"tapered", "perpendicular", "sweep"} and not direction and vector is None:
        raise RuntimeError("This ruled mode requires a direction_selection or a sweep direction_vector.")
    if mode in {"tangent", "normal"} and direction:
        raise RuntimeError("Tangent and normal modes do not accept direction_selection.")
    if args.get("flip_pull_direction", False) and mode not in {"normal", "tapered"}:
        raise RuntimeError("flip_pull_direction applies only to normal and tapered modes.")
    if args.get("flip_direction", False) and mode != "perpendicular":
        raise RuntimeError("flip_direction applies only to perpendicular mode.")
    _, doc = require_part()
    exit_active_sketch(doc)
    require_selection(doc, args["selection"], mark=6 if args.get("alternate_face", False) else 4)
    if direction and require_selection(doc, direction, mark=1, append=True) != 1:
        raise RuntimeError("Select exactly one direction reference.")
    manager = flag_methods(feature_manager(doc), "InsertRuledSurfaceFromEdge2", "InsertRuledSurfaceFromEdge")
    parameters = (modes[mode], to_m(length), bool(args.get("flip_pull_direction", False)), bool(args.get("flip_direction", False)),
        bool(args.get("trim_and_knit", False)), to_rad(angle), vector is not None, *(vector or [0, 0, 0]))
    candidates = [("InsertRuledSurfaceFromEdge2", parameters + (bool(args.get("remove_connecting_surfaces", False)),))]
    if not args.get("remove_connecting_surfaces", False):
        candidates.append(("InsertRuledSurfaceFromEdge", parameters))
    feature = call_versioned(manager, *candidates)
    angle_applied = True
    if feature is not None and mode == "tapered":
        data = flag_methods(value(feature, "GetDefinition"), "AccessSelections", "ReleaseSelectionAccess")
        if not data.AccessSelections(doc, nothing()):
            angle_applied = False
        else:
            try:
                if not math.isclose(float(value(data, "Angle")), to_rad(angle), abs_tol=1e-8):
                    data.Angle = to_rad(angle)
                    angle_applied = bool(flag_methods(feature, "ModifyDefinition").ModifyDefinition(data, doc, nothing()))
            finally:
                data.ReleaseSelectionAccess()
        angle_applied = angle_applied and math.isclose(float(value(value(feature, "GetDefinition"), "Angle")), to_rad(angle), abs_tol=1e-8)
    payload = _finish(doc, feature, args, "ruled surface")
    if not angle_applied:
        payload.update(ok=False, message="Ruled surface was created, but the requested taper angle could not be confirmed.")
    if feature is not None and mode == "normal" and args.get("flip_pull_direction", False):
        payload.update(ok=False, message="Ruled surface was created, but normal-mode pull reversal is not confirmed by this implementation.")
    return payload
