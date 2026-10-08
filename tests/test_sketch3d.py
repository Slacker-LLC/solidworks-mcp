# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
import pythoncom
from solidworks_mcp import sw_core as core, sw_sketch as sketch


class Sketch3DContracts(unittest.TestCase):
    def test_3d_features_are_resolved_and_listed_with_2d_features(self):
        planar = SimpleNamespace(Name="Planar", GetTypeName2=lambda: "ProfileFeature")
        spatial = SimpleNamespace(Name="Spatial", GetTypeName2=lambda: "3DProfileFeature")
        with patch.object(core, "iter_feature_objects", return_value=[planar, spatial]):
            self.assertEqual(core.sketch_names("doc"), ["Planar", "Spatial"])
            self.assertEqual(core.latest_sketch("doc"), ("Spatial", spatial))
        with patch.object(core, "find_feature", return_value=spatial):
            self.assertEqual(core.resolve_sketch("doc", "Spatial"), ("Spatial", spatial))

    def test_reopened_older_sketch_is_not_named_after_newest_feature(self):
        older, newer = object(), object()
        features = [SimpleNamespace(Name="Spatial", GetSpecificFeature2=lambda: older),
                    SimpleNamespace(Name="Planar", GetSpecificFeature2=lambda: newer)]
        with patch.object(core, "sketch_features", return_value=features), patch.object(core, "persistent_reference_id", side_effect=lambda d, o: b"old" if o is older else b"new"):
            self.assertEqual(core.sketch_name_for_object("doc", older), "Spatial")

    def test_modern_void_toggle_never_falls_back_or_toggles_2d(self):
        modern, legacy = Mock(return_value=None), Mock()
        doc = SimpleNamespace(Insert3DSketch2=modern, Insert3DSketch=legacy)
        core.toggle_sketch(doc, True)
        modern.assert_called_once_with(True)
        legacy.assert_not_called()

    def test_legacy_toggle_only_when_modern_member_is_absent(self):
        legacy = Mock()
        core.toggle_sketch(SimpleNamespace(Insert3DSketch=legacy), True)
        legacy.assert_called_once_with()

    def test_native_toggle_exception_is_not_retried(self):
        legacy = Mock()
        doc = SimpleNamespace(Insert3DSketch2=Mock(side_effect=RuntimeError("native error")), Insert3DSketch=legacy)
        with self.assertRaisesRegex(RuntimeError, "native error"):
            core.toggle_sketch(doc, True)
        legacy.assert_not_called()

    def test_auto_exit_uses_the_native_active_sketch_mode(self):
        manager = SimpleNamespace(ActiveSketch=SimpleNamespace(Is3D=lambda: True), InsertSketch=Mock())
        close = Mock(side_effect=lambda update: setattr(manager, "ActiveSketch", None))
        doc = SimpleNamespace(SketchManager=manager, Insert3DSketch2=close)
        core.exit_active_sketch(doc)
        close.assert_called_once_with(True)
        manager.InsertSketch.assert_not_called()

    def test_close_must_actually_exit_the_sketch(self):
        doc = SimpleNamespace(SketchManager=SimpleNamespace(ActiveSketch=SimpleNamespace(Is3D=lambda: True)), Insert3DSketch2=lambda *a: None)
        with self.assertRaisesRegex(RuntimeError, "did not close"):
            core.exit_active_sketch(doc)

    def test_creators_refuse_to_toggle_an_existing_edit_context(self):
        doc = SimpleNamespace(SketchManager=SimpleNamespace(ActiveSketch=object()))
        with patch.object(sketch, "active_document", return_value=(None, doc)), patch.object(sketch, "document_type", return_value=1), patch.object(sketch, "require_part", return_value=(None, doc)), patch.object(sketch, "toggle_sketch") as toggle:
            self.assertFalse(sketch.create_3d_sketch({})["ok"])
            self.assertFalse(sketch.create_sketch({"plane": "front"})["ok"])
            toggle.assert_not_called()

    def test_same_dimension_wrong_sketch_reference_is_not_success(self):
        expected = SimpleNamespace(Is3D=lambda: True)
        other = SimpleNamespace(Is3D=lambda: True)
        feature = SimpleNamespace(GetSpecificFeature2=lambda: expected)
        doc = SimpleNamespace(SketchManager=SimpleNamespace(ActiveSketch=None))
        with ExitStack() as stack:
            for name, returned in (("active_document", (None, doc)), ("document_type", 1),
                                   ("resolve_sketch", ("Spatial", feature)), ("select_by_id", True)):
                stack.enter_context(patch.object(sketch, name, return_value=returned))
            stack.enter_context(patch.object(sketch, "clear_selection"))
            stack.enter_context(patch.object(sketch, "persistent_reference_id", side_effect=[b"wanted", b"other"]))
            stack.enter_context(patch.object(sketch, "toggle_sketch", side_effect=lambda *a: setattr(doc.SketchManager, "ActiveSketch", other)))
            payload = sketch.edit_sketch({"sketch_name": "Spatial"})
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["data"]["reference_confirmed"])

    def test_2d_z_rejection_happens_before_native_geometry_creation(self):
        manager = SimpleNamespace(CreateLine=Mock(), CreatePoint=Mock(), CreateSpline=Mock())
        doc = SimpleNamespace(SketchManager=SimpleNamespace(ActiveSketch=SimpleNamespace(Is3D=lambda: False)))
        for handler, args in ((sketch.draw_line, {"x1_mm": 0, "y1_mm": 0, "x2_mm": 1, "y2_mm": 1, "z2_mm": 1}),
                              (sketch.draw_point, {"x_mm": 1, "y_mm": 1, "z_mm": 1}),
                              (sketch.draw_spline, {"points": [{"x_mm": 0, "y_mm": 0}, {"x_mm": 1, "y_mm": 1, "z_mm": 1}]})):
            with patch.object(sketch, "_require_open_sketch", return_value=(doc, manager)):
                with self.assertRaisesRegex(RuntimeError, "Nonzero Z"):
                    handler(args)
        for method in (manager.CreateLine, manager.CreatePoint, manager.CreateSpline): method.assert_not_called()

    def test_line_preserves_xyz_and_converts_mm_to_native_metres(self):
        create = Mock(return_value=SimpleNamespace(GetStartPoint2=lambda: SimpleNamespace(X=.001, Y=.002, Z=.003), GetEndPoint2=lambda: SimpleNamespace(X=.004, Y=.005, Z=.006)))
        doc = SimpleNamespace(SketchManager=SimpleNamespace(ActiveSketch=SimpleNamespace(Is3D=lambda: True)))
        with patch.object(sketch, "_require_open_sketch", return_value=(doc, SimpleNamespace(AddToDB=False, CreateLine=create))), patch.object(sketch, "_segment_count", return_value=0), patch.object(sketch, "_drawn", return_value={"ok": True, "data": {}}):
            sketch.draw_line({"x1_mm": 1, "y1_mm": 2, "z1_mm": 3, "x2_mm": 4, "y2_mm": 5, "z2_mm": 6})
        create.assert_called_once_with(.001, .002, .003, .004, .005, .006)

    def test_spline_uses_ordered_xyz_r8_array(self):
        create = Mock()
        doc = SimpleNamespace(SketchManager=SimpleNamespace(ActiveSketch=SimpleNamespace(Is3D=lambda: True)))
        with patch.object(sketch, "_require_open_sketch", return_value=(doc, SimpleNamespace(CreateSpline=create))), patch.object(sketch, "_segment_count", return_value=0), patch.object(sketch, "_drawn", return_value={"ok": True}):
            sketch.draw_spline({"points": [{"x_mm": 1, "y_mm": 2, "z_mm": 3}, {"x_mm": 4, "y_mm": 5, "z_mm": 6}]})
        array = create.call_args.args[0]
        self.assertEqual(array.varianttype, pythoncom.VT_ARRAY | pythoncom.VT_R8)
        self.assertEqual(tuple(array.value), (.001, .002, .003, .004, .005, .006))

    def test_nonnull_point_with_wrong_coordinates_is_not_success(self):
        doc = SimpleNamespace(SketchManager=SimpleNamespace(ActiveSketch=SimpleNamespace(Is3D=lambda: True)))
        manager = SimpleNamespace(CreatePoint=lambda *a: SimpleNamespace(X=.005, Y=0, Z=0))
        with patch.object(sketch, "_require_open_sketch", return_value=(doc, manager)):
            payload = sketch.draw_point({"x_mm": 5, "y_mm": -4, "z_mm": 7})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["data"]["point_mm"], [5, 0, 0])

    def test_circle_bypasses_inference_and_restores_original_state(self):
        for original in (False, True):
            manager = SimpleNamespace(AddToDB=original)
            def create(*coords):
                self.assertTrue(manager.AddToDB)
                self.assertEqual(coords, (.093, .095, 0, .001))
                return SimpleNamespace(GetCenterPoint2=lambda: SimpleNamespace(X=.093, Y=.095, Z=0), GetRadius=lambda: .001)
            manager.CreateCircleByRadius = create
            with patch.object(sketch, "_require_open_sketch", return_value=(None, manager)), patch.object(sketch, "_segment_count", side_effect=[0, 1]):
                self.assertTrue(sketch.draw_circle({"x_mm": 93, "y_mm": 95, "radius_mm": 1})["ok"])
            self.assertEqual(manager.AddToDB, original)

    def test_circle_restores_inference_after_native_exception(self):
        manager = SimpleNamespace(AddToDB=False, CreateCircleByRadius=Mock(side_effect=RuntimeError("native rejection")))
        with patch.object(sketch, "_require_open_sketch", return_value=(None, manager)), patch.object(sketch, "_segment_count", return_value=0):
            with self.assertRaisesRegex(RuntimeError, "native rejection"):
                sketch.draw_circle({"x_mm": 93, "y_mm": 95, "radius_mm": 1})
        self.assertFalse(manager.AddToDB)

    def test_line_wrong_endpoint_is_not_success(self):
        segment = SimpleNamespace(GetStartPoint2=lambda: SimpleNamespace(X=0, Y=0, Z=0), GetEndPoint2=lambda: SimpleNamespace(X=.009, Y=.020, Z=.030))
        manager = SimpleNamespace(AddToDB=False, CreateLine=lambda *a: segment)
        doc = SimpleNamespace(SketchManager=SimpleNamespace(ActiveSketch=SimpleNamespace(Is3D=lambda: True)))
        with patch.object(sketch, "_require_open_sketch", return_value=(doc, manager)), patch.object(sketch, "_segment_count", side_effect=[0, 1]):
            self.assertFalse(sketch.draw_line({"x1_mm": 0, "y1_mm": 0, "x2_mm": 10, "y2_mm": 20, "z2_mm": 30})["ok"])
        self.assertFalse(manager.AddToDB)

    def test_line_restores_inference_after_native_exception(self):
        manager = SimpleNamespace(AddToDB=True, CreateLine=Mock(side_effect=RuntimeError("native rejection")))
        doc = SimpleNamespace(SketchManager=SimpleNamespace(ActiveSketch=SimpleNamespace(Is3D=lambda: True)))
        with patch.object(sketch, "_require_open_sketch", return_value=(doc, manager)), patch.object(sketch, "_segment_count", return_value=0):
            with self.assertRaisesRegex(RuntimeError, "native rejection"):
                sketch.draw_line({"x1_mm": 0, "y1_mm": 0, "x2_mm": 10, "y2_mm": 20, "z2_mm": 30})
        self.assertTrue(manager.AddToDB)

    def test_circle_wrong_radius_is_not_success(self):
        segment = SimpleNamespace(GetCenterPoint2=lambda: SimpleNamespace(X=.093, Y=.095, Z=0), GetRadius=lambda: .002)
        manager = SimpleNamespace(AddToDB=False, CreateCircleByRadius=lambda *a: segment)
        with patch.object(sketch, "_require_open_sketch", return_value=(None, manager)), patch.object(sketch, "_segment_count", side_effect=[0, 1]):
            self.assertFalse(sketch.draw_circle({"x_mm": 93, "y_mm": 95, "radius_mm": 1})["ok"])

    def test_named_point_enumeration_reports_local_and_model_positions(self):
        matrix = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, .035, 1, 0, 0, 0]
        specific = SimpleNamespace(Is3D=lambda: False, GetSketchPoints2=lambda: [SimpleNamespace(X=.003, Y=0, Z=0, Type=1)],
                                   ModelToSketchTransform=SimpleNamespace(Inverse=SimpleNamespace(ArrayData=matrix)))
        feature = SimpleNamespace(GetSpecificFeature2=lambda: specific)
        doc = SimpleNamespace(SketchManager=SimpleNamespace(ActiveSketch=None))
        with patch.object(sketch, "active_document", return_value=(None, doc)), patch.object(sketch, "document_type", return_value=1), patch.object(sketch, "resolve_sketch", return_value=("Apex", feature)):
            points = sketch.list_sketch_points({"sketch_name": "Apex"})["data"]["points"]
        self.assertEqual(points[0]["index"], 0)
        self.assertEqual(points[0]["point_mm"], [3, 0, 0])
        self.assertEqual(points[0]["model_point_mm"], [3, 0, 35])


if __name__ == "__main__":
    unittest.main()
