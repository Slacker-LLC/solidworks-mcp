# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for details.

"""Offset, knit and thicken tests with surface area and solid-volume readback."""

from live_expansion_regression import block, call, near, volume
from solidworks_mcp.sw_core import BODY_SHEET, as_list, byref_long, get_bodies, require_part, running_app, value


def rectangle_surface(name, start, end):
    call("create_sketch", plane="front", name=name)
    call("draw_rectangle", x1_mm=start, y1_mm=0, x2_mm=end, y2_mm=10)
    call("close_sketch")
    call("planar_surface", sketch_name=name)


def run():
    app = running_app()
    initial = {str(value(d, "GetTitle")) for d in as_list(value(app, "GetDocuments"))}
    original = str(value(app.ActiveDoc, "GetTitle")) if app.ActiveDoc is not None else ""
    try:
        block()
        top = call("list_faces", normal=[0, 0, 1])["faces"]
        assert len(top) == 1, top
        call("offset_surface", selection={"faces": [top[0]["index"]]}, distance_mm=5)
        near(sum(b["area_mm2"] for b in call("list_surface_bodies")["surface_bodies"]), 100, "offset area")
        _, doc = require_part()
        box = list(value(get_bodies(doc, BODY_SHEET)[0], "GetBodyBox"))
        near(abs(box[2] * 1000 - 10), 5, "offset distance")
        call("create_new_document", kind="part")
        rectangle_surface("First", 0, 10)
        rectangle_surface("Second", 10, 20)
        assert len(call("list_surface_bodies")["surface_bodies"]) == 2
        call("knit_surfaces", selection={"surface_bodies": [0, 1]})
        bodies = call("list_surface_bodies")["surface_bodies"]
        assert len(bodies) == 1, bodies
        near(bodies[0]["area_mm2"], 200, "knit area")
        call("thicken_surface", selection={"surface_bodies": [0]}, thickness_mm=3)
        near(volume(), 600, "thickened volume")
        call("create_new_document", kind="part")
        rectangle_surface("BothSides", 0, 10)
        call("thicken_surface", selection={"surface_bodies": [0]}, thickness_mm=3, direction="both")
        near(volume(), 600, "two-sided thickened volume")
        print("Offset distance, knit area and thicken volume verified.", flush=True)
    finally:
        for title in [str(value(doc, "GetTitle")) for doc in as_list(value(app, "GetDocuments"))]:
            if title not in initial:
                app.CloseDoc(title)
        if original:
            app.ActivateDoc3(original, False, 0, byref_long())
        assert {str(value(d, "GetTitle")) for d in as_list(value(app, "GetDocuments"))} == initial


if __name__ == "__main__":
    run()
