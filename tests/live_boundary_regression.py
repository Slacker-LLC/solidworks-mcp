# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

"""Boundary surfaces and solid-forming options on scratch parts."""
import math
from live_expansion_regression import call, near
from solidworks_mcp.sw_core import HANDLERS


def run():
    original = call("list_open_documents")["active_title"]
    scratch = []
    def part():
        scratch.append(call("create_new_document", kind="part")["document"]["title"])
    def line(name, points):
        call("create_sketch", plane="front", name=name)
        call("draw_line", x1_mm=points[0], y1_mm=points[1], x2_mm=points[2], y2_mm=points[3]); call("close_sketch")
    def curves(names):
        return [{"selection": {"sketches": [name]}} for name in names]
    def finish(args, area):
        info = call("boundary_surface", **args, name="Boundary")["boundary"]
        near(info["feature_face_area_mm2"], area, "boundary face area")
        features = call("list_features")
        assert call("get_boundary_feature_data", name="Boundary")["boundary"] == info
        assert call("list_features") == features
        return info
    try:
        for two_directions, trim1, trim2 in ((False, False, False), (True, False, False), (True, True, False), (True, False, True), (True, True, True)):
            part()
            line("Bottom", (-5, 0, 15, 0)); line("Top", (-5, 10, 15, 10))
            args = {"direction1": curves(["Bottom", "Top"])}
            if two_directions:
                line("Left", (0, -5, 0, 15)); line("Right", (10, -5, 10, 15))
                args.update(direction2=curves(["Left", "Right"]), trim_direction1=trim1, trim_direction2=trim2)
            # Full extent 20x20 with both directions; each trim restricts its extent to 10 mm.
            expected = (10 if trim1 else 20) * (10 if trim2 else 20) if two_directions else 200
            info = finish(args, expected)
            assert [d["curve_count"] for d in info["directions"]] == [2, 2 if two_directions else 0]
            assert info["bodies"][0]["body_type"] == 1

        part()
        for name, points in (("Bottom", (0, 0, 10, 0)), ("Middle", (0, 5, 10, 5)), ("Top", (0, 10, 10, 10))): line(name, points)
        info = finish({"direction1": curves(["Bottom", "Middle", "Top"])}, 100)
        assert info["directions"][0]["curve_count"] == 3

        for solid, tangency, profile in ((False, "none", "sketch"), (False, "normal_to_profile", "sketch"),
                                         (True, "none", "body"), (False, "none", "edge"),
                                         (False, "tangent_to_face", "face"), (False, "curvature_to_face", "face")):
            part(); call("create_plane", mode="offset", selection={"planes": ["front"]}, distance_mm=10, name="EndPlane")
            for name, plane in (("Start", "front"), ("End", "EndPlane")):
                call("create_sketch", plane=plane, name=name); call("draw_circle", x_mm=0, y_mm=0, radius_mm=5); call("close_sketch")
                if profile != "sketch": call("planar_surface", sketch_name=name, name=name + "Cap")
            if profile == "sketch": selected = [{"sketches": [name]} for name in ("Start", "End")]
            elif profile == "body": selected = [{"surface_bodies": [b["index"]]} for b in call("list_surface_bodies")["surface_bodies"]]
            elif profile == "edge": selected = [{"surface_edges": [e["index"]]} for e in call("list_edges", body_type="surface")["edges"]]
            else: selected = [{"surface_faces": [f["index"]]} for f in call("list_faces", body_type="surface")["faces"]]
            args = {"direction1": [{"selection": s, "tangency": tangency} for s in selected], "create_solid": solid, "merge_result": solid}
            if tangency in ("tangent_to_face", "curvature_to_face"):
                payload = HANDLERS["boundary_surface"]({**args, "name": "UnconfirmedContinuity"})
                assert not payload["ok"] and payload["data"]["feature"] == "UnconfirmedContinuity"
                checks = [c["continuity"] for c in payload["data"]["boundary"]["directions"][0]["curves"]]
                assert all(not c["confirmed"] and c["maximum_normal_angle_deg"] > 89 for c in checks)
                print("Native stored continuity does not match endpoint normals; feature preserved as unconfirmed.", flush=True)
            else:
                info = finish(args, 2 * math.pi * 5 * 10)
                assert info["bodies"][0]["body_type"] == (0 if solid else 1)
                if solid: near(info["bodies"][0]["volume_mm3"], math.pi * 25 * 10, "formed solid volume")
        print("Boundary one/two-direction trims, three profiles, circle/edge profiles, normal condition and formed-solid volume verified on 12 scratch parts; G1/G2 remains a native gap.", flush=True)
    finally:
        opened = {d["title"] for d in call("list_open_documents")["documents"]}
        for name in scratch:
            if name in opened: call("close_document", name=name, discard_changes=True)
        if original in opened: call("activate_document", name=original)


if __name__ == "__main__":
    run()
