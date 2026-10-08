# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for details.
# limitations under the License.

"""Surface boundary selection and editing on new disposable parts."""

import math
from live_expansion_regression import call, near
from live_surface_construction_regression import area
from solidworks_mcp.sw_core import as_list, byref_long, running_app, value


def plate(hole=False):
    call("create_new_document", kind="part")
    call("create_sketch", plane="front", name="PlateProfile")
    call("draw_rectangle", x1_mm=0, y1_mm=0, x2_mm=10, y2_mm=10)
    if hole:
        call("draw_circle", x_mm=5, y_mm=5, radius_mm=1)
    call("close_sketch")
    call("planar_surface", sketch_name="PlateProfile")
    near(area(), 100 - (math.pi if hole else 0), "initial plate area")
    faces = call("list_faces", body_type="surface")["faces"]
    edges = call("list_edges", body_type="surface")["edges"]
    assert len(faces) == 1 and len(edges) == (5 if hole else 4)
    assert call("list_faces")["total_faces"] == 0
    return faces, edges


def run():
    app = running_app()
    initial = {str(value(d, "GetTitle")) for d in as_list(value(app, "GetDocuments"))}
    original = str(value(app.ActiveDoc, "GetTitle")) if app.ActiveDoc is not None else ""
    try:
        for linear in (False, True):
            plate()
            call("extend_surface", selection={"surface_edges": [0]}, distance_mm=5, linear=linear)
            near(area(), 150, "one edge extension")
        plate()
        call("extend_surface", selection={"surface_faces": [0]}, distance_mm=5)
        near(area(), 400, "all boundary extension")
        plate(hole=True)
        call("untrim_surface", selection={"surface_faces": [0]}, face_mode="internal")
        near(area(), 100, "internal hole restoration")
        _, edges = plate()
        top = next(e["index"] for e in edges if abs(e["start_mm"][1] - 10) < 1e-6 and abs(e["end_mm"][1] - 10) < 1e-6)
        call("create_sketch", plane="front", name="TargetPoint")
        call("draw_point", x_mm=5, y_mm=15)
        call("close_sketch")
        call("extend_surface", selection={"surface_edges": [top]}, end_condition="up_to_point",
             target_selection={"sketch_points": [0], "sketch_name": "TargetPoint"})
        near(area(), 150, "extension to point")
        _, edges = plate()
        top = next(e["index"] for e in edges if abs(e["start_mm"][1] - 10) < 1e-6 and abs(e["end_mm"][1] - 10) < 1e-6)
        call("create_sketch", plane="front", name="StopBodyProfile")
        call("draw_rectangle", x1_mm=0, y1_mm=15, x2_mm=10, y2_mm=20)
        call("close_sketch")
        call("boss_extrude", sketch_name="StopBodyProfile", depth_mm=5)
        face = call("list_faces", normal=[0, -1, 0])["faces"][0]["index"]
        call("extend_surface", selection={"surface_edges": [top]}, end_condition="up_to_surface",
             target_selection={"faces": [face]})
        near(area(), 150, "extension to solid face")
        for merge, opposite in ((True, False), (False, False), (False, True)):
            call("create_new_document", kind="part")
            call("create_sketch", plane="front", name="CircleProfile")
            call("draw_circle", x_mm=0, y_mm=0, radius_mm=3)
            call("close_sketch")
            call("planar_surface", sketch_name="CircleProfile")
            near(area(), math.pi * 9, "circular patch")
            call("untrim_surface", selection={"surface_faces": [0]}, face_mode="external",
                 merge=merge, trim_opposite_side=opposite)
            bodies = call("list_surface_bodies")["surface_bodies"]
            edges = call("list_edges", body_type="surface")["edges"]
            assert len(bodies) == (1 if merge else 2)
            lines = [edge for edge in edges if edge["curve_type"] == "line"]
            assert len(lines) == 4
            new_index = lines[0]["body_index"]
            assert all(edge["body_index"] == new_index for edge in lines)
            corners = [edge[key] for edge in lines for key in ("start_mm", "end_mm")]
            rectangle = ((max(p[0] for p in corners) - min(p[0] for p in corners)) *
                         (max(p[1] for p in corners) - min(p[1] for p in corners)))
            assert rectangle > math.pi * 9
            near(bodies[new_index]["area_mm2"], rectangle - (math.pi * 9 if opposite else 0), "external untrim region")
            if not merge:
                near(bodies[1 - new_index]["area_mm2"], math.pi * 9, "original circular patch preserved")
        plate()
        call("untrim_surface", selection={"surface_edges": [0]}, extend_percent=20)
        near(area(), 120, "edge untrim percentage")
        call("create_new_document", kind="part")
        call("create_sketch", plane="front", name="NotchedProfile")
        points = [(0, 0), (10, 0), (10, 5), (5, 5), (5, 10), (0, 10)]
        for first, second in zip(points, points[1:] + points[:1]):
            call("draw_line", x1_mm=first[0], y1_mm=first[1], x2_mm=second[0], y2_mm=second[1])
        call("close_sketch")
        call("planar_surface", sketch_name="NotchedProfile")
        near(area(), 75, "notched surface")
        edges = call("list_edges", body_type="surface")["edges"]
        notch = [e["index"] for e in edges if all(p[0] >= 5 and p[1] >= 5 for p in (e["start_mm"], e["end_mm"]))]
        assert len(notch) == 2
        call("untrim_surface", selection={"surface_edges": notch}, edge_mode="connect_endpoints")
        near(area(), 87.5, "connected notch triangular fill")
        print("Surface extension, face/edge untrim, percentage and opposite-region geometry verified.", flush=True)
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
