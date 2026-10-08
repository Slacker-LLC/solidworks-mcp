# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

"""Native domes, shape checks and definition edits on scratch parts."""
import math
from live_expansion_regression import call
from solidworks_mcp.sw_core import HANDLERS


def run(shapes=True):
    original = call("list_open_documents")["active_title"]
    scratch = []
    def scene(shape="circle", two=False):
        scratch.append(call("create_new_document", kind="part")["document"]["title"])
        call("create_sketch", plane="front", name="Base")
        if shape == "rectangle": call("draw_rectangle", x1_mm=-10, y1_mm=-5, x2_mm=10, y2_mm=5)
        elif shape == "ellipse": call("draw_ellipse", center_x_mm=0, center_y_mm=0, major_x_mm=10, major_y_mm=0, minor_x_mm=0, minor_y_mm=5)
        else: call("draw_circle", x_mm=0, y_mm=0, radius_mm=10)
        if two: call("draw_circle", x_mm=30, y_mm=0, radius_mm=5)
        call("close_sketch"); call("boss_extrude", sketch_name="Base", depth_mm=30, merge=False)
        return [f["index"] for f in call("list_faces")["faces"] if f.get("normal", [0, 0, 0])[2] > .99]
    def near(actual, expected, tol=.2):
        assert abs(actual - expected) < tol, (actual, expected)
    try:
        for shape in (("circle", "ellipse") if shapes else ()):
            for reverse in (False, True):
                for elliptical in (False, True) if shape == "circle" else (True,):
                    index = scene(shape)[0]
                    data = call("dome", face_index=index, height_mm=5, reverse_direction=reverse, elliptical=elliptical, name="Dome")
                    expected = (2 / 3 * math.pi * 10 * (10 if shape == "circle" else 5) * 5 if elliptical else math.pi * 5 * (300 + 25) / 6)
                    # Elliptical native surfaces are spline approximations; also
                    # require the independent 24-point surface-gap check below.
                    near(data["volume_change_mm3"], expected * (-1 if reverse else 1),
                         max(.2, expected * .002) if elliptical else .2)
                    assert data["geometry_confirmed"]
                    if elliptical: assert data["dome"]["ellipsoid_check"]["confirmed"] and data["dome"]["ellipsoid_check"]["sample_count"] == 24
                    queried = call("get_dome_data", name="Dome")["dome"]
                    assert queried["height_mm"] == 5 and queried["reverse_direction"] == reverse
                    near(queried["solid_volume_mm3"], data["dome"]["solid_volume_mm3"], 1e-6)
                    print("SHAPE", shape, reverse, elliptical, data["volume_change_mm3"],
                          data["dome"].get("ellipsoid_check"), flush=True)
                    if shape == "circle" and not reverse and not elliptical:
                        for args, expected in (({"height_mm": 8}, math.pi * 8 * (300 + 64) / 6),
                                               ({"elliptical": True}, 2 / 3 * math.pi * 100 * 8),
                                               ({"reverse_direction": True}, -2 / 3 * math.pi * 100 * 8)):
                            edited = call("set_dome_parameters", name="Dome", **args)
                            near(edited["volume_change_from_input_mm3"], expected)
                            assert edited["parameters_confirmed"] and edited["geometry_confirmed"]

        index = scene("rectangle")[0]
        call("create_plane", mode="offset", selection={"planes": ["front"]}, distance_mm=35, name="ApexPlane")
        call("create_sketch", plane_name="ApexPlane", name="Apex")
        call("draw_point", x_mm=3, y_mm=0); call("close_sketch")
        data = call("dome", face_index=index, height_mm=5, name="RectDome")
        near(data["dome"]["center_probes"][0]["normal_displacement_mm"], 5, .001)
        # Native B-spline boxes overestimate the height; center samples remain exact.
        assert data["dome"]["feature_faces"][0]["box_mm"][5] > 38
        constrained = call("set_dome_parameters", name="RectDome", constraint_selection={"sketch_name": "Apex", "sketch_points": [0]})
        assert constrained["dome"]["height_mm"] == 0 and constrained["dome"]["constraint_reference_confirmed"]
        near(constrained["dome"]["constraint_point_mm"][2], 35, .001)
        assert constrained["dome"]["constraint_surface_gap_mm"] < .01

        index = scene("rectangle")[0]
        call("dome", face_index=index, height_mm=5, name="FaceEdit")
        bottom = next(f["index"] for f in call("list_faces")["faces"] if f.get("normal", [0, 0, 0])[2] < -.99)
        edited = call("set_dome_parameters", name="FaceEdit", face_index=bottom)
        assert edited["dome"]["face_references_confirmed"] and edited["geometry_confirmed"]
        near(edited["dome"]["center_probes"][0]["point_mm"][2], -5, .001)

        index = scene("rectangle")[0]
        call("dome", face_index=index, height_mm=5, name="DirectionEdit")
        edge = next(e["index"] for e in call("list_edges")["edges"] if e.get("curve_type") == "line" and abs(e.get("direction", [0])[0]) > .99)
        payload = HANDLERS["set_dome_parameters"]({"name": "DirectionEdit", "direction_edge_index": edge})
        print("DIRECTION", payload, flush=True)
        assert payload["data"]["dome"]["has_direction"]
        # Selecting an output edge can change its native persistent identity
        # during rollback. Presence alone must never count as identity proof.
        assert not payload["ok"] and not payload["data"]["geometry_confirmed"]
        near(payload["data"]["volume_change_from_input_mm3"], 0, 1e-6)

        index = scene("rectangle")[0]
        payload = HANDLERS["dome"]({"face_index": index, "height_mm": 5, "elliptical": True, "name": "UnconfirmedEllipsoid"})
        assert not payload["ok"] and not payload["data"]["dome"]["ellipsoid_check"]["confirmed"]
        indices = scene(two=True)
        assert len(indices) == 2
        payload = HANDLERS["dome"]({"face_indices": indices, "height_mm": 5, "name": "Disconnected"})
        assert not payload["ok"]
        print("Dome regression passed; scratch parts:", len(scratch), "shape cases:", shapes, flush=True)
    finally:
        opened = {d["title"] for d in call("list_open_documents")["documents"]}
        for title in scratch:
            if title in opened: call("close_document", name=title, discard_changes=True)
        if original in opened: call("activate_document", name=original)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controls-only", action="store_true")
    run(shapes=not parser.parse_args().controls_only)
