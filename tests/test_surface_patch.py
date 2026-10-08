# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for details.

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from solidworks_mcp import sw_multibody


class SurfacePatchContracts(unittest.TestCase):
    def test_invalid_fill_boundaries_fail_before_com(self):
        cases = [{"boundaries": []}, {"boundaries": [{"selection": {"faces": [0]}}]},
                 {"boundaries": [{"selection": {"sketches": ["Profile"]}, "continuity": "tangent"}]},
                 {"boundaries": [{"selection": {"edges": [0]}}, {"selection": {"edges": [0]}}]},
                 {"boundaries": [{"selection": {"edges": [0]}}], "resolution": True}]
        for args in cases:
            with self.subTest(args=args), patch.object(sw_multibody, "require_part") as require:
                with self.assertRaises(RuntimeError):
                    sw_multibody.fill_surface(args)
                require.assert_not_called()

    def test_invalid_ruled_arguments_cannot_silently_discard_a_direction(self):
        cases = [{"length_mm": float("inf")}, {"mode": "tapered"}, {"mode": "perpendicular"},
                 {"mode": "sweep", "direction_vector": [0, 0, 0]}, {"direction_vector": [0, 0, 1]},
                 {"flip_pull_direction": True}, {"mode": "normal", "flip_direction": True},
                 {"mode": "tapered", "direction_selection": {"planes": ["front"]}, "angle_deg": 90}]
        for options in cases:
            with self.subTest(options=options), patch.object(sw_multibody, "require_part") as require:
                with self.assertRaises(RuntimeError):
                    sw_multibody.ruled_surface({"selection": {"edges": [0]}, "length_mm": 5, **options})
                require.assert_not_called()

    def test_ruled_numeric_vector_is_unitless_and_length_is_metres(self):
        insert = Mock(return_value="feature")
        manager = SimpleNamespace(InsertRuledSurfaceFromEdge2=insert)
        with patch.object(sw_multibody, "require_part", return_value=(None, object())), \
             patch.object(sw_multibody, "exit_active_sketch"), patch.object(sw_multibody, "require_selection") as selection, \
             patch.object(sw_multibody, "feature_manager", return_value=manager), \
             patch.object(sw_multibody, "_finish", return_value={"ok": True}):
            sw_multibody.ruled_surface({"selection": {"surface_edges": [0]}, "mode": "sweep", "length_mm": 5,
                                        "direction_vector": [0, 0, 10], "alternate_face": True})
        insert.assert_called_once_with(4, 0.005, False, False, False, 0.0, True, 0.0, 0.0, 1.0, False)
        self.assertEqual(selection.call_args.kwargs["mark"], 6)

    def test_legacy_ruled_api_does_not_drop_remove_connecting_surfaces(self):
        old = Mock(return_value="feature")
        manager = SimpleNamespace(InsertRuledSurfaceFromEdge=old)
        with patch.object(sw_multibody, "require_part", return_value=(None, object())), \
             patch.object(sw_multibody, "exit_active_sketch"), patch.object(sw_multibody, "require_selection"), \
             patch.object(sw_multibody, "feature_manager", return_value=manager), \
             patch.object(sw_multibody, "_finish", return_value={"ok": True}):
            sw_multibody.ruled_surface({"selection": {"edges": [0]}, "length_mm": 5})
            self.assertEqual(old.call_count, 1)
            old.reset_mock()
            with self.assertRaises(RuntimeError):
                sw_multibody.ruled_surface({"selection": {"edges": [0]}, "length_mm": 5, "remove_connecting_surfaces": True})
            old.assert_not_called()

    def test_unconfirmed_normal_reversal_does_not_report_success(self):
        manager = SimpleNamespace(InsertRuledSurfaceFromEdge2=Mock(return_value="feature"))
        with patch.object(sw_multibody, "require_part", return_value=(None, object())), \
             patch.object(sw_multibody, "exit_active_sketch"), patch.object(sw_multibody, "require_selection"), \
             patch.object(sw_multibody, "feature_manager", return_value=manager), \
             patch.object(sw_multibody, "_finish", return_value={"ok": True, "data": {"feature": "NativeFeature"}}):
            output = sw_multibody.ruled_surface({"selection": {"surface_edges": [0]}, "mode": "normal",
                                                "length_mm": 5, "flip_pull_direction": True})
        self.assertFalse(output["ok"])
        self.assertEqual(output["data"]["feature"], "NativeFeature")

    def test_fill_does_not_report_success_for_unconfirmed_native_continuity(self):
        data = SimpleNamespace(AccessSelections=lambda *args: True, ReleaseSelectionAccess=Mock(),
                               GetPatchBoundary=lambda *args: [object()], GetCurvatureControl=lambda obj: -1)
        feature = SimpleNamespace(GetDefinition=lambda: data)
        manager = SimpleNamespace(InsertFillSurface2=Mock(return_value=feature))
        with patch.object(sw_multibody, "require_part", return_value=(None, object())), \
             patch.object(sw_multibody, "exit_active_sketch"), patch.object(sw_multibody, "clear_selection"), \
             patch.object(sw_multibody, "require_selection"), patch.object(sw_multibody, "_selected_objects", return_value=[object()]), \
             patch.object(sw_multibody, "get_bodies", return_value=[]), patch.object(sw_multibody, "feature_manager", return_value=manager), \
             patch.object(sw_multibody, "_finish", return_value={"ok": True, "data": {}}):
            output = sw_multibody.fill_surface({"boundaries": [{"selection": {"edges": [0]}, "continuity": "curvature"}]})
        self.assertFalse(output["ok"])
        self.assertEqual(output["data"]["native_continuity_controls"], [-1])
        data.ReleaseSelectionAccess.assert_called_once()


if __name__ == "__main__":
    unittest.main()
