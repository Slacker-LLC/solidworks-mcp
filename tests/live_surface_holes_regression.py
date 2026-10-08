# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for details.
# limitations under the License.

"""Verify selective and multiple surface-hole removal on disposable parts."""
import math
from types import SimpleNamespace
from unittest.mock import patch
from live_expansion_regression import call, near
from live_surface_construction_regression import area
from solidworks_mcp import sw_multibody
from solidworks_mcp.sw_core import running_app


def run():
    app = running_app()
    original = call("list_open_documents")["active_title"]
    scratch = []
    try:
        for legacy in (False, True):
            title = call("create_new_document", kind="part")["document"]["title"]
            scratch.append(title)
            call("create_sketch", plane="front", name="ThreeHoles")
            call("draw_rectangle", x1_mm=0, y1_mm=0, x2_mm=20, y2_mm=10)
            for x, radius in ((4, 1), (10, 2), (16, 1)):
                call("draw_circle", x_mm=x, y_mm=5, radius_mm=radius)
            call("close_sketch")
            call("planar_surface", sketch_name="ThreeHoles")
            near(area(), 200 - 6 * math.pi, "three-hole initial area")
            edges = call("list_edges", body_type="surface")["edges"]
            holes = [e["index"] for e in edges if e["curve_type"] == "circle"]
            assert len(holes) == 3
            with patch.object(sw_multibody, "feature_manager", return_value=SimpleNamespace()) if legacy else patch.object(sw_multibody, "feature_manager", wraps=sw_multibody.feature_manager):
                call("delete_surface_holes", selection={"surface_edges": [holes[0]]}, name="FirstHoleRemoved")
            remaining = call("list_edges", body_type="surface")["edges"]
            remaining_holes = [e["index"] for e in remaining if e["curve_type"] == "circle"]
            assert len(remaining_holes) == 2 and len(remaining) == 6
            # Read the remaining circular edge lengths instead of assuming index order.
            expected = 200 - sum((e["length_mm"] / (2 * math.pi)) ** 2 * math.pi
                                 for e in remaining if e["curve_type"] == "circle")
            near(area(), expected, "unselected holes preserved")
            call("delete_surface_holes", selection={"surface_edges": remaining_holes}, name="RemainingHolesRemoved")
            near(area(), 200, "multiple-hole removal area")
            assert len(call("list_edges", body_type="surface")["edges"]) == 4
            assert len(call("list_surface_bodies")["surface_bodies"]) == 1
            assert call("list_faces")["total_faces"] == 0
        print("Selective/multiple surface-hole removal and legacy native path verified.", flush=True)
    finally:
        open_titles = {d["title"] for d in call("list_open_documents")["documents"]}
        for title in scratch:
            if title in open_titles:
                call("close_document", name=title, discard_changes=True)
        if original and original in open_titles:
            call("activate_document", name=original)


if __name__ == "__main__":
    run()
