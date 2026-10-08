# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

"""Native XYZ geometry and 2D/3D sketch lifecycle on scratch documents."""
import math
from live_expansion_regression import call
from solidworks_mcp.sw_core import HANDLERS, require_part, sketch_segment_objects, value, flag_methods


def run():
    original = call("list_open_documents")["active_title"]
    scratch = []
    try:
        for kind in ("part", "assembly"):
            scratch.append(call("create_new_document", kind=kind)["document"]["title"])
            assert call("create_3d_sketch", name="Spatial")["is_3d"]
            call("draw_line", x1_mm=0, y1_mm=0, z1_mm=0, x2_mm=10, y2_mm=20, z2_mm=30)
            point = call("draw_point", x_mm=5, y_mm=-4, z_mm=7)
            assert math.dist(point["point_mm"], [5, -4, 7]) < .001
            curve_points = [[20, 0, 0], [25, 5, 8], [30, 0, 15]]
            call("draw_spline", points=[dict(zip(("x_mm", "y_mm", "z_mm"), p)) for p in curve_points])
            segments = call("list_sketch_segments")
            assert segments["sketch"] == "Spatial" and len(segments["segments"]) == 2
            line = next(s for s in segments["segments"] if s["type"] == "line")
            assert math.dist(line["start_mm"], [0, 0, 0]) < .001
            assert math.dist(line["end_mm"], [10, 20, 30]) < .001
            assert abs(line["length_mm"] - math.sqrt(1400)) < .001
            points = call("list_sketch_points")
            assert points["is_3d"] and points["sketch"] == "Spatial"
            assert len(points["points"]) == 6
            assert any(p["native_type"] == 1 and math.dist(p["model_point_mm"], [5, -4, 7]) < .001 for p in points["points"])
            if kind == "part":
                _, doc = require_part()
                spline = next(s for s in sketch_segment_objects(doc, "Spatial") if value(s, "GetType") == 3)
                curve = flag_methods(value(spline, "GetCurve"), "GetClosestPointOn")
                gaps = [math.dist([c / 1000 for c in p], curve.GetClosestPointOn(*[c / 1000 for c in p])[:3]) * 1000 for p in curve_points]
                assert max(gaps) < .01
            assert call("close_sketch")["is_3d"]
            if kind == "part":
                call("create_sketch", plane="front", name="Planar")
                for tool, args in (("draw_line", {"x1_mm": 0, "y1_mm": 0, "z1_mm": 1, "x2_mm": 1, "y2_mm": 1}),
                                   ("draw_point", {"x_mm": 1, "y_mm": 1, "z_mm": 1}),
                                   ("draw_spline", {"points": [{"x_mm": 0, "y_mm": 0}, {"x_mm": 1, "y_mm": 1, "z_mm": 1}]})):
                    try: HANDLERS[tool](args)
                    except RuntimeError as exc: assert "Nonzero Z" in str(exc)
                    else: raise AssertionError("2D geometry silently ignored Z.")
                assert call("list_sketch_segments")["segments"] == []
                call("draw_line", x1_mm=0, y1_mm=0, x2_mm=10, y2_mm=10)
                assert not call("close_sketch")["is_3d"]
            edited = call("edit_sketch", sketch_name="Spatial")
            assert edited["is_3d"] and edited["reference_confirmed"]
            assert call("list_sketch_points")["sketch"] == "Spatial"
            assert call("list_sketch_segments")["sketch"] == "Spatial"
            call("set_construction_geometry", selection={"sketch_name": "Spatial", "sketch_segments": [0]}, construction=True)
            assert call("list_sketch_segments")["segments"][0]["construction"]
            call("set_construction_geometry", selection={"sketch_name": "Spatial", "sketch_segments": [0]}, construction=False)
            assert not call("list_sketch_segments")["segments"][0]["construction"]
            relation = call("add_relation", relation="fixed", selection={"sketch_name": "Spatial", "sketch_segments": [0]})
            print("XYZ_FIXED_RELATION", kind, relation, flush=True)
            rejected = HANDLERS["create_3d_sketch"]({"name": "Unexpected"})
            assert not rejected["ok"]
            if kind == "part": assert not HANDLERS["create_sketch"]({"plane": "front"})["ok"]
            assert call("list_sketch_points")["is_3d"]
            call("draw_point", x_mm=-5, y_mm=4, z_mm=15)
            closed = call("close_sketch")
            assert closed["sketch"] == "Spatial" and closed["is_3d"]
            points = call("list_sketch_points", sketch_name="Spatial")
            assert sum(p["native_type"] == 1 for p in points["points"]) == 2
            sketches = call("list_sketches")
            assert {d["name"]: d["is_3d"] for d in sketches["sketch_details"]}["Spatial"]
            if kind == "part":
                call("edit_sketch", sketch_name="Spatial")
                call("create_plane", mode="offset", selection={"planes": ["front"]}, distance_mm=5, name="AutoExitPlane")
                assert not call("list_sketches")["sketch_open"]
                call("create_3d_sketch", name="TubePath")
                call("draw_line", x1_mm=0, y1_mm=0, x2_mm=10, y2_mm=20, z2_mm=30)
                call("surface_sweep", path_sketch="TubePath", circular_diameter_mm=2, name="SpatialTube")
                assert not call("list_sketches")["sketch_open"]
                area = sum(b["area_mm2"] for b in call("list_surface_bodies")["surface_bodies"])
                assert abs(area - 2 * math.pi * math.sqrt(1400)) < .01
                print("XYZ_SWEEP_AREA", area, flush=True)
            print("XYZ/LIFECYCLE", kind, segments["segments"], points["points"], flush=True)
        print("3D part/assembly line, point, spline, readback, reopen identity and 2D Z guards passed.", flush=True)
    finally:
        opened = {d["title"] for d in call("list_open_documents")["documents"]}
        for title in scratch:
            if title in opened: call("close_document", name=title, discard_changes=True)
        if original in opened: call("activate_document", name=original)


if __name__ == "__main__":
    run()
