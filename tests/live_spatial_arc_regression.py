# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

"""Check native spatial construction lines and minor/major three-point arcs."""
import math
from live_expansion_regression import call
from solidworks_mcp.sw_core import HANDLERS


def run():
    original = call("list_open_documents")["active_title"]
    scratch = []
    try:
        for kind in ("part", "assembly"):
            scratch.append(call("create_new_document", kind=kind)["document"]["title"])
            call("create_3d_sketch", name="SpatialArcs")
            call("draw_centerline", x1_mm=0, y1_mm=0, x2_mm=10, y2_mm=20, z2_mm=30)
            line = call("list_sketch_segments")["segments"][0]
            assert line["construction"] and math.dist(line["end_mm"], [10,20,30]) < .001
            for middle, expected in (([20,5,5], math.pi*5), ([40,-5,5], 1.5*math.pi*5)):
                end = [20,10,0] if middle[0] == 20 else [40,5,5]
                start = [middle[0],0,0]
                args = {f"{k}{i}_mm": v for i,p in enumerate((start,end,middle),1) for k,v in zip("xyz",p)}
                arc = call("draw_3point_arc", **args)
                assert math.isclose(arc["radius_mm"], 5, abs_tol=.01), arc
                assert math.isclose(arc["length_mm"], expected, abs_tol=.01), arc
                assert arc["max_point_gap_mm"] < .01
            count = len(call("list_sketch_segments")["segments"])
            try:
                HANDLERS["draw_3point_arc"]({"x1_mm":0,"y1_mm":0,"x2_mm":1,"y2_mm":1,"x3_mm":2,"y3_mm":2})
            except RuntimeError as exc:
                assert "non-collinear" in str(exc)
            else:
                raise AssertionError("Collinear arc input was not rejected.")
            assert len(call("list_sketch_segments")["segments"]) == count
            call("close_sketch")
        print("Spatial centerlines and minor/major arcs passed for part/assembly.", flush=True)
    finally:
        opened = {d["title"] for d in call("list_open_documents")["documents"]}
        for title in scratch:
            if title in opened: call("close_document", name=title, discard_changes=True)
        if original: call("activate_document", name=original)


if __name__ == "__main__":
    run()
