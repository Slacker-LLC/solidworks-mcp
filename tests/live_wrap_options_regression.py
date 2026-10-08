# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

"""Multi-face wrap, pull references and definition edits on scratch parts."""
from live_expansion_regression import call
from solidworks_mcp.sw_core import HANDLERS


def run():
    original = call("list_open_documents")["active_title"]
    scratch = []
    def scene():
        scratch.append(call("create_new_document", kind="part")["document"]["title"])
        call("create_sketch", plane="front", name="Base")
        call("draw_circle", x_mm=0, y_mm=0, radius_mm=10); call("close_sketch")
        call("boss_extrude", sketch_name="Base", depth_mm=30)
        call("create_plane", mode="offset", selection={"planes": ["right"]}, distance_mm=10, name="WrapPlane")
        rectangle("Profile", (-20, -2, -10, 2))
        return next(f["index"] for f in call("list_faces")["faces"] if f["surface_type"] == "cylinder")
    def rectangle(name, coords):
        call("create_sketch", plane_name="WrapPlane", name=name)
        call("draw_rectangle", **dict(zip(("x1_mm", "y1_mm", "x2_mm", "y2_mm"), coords)))
        call("close_sketch")
    def near(actual, expected, tol=.1):
        assert abs(actual - expected) < tol, (actual, expected)
    try:
        for mode, method, expected in ((m, method, v) for method in ("analytical", "spline")
                                      for m, v in (("emboss", 168), ("engrave", -152), ("scribe", 0))):
            index = scene()
            call("wrap_sketch", sketch_name="Profile", face_index=index, mode="scribe", name="Split")
            rectangle("WideProfile", (-25, -4, -5, 4))
            indices = [f["index"] for f in call("list_faces")["faces"] if f["surface_type"] == "cylinder"]
            assert len(indices) == 2
            data = call("wrap_sketch", sketch_name="WideProfile", face_indices=indices, mode=mode, method=method, name="MultiWrap")
            near(data["volume_change_mm3"], expected)
            assert data["geometry_confirmed"] and data["requested_parameters"]["face_indices"] == indices
            assert call("get_wrap_data", name="MultiWrap")["wrap"]["source_sketch"] == "WideProfile"
            print("MULTI", mode, method, data["volume_change_mm3"], flush=True)

        for direction, expected in (("right", 39.9754168573), ("top", 75.4935086176), ("line", 39.9754168573), ("edge", 39.9754168573)):
            index = scene()
            if direction == "line":
                call("create_sketch", plane="front", name="Direction")
                call("draw_line", x1_mm=0, y1_mm=0, x2_mm=10, y2_mm=0); call("close_sketch")
                pull = {"sketch_name": "Direction", "sketch_segments": [0]}
            elif direction == "edge":
                call("create_sketch", plane="front", name="DirectionBlock")
                call("draw_rectangle", x1_mm=30, y1_mm=0, x2_mm=40, y2_mm=10); call("close_sketch")
                call("boss_extrude", sketch_name="DirectionBlock", depth_mm=30, merge=False)
                edge = next(e["index"] for e in call("list_edges")["edges"]
                            if e.get("curve_type") == "line" and abs(e.get("direction", [0])[0]) > .99)
                pull = {"edges": [edge]}
                index = next(f["index"] for f in call("list_faces")["faces"] if f["surface_type"] == "cylinder")
            else:
                pull = {"planes": [direction]}
            data = call("wrap_sketch", sketch_name="Profile", face_index=index, pull_selection=pull, name="DirectedWrap")
            near(data["volume_change_mm3"], expected, .001)
            assert data["wrap"]["pull_reference_confirmed"]
            print("PULL", direction, data["volume_change_mm3"], flush=True)

        index = scene()
        call("wrap_sketch", sketch_name="Profile", face_index=index, name="Editable")
        for args, expected in (({"thickness_mm": 2}, 88), ({"mode": "engrave"}, -72),
                               ({"mode": "scribe"}, 0), ({"mode": "emboss"}, 88),
                               ({"pull_selection": {"planes": ["right"]}}, 79.9100175565)):
            data = call("set_wrap_parameters", name="Editable", **args)
            near(data["volume_change_from_input_mm3"], expected, .001)
            assert data["parameters_confirmed"] and data["geometry_confirmed"]
            assert call("get_wrap_data", name="Editable")["wrap"]["thickness_mm"] == 2
            print("EDIT", args, data["volume_change_from_input_mm3"], flush=True)
        payload = HANDLERS["set_wrap_parameters"]({"name": "Editable", "clear_pull_direction": True})
        assert not payload["ok"] and not payload["data"]["parameters_confirmed"]
        assert payload["data"]["wrap"]["has_pull_direction"]
        payload = HANDLERS["set_wrap_parameters"]({"name": "Editable", "reverse_direction": True})
        assert not payload["ok"] and payload["data"]["parameters_confirmed"] and not payload["data"]["reverse_geometry_confirmed"]
        assert payload["data"]["wrap"]["reverse_direction"]
        call("set_wrap_parameters", name="Editable", reverse_direction=False)

        index = scene()
        payload = HANDLERS["wrap_sketch"]({"sketch_name": "Profile", "face_index": index, "reverse_direction": True, "name": "Reverse"})
        assert not payload["ok"] and payload["data"]["wrap"]["reverse_direction"]
        assert not payload["data"]["reverse_geometry_confirmed"]
        print("Multi-face modes/methods, plane/line/edge pull identity, thickness/mode/pull edits verified on twelve scratch parts; reverse geometry and clearing pull remain native gaps.", flush=True)
    finally:
        opened = {d["title"] for d in call("list_open_documents")["documents"]}
        for title in scratch:
            if title in opened: call("close_document", name=title, discard_changes=True)
        if original in opened: call("activate_document", name=original)


if __name__ == "__main__":
    run()
