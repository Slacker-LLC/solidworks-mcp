# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

"""Source Sketch replacement and target-reference readback on scratch parts."""
import math
from live_expansion_regression import call
from solidworks_mcp.sw_core import HANDLERS, require_part, get_bodies, flag_methods, value


def run():
    original = call("list_open_documents")["active_title"]
    scratch = []
    def scene():
        scratch.append(call("create_new_document", kind="part")["document"]["title"])
        for name, x, radius in (("Main", 0, 10), ("Other", 30, 5)):
            call("create_sketch", plane="front", name=name)
            call("draw_circle", x_mm=x, y_mm=0, radius_mm=radius); call("close_sketch")
            call("boss_extrude", sketch_name=name, depth_mm=30, merge=False, name=name + "Body")
        call("create_plane", mode="offset", selection={"planes": ["right"]}, distance_mm=10, name="WrapPlane")
        for name, half_width in (("Narrow", 2), ("Wide", 4)):
            call("create_sketch", plane_name="WrapPlane", name=name)
            call("draw_rectangle", x1_mm=-20, y1_mm=-half_width, x2_mm=-10, y2_mm=half_width)
            call("close_sketch")
        return next(f["index"] for f in call("list_faces")["faces"] if f.get("radius_mm") == 10)
    try:
        for method in ("analytical", "spline"):
            for mode, narrow, wide in (("emboss", 42, 84), ("engrave", -38, -76), ("scribe", 0, 0)):
                index = scene()
                initial = call("wrap_sketch", sketch_name="Narrow", face_index=index, mode=mode, method=method, name="Editable")
                target = initial["wrap"]["target_face"]
                assert target["surface_type"] == "cylinder" and target["radius_mm"] == 10
                assert math.isclose(target["area_mm2"], 2 * math.pi * 10 * 30, abs_tol=1e-6)
                for name, expected in (("Wide", wide), ("Narrow", narrow)):
                    data = call("set_wrap_parameters", name="Editable", source_sketch_name=name)
                    assert data["parameters_confirmed"] and data["geometry_confirmed"]
                    assert data["wrap"]["source_reference_confirmed"] and data["wrap"]["source_sketch"] == name
                    assert abs(data["volume_change_from_input_mm3"] - expected) < .1
                    queried = call("get_wrap_data", name="Editable")["wrap"]
                    assert queried["source_sketch"] == name and queried["target_face"] == target
                    assert queried["solid_body_count"] == 2
                    _, doc = require_part()
                    other = next(b for b in get_bodies(doc) if value(b, "Name") == "OtherBody")
                    other_volume = float(flag_methods(other, "GetMassProperties").GetMassProperties(1.)[3]) * 1e9
                    assert math.isclose(other_volume, math.pi * 25 * 30, abs_tol=1e-6)
                    print("SOURCE", method, mode, name, data["volume_change_from_input_mm3"], flush=True)
        for combined in (False, True):
            index = scene()
            call("wrap_sketch", sketch_name="Narrow", face_index=index, name="Editable")
            target = next(f["index"] for f in call("list_faces")["faces"] if f.get("radius_mm") == 5)
            args = {"name": "Editable", "target_face_index": target}
            if combined: args["source_sketch_name"] = "Wide"
            payload = HANDLERS["set_wrap_parameters"](args)
            assert not payload["ok"] and not payload["data"]["parameters_confirmed"]
            assert not payload["data"]["wrap"]["target_reference_confirmed"]
            assert payload["data"]["wrap"]["target_face"]["radius_mm"] == 10
            if combined:
                assert payload["data"]["wrap"]["source_reference_confirmed"]
            print("TARGET remains unchanged; partial source edit is reported accurately", combined, flush=True)
        print("Source edits in three modes and two methods, original-target geometry, two bodies and ignored target edits verified on eight scratch parts.", flush=True)
    finally:
        opened = {d["title"] for d in call("list_open_documents")["documents"]}
        for title in scratch:
            if title in opened: call("close_document", name=title, discard_changes=True)
        if original in opened: call("activate_document", name=original)


if __name__ == "__main__":
    run()
