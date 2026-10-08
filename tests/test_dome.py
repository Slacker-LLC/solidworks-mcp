# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
import pythoncom
from solidworks_mcp import sw_dome as dome


def geometry(**changes):
    return {"input_geometry": {"solid_volume_mm3": 100}, "solid_volume_mm3": 110,
            "reverse_direction": False, "has_constraint": False, "height_mm": 5,
            "center_probes": [{"normal_displacement_mm": 5}], **changes}


class DomeContracts(unittest.TestCase):
    def test_conflicting_or_invalid_controls_never_access_com(self):
        for changes in ({"constraint_sketch_name": " "}, {"constraint_sketch_name": "A", "height_mm": 5},
                        {"constraint_sketch_name": "A", "constraint_selection": {"sketch_name": "A", "sketch_points": [0]}},
                        {"clear_constraint": True}, {"clear_direction": 1}, {"clear_direction": False},
                        {"clear_direction": True, "direction_edge_index": 0},
                        {"clear_constraint": True, "height_mm": 5, "constraint_sketch_name": "A"}):
            with patch.object(dome, "require_part") as part:
                with self.assertRaises(RuntimeError):
                    dome.set_dome_parameters({"name": "Dome", **changes})
                part.assert_not_called()

    def test_invalid_inputs_do_not_access_com(self):
        for changes in ({"height_mm": 0}, {"height_mm": float("nan")}, {"face_index": -1},
                        {"face_indices": [0, 0]}, {"reverse_direction": 1}, {"elliptical": 1},
                        {"direction_edge_index": True},
                        {"constraint_selection": {"sketch_name": "Apex", "sketch_points": [0]}}):
            with patch.object(dome, "require_part") as part:
                with self.assertRaises(RuntimeError):
                    dome.dome({"height_mm": 5, "face_index": 0, **changes})
                part.assert_not_called()

    def test_void_creation_recovers_feature_and_uses_native_units(self):
        create = Mock(return_value=None)
        doc = SimpleNamespace(InsertDome=create)
        info = geometry(input_face_count=1, elliptical=False)
        with ExitStack() as stack:
            for name, returned in (("require_part", (None, doc)), ("_feature_names", []),
                                   ("_feature_created_after", "recovered"), ("_dome_info", info)):
                stack.enter_context(patch.object(dome, name, return_value=returned))
            for name in ("exit_active_sketch", "clear_selection"):
                stack.enter_context(patch.object(dome, name))
            select = stack.enter_context(patch.object(dome, "require_selection"))
            stack.enter_context(patch.object(dome, "_finish", return_value={"ok": True, "data": {"feature": "recovered"}}))
            payload = dome.dome({"height_mm": 5, "face_index": 2})
        create.assert_called_once_with(.005, False, False)
        select.assert_called_once_with(doc, {"faces": [2]}, mark=1)
        self.assertTrue(payload["ok"])

    def test_zero_effect_and_incorrect_height_are_unconfirmed(self):
        for info in (geometry(solid_volume_mm3=100), geometry(center_probes=[]),
                     geometry(center_probes=[{"normal_displacement_mm": 8.888}]),
                     geometry(ellipsoid_check={"confirmed": False})):
            self.assertFalse(dome._geometry_confirmed(info)[1])

    def test_concave_requires_negative_volume_and_displacement(self):
        self.assertTrue(dome._geometry_confirmed(geometry(reverse_direction=True, solid_volume_mm3=90,
                            center_probes=[{"normal_displacement_mm": -5}]))[1])
        self.assertFalse(dome._geometry_confirmed(geometry(reverse_direction=True))[1])

    def test_direction_sign_and_distance_are_checked_in_native_vector_space(self):
        info = geometry(has_direction=True, direction_vector=[0, 0, -1], expected_volume_sign=-1,
                        solid_volume_mm3=90, center_probes=[{"normal_displacement_mm": -5,
                            "direction_displacement_mm": 5, "surface_gap_mm": 0}])
        self.assertTrue(dome._geometry_confirmed(info)[1])
        self.assertFalse(dome._geometry_confirmed({**info, "expected_volume_sign": 0})[1])
        self.assertFalse(dome._geometry_confirmed({**info, "solid_volume_mm3": 110})[1])
        self.assertFalse(dome._geometry_confirmed({**info, "center_probes": [{"direction_displacement_mm": 5,
                                               "surface_gap_mm": 1}]})[1])

    def test_constraint_controls_height_when_native_height_is_zero(self):
        for gap, expected in ((.001, True), (.1, False), (None, False)):
            self.assertEqual(dome._geometry_confirmed(geometry(has_constraint=True, height_mm=0,
                                 constraint_surface_gap_mm=gap))[1], expected)
        self.assertFalse(dome._geometry_confirmed(geometry(has_constraint=True, height_mm=0,
                         constraint_surface_gap_mm=0, constraint_check={"confirmed": False}))[1])

    def test_whole_sketch_samples_user_points_in_model_coordinates(self):
        matrix = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, .035, 1, 0, 0, 0]
        sketch = SimpleNamespace(ModelToSketchTransform=SimpleNamespace(Inverse=SimpleNamespace(ArrayData=matrix)))
        point = lambda x, kind: SimpleNamespace(X=x, Y=0, Z=0, Type=kind, GetSketch=lambda: sketch)
        user, internal = point(.003, 1), point(0, 0)
        line = SimpleNamespace(GetType=lambda: 0, ConstructionGeometry=False,
                               GetStartPoint2=lambda: point(-.003, 0), GetEndPoint2=lambda: point(.003, 0))
        sketch.GetSketchSegments = lambda: [line]
        sketch.GetSketchPoints2 = lambda: [user, internal]
        samples, meta = dome._constraint_samples(sketch)
        self.assertTrue(meta["sampling_complete"])
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0], [.003, 0, .035])
        self.assertEqual(meta["other_sketch_point_count"], 1)

    def test_curve_centers_and_endpoints_are_not_implicit_user_constraints(self):
        internal = SimpleNamespace(Type=0)
        sketch = SimpleNamespace(GetSketchPoints2=lambda: [internal, internal])
        samples, meta = dome._constraint_samples(sketch)
        self.assertFalse(meta["sampling_complete"])
        self.assertEqual(samples, [])
        self.assertEqual(meta["other_sketch_point_count"], 2)

    def test_empty_sketch_cannot_prove_constraint_geometry(self):
        sketch = SimpleNamespace(GetSketchPoints2=lambda: [])
        samples, meta = dome._constraint_samples(sketch)
        self.assertFalse(meta["sampling_complete"])
        self.assertEqual(samples, [])
        self.assertEqual(meta["user_point_count"], 0)

    def test_clear_controls_uses_typed_null_and_checks_absence(self):
        data = SimpleNamespace(AccessSelections=lambda *a: True)
        feature = SimpleNamespace(GetDefinition=lambda: data, ModifyDefinition=lambda *a: True)
        for cleared in (True, False):
            info = geometry(has_direction=True, has_constraint=True, constraint_surface_gap_mm=.001,
                            direction_reference_confirmed=cleared, constraint_reference_confirmed=cleared)
            with ExitStack() as stack:
                for name, returned in (("require_part", (None, "doc")), ("find_feature", feature),
                                       ("_dome_info", info), ("whats_wrong", [])):
                    mocked = stack.enter_context(patch.object(dome, name, return_value=returned))
                    if name == "_dome_info": readback = mocked
                for name in ("exit_active_sketch", "clear_selection", "rebuild"):
                    stack.enter_context(patch.object(dome, name))
                payload = dome.set_dome_parameters({"name": "Dome", "height_mm": 5,
                                                   "clear_constraint": True, "clear_direction": True})
            self.assertEqual(data.ConstraintPointOrSketch.varianttype, pythoncom.VT_DISPATCH)
            self.assertIsNone(data.Direction.value)
            self.assertEqual(data.Height, .005)
            self.assertEqual(readback.call_args.args[-1], {"constraint": None, "direction": None})
            self.assertEqual(payload["ok"], cleared)

    def test_native_clear_rejection_releases_rollback_and_does_not_modify(self):
        released, modify = Mock(), Mock()
        class Definition:
            def AccessSelections(self, *args): return True
            def ReleaseSelectionAccess(self): released()
            @property
            def Direction(self): return "edge"
            @Direction.setter
            def Direction(self, value): raise AttributeError("Native direction clear rejected")
        feature = SimpleNamespace(GetDefinition=lambda: Definition(), ModifyDefinition=modify)
        info = geometry(has_direction=True, direction_reference_confirmed=False)
        with ExitStack() as stack:
            for name, returned in (("require_part", (None, "doc")), ("find_feature", feature),
                                   ("_dome_info", info), ("whats_wrong", [])):
                stack.enter_context(patch.object(dome, name, return_value=returned))
            for name in ("exit_active_sketch", "clear_selection", "rebuild"):
                stack.enter_context(patch.object(dome, name))
            payload = dome.set_dome_parameters({"name": "Dome", "clear_direction": True})
        released.assert_called_once()
        modify.assert_not_called()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["data"]["native_clear_error"], "Native direction clear rejected")

    def test_already_absent_direction_is_verified_without_null_setter(self):
        data = SimpleNamespace(AccessSelections=lambda *a: True)
        feature = SimpleNamespace(GetDefinition=lambda: data, ModifyDefinition=lambda *a: True)
        info = geometry(has_direction=False, direction_reference_confirmed=True)
        with ExitStack() as stack:
            for name, returned in (("require_part", (None, "doc")), ("find_feature", feature),
                                   ("_dome_info", info), ("whats_wrong", [])):
                stack.enter_context(patch.object(dome, name, return_value=returned))
            for name in ("exit_active_sketch", "clear_selection", "rebuild"):
                stack.enter_context(patch.object(dome, name))
            payload = dome.set_dome_parameters({"name": "Dome", "clear_direction": True})
        self.assertFalse(hasattr(data, "Direction"))
        self.assertTrue(payload["ok"])

    def test_rollback_is_released_before_output_geometry(self):
        events = []
        data = SimpleNamespace(AccessSelections=lambda *a: True, GetFaceCount=lambda: 0, Faces=[],
                               Height=.005, ReverseDir=False, Elliptical=False, Direction=None,
                               ConstraintPointOrSketch=None, ReleaseSelectionAccess=lambda: events.append("release"))
        feature = SimpleNamespace(GetTypeName2=lambda: "Dome", GetDefinition=lambda: data, GetFaces=lambda: [])
        with patch.object(dome, "_geometry", side_effect=lambda d: events.append("geometry") or {}):
            dome._dome_info("doc", feature)
        self.assertEqual(events, ["geometry", "release", "geometry"])

    def test_face_count_error_still_releases_rollback(self):
        released = Mock()
        data = SimpleNamespace(AccessSelections=lambda *a: True, GetFaceCount=lambda: 1,
                               Faces=[], ReleaseSelectionAccess=lambda: released())
        feature = SimpleNamespace(GetTypeName2=lambda: "Dome", GetDefinition=lambda: data)
        with self.assertRaisesRegex(RuntimeError, "references differ"):
            dome._dome_info("doc", feature)
        released.assert_called_once()

    def test_unsupported_boundary_cannot_prove_ellipsoid(self):
        self.assertIsNone(dome._ellipsoid_queries(SimpleNamespace(GetEdges=lambda: []), 5, False))

    def test_failed_edit_releases_native_selection_access(self):
        released = Mock()
        data = SimpleNamespace(AccessSelections=lambda *a: True, ReleaseSelectionAccess=lambda: released())
        feature = SimpleNamespace(GetDefinition=lambda: data, ModifyDefinition=lambda *a: False)
        info = geometry()
        with ExitStack() as stack:
            for name, returned in (("require_part", (None, "doc")), ("find_feature", feature),
                                   ("_dome_info", info), ("whats_wrong", [])):
                stack.enter_context(patch.object(dome, name, return_value=returned))
            for name in ("exit_active_sketch", "clear_selection", "rebuild"):
                stack.enter_context(patch.object(dome, name))
            payload = dome.set_dome_parameters({"name": "Dome", "height_mm": 5})
        released.assert_called_once()
        self.assertFalse(payload["ok"])

    def test_constraint_requires_exactly_one_named_sketch_point(self):
        for spec in ({"sketch_name": "A", "sketch_points": []},
                     {"sketch_name": "A", "sketch_points": [0, 1]},
                     {"sketch_name": "", "sketch_points": [0]},
                     {"sketch_name": "A", "sketch_points": [True]}):
            with patch.object(dome, "require_part") as part:
                with self.assertRaises(RuntimeError):
                    dome.set_dome_parameters({"name": "Dome", "constraint_selection": spec})
                part.assert_not_called()


if __name__ == "__main__":
    unittest.main()
