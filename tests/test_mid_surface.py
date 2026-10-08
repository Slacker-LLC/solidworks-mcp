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
from solidworks_mcp import sw_core, sw_multibody as surfaces


class MidSurfaceContract(unittest.TestCase):
    def test_invalid_placement_rejected_before_com(self):
        for placement in (-1.01, 1.01, float("nan"), float("inf")):
            with self.subTest(placement=placement), patch.object(surfaces, "require_part") as require:
                with self.assertRaises(RuntimeError):
                    surfaces.mid_surface({"placement": placement})
                require.assert_not_called()

    def test_out_parameters_use_double_and_dispatch_types(self):
        with patch.object(sw_core.win32com.client, "VARIANT") as variant:
            sw_core.byref_double(2)
            variant.assert_called_with(sw_core.pythoncom.VT_BYREF | sw_core.pythoncom.VT_R8, 2.0)
            sw_core.byref_dispatch()
            variant.assert_called_with(sw_core.pythoncom.VT_BYREF | sw_core.pythoncom.VT_DISPATCH, None)

    def test_geometry_measurement_detects_actual_placement(self):
        neutral = object()
        first = SimpleNamespace(GetClosestPointOn=lambda *p: (0, 0, 0.002))
        partner = SimpleNamespace(GetClosestPointOn=lambda *p: (0, 0, 0))
        for height, expected in ((0.001, 0), (0, -1), (0.002, 1)):
            with patch.object(surfaces, "_face_point", return_value=[0, 0, height]):
                self.assertAlmostEqual(surfaces._mid_placement(neutral, first, partner, 0.002), expected)
        with patch.object(surfaces, "_face_point", return_value=[1, 0, 0.001]):
            self.assertIsNone(surfaces._mid_placement(neutral, first, partner, 0.002))

    def test_incomplete_native_topology_cannot_be_reported_as_valid(self):
        mid = SimpleNamespace(GetFacePairCount=lambda: 1, GetFaceCount=lambda: 2, GetFaces=lambda: [object()])
        feature = SimpleNamespace(GetTypeName2=lambda: "MidRefSurface", GetSpecificFeature2=lambda: mid)
        with self.assertRaisesRegex(RuntimeError, "inconsistent"):
            surfaces._mid_surface_info(feature)
        with self.assertRaisesRegex(RuntimeError, "existing midsurface"):
            surfaces._mid_surface_info(SimpleNamespace(GetTypeName2=lambda: "Extrusion"))

    def test_version_dispatch_and_placement_failure_preserve_feature_evidence(self):
        for modern in (True, False):
            create = Mock(return_value=object())
            legacy = Mock(return_value=object())
            manager = SimpleNamespace(**({"InsertMidSurface": create} if modern else {}))
            doc = SimpleNamespace(InsertMidSurfaceExt=legacy)
            info = {"sheet_count": 1, "faces": [{"measured_placement": 0}]}
            with patch.object(surfaces, "require_part", return_value=(None, doc)), \
                 patch.object(surfaces, "exit_active_sketch"), patch.object(surfaces, "clear_selection"), \
                 patch.object(surfaces, "feature_manager", return_value=manager), \
                 patch.object(surfaces, "_feature_names", return_value=[]), \
                 patch.object(surfaces, "_feature_created_after", return_value="feature"), \
                 patch.object(surfaces, "_finish", side_effect=lambda *a: {"ok": True, "data": {"feature": "Mid"}}), \
                 patch.object(surfaces, "_mid_surface_info", return_value=info):
                self.assertTrue(surfaces.mid_surface({})["ok"])
                payload = surfaces.mid_surface({"placement": 0.5})
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["data"]["feature"], "Mid")
                if modern:
                    legacy.assert_not_called()
                    self.assertEqual(create.call_args.args[0].varianttype, sw_core.pythoncom.VT_DISPATCH)
                    create.side_effect = RuntimeError("native failure")
                    with self.assertRaisesRegex(RuntimeError, "native failure"):
                        surfaces.mid_surface({})
                    legacy.assert_not_called()
                else:
                    legacy.assert_called_with(0.5, True)
