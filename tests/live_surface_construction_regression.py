# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for details.

"""Surface construction areas and scoped surface-cut volumes on scratch parts."""

import math
from live_expansion_regression import block, call, near, volume
from solidworks_mcp.sw_core import as_list, byref_long, running_app, value


def area():
    return sum(body["area_mm2"] for body in call("list_surface_bodies")["surface_bodies"])


def revolve(angle, mode="one_direction", second=0):
    call("create_new_document", kind="part")
    call("create_sketch", plane="front", name="RevolveProfile")
    call("draw_line", x1_mm=5, y1_mm=0, x2_mm=5, y2_mm=10)
    call("draw_centerline", x1_mm=0, y1_mm=0, x2_mm=0, y2_mm=10)
    call("close_sketch")
    call("surface_revolve", sketch_name="RevolveProfile", angle_deg=angle, mode=mode, angle2_deg=second)
    near(area(), 2 * math.pi * 5 * 10 * (angle + second) / 360, "revolved surface area")


def loft():
    call("create_new_document", kind="part")
    call("create_plane", mode="offset", selection={"planes": ["front"]}, distance_mm=10, name="UpperPlane")
    for plane, name in (("front", "Lower"), ("UpperPlane", "Upper")):
        call("create_sketch", plane=plane, name=name)
        call("draw_circle", x_mm=0, y_mm=0, radius_mm=5)
        call("close_sketch")
    call("surface_loft", profile_sketches=["Lower", "Upper"])
    near(area(), 2 * math.pi * 5 * 10, "lofted cylinder area")


def cut(flip=False, scoped=False):
    block()
    if scoped:
        call("move_copy_bodies", selection={"bodies": [0]}, copy=True, copies=1, dx_mm=15)
        near(volume(), 2000, "surface-cut initial two-body volume")
    call("create_plane", mode="offset", selection={"planes": ["front"]}, distance_mm=5, name="CutPlane")
    arguments = {"body_indices": [0]} if scoped else {}
    call("cut_with_surface", selection={"planes": ["CutPlane"]}, flip=flip, **arguments)
    near(volume(), 1500 if scoped else 500, "scoped surface-cut volume")


def run():
    app = running_app()
    initial = {str(value(d, "GetTitle")) for d in as_list(value(app, "GetDocuments"))}
    original = str(value(app.ActiveDoc, "GetTitle")) if app.ActiveDoc is not None else ""
    try:
        revolve(360)
        revolve(180, "midplane")
        revolve(90, "two_directions", 90)
        loft()
        cut()
        cut(flip=True)
        cut(scoped=True)
        print("Surface revolve/loft areas and both-direction/scoped surface-cut volumes verified.", flush=True)
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
