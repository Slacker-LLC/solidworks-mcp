# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
"""Validate native spatial circle/ellipse conversion on three planes and an offset."""
import math
from live_expansion_regression import call
from solidworks_mcp.sw_core import HANDLERS, require_part, sketch_segment_objects, value


def run():
    original=call("list_open_documents")["active_title"]
    scratch=[]
    try:
        for plane,axis in (("front",2),("top",1),("right",0),("offset",2)):
            scratch.append(call("create_new_document",kind="part")["document"]["title"])
            if plane=="offset":
                call("create_plane",mode="offset",selection={"planes":["front"]},distance_mm=5,name="SourcePlane")
                call("create_sketch",plane_name="SourcePlane",name="SourceCurves")
            else:
                call("create_sketch",plane=plane,name="SourceCurves")
            call("draw_circle",x_mm=20,y_mm=5,radius_mm=2)
            ellipse=call("draw_ellipse",center_x_mm=0,center_y_mm=0,major_x_mm=10/math.sqrt(2),major_y_mm=10/math.sqrt(2),minor_x_mm=-5/math.sqrt(2),minor_y_mm=5/math.sqrt(2))
            assert abs(ellipse["length_mm"]-48.4422411027)<.001
            call("close_sketch");call("create_3d_sketch",name="SpatialCurves")
            call("draw_line",x1_mm=100,y1_mm=100,z1_mm=100,x2_mm=110,y2_mm=100,z2_mm=100)
            converted=call("convert_entities",selection={"sketch_name":"SourceCurves","sketch_segments":[0,1]},chain=False)
            assert converted["geometry_correspondence_confirmed"] and converted["max_source_point_gap_mm"]<.01
            assert converted["native_types"]==[1,3]
            _,doc=require_part()
            segments=sketch_segment_objects(doc)
            circle=next(s for s in segments if int(value(s,"GetType"))==1)
            params=list(value(value(circle,"GetCurve"),"CircleParams"))
            assert abs(abs(params[axis+3])-1)<1e-6,params
            if plane=="offset": assert abs(abs(params[2]*1000)-5)<.001,params
            assert abs(sum(float(value(s,"GetLength"))*1000 for s in segments)-(10+4*math.pi+48.4422411027))<.01
            count=len(segments)
            try:
                HANDLERS["draw_ellipse"](dict(center_x_mm=0,center_y_mm=0,major_x_mm=10,major_y_mm=0,minor_x_mm=0,minor_y_mm=5))
            except RuntimeError as exc: assert "2D sketch" in str(exc)
            else: raise AssertionError("Direct unsupported 3D ellipse creation was not rejected.")
            assert len(sketch_segment_objects(doc))==count
            call("close_sketch")
            print("SPATIAL_CONVERT",plane,converted,params,flush=True)
        print("Spatial circle/ellipse conversion and unsupported native creation guards passed.",flush=True)
    finally:
        opened={d["title"] for d in call("list_open_documents")["documents"]}
        for title in scratch:
            if title in opened:call("close_document",name=title,discard_changes=True)
        if original:call("activate_document",name=original)


if __name__=="__main__":run()
