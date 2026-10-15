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

"""Material readback and package import regressions, without a CAD session."""

import ast
import importlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from solidworks_mcp import sw_drawing, sw_file


def document(kind: int, title: str = "Part1"):
    return SimpleNamespace(
        GetType=lambda: kind,
        GetTitle=lambda: title,
        GetPathName=lambda: "",
        GetSaveFlag=lambda: True,
    )


class DocumentTemplateTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def template(self, name: str) -> Path:
        path = self.root / name
        path.write_bytes(b"template")
        return path

    def test_explicit_template_is_passed_exactly_to_new_document(self):
        template = self.template("Custom.PRTDOT")
        app = Mock()
        app.NewDocument.return_value = document(1)
        with patch.object(sw_file, "running_app", return_value=app):
            payload = sw_file.create_new_document({"kind": "part", "template_path": str(template)})
        self.assertTrue(payload["ok"])
        app.NewDocument.assert_called_once_with(str(template.resolve()), 0, 0.0, 0.0)
        app.GetUserPreferenceStringValue.assert_not_called()
        self.assertEqual(payload["data"]["template"]["source"], "explicit")
        self.assertEqual(payload["data"]["template"]["resolved_path"], str(template.resolve()))
        self.assertEqual(payload["data"]["verification"]["actual_document_type"], "part")
        self.assertFalse(payload["data"]["verification"]["source_template_readback_available"])

    def test_missing_explicit_template_never_falls_back(self):
        missing = self.root / "missing.prtdot"
        with patch.object(sw_file, "running_app") as running, \
                patch.object(sw_file, "discover_template") as discover:
            payload = sw_file.create_new_document({"kind": "part", "template_path": str(missing)})
        self.assertFalse(payload["ok"])
        running.assert_not_called()
        discover.assert_not_called()

    def test_wrong_explicit_extension_is_rejected_case_insensitively(self):
        app = Mock()
        wrong = self.template("Drawing.DRWDOT")
        with patch.object(sw_file, "running_app", return_value=app):
            payload = sw_file.create_new_document({"kind": "part", "template_path": str(wrong)})
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["data"]["template"]["extension_valid"])
        app.NewDocument.assert_not_called()

    def test_uppercase_matching_extension_is_accepted(self):
        template = self.template("Plate.PRTDOT")
        app = Mock()
        app.NewDocument.return_value = document(1)
        with patch.object(sw_file, "running_app", return_value=app):
            payload = sw_file.create_new_document({"kind": "part", "template_path": str(template)})
        self.assertTrue(payload["ok"])

    def test_unreadable_explicit_template_is_rejected_before_com(self):
        template = self.template("Locked.prtdot")
        with patch.object(Path, "open", side_effect=PermissionError("denied")), \
                patch.object(sw_file, "running_app") as running:
            payload = sw_file.create_new_document({"kind": "part", "template_path": str(template)})
        self.assertFalse(payload["ok"])
        self.assertIn("not readable", payload["message"])
        running.assert_not_called()

    def test_wrong_result_type_fails_and_leaves_document_available(self):
        template = self.template("Part.prtdot")
        wrong = document(2, "Assembly1")
        app = Mock()
        app.NewDocument.return_value = wrong
        with patch.object(sw_file, "running_app", return_value=app):
            payload = sw_file.create_new_document({"kind": "part", "template_path": str(template)})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["data"]["document"]["title"], "Assembly1")
        self.assertEqual(payload["data"]["verification"]["actual_document_type"], "assembly")
        app.CloseDoc.assert_not_called()

    def test_legacy_configured_default_is_preserved(self):
        template = self.template("Default.prtdot")
        app = Mock()
        app.GetUserPreferenceStringValue.return_value = str(template)
        app.NewDocument.return_value = document(1)
        with patch.object(sw_file, "running_app", return_value=app):
            payload = sw_file.create_new_document({"kind": "part"})
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["template"]["source"], "solidworks_default")

    def test_legacy_discovery_is_preserved(self):
        template = self.template("Found.prtdot")
        app = Mock()
        app.GetUserPreferenceStringValue.return_value = ""
        app.NewDocument.return_value = document(1)
        with patch.object(sw_file, "running_app", return_value=app), \
                patch.object(sw_file, "discover_template", return_value=template):
            payload = sw_file.create_new_document({"kind": "part"})
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["template"]["source"], "discovered")

    def test_create_drawing_forwards_template_and_reports_sheet_readback(self):
        template = self.template("A4.drwdot")
        sheet = SimpleNamespace(
            GetName=lambda: "Sheet1",
            GetProperties2=lambda: [6, 6, 1.0, 2.0, True, 0.297, 0.21],
            GetTemplateName=lambda: r"C:\formats\A4.slddrt",
        )
        drawing = document(3, "Drawing1")
        drawing.GetCurrentSheet = lambda: sheet
        drawing.GetSheetNames = lambda: ["Sheet1"]
        created = {
            "ok": True,
            "data": {
                "template": {"resolved_path": str(template), "source": "explicit"},
                "verification": {"actual_document_type": "drawing"},
            },
        }
        with patch.object(sw_drawing, "new_document", return_value=created) as new_doc, \
                patch.object(sw_drawing, "require_drawing", return_value=(Mock(), drawing)):
            payload = sw_drawing.create_drawing({"template_path": str(template)})
        new_doc.assert_called_once_with("drawing", str(template))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["sheet_size_mm"], [297.0, 210.0])
        self.assertEqual(payload["data"]["scale"], [1.0, 2.0])
        self.assertTrue(payload["data"]["first_angle"])
        self.assertEqual(payload["data"]["sheet_format_path"], r"C:\formats\A4.slddrt")


