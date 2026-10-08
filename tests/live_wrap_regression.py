# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

"""Wrap geometry checks using independent scratch parts."""
from live_expansion_regression import call, near
from solidworks_mcp.sw_core import require_part, get_bodies, flag_methods


def run():
    original = call("list_open_documents")["active_title"]
    scratch = []
    try:
        for mode in (0, 1, 2):
            for method in (0, 1):
                scratch.append(call("create_new_document", kind="part")["document"]["title"])
                call("create_sketch", plane="front", name="Base")
                call("draw_circle", x_mm=0, y_mm=0, radius_mm=10); call("close_sketch")
                call("boss_extrude", sketch_name="Base", depth_mm=30)
                call("create_plane", mode="offset", selection={"planes": ["right"]}, distance_mm=10, name="WrapPlane")
                call("create_sketch", plane_name="WrapPlane", name="WrapProfile")
                call("draw_rectangle", x1_mm=-20, y1_mm=-2, x2_mm=-10, y2_mm=2); call("close_sketch")
                _, doc = require_part()
                def volume():
                    return sum(flag_methods(b, "GetMassProperties").GetMassProperties(1.)[3] * 1e9 for b in get_bodies(doc))
                before = volume()
                face_index = next(f["index"] for f in call("list_faces")["faces"] if f["surface_type"] == "cylinder")
                before_count = len(call("list_faces")["faces"])
                data = call("wrap_sketch", sketch_name="WrapProfile", face_index=face_index,
                            mode=("emboss", "engrave", "scribe")[mode], method=("analytical", "spline")[method],
                            thickness_mm=1, name="Wrapped")
                delta = data["volume_change_mm3"]
                info = data["wrap"]
                assert data["geometry_confirmed"] and info["source_sketch"] == "WrapProfile"
                queried = call("get_wrap_data", name="Wrapped")["wrap"]
                assert queried.keys() == info.keys()
                for key, expected in info.items():
                    if isinstance(expected, float):
                        assert abs(queried[key] - expected) < 1e-6, (key, queried[key], expected)
                    else:
                        assert queried[key] == expected, (key, queried[key], expected)
                assert abs(volume() - before - delta) < 1e-6
                assert info["solid_face_count"] > before_count
                print(mode, method, delta, info["solid_face_count"], flush=True)
                assert abs(delta - (42., -38., 0.)[mode]) < .1
        print("Wrap modes, methods, volume changes, split topology and read-only definition verified on six scratch parts.", flush=True)
    finally:
        opened = {d["title"] for d in call("list_open_documents")["documents"]}
        for name in scratch:
            if name in opened: call("close_document", name=name, discard_changes=True)
        if original in opened: call("activate_document", name=original)


if __name__ == "__main__":
    run()
