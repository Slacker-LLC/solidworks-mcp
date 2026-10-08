# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for details.

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from solidworks_mcp import sw_geometry


class GeometryBoundaryTests(unittest.TestCase):
    def test_face_edit_rejects_nonfaces_before_com(self):
        for selection in ({"edges": [0]}, {"bodies": [0]}, {"points": [{"type": "EDGE"}]}, {}):
            with self.subTest(selection=selection), patch.object(sw_geometry, "require_part") as require:
                with self.assertRaises(RuntimeError):
                    sw_geometry.move_faces({"selection": selection, "mode": "offset", "distance_mm": 1})
                require.assert_not_called()

    def test_move_vectors_are_validated_before_com(self):
        for args in ({"mode": "offset", "distance_mm": float("nan")}, {"mode": "offset", "distance_mm": 0},
                     {"mode": "translate", "translation_mm": [0, 0, 0]},
                     {"mode": "rotate", "rotation_deg": [1, 2]},
                     {"mode": "rotate", "rotation_deg": [1, 0, 0], "origin_mm": [0, float("inf"), 0]}):
            with self.subTest(args=args), patch.object(sw_geometry, "require_part") as require:
                with self.assertRaises(RuntimeError):
                    sw_geometry.move_faces({"selection": {"faces": [0]}, **args})
                require.assert_not_called()

    def test_curve_rejects_duplicate_or_invalid_points_before_com(self):
        for points in ([[0, 0, 0]], [[0, 0, 0], [0, 0, 0]], [[0, 0, 0], [1, 2]], [[0, 0, 0], [0, 1, float("nan")]]):
            with self.subTest(points=points), patch.object(sw_geometry, "require_part") as require:
                with self.assertRaises(RuntimeError):
                    sw_geometry.create_curve_through_points({"points_mm": points})
                require.assert_not_called()

    def test_point_count_and_distribution_are_validated_before_com(self):
        for args in ({"mode": "arc_center", "count": 2}, {"mode": "evenly", "count": True},
                     {"mode": "evenly", "count": 1001}, {"mode": "along_percentage", "percentage": 101}):
            with self.subTest(args=args), patch.object(sw_geometry, "_model") as model:
                with self.assertRaises(RuntimeError):
                    sw_geometry.create_reference_points({"selection": {"edges": [0]}, **args})
                model.assert_not_called()

    def test_unimplemented_reference_plane_projection_is_explicit(self):
        with patch.object(sw_geometry, "_model") as model:
            with self.assertRaisesRegex(RuntimeError, "coverage gap"):
                sw_geometry.create_reference_points({"mode": "projection", "selection": {"planes": ["front"], "vertices": [0]}})
            model.assert_not_called()

    def test_curve_readback_cannot_hide_a_point_count_mismatch(self):
        definition = SimpleNamespace(PointArray=[0, 0, 0], GetPointCount=lambda: 2)
        feature = SimpleNamespace(GetDefinition=lambda: definition)
        with self.assertRaises(RuntimeError):
            sw_geometry._curve_info(feature)

    def test_native_point_count_counts_coordinates_instead_of_points(self):
        definition = SimpleNamespace(PointArray=[0, 0, 0, 0.01, 0.02, 0.03], GetPointCount=lambda: 6)
        feature = SimpleNamespace(GetDefinition=lambda: definition, Name="Curve")
        info = sw_geometry._curve_info(feature)
        self.assertEqual(info["point_count"], 2)
        self.assertEqual(info["points_mm"], [[0, 0, 0], [10, 20, 30]])

    def test_replacement_cannot_use_an_unrelated_selection(self):
        with patch.object(sw_geometry, "require_part") as require:
            with self.assertRaises(RuntimeError):
                sw_geometry.replace_faces({"faces_to_replace": {"faces": [0]}, "replacement": {"edges": [0]}})
            require.assert_not_called()

    def test_composite_curve_uses_the_native_selection_mark(self):
        doc = SimpleNamespace(InsertCompositeCurve=lambda: False)
        with patch.object(sw_geometry, "require_part", return_value=(None, doc)), \
                patch.object(sw_geometry, "exit_active_sketch"), \
                patch.object(sw_geometry, "require_selection") as selection, \
                patch.object(sw_geometry, "_feature_names", return_value=set()), \
                patch.object(sw_geometry, "_feature_created_after", return_value=None), \
                patch.object(sw_geometry, "_finish", return_value={"ok": False}):
            self.assertFalse(sw_geometry.composite_curve({"selection": {"edges": [0, 1]}})["ok"])
            selection.assert_called_once_with(doc, {"edges": [0, 1]}, mark=1)


if __name__ == "__main__":
    unittest.main()
