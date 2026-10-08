# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for details.
# limitations under the License.

"""Midsurface geometry, pairing, knitting and unconfirmed placement on scratch parts."""
import math
from live_expansion_regression import call, near, volume
from solidworks_mcp.sw_core import HANDLERS


def run():
    original = call("list_open_documents")["active_title"]
    scratch = []
    def part():
        scratch.append(call("create_new_document", kind="part")["document"]["title"])
        call("create_sketch", plane="front", name="SourceProfile")
    try:
        for knit, hole in ((False, False), (True, False), (True, True)):
            part()
            call("draw_rectangle", x1_mm=0, y1_mm=0, x2_mm=20, y2_mm=10)
            if hole:
                call("draw_circle", x_mm=10, y_mm=5, radius_mm=1)
            call("close_sketch")
            call("boss_extrude", sketch_name="SourceProfile", depth_mm=2)
            source_volume = volume()
            info = call("mid_surface", knit=knit, name="PlateMid")["midsurface"]
            assert info["face_pair_count"] == info["face_count"] == info["sheet_count"] == 1
            near(info["area_mm2"], 200 - (math.pi if hole else 0), "plate midsurface area")
            near(info["faces"][0]["point_mm"][2], 1, "plate midsurface position")
            near(info["face_pairs"][0]["thickness_mm"], 2, "source face-pair thickness")
            near(info["faces"][0]["measured_placement"], 0, "measured central placement")
            before = call("list_features")
            assert call("get_mid_surface_data", name="PlateMid")["midsurface"] == info
            assert call("list_features") == before
            near(volume(), source_volume, "original solid volume preserved")
        for knit in (False, True):
            part()
            points = [(0, 0), (20, 0), (20, 2), (2, 2), (2, 10), (0, 10)]
            for a, b in zip(points, points[1:] + points[:1]):
                call("draw_line", x1_mm=a[0], y1_mm=a[1], x2_mm=b[0], y2_mm=b[1])
            call("close_sketch")
            call("boss_extrude", sketch_name="SourceProfile", depth_mm=30)
            info = call("mid_surface", knit=knit, name="LMid")["midsurface"]
            assert info["face_pair_count"] == info["face_count"] == 2
            assert info["sheet_count"] == (1 if knit else 2)
            near(info["area_mm2"], ((20 - 1) + (10 - 1)) * 30, "L midsurface area")
            assert sorted(round(face["area_mm2"]) for face in info["faces"]) == [270, 570]
            assert all(math.isclose(pair["thickness_mm"], 2) for pair in info["face_pairs"])
            assert all(abs(face["measured_placement"]) < 1e-6 for face in info["faces"])
            assert call("get_mid_surface_data", name="LMid")["midsurface"] == info
        part()
        for radius in (7, 5):
            call("draw_circle", x_mm=0, y_mm=0, radius_mm=radius)
        call("close_sketch")
        call("boss_extrude", sketch_name="SourceProfile", depth_mm=30)
        info = call("mid_surface", name="PipeMid")["midsurface"]
        near(info["area_mm2"], 2 * math.pi * 6 * 30, "cylindrical midsurface area")
        near(info["faces"][0]["radius_mm"], 6, "neutral cylinder radius")
        near(info["face_pairs"][0]["thickness_mm"], 2, "pipe wall thickness")
        assert call("get_mid_surface_data", name="PipeMid")["midsurface"] == info
        for placement in (-1, -0.5, 0.5, 1):
            part()
            call("draw_rectangle", x1_mm=0, y1_mm=0, x2_mm=20, y2_mm=10)
            call("close_sketch")
            call("boss_extrude", sketch_name="SourceProfile", depth_mm=2)
            payload = HANDLERS["mid_surface"]({"placement": placement, "name": "UnconfirmedMid"})
            assert not payload["ok"] and payload["data"]["feature"] == "UnconfirmedMid"
            actual = call("get_mid_surface_data", name="UnconfirmedMid")["midsurface"]
            near(actual["faces"][0]["measured_placement"], 0, "native nonzero placement remains unapplied")
        print("Planar/holed/L/cylindrical midsurfaces and read-only inspection verified; nonzero placement remains a native gap.", flush=True)
    finally:
        opened = {d["title"] for d in call("list_open_documents")["documents"]}
        for title in scratch:
            if title in opened:
                call("close_document", name=title, discard_changes=True)
        if original and original in opened:
            call("activate_document", name=original)


if __name__ == "__main__":
    run()
