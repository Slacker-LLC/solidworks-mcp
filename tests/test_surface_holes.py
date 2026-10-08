# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for details.
# limitations under the License.

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from solidworks_mcp import sw_multibody as surfaces


class SurfaceHoleContract(unittest.TestCase):
    def test_invalid_selection_rejected_before_com(self):
        for selection in ({}, {"edges": [0]}, {"surface_faces": [0]},
                          {"surface_edges": [True]}, {"surface_edges": [0, 0]}):
            with self.subTest(selection=selection), patch.object(surfaces, "require_part") as require:
                with self.assertRaises(RuntimeError):
                    surfaces.delete_surface_holes({"selection": selection})
                require.assert_not_called()

    def test_version_dispatch_preserves_real_failures(self):
        for modern in (True, False):
            legacy = Mock()
            create = Mock(return_value="feature")
            manager = SimpleNamespace(**({"InsertDeleteHoleForSurface": create} if modern else {}))
            doc = SimpleNamespace(InsertDeleteHole=legacy)
            with patch.object(surfaces, "require_part", return_value=(None, doc)), \
                 patch.object(surfaces, "exit_active_sketch"), patch.object(surfaces, "require_selection"), \
                 patch.object(surfaces, "feature_manager", return_value=manager), \
                 patch.object(surfaces, "_feature_names", return_value=["before"]), \
                 patch.object(surfaces, "_feature_created_after", return_value="feature"), \
                 patch.object(surfaces, "_finish", return_value={"ok": True}):
                surfaces.delete_surface_holes({"selection": {"surface_edges": [0]}})
                self.assertEqual(legacy.call_count, 0 if modern else 1)
                if modern:
                    create.side_effect = RuntimeError("native failure")
                    with self.assertRaisesRegex(RuntimeError, "native failure"):
                        surfaces.delete_surface_holes({"selection": {"surface_edges": [0]}})
                    legacy.assert_not_called()
