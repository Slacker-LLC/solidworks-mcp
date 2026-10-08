# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
import unittest
from contextlib import ExitStack, nullcontext
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from solidworks_mcp import sw_sketch as sketch


class AxisDimensionTests(unittest.TestCase):
    def exercise(self, *, solve=True, apply=True, code=0, restore=True, created=True, selection_matches=True):
        current=NS(reference=b"sketch",Is3D=lambda:True)
        p=NS(reference=b"a",X=0,Y=0,Z=0)
        q=NS(reference=b"b",X=.01,Y=.02,Z=.03)
        dimension=NS(SystemValue=.01,FullName="D1@Spatial")
        def set_value(target,configuration,empty):
            if apply:dimension.SystemValue=target
            return code
        dimension.SetSystemValue3=set_value
        display=NS(GetDimension2=lambda i:dimension,Type2=2)
        manager=NS(ActiveSketch=current,AddAlongXDimension=Mock(return_value=display if created else None))
        doc=NS(SketchManager=manager,SelectionManager=NS(GetSelectedObjectCount2=lambda m:2,GetSelectedObject6=lambda i,m:([p,q] if selection_matches else [q,p])[i-1]))
        def rebuild(doc):
            manager.ActiveSketch=None
            if solve:q.X=.015
            return True
        def reopen(args):
            manager.ActiveSketch=current if restore else None
            return {"ok":restore}
        with ExitStack() as stack:
            for name,replacement in {"active_document":lambda:(None,doc),"sketch_manager":lambda d:manager,
                "sketch_point_objects":lambda *a:[p,q],"persistent_reference_id":lambda d,o:o.reference,
                "sketch_name_for_object":lambda *a:"Spatial","flag_methods":lambda o,*a:o,
                "require_selection":lambda *a:2,"clear_selection":lambda *a:None,
                "dimension_dialog_suppressed":lambda *a:nullcontext(),"rebuild":rebuild,"edit_sketch":reopen}.items():
                stack.enter_context(patch.object(sketch,name,side_effect=replacement))
            payload=sketch.add_3d_dimension(dict(axis="x",point_indices=[0,1],value_mm=15,place_x_mm=50,place_y_mm=20,place_z_mm=30))
        if selection_matches:manager.AddAlongXDimension.assert_called_once_with(.05,.02,.03)
        else:manager.AddAlongXDimension.assert_not_called()
        return payload,manager

    def test_dimension_requires_solved_geometry_and_restored_context(self):
        payload,manager=self.exercise()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["projected_distance_mm"],15)
        self.assertIsNotNone(manager.ActiveSketch)

    def test_dimension_value_without_driven_geometry_is_failure(self):
        payload,_=self.exercise(solve=False)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["data"]["value_mm"],15)
        self.assertEqual(payload["data"]["projected_distance_mm"],10)

    def test_ignored_value_and_native_status_are_failures(self):
        for options in ({"apply":False},{"code":1}):
            self.assertFalse(self.exercise(**options)[0]["ok"])

    def test_failed_context_restore_is_failure(self):
        self.assertFalse(self.exercise(restore=False)[0]["ok"])

    def test_null_display_is_not_success(self):
        self.assertFalse(self.exercise(created=False)[0]["ok"])

    def test_wrong_native_selection_is_rejected_before_dimension_creation(self):
        self.assertFalse(self.exercise(selection_matches=False)[0]["ok"])

    def test_dimension_listing_distinguishes_driving_and_driven_native_states(self):
        for state,driven in ((0,False),(1,True),(2,False)):
            dimension=NS(FullName="D1@Spatial",Name="D1",SystemValue=.015,DrivenState=state,GetType=lambda:2)
            display=NS(GetDimension2=lambda i:dimension)
            feature=NS(Name="Spatial",GetFirstDisplayDimension=lambda:display)
            with patch.object(sketch,"active_document",return_value=(None,None)),patch.object(sketch,"find_feature",return_value=feature),patch.object(sketch,"flag_methods",side_effect=lambda o,*a:o),patch.object(sketch,"_next_display",return_value=None):
                info=sketch.list_dimensions({"feature_name":"Spatial"})["data"]["dimensions"][0]
            self.assertEqual(info["driven"],driven)
            self.assertEqual(info["driven_state"],state)

    def test_2d_and_duplicate_points_are_rejected_before_mutation(self):
        for is3d,indices in ((False,[0,1]),(True,[0,0])):
            doc=NS(SketchManager=NS(ActiveSketch=NS(Is3D=lambda:is3d)))
            with patch.object(sketch,"active_document",return_value=(None,doc)),patch.object(sketch,"sketch_point_objects",return_value=[NS(),NS()]),patch.object(sketch,"require_selection") as select:
                with self.assertRaises(RuntimeError):sketch.add_3d_dimension(dict(axis="x",point_indices=indices))
                select.assert_not_called()


if __name__=="__main__":unittest.main()
