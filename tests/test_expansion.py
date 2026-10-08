# Copyright 2026 JIALE LIU
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Offline regressions for expanded tool boundaries and result verification."""

from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from solidworks_mcp import server, sw_core, sw_manage, sw_multibody


class BodySelectionTests(unittest.TestCase):
    def test_body_select_uses_select_data_not_an_integer(self):
        body, doc, data = Mock(), Mock(), Mock()
        doc.SelectionManager.CreateSelectData.return_value = data
        body.Select2.return_value = True
        self.assertTrue(sw_core.select_body(doc, body, mark=7, append=False))
        self.assertEqual(data.Mark, 7)
        body.Select2.assert_called_once_with(False, data)

    def test_invalid_indices_do_not_change_selection(self):
        body = Mock()
        for spec in ({"bodies": [-1]}, {"bodies": [1]}, {"bodies": [0, 0]}, {"bodies": []}, {"bodies": [0], "faces": [1]}):
            with self.subTest(spec=spec), patch.object(sw_multibody, "get_bodies", return_value=[body]), \
                    patch.object(sw_multibody, "require_selection") as select:
                with self.assertRaises(RuntimeError):
                    sw_multibody._selected_bodies(Mock(), spec)
                select.assert_not_called()

    def test_scale_rejects_nonpositive_and_nonfinite_factors_before_com(self):
        for factor in (0, -1, float("nan"), float("inf")):
            with self.subTest(factor=factor), patch.object(sw_multibody, "require_part") as require:
                with self.assertRaises(RuntimeError):
                    sw_multibody.scale_bodies({"selection": {"bodies": [0]}, "factor": factor})
                require.assert_not_called()

    def test_translation_and_rotation_cannot_be_silently_combined(self):
        with patch.object(sw_multibody, "require_part") as require:
            with self.assertRaisesRegex(RuntimeError, "separate calls"):
                sw_multibody.move_copy_bodies({"selection": {"bodies": [0]}, "x_mm": 5, "rotation_z_deg": 90})
            require.assert_not_called()

    def test_move_uses_the_body_selection_mark_required_by_solidworks(self):
        doc, body = Mock(), Mock()
        with patch.object(sw_multibody, "require_part", return_value=(None, doc)), \
                patch.object(sw_multibody, "_selected_bodies", return_value=[body]) as selected, \
                patch.object(sw_multibody, "get_bodies", return_value=[body]), \
                patch.object(sw_multibody, "feature_manager"), \
                patch.object(sw_multibody, "_finish", return_value={"ok": True}):
            self.assertTrue(sw_multibody.move_copy_bodies({"selection": {"bodies": [0]}, "x_mm": 5})["ok"])
            selected.assert_called_once_with(doc, {"bodies": [0]}, mark=1)


class ManagementTests(unittest.TestCase):
    def test_dirty_document_close_requires_explicit_discard(self):
        app, doc = Mock(), Mock()
        with patch.object(sw_manage, "running_app", return_value=app), \
                patch.object(sw_manage, "_open_doc", return_value=doc), \
                patch.object(sw_manage, "document_info", return_value={"dirty": True, "title": "Part1"}):
            self.assertFalse(sw_manage.close_document({"name": "Part1"})["ok"])
            app.CloseDoc.assert_not_called()

    def test_active_configuration_cannot_be_deleted(self):
        doc = Mock()
        doc.ConfigurationManager.ActiveConfiguration.Name = "Default"
        with patch.object(sw_manage, "_configuration_doc", return_value=doc), \
                patch.object(sw_manage, "_config"), \
                patch.object(sw_manage, "_config_names", return_value=["Default", "Alt"]):
            self.assertFalse(sw_manage.delete_configuration({"name": "Default"})["ok"])
            doc.DeleteConfiguration2.assert_not_called()

    def test_out_of_range_equation_indices(self):
        manager = SimpleNamespace(GetCount=lambda: 2)
        for index in (-1, 2, 100):
            with self.subTest(index=index), self.assertRaises(RuntimeError):
                sw_manage._eq_index(manager, index)

    def test_equation_error_is_not_reported_as_success(self):
        manager = Mock()
        with patch.object(sw_manage, "_equations", return_value=[{"index": 0, "status": -1}]), \
                patch.object(sw_manage, "_changed", return_value={"ok": True, "message": "done"}):
            self.assertFalse(sw_manage._equation_result(Mock(), manager, "done")["ok"])

    def test_material_search_supports_namespaces_and_truncation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "materials.sldmat"
            path.write_text('<materials xmlns="urn:materials"><classification><material name="Steel"/><material name="Stainless Steel"/><material name="Aluminum"/></classification></materials>', encoding="utf-8")
            app = SimpleNamespace(GetMaterialDatabases=lambda: [str(path)])
            with patch.object(sw_manage, "running_app", return_value=app):
                payload = sw_manage.list_materials({"query": "STEEL", "limit": 1})
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["data"]["total_matches"], 2)
            self.assertTrue(payload["data"]["truncated"])
            self.assertEqual(payload["data"]["materials"], [{"name": "Steel", "database": str(path)}])

    def test_unreadable_material_database_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            app = SimpleNamespace(GetMaterialDatabases=lambda: [str(Path(folder) / "missing.sldmat")])
            with patch.object(sw_manage, "running_app", return_value=app):
                payload = sw_manage.list_materials({})
            self.assertFalse(payload["ok"])
            self.assertEqual(len(payload["data"]["failures"]), 1)


class RegistryTests(unittest.TestCase):
    def test_expanded_tools_have_unique_names_and_valid_handlers(self):
        names = [entry.name for entry in server.TOOLS]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names), set(sw_core.HANDLERS))
        expected = {"scale_bodies", "move_copy_bodies", "combine_bodies", "delete_bodies", "surface_extrude", "planar_surface", "create_helix", "list_surface_bodies", "list_materials", "create_configuration", "set_equation", "set_custom_property", "set_component_visibility", "set_component_suppression", "set_component_configuration"}
        self.assertLessEqual(expected, set(names))
