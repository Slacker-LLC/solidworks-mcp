# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for details.

"""Native feature boundary checks; geometry acceptance stays in live tests."""

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from solidworks_mcp import sw_core, sw_multibody, sw_sheetmetal, sw_weldment


class SheetMetalBoundaryTests(unittest.TestCase):
    def test_invalid_sheet_parameters_are_rejected_before_com(self):
        for args in ({"thickness_mm": 0}, {"thickness_mm": 1, "bend_radius_mm": -1},
                     {"thickness_mm": 1, "k_factor": 1.1}, {"thickness_mm": 1, "k_factor": float("nan")}):
            with self.subTest(args=args), patch.object(sw_sheetmetal, "require_part") as require:
                with self.assertRaises(RuntimeError):
                    sw_sheetmetal.sheet_metal_base_flange(args)
                require.assert_not_called()

    def test_empty_parameter_update_cannot_roll_back_the_model(self):
        with patch.object(sw_sheetmetal, "require_part") as require:
            with self.assertRaises(RuntimeError):
                sw_sheetmetal.set_sheet_metal_parameters({})
            require.assert_not_called()

    def test_parameter_edit_uses_native_template_defaults(self):
        template = object()
        ext = SimpleNamespace(GetTemplateSheetMetal=lambda: template)
        with patch.object(sw_sheetmetal, "extension", return_value=ext):
            self.assertIs(sw_sheetmetal._sheet_feature(object(), None), template)

    def test_export_refuses_an_unsaved_part_before_creating_a_file(self):
        doc = SimpleNamespace(GetPathName=lambda: "")
        with patch.object(sw_sheetmetal, "require_part", return_value=(None, doc)), \
                patch.object(sw_sheetmetal, "validated_output_path") as output:
            self.assertFalse(sw_sheetmetal.export_flat_pattern({"path": "part.dxf"})["ok"])
            output.assert_not_called()


class SurfaceBoundaryTests(unittest.TestCase):
    def test_knit_tolerance_is_validated_before_selection(self):
        for tolerance in (0, 1, float("nan")):
            with self.subTest(tolerance=tolerance), patch.object(sw_multibody, "require_part") as require:
                with self.assertRaises(RuntimeError):
                    sw_multibody.knit_surfaces({"selection": {"surface_bodies": [0, 1]}, "tolerance_mm": tolerance})
                require.assert_not_called()

    def test_surface_indices_resolve_from_the_sheet_body_list(self):
        sheet = object()
        doc = object()
        with patch.object(sw_core, "clear_selection"), \
                patch.object(sw_core, "get_bodies", return_value=[sheet]) as bodies, \
                patch.object(sw_core, "select_body", return_value=True) as select:
            self.assertEqual(sw_core.apply_selection(doc, {"surface_bodies": [0]}, mark=1), 1)
            bodies.assert_called_once_with(doc, sw_core.BODY_SHEET)
            select.assert_called_once_with(doc, sheet, 1, True)

    def test_out_of_range_surface_indices_cannot_select_a_solid_body(self):
        with patch.object(sw_core, "clear_selection"), \
                patch.object(sw_core, "get_bodies", return_value=[]) as bodies, \
                patch.object(sw_core, "select_body") as select:
            with self.assertRaises(RuntimeError):
                sw_core.apply_selection(object(), {"surface_bodies": [0]})
            select.assert_not_called()


class WeldmentBoundaryTests(unittest.TestCase):
    def test_only_existing_native_profile_files_are_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            invalid = Path(folder) / "profile.step"
            invalid.write_text("fixture", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                sw_weldment._profile_path(invalid)
            with self.assertRaises(RuntimeError):
                sw_weldment._profile_path(Path(folder) / "missing.sldlfp")

    def test_invalid_group_index_is_rejected_before_feature_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder) / "profile.sldlfp"
            profile.write_bytes(b"fixture")
            with patch.object(sw_weldment, "require_part", return_value=(None, object())), \
                    patch.object(sw_weldment, "exit_active_sketch"), \
                    patch.object(sw_weldment, "sketch_segment_objects", return_value=[object()]), \
                    patch.object(sw_weldment, "feature_manager") as manager:
                with self.assertRaises(RuntimeError):
                    sw_weldment.insert_structural_member({"profile_path": str(profile), "groups": [{"sketch_name": "Path", "segments": [1]}]})
                manager.assert_not_called()


if __name__ == "__main__":
    unittest.main()
