# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
"""Native axis dimensions must drive point geometry and restore 3D editing."""
from live_expansion_regression import call
from solidworks_mcp.sw_core import HANDLERS


def run():
    original=call("list_open_documents")["active_title"]
    scratch=[]
    try:
        for kind in ("part","assembly"):
            scratch.append(call("create_new_document",kind=kind)["document"]["title"])
            call("create_3d_sketch",name="AxisDimensions")
            call("draw_line",x1_mm=0,y1_mm=0,x2_mm=10,y2_mm=20,z2_mm=30)
            names=[]
            for axis,target in zip("xyz",(15,25,35)):
                if kind=="assembly" and axis=="z":
                    rejected=HANDLERS["add_3d_dimension"](dict(axis=axis,point_indices=[0,1],value_mm=target,place_x_mm=50,place_y_mm=50,place_z_mm=50))
                    assert not rejected["ok"] and abs(rejected["data"]["projected_before_mm"]-30)<.01
                    print("ASSEMBLY_Z_NATIVE_REFUSAL",rejected,flush=True)
                    continue
                added=call("add_3d_dimension",axis=axis,point_indices=[0,1],value_mm=target,place_x_mm=50,place_y_mm=50,place_z_mm=50)
                assert added["edit_context_restored"] and added["point_references_confirmed"]
                assert abs(added["projected_distance_mm"]-target)<.01
                names.append(added["full_name"])
                print("AXIS_DIM",kind,added,flush=True)
                print("AXIS_POINTS",kind,axis,call("list_sketch_points"),flush=True)
            points=call("list_sketch_points")["points"]
            expected=(15,25,35) if kind=="part" else (15,25,30)
            assert all(abs(abs(points[1]["model_point_mm"][i]-points[0]["model_point_mm"][i])-v)<.01 for i,v in enumerate(expected))
            call("close_sketch")
            call("set_dimension",full_name=names[0],value_mm=17)
            points=call("list_sketch_points",sketch_name="AxisDimensions")["points"]
            assert abs(abs(points[1]["model_point_mm"][0]-points[0]["model_point_mm"][0])-17)<.01
        print("Part XYZ and assembly XY driven dimensions passed; assembly Z native refusal remains a gap.",flush=True)
    finally:
        opened={d["title"] for d in call("list_open_documents")["documents"]}
        for title in scratch:
            if title in opened:call("close_document",name=title,discard_changes=True)
        if original:call("activate_document",name=original)


if __name__=="__main__":run()
