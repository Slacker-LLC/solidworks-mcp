# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for details.

"""Direct-edit volumes and native reference geometry readback on scratch parts."""

import math

from live_expansion_regression import block, call, near, volume
from solidworks_mcp.sw_core import as_list, byref_long, running_app, value


def top_face():
    faces = call("list_faces", normal=[0, 0, 1])["faces"]
    assert len(faces) == 1, faces
    return faces[0]["index"]


def test_direct_edit():
    block()
    call("move_faces", selection={"faces": [top_face()]}, mode="offset", distance_mm=5)
    near(volume(), 1500, "offset face volume")
    call("move_faces", selection={"faces": [top_face()]}, mode="translate", translation_mm=[0, 0, 5])
    near(volume(), 2000, "translated face volume")
    block()
    call("move_faces", selection={"faces": [top_face()]}, mode="rotate", rotation_deg=[30, 0, 0], origin_mm=[5, 5, 10])
    near(volume(), 1000, "rotated top face volume")
    tilted = call("list_faces", normal=[0, -0.5, math.sqrt(3)/2])["faces"]
    assert len(tilted) == 1, tilted
    block()
    call("delete_faces", selection={"faces": [top_face()]})
    sheets = call("list_surface_bodies")["surface_bodies"]
    assert len(sheets) == 1 and sheets[0]["face_count"] == 5, sheets
    near(sheets[0]["area_mm2"], 500, "open cube surface area")
    block()
    call("create_sketch", plane="front", name="Hole")
    call("draw_circle", x_mm=5, y_mm=5, radius_mm=1)
    call("close_sketch")
    call("cut_extrude", sketch_name="Hole", end_condition="through_all_both")
    near(volume(), 1000 - math.pi*10, "hole volume")
    cylindrical = call("list_faces", surface_type="cylinder")["faces"]
    assert len(cylindrical) == 1, cylindrical
    call("delete_faces", selection={"faces": [cylindrical[0]["index"]]}, mode="patch")
    near(volume(), 1000, "healed cylindrical face volume")
    block()
    call("offset_surface", selection={"faces": [top_face()]}, distance_mm=5)
    call("replace_faces", faces_to_replace={"faces": [top_face()]}, replacement={"surface_bodies": [0]})
    near(volume(), 1500, "replacement face volume")


def test_reference_geometry():
    block()
    face = top_face()
    point = call("create_reference_points", mode="face_center", selection={"faces": [face]}, name="TopCenter")["points"][0]
    for a, b in zip(point["position_mm"], [5, 5, 10]):
        near(a, b, "face center reference point")
    edge = next(e for e in call("list_edges", curve_type="line")["edges"]
                if e["start_mm"][0] != e["end_mm"][0] and abs(e["start_mm"][1]) < 1e-6 and abs(e["start_mm"][2]) < 1e-6)
    center = call("create_reference_points", mode="edge_midpoint", selection={"edges": [edge["index"]]})["points"][0]
    near(center["position_mm"][0], 5, "edge center")
    distributed = call("create_reference_points", mode="evenly", count=3, selection={"edges": [edge["index"]]}, name="EdgePoints")["points"]
    coords = sorted(p["position_mm"][0] for p in distributed)
    for a, b in zip(coords, [2.5, 5, 7.5]):
        near(a, b, "evenly distributed point")
    assert len(call("list_reference_points")["points"]) == 5
    cs = call("create_coordinate_system", origin_mm=[2, 3, 4], rotation_deg=[0, 0, 90], name="LocalAxes")["coordinate_system"]
    for a, b in zip(cs["origin_mm"], [2, 3, 4]):
        near(a, b, "coordinate origin")
    for a, b in zip(cs["rotation_matrix"][:3], [0, 1, 0]):
        near(a, b, "coordinate X orientation")
    assert len(call("list_coordinate_systems")["coordinate_systems"]) == 1
    distances = call("create_reference_points", mode="along_distance", distance_mm=2, count=2,
                     selection={"edges": [edge["index"]]})["points"]
    distance_x = sorted(p["position_mm"][0] for p in distances)
    # Edge orientation can be reversed; successive positions must be 2 mm apart.
    near(distance_x[1] - distance_x[0], 2, "distance point spacing")
    percentages = call("create_reference_points", mode="along_percentage", percentage=20, count=2,
                       selection={"edges": [edge["index"]]})["points"]
    percentage_x = sorted(p["position_mm"][0] for p in percentages)
    near(percentage_x[1] - percentage_x[0], 2, "percentage point spacing")
    call("create_sketch", plane="front", name="CirclePoint")
    call("draw_circle", x_mm=3, y_mm=4, radius_mm=2)
    call("close_sketch")
    center = call("create_reference_points", mode="arc_center", selection={"sketch_name": "CirclePoint", "sketch_segments": [0]})["points"][0]
    for a, b in zip(center["position_mm"], [3, 4, 0]):
        near(a, b, "arc center")
    call("create_sketch", plane="front", name="StandalonePoint")
    call("draw_point", x_mm=7, y_mm=8)
    call("close_sketch")
    point = call("create_reference_points", mode="sketch_point", selection={"sketch_name": "StandalonePoint", "sketch_points": [0]})["points"][0]
    for a, b in zip(point["position_mm"], [7, 8, 0]):
        near(a, b, "sketch reference point")
    call("create_sketch", plane="front", name="Crossing")
    call("draw_line", x1_mm=0, y1_mm=0, x2_mm=10, y2_mm=10)
    call("draw_line", x1_mm=0, y1_mm=10, x2_mm=10, y2_mm=0)
    call("close_sketch")
    intersection = call("create_reference_points", mode="intersection", selection={"sketch_name": "Crossing", "sketch_segments": [0, 1]})["points"][0]
    for a, b in zip(intersection["position_mm"], [5, 5, 0]):
        near(a, b, "intersection reference point")
    vertex = next(v for v in call("list_vertices")["vertices"] if v["point_mm"] == [10, 10, 10])
    bottom = call("list_faces", normal=[0, 0, -1])["faces"][0]["index"]
    projected = call("create_reference_points", mode="projection", selection={"vertices": [vertex["index"]], "faces": [bottom]})["points"][0]
    for a, b in zip(projected["position_mm"], [10, 10, 0]):
        near(a, b, "projected reference point")


def test_curves():
    call("create_new_document", kind="part")
    points = [[0, 0, 0], [10, 5, 2], [20, 0, 4], [30, 5, 6]]
    call("create_curve_through_points", points_mm=points, name="XYZPath")
    assert call("get_curve_points", name="XYZPath")["curve"]["points_mm"] == points
    edited = [[0, 0, 0], [20, 10, 4], [40, 0, 8]]
    call("set_curve_points", name="XYZPath", points_mm=edited)
    assert call("get_curve_points", name="XYZPath")["curve"]["points_mm"] == edited
    call("create_new_document", kind="part")
    call("create_sketch", plane="front", name="Chain")
    call("draw_line", x1_mm=0, y1_mm=0, x2_mm=10, y2_mm=0)
    call("draw_line", x1_mm=10, y1_mm=0, x2_mm=20, y2_mm=10)
    call("close_sketch")
    call("composite_curve", selection={"sketches": ["Chain"]}, name="CompositePath")


def run():
    app = running_app()
    initial = {str(value(d, "GetTitle")) for d in as_list(value(app, "GetDocuments"))}
    original = str(value(app.ActiveDoc, "GetTitle")) if app.ActiveDoc is not None else ""
    try:
        test_direct_edit()
        test_reference_geometry()
        test_curves()
        print("Direct editing, reference points, coordinate transform and XYZ/composite curves verified.", flush=True)
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
