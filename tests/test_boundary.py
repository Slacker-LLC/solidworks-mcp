# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
from solidworks_mcp import sw_boundary as boundary


ARGS = {"direction1": [{"selection": {"sketches": ["A"]}}, {"selection": {"sketches": ["B"]}}],
        "direction2": [{"selection": {"sketches": ["C"]}}, {"selection": {"sketches": ["D"]}}]}


class BoundaryContracts(unittest.TestCase):
    def test_invalid_inputs_fail_before_com(self):
        for args in ({"direction1": []}, {"direction1": ARGS["direction1"][:1]},
                     {"direction1": [{"selection": {"planes": ["front"]}}] * 2},
                     {"direction1": [{"selection": {"sketches": ["A"]}, "tangency": "unknown"}] * 2},
                     {**ARGS, "direction1_influence": "unknown"}):
            with patch.object(boundary, "require_part") as part:
                with self.assertRaises(RuntimeError):
                    boundary.boundary_surface(args)
                part.assert_not_called()

    def _patches(self, stack, manager, info=None):
        values = {"require_part": (None, "doc"), "feature_manager": manager, "_feature_names": [],
                  "_feature_created_after": "recovered", "_boundary_info": info}
        for name, returned in values.items():
            stack.enter_context(patch.object(boundary, name, return_value=returned))
        stack.enter_context(patch.object(boundary, "exit_active_sketch"))
        stack.enter_context(patch.object(boundary, "clear_selection"))
        stack.enter_context(patch.object(boundary, "_finish", side_effect=lambda doc, feature, *args: {"ok": info is not None, "data": {"feature": feature}}))

    def test_selection_order_marks_and_void_creation_recovery(self):
        events = []
        manager = SimpleNamespace(SetNetBlendCurveData=lambda *args: events.append(("condition", args)),
                                  SetNetBlendDirectionData=lambda *args: None,
                                  InsertNetBlend2=lambda *args: None)
        with ExitStack() as stack:
            self._patches(stack, manager)
            stack.enter_context(patch.object(boundary, "require_selection", side_effect=lambda doc, spec, **kw: events.append(("select", kw["mark"])) or 1))
            payload = boundary.boundary_surface(ARGS)
        self.assertEqual(payload["data"]["feature"], "recovered")
        self.assertEqual(events[:4], [("select", 8193), ("select", 16385), ("select", 8194), ("select", 16386)])
        self.assertTrue(all(event[0] == "condition" for event in events[4:]))

    def test_legacy_method_preserves_surface_but_cannot_form_solid(self):
        legacy = Mock(return_value=None)
        manager = SimpleNamespace(SetNetBlendCurveData=lambda *a: None, SetNetBlendDirectionData=lambda *a: None, InsertNetBlend=legacy)
        with ExitStack() as stack:
            self._patches(stack, manager)
            stack.enter_context(patch.object(boundary, "require_selection", return_value=1))
            boundary.boundary_surface(ARGS)
            self.assertEqual(len(legacy.call_args.args), 20)
            legacy.reset_mock()
            with self.assertRaisesRegex(RuntimeError, "older method"):
                boundary.boundary_surface({**ARGS, "create_solid": True})
            legacy.assert_not_called()

    def test_real_creation_failure_is_not_retried_through_legacy_method(self):
        legacy = Mock()
        manager = SimpleNamespace(SetNetBlendCurveData=lambda *a: None, SetNetBlendDirectionData=lambda *a: None,
                                  InsertNetBlend2=Mock(side_effect=RuntimeError("native error")), InsertNetBlend=legacy)
        with ExitStack() as stack:
            self._patches(stack, manager)
            stack.enter_context(patch.object(boundary, "require_selection", return_value=1))
            with self.assertRaisesRegex(RuntimeError, "native error"):
                boundary.boundary_surface(ARGS)
            legacy.assert_not_called()

    def test_inconsistent_curve_references_release_selection_access(self):
        release = Mock()
        definition = SimpleNamespace(AccessSelections=lambda *a: True, GetCurvesCount=lambda d: 2, D1Curves=[], ReleaseSelectionAccess=lambda: release())
        feature = SimpleNamespace(GetTypeName2=lambda: "NetBlend", GetDefinition=lambda: definition)
        with self.assertRaisesRegex(RuntimeError, "inconsistent"):
            boundary._boundary_info(None, feature)
        release.assert_called_once()

    def test_wrong_body_type_retains_feature_and_reports_failure(self):
        info = {"directions": [{"curve_count": 2, "influence": 32, "curves": [{"tangency": 0}] * 2}] * 2,
                "trim_direction1": False, "merge_result": False, "bodies": [{"body_type": 0}]}
        manager = SimpleNamespace(SetNetBlendCurveData=lambda *a: None, SetNetBlendDirectionData=lambda *a: None, InsertNetBlend2=lambda *a: "feature")
        with ExitStack() as stack:
            self._patches(stack, manager, info)
            stack.enter_context(patch.object(boundary, "require_selection", return_value=1))
            payload = boundary.boundary_surface(ARGS)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["data"]["feature"], "feature")

    def test_curvature_comparison_uses_world_tangent_directions_and_mm(self):
        # Cylinder R=5 mm; two equivalent parameterizations, including swapped axes.
        cylinder = {"du": [0, .005, 0], "dv": [0, 0, 1], "normal": [1, 0, 0], "e": -.005, "f": 0, "g": 0}
        swapped = {"du": [0, 0, 2], "dv": [0, .01, 0], "normal": [-1, 0, 0], "e": 0, "f": 0, "g": .02}
        for frame in (cylinder, swapped):
            self.assertAlmostEqual(boundary._curvature(frame, [0, 1, 0], [0, 1, 0], [1, 0, 0]), -.2)
            self.assertAlmostEqual(boundary._curvature(frame, [0, 0, 1], [0, 0, 1], [1, 0, 0]), 0)

    def test_no_geometric_reference_cannot_confirm_continuity(self):
        self.assertFalse(boundary._verify_continuity(None, [], True)["confirmed"])

    def test_tangency_only_needs_first_derivatives(self):
        # Native blend surfaces reject second-order requests but allow first order.
        evaluate = Mock(return_value=[0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1])
        surface = SimpleNamespace(GetClosestPointOn=lambda *p: [0, 0, 0, .2, .3], Evaluate=evaluate)
        self.assertEqual(boundary._frame(surface, [0, 0, 0], False)["normal"], [0, 0, 1])
        evaluate.assert_called_once_with(.2, .3, 1, 1)
