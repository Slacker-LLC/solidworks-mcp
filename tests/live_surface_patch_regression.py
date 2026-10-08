# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for details.

"""Filled and ruled surface geometry checks on disposable parts."""

import math
import pythoncom
import win32com.client
from live_expansion_regression import block, call, near, volume
from live_surface_boundary_regression import plate
from live_surface_construction_regression import area
from solidworks_mcp.sw_core import as_list, byref_long, running_app, value, find_feature, flag_methods, require_part, nothing, HANDLERS


def fill_controls(continuity, constraints=0):
    _, doc = require_part()
    feature = find_feature(doc, "TestFill")
    data = flag_methods(value(feature, "GetDefinition"), "AccessSelections", "ReleaseSelectionAccess", "GetPatchBoundary", "GetCurvatureControl")
    assert data.AccessSelections(doc, nothing())
    try:
        types = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_VARIANT, None)
        entities = as_list(data.GetPatchBoundary(types))
        assert entities
        expected = {"contact": 0, "tangent": 1, "curvature": 2}[continuity]
        actual = [data.GetCurvatureControl(entity) for entity in entities]
        assert all(control == expected for control in actual), (continuity, expected, actual)
        assert value(data, "GetConstraintCurvesCount") == constraints
    finally:
        data.ReleaseSelectionAccess()


def sketch_fill(constraint=False):
    call("create_new_document", kind="part")
    call("create_sketch", plane="front", name="FillProfile")
    call("draw_rectangle", x1_mm=0, y1_mm=0, x2_mm=10, y2_mm=10)
    call("close_sketch")
    options = {}
    if constraint:
        call("create_sketch", plane="front", name="FillConstraint")
        call("draw_line", x1_mm=0, y1_mm=5, x2_mm=10, y2_mm=5)
        call("close_sketch")
        options = {"constraint_selection": {"sketches": ["FillConstraint"]}, "optimize": False, "resolution": 3}
    call("fill_surface", boundaries=[{"selection": {"sketches": ["FillProfile"]}}], name="TestFill", **options)
    near(area(), 100, "sketch filled surface area")
    fill_controls("contact", 1 if constraint else 0)
    if constraint:
        _, doc = require_part()
        data = value(find_feature(doc, "TestFill"), "GetDefinition")
        assert not value(data, "OptimizeSurface") and value(data, "ResolutionControl") == 3


def hole_fill(continuity, merge):
    _, edges = plate(hole=True)
    hole = next(e["index"] for e in edges if e["curve_type"] == "circle")
    arguments = dict(boundaries=[{"selection": {"surface_edges": [hole]}, "continuity": continuity,
                     "direction_face_selection": {"surface_faces": [0]}}], merge=merge, name="TestFill")
    if continuity == "curvature":
        payload = HANDLERS["fill_surface"](arguments)
        assert not payload["ok"] and payload["data"]["native_continuity_controls"] == [-1], payload
        print("Verified native curvature-readback gap; not reported as success.", flush=True)
    else:
        call("fill_surface", **arguments)
    near(area(), 100, "filled hole total area")
    assert len(call("list_surface_bodies")["surface_bodies"]) == (1 if merge else 2)
    if continuity != "curvature":
        fill_controls(continuity)


def close_cube():
    block()
    top = call("list_faces", normal=[0, 0, 1])["faces"][0]["index"]
    call("delete_faces", selection={"faces": [top]}, mode="delete")
    edges = call("list_edges", body_type="surface")["edges"]
    rim = [e["index"] for e in edges if all(abs(e[key][2] - 10) < 1e-6 for key in ("start_mm", "end_mm"))]
    assert len(rim) == 4
    call("fill_surface", boundaries=[{"selection": {"surface_edges": rim}}], merge=True, try_form_solid=True)
    near(volume(), 1000, "filled cube closure volume")


def ruled(mode, **options):
    plate()
    arguments = {"direction_vector": [0, 0, 1]} if mode == "sweep" else {}
    if mode in {"tapered", "perpendicular"}:
        arguments = {"direction_selection": {"planes": ["front"]}}
    if mode == "tapered":
        arguments["angle_deg"] = 30
    call("ruled_surface", selection={"surface_edges": [0]}, mode=mode, length_mm=5, name="TestRuled", **arguments, **options)
    bodies = call("list_surface_bodies")["surface_bodies"]
    near(area(), 150, "ruled patch total area")
    faces = call("list_faces", body_type="surface")["faces"]
    patch = next(face for face in faces if abs(face["area_mm2"] - 50) < 1e-4)
    near(abs(patch["normal"][2]), 0.5 if mode == "tapered" else (1 if mode in {"tangent", "perpendicular"} else 0), "ruled patch inclination")
    _, doc = require_part()
    data = value(find_feature(doc, "TestRuled"), "GetDefinition")
    assert value(data, "Type") == {"tangent": 1, "normal": 2, "tapered": 3, "perpendicular": 4, "sweep": 5}[mode]
    near(value(data, "Distance") * 1000, 5, "ruled length readback")
    if mode == "tapered":
        near(value(data, "Angle") * 180 / math.pi, 30, "ruled angle readback")
    assert bool(value(data, "TrimAndKnit")) == bool(options.get("trim_and_knit", False))
    edges = call("list_edges", body_type="surface")["edges"]
    corners = [edge[key] for edge in edges if edge["body_index"] == patch["body_index"] for key in ("start_mm", "end_mm")]
    return [(min(p[i] for p in corners), max(p[i] for p in corners)) for i in range(3)]


def run():
    app = running_app()
    initial = {str(value(d, "GetTitle")) for d in as_list(value(app, "GetDocuments"))}
    original = str(value(app.ActiveDoc, "GetTitle")) if app.ActiveDoc is not None else ""
    try:
        sketch_fill()
        sketch_fill(constraint=True)
        for continuity in ("contact", "tangent", "curvature"):
            hole_fill(continuity, True)
        hole_fill("contact", False)
        close_cube()
        bounds = {mode: ruled(mode) for mode in ("tangent", "normal", "sweep", "tapered", "perpendicular")}
        plate()
        payload = HANDLERS["ruled_surface"]({"selection": {"surface_edges": [0]}, "mode": "normal",
                                            "length_mm": 5, "flip_pull_direction": True})
        assert not payload["ok"] and "not confirmed" in payload["message"], payload
        near(area(), 150, "normal reversal gap does not report successful direction change")
        flipped = ruled("perpendicular", flip_direction=True)
        assert any(abs(a-b) > 1 for pair, other in zip(bounds["perpendicular"], flipped) for a,b in zip(pair, other))
        ruled("normal", trim_and_knit=True)
        normals = []
        for alternate in (False, True):
            block()
            call("ruled_surface", selection={"edges": [0]}, mode="normal", length_mm=5, alternate_face=alternate)
            near(area(), 50, "solid-edge ruled area")
            face = call("list_faces", body_type="surface")["faces"][0]
            normals.append(face["normal"])
        near(sum(a*b for a,b in zip(*normals)), 0, "alternate ruled face orthogonality")
        print("Filled and ruled surface geometry verified.", flush=True)
    finally:
        titles = [str(value(d, "GetTitle")) for d in as_list(value(app, "GetDocuments"))]
        for title in titles:
            if title not in initial:
                app.CloseDoc(title)
        if original:
            app.ActivateDoc3(original, False, 0, byref_long())
        assert {str(value(d, "GetTitle")) for d in as_list(value(app, "GetDocuments"))} == initial


if __name__ == "__main__":
    run()
