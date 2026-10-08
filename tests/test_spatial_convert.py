# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import pythoncom
from solidworks_mcp import sw_sketch as sketch


class SpatialConvertTests(unittest.TestCase):
    def test_ellipse_rejects_invalid_axes_before_native_access(self):
        for minor in ((0,0),(2,2),(0,11)):
            with patch.object(sketch,"_require_open_sketch") as native:
                with self.assertRaisesRegex(RuntimeError,"Ellipse axes"):
                    sketch.draw_ellipse(dict(center_x_mm=0,center_y_mm=0,major_x_mm=10,major_y_mm=0,minor_x_mm=minor[0],minor_y_mm=minor[1]))
                native.assert_not_called()

    def test_ellipse_rejects_direct_3d_creation_before_mutation(self):
        doc=NS(SketchManager=NS(ActiveSketch=NS(Is3D=lambda:True)))
        manager=NS(CreateEllipse=Mock())
        with patch.object(sketch,"_require_open_sketch",return_value=(doc,manager)):
            with self.assertRaisesRegex(RuntimeError,"2D sketch"):
                sketch.draw_ellipse(dict(center_x_mm=0,center_y_mm=0,major_x_mm=10,major_y_mm=0,minor_x_mm=0,minor_y_mm=5))
        manager.CreateEllipse.assert_not_called()

    def test_ellipse_placeholder_without_curve_is_failure(self):
        doc=NS(SketchManager=NS(ActiveSketch=NS(Is3D=lambda:False)))
        manager=NS(AddToDB=False,CreateEllipse=lambda *a:NS(GetCurve=lambda:None))
        with patch.object(sketch,"_require_open_sketch",return_value=(doc,manager)),patch.object(sketch,"_segment_count",side_effect=[0,1]):
            self.assertFalse(sketch.draw_ellipse(dict(center_x_mm=0,center_y_mm=0,major_x_mm=10,major_y_mm=0,minor_x_mm=0,minor_y_mm=5))["ok"])
        self.assertFalse(manager.AddToDB)

    def test_curve_bounds_use_typed_mutable_outputs(self):
        def bounds(*refs):
            self.assertEqual([r.varianttype for r in refs],[pythoncom.VT_BYREF|pythoncom.VT_R8]*2+[pythoncom.VT_BYREF|pythoncom.VT_BOOL]*2)
            refs[0].value=2.;refs[1].value=4.
            return True
        curve=NS(GetEndParams=bounds,Evaluate2=lambda t,n:[t,0,0,999])
        with patch.object(sketch,"flag_methods",side_effect=lambda o,*a:o):
            samples=sketch._curve_samples(curve)
        self.assertEqual(len(samples),25)
        self.assertEqual(samples[0],[2,0,0]);self.assertEqual(samples[-1],[4,0,0])

    def test_conversion_native_acceptance_without_output_is_failure(self):
        with patch.object(sketch,"_require_open_sketch",return_value=(None,NS(SketchUseEdge3=lambda *a:True))),patch.object(sketch,"_segment_count",return_value=0),patch.object(sketch,"require_selection"),patch.object(sketch,"sketch_segment_objects",return_value=[]):
            self.assertFalse(sketch.convert_entities({"selection":{"edges":[0]}})["ok"])

    def test_conversion_identifies_new_curves_when_native_order_changes(self):
        old=NS(reference=b"old",GetCurve=lambda:None)
        new=NS(reference=b"new",GetCurve=lambda:NS(),GetLength=lambda:.01,GetType=lambda:1)
        with patch.object(sketch,"_require_open_sketch",return_value=(None,NS(SketchUseEdge3=lambda *a:True))),patch.object(sketch,"sketch_segment_objects",side_effect=[[old],[new,old]]),patch.object(sketch,"persistent_reference_id",side_effect=lambda doc,obj:obj.reference),patch.object(sketch,"require_selection"):
            payload=sketch.convert_entities({"selection":{"edges":[0]}})
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["segments_added"],1)
        self.assertIsNone(payload["data"]["geometry_correspondence_confirmed"])

    def test_conversion_checks_transformed_samples_and_rejects_wrong_output(self):
        matrix=[1,0,0,0,1,0,0,0,1,0,0,.035,1,0,0,0]
        identity=[1,0,0,0,1,0,0,0,1,0,0,0,1,0,0,0]
        source=NS(GetCurve=lambda:NS(),GetLength=lambda:.01,GetType=lambda:0)
        source_sketch=NS(ModelToSketchTransform=NS(Inverse=NS(ArrayData=matrix)))
        target_sketch=NS(ModelToSketchTransform=NS(ArrayData=identity))
        for error,length,ok in ((0,.01,True),(.001,.01,False),(0,.005,False)):
            nearest=Mock(side_effect=lambda x,y,z:[x+error,y,z])
            output=NS(GetCurve=lambda:NS(GetClosestPointOn=nearest),GetLength=lambda:length,GetType=lambda:0)
            manager=NS(ActiveSketch=target_sketch,SketchUseEdge3=lambda *a:True)
            with patch.object(sketch,"_require_open_sketch",return_value=(None,manager)),patch.object(sketch,"_segment_count",return_value=0),patch.object(sketch,"sketch_segment_objects",side_effect=[[],[source],[output]]),patch.object(sketch,"resolve_sketch",return_value=("Source",NS(GetSpecificFeature2=lambda:source_sketch))),patch.object(sketch,"_curve_samples",return_value=[[0,0,0],[.01,0,0]]),patch.object(sketch,"require_selection"),patch.object(sketch,"flag_methods",side_effect=lambda o,*a:o):
                payload=sketch.convert_entities({"selection":{"sketch_name":"Source","sketch_segments":[0]},"chain":False})
            self.assertEqual(payload["ok"],ok)
            self.assertEqual(payload["data"]["geometry_correspondence_confirmed"],ok)
            nearest.assert_any_call(0,0,.035)


if __name__=="__main__":
    unittest.main()