class MaterialReadbackTests(unittest.TestCase):
    def test_byref_name(self):
        doc = Mock()
        doc.GetMaterialPropertyName2.return_value = "AISI 1020"
        self.assertEqual(sw_file._material_readback(doc, "Materials"), "AISI 1020")
        doc.GetMaterialPropertyName2.assert_called_once()

    def test_tuple_return(self):
        doc = Mock()
        doc.GetMaterialPropertyName2.return_value = ("AISI 1020", "Materials")
        self.assertEqual(sw_file._material_readback(doc, "Materials"), "AISI 1020")

    def test_plain_string_fallback(self):
        doc = Mock()
        doc.GetMaterialPropertyName2.side_effect = [TypeError("byref unsupported"), "Steel"]
        self.assertEqual(sw_file._material_readback(doc, "Materials"), "Steel")

    def test_material_id_fallback(self):
        doc = Mock()
        doc.GetMaterialPropertyName2.return_value = ""
        doc.MaterialIdName = "Materials|Steel"
        self.assertEqual(sw_file._material_readback(doc, "Materials"), "Steel")

    def test_missing_material(self):
        doc = Mock()
        doc.GetMaterialPropertyName2.return_value = ""
        doc.MaterialIdName = ""
        self.assertEqual(sw_file._material_readback(doc, "Materials"), "")

    def test_unknown_material_is_not_reported_as_applied(self):
        doc = Mock()
        doc.GetMaterialPropertyName2.return_value = "Previous material"
        with patch.object(sw_file, "require_part", return_value=(None, doc)), \
                patch.object(sw_file, "rebuild"):
            payload = sw_file.set_material({"name": "Missing material"})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["data"]["material"], "Previous material")


class PackageImportTests(unittest.TestCase):
    def test_deferred_helper_imports_resolve_in_package_context(self):
        # Server import alone never executes imports hidden inside handlers.
        # Resolve those imports too, without invoking geometry-changing tools.
        root = Path(sw_file.__file__).parent
        for source in root.glob("sw_*.py"):
            for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.ImportFrom):
                    continue
                if not (node.module or "").startswith("sw_"):
                    continue
                with self.subTest(source=source.name, line=node.lineno):
                    module = importlib.import_module(
                        "." * node.level + node.module, "solidworks_mcp"
                    )
                    for alias in node.names:
                        self.assertTrue(hasattr(module, alias.name), alias.name)

