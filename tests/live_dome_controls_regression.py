# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

"""Whole-sketch dome constraints and native control-clear readback."""
import math
from live_expansion_regression import call
from solidworks_mcp.sw_core import HANDLERS


def run():
    original = call("list_open_documents")["active_title"]
    scratch = []
    try:
        scratch.append(call("create_new_document", kind="part")["document"]["title"])
        call("create_sketch", plane="front", name="Base")
        call("draw_circle", x_mm=0, y_mm=0, radius_mm=10); call("close_sketch")
        call("boss_extrude", sketch_name="Base", depth_mm=30)
        call("create_plane", mode="offset", selection={"planes": ["front"]}, distance_mm=35, name="ApexPlane")
        for name, shape in (("Apex", "point"), ("ApexTwo", "two"), ("ApexMixed", "mixed"), ("ApexLine", "line"), ("ApexCircle", "circle")):
            call("create_sketch", plane_name="ApexPlane", name=name)
            if shape in ("point", "two", "mixed"):
                call("draw_point", x_mm=3, y_mm=0)
                if shape != "point": call("draw_point", x_mm=-3, y_mm=0)
                if shape == "mixed": call("draw_line", x1_mm=-2, y1_mm=-2, x2_mm=2, y2_mm=-2)
            elif shape == "line": call("draw_line", x1_mm=-3, y1_mm=0, x2_mm=3, y2_mm=0)
            else: call("draw_circle", x_mm=0, y_mm=0, radius_mm=2)
            call("close_sketch")
        top = next(f["index"] for f in call("list_faces")["faces"] if f.get("normal", [0, 0, 0])[2] > .99)
        call("dome", face_index=top, height_mm=5, name="SketchDome")
        point = call("set_dome_parameters", name="SketchDome", constraint_sketch_name="Apex")
        check = point["dome"]["constraint_check"]
        assert point["dome"]["constraint_reference_confirmed"] and point["dome"]["height_mm"] == 0
        assert check["kind"] == "sketch" and check["sample_count"] == 1 and check["confirmed"]
        assert math.dist(check["samples_mm"][0], [3, 0, 35]) < .001
        print("WHOLE_POINT", check, flush=True)
        clear = HANDLERS["set_dome_parameters"]({"name": "SketchDome", "clear_constraint": True, "height_mm": 5})
        assert not clear["ok"] and not clear["data"]["parameters_confirmed"]
        assert clear["data"]["dome"]["has_constraint"] and not clear["data"]["dome"]["constraint_reference_confirmed"]
        assert clear["data"]["dome"]["height_mm"] == 0
        assert abs(clear["data"]["dome"]["solid_volume_mm3"] - point["dome"]["solid_volume_mm3"]) < 1e-6
        print("CLEAR_CONSTRAINT_IGNORED", flush=True)
        for name in ("ApexTwo", "ApexMixed"):
            edited = call("set_dome_parameters", name="SketchDome", constraint_sketch_name=name)
            check = edited["dome"]["constraint_check"]
            assert edited["dome"]["constraint_reference_confirmed"]
            assert check["confirmed"] and check["sample_count"] == 2
            assert sorted(round(p[0]) for p in check["samples_mm"]) == [-3, 3]
            assert all(abs(p[2] - 35) < .001 for p in check["samples_mm"])
            print("WHOLE_POINT_SKETCH", name, check, flush=True)
        volume = call("get_dome_data", name="SketchDome")["dome"]["solid_volume_mm3"]
        for name in ("ApexLine", "ApexCircle"):
            try:
                HANDLERS["set_dome_parameters"]({"name": "SketchDome", "constraint_sketch_name": name})
            except RuntimeError as exc:
                assert "explicit user sketch point" in str(exc)
            else:
                raise AssertionError("A curve-only constraint must be rejected before native modification.")
            assert abs(call("get_dome_data", name="SketchDome")["dome"]["solid_volume_mm3"] - volume) < 1e-6
            print("CURVE_ONLY_SKETCH_REJECTED", name, flush=True)

        scratch.append(call("create_new_document", kind="part")["document"]["title"])
        call("create_sketch", plane="front", name="RectBase")
        call("draw_rectangle", x1_mm=-10, y1_mm=-5, x2_mm=10, y2_mm=5); call("close_sketch")
        call("boss_extrude", sketch_name="RectBase", depth_mm=30)
        top = next(f["index"] for f in call("list_faces")["faces"] if f.get("normal", [0, 0, 0])[2] > .99)
        call("dome", face_index=top, height_mm=5, name="DirectionDome")
        absent = call("set_dome_parameters", name="DirectionDome", clear_direction=True)
        assert absent["dome"]["direction_reference_confirmed"] and not absent["dome"]["has_direction"]
        vertical = next(e["index"] for e in call("list_edges")["edges"] if e.get("curve_type") == "line" and abs(e.get("direction", [0, 0, 0])[2]) > .99)
        directed = HANDLERS["set_dome_parameters"]({"name": "DirectionDome", "direction_edge_index": vertical})
        print("VERTICAL_DIRECTION", directed, flush=True)
        assert not directed["ok"] and not directed["data"]["dome"]["direction_reference_confirmed"]
        assert directed["data"]["geometry_confirmed"]
        assert abs(directed["data"]["volume_change_from_input_mm3"] + 416.07447704119) < .001
        assert directed["data"]["dome"]["direction_vector"][2] < -.99
        assert directed["data"]["dome"]["center_probes"][0]["surface_gap_mm"] < .01
        edge = next(e["index"] for e in call("list_edges")["edges"] if e.get("curve_type") == "line" and abs(e.get("direction", [0])[0]) > .99)
        edited = HANDLERS["set_dome_parameters"]({"name": "DirectionDome", "direction_edge_index": edge})
        assert not edited["ok"] and edited["data"]["dome"]["has_direction"]
        cleared = HANDLERS["set_dome_parameters"]({"name": "DirectionDome", "clear_direction": True})
        print("CLEAR_DIRECTION", cleared, flush=True)
        assert not cleared["ok"] and cleared["data"]["dome"]["has_direction"]
        assert not cleared["data"]["dome"]["direction_reference_confirmed"]
        assert abs(cleared["data"]["volume_change_from_input_mm3"]) < 1e-6
        print("Single/multiple/mixed point-sketch constraints, curve-only rejection and native control clears verified on two scratch parts.", flush=True)
    finally:
        opened = {d["title"] for d in call("list_open_documents")["documents"]}
        for title in scratch:
            if title in opened: call("close_document", name=title, discard_changes=True)
        if original in opened: call("activate_document", name=original)


if __name__ == "__main__":
    run()
