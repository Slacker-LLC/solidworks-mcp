# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

import math
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from solidworks_mcp import sw_sketch as sketch


class SpatialArcTests(unittest.TestCase):
    def test_centerline_preserves_xyz_and_requires_construction(self):
        args = dict(x1_mm=0,y1_mm=0,x2_mm=10,y2_mm=20,z2_mm=30)
        with patch.object(sketch,"draw_line",return_value={"ok":True}) as draw:
            sketch.draw_centerline(args)
        draw.assert_called_once_with({**args,"construction":True})

    def test_collinear_and_coincident_rejected_before_native_access(self):
        for third in ((2,2,2),(0,0,0)):
            args={f"{k}{i}_mm":v for i,p in enumerate(((0,0,0),(1,1,1),third),1) for k,v in zip("xyz",p)}
            with patch.object(sketch,"_require_open_sketch") as native:
                with self.assertRaisesRegex(RuntimeError,"non-collinear"):
                    sketch.draw_3point_arc(args)
                native.assert_not_called()

    def test_nonzero_z_rejected_in_2d_before_creation(self):
        doc=NS(SketchManager=NS(ActiveSketch=NS(Is3D=lambda:False)))
        manager=NS(Create3PointArc=Mock())
        with patch.object(sketch,"_require_open_sketch",return_value=(doc,manager)):
            with self.assertRaisesRegex(RuntimeError,"Nonzero Z"):
                sketch.draw_3point_arc(dict(x1_mm=20,y1_mm=0,x2_mm=20,y2_mm=10,x3_mm=20,y3_mm=5,z3_mm=5))
        manager.Create3PointArc.assert_not_called()

    def test_major_arc_length_and_wrong_span_are_distinguished(self):
        points=((.04,0,0),(.04,.005,.005),(.04,-.005,.005))
        args={f"{k}{i}_mm":v*1000 for i,p in enumerate(points,1) for k,v in zip("xyz",p)}
        curve=NS(GetClosestPointOn=lambda *p:p)
        doc=NS(SketchManager=NS(ActiveSketch=NS(Is3D=lambda:True)))
        for length,ok in ((.005*1.5*math.pi,True),(.005*.5*math.pi,False)):
            segment=NS(GetCurve=lambda:curve,GetRadius=lambda:.005,GetLength=lambda:length,
                       GetStartPoint2=lambda:NS(X=points[0][0],Y=points[0][1],Z=points[0][2]),
                       GetEndPoint2=lambda:NS(X=points[1][0],Y=points[1][1],Z=points[1][2]))
            manager=NS(AddToDB=False,Create3PointArc=Mock(return_value=segment))
            with patch.object(sketch,"_require_open_sketch",return_value=(doc,manager)), patch.object(sketch,"_segment_count",side_effect=[0,1]), patch.object(sketch,"flag_methods",side_effect=lambda obj,*a:obj):
                self.assertEqual(sketch.draw_3point_arc(args)["ok"],ok)
            manager.Create3PointArc.assert_called_once_with(*(v for p in points for v in p))
            self.assertFalse(manager.AddToDB)


if __name__ == "__main__":
    unittest.main()
