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
import unittest
from unittest.mock import Mock, patch

from solidworks_mcp import sw_file


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
