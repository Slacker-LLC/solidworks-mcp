# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for details.
# limitations under the License.

import math
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch
from solidworks_mcp import sw_multibody as surfaces


class SurfaceSweepContract(unittest.TestCase):
    def test_incompatible_options_fail_before_com(self):
        cases = [{}, {"profile_sketch": "P", "circular_diameter_mm": 10},
                 {"circular_diameter_mm": float("nan")},
                 {"profile_sketch": "P", "twist_angle_deg": 20},
                 {"profile_sketch": "P", "twist_control": "constant_twist", "twist_angle_deg": -20},
                 {"profile_sketch": "P", "twist_control": "two_guides", "guide_sketches": ["G"]},
                 {"profile_sketch": "P", "direction": "both", "guide_sketches": ["G"]},
                 {"profile_sketch": "Path"},
                 {"profile_sketch": "P", "guide_sketches": ["G", "G"]}]
        for options in cases:
            with self.subTest(options=options), patch.object(surfaces, "require_part") as require:
                with self.assertRaises(RuntimeError):
                    surfaces.surface_sweep({"path_sketch": "Path", **options})
                require.assert_not_called()

    def invoke(self, options=None, overrides=None, edit_success=True):
        settings = dict(TwistControlType=0, CircularProfile=False, Direction=-1,
                        MaintainTangency=False, AdvancedSmoothing=False, MergeSmoothFaces=False,
                        AlignWithEndFaces=False, GetTwistAngle=lambda: -math.pi / 2,
                        GetD2TwistAngle=lambda: math.pi / 4, GetGuideCurvesCount=lambda: 0)
        settings.update(overrides or {})
        definition = SimpleNamespace(**settings, AccessSelections=Mock(return_value=edit_success),
                                     ReleaseSelectionAccess=Mock(), SetTwistAngle=Mock(), SetD2TwistAngle=Mock())
        body = SimpleNamespace(GetType=lambda: surfaces.BODY_SHEET)
        face = SimpleNamespace(GetBody=lambda: body)
        feature = SimpleNamespace(GetDefinition=lambda: definition, GetFaces=lambda: [face],
                                  ModifyDefinition=Mock(return_value=edit_success))
        manager = SimpleNamespace(CreateDefinition=Mock(return_value=SimpleNamespace(SetTwistAngle=Mock(), SetD2TwistAngle=Mock())),
                                  CreateFeature=Mock(return_value=feature))
        with ExitStack() as stack:
            for name, kwargs in (("require_part", {"return_value": (None, object())}),
                                 ("feature_manager", {"return_value": manager}),
                                 ("_finish", {"return_value": {"ok": True, "data": {"feature": "Sweep"}}}),
                                 ("exit_active_sketch", {}), ("clear_selection", {}), ("require_selection", {})):
                stack.enter_context(patch.object(surfaces, name, **kwargs))
            return surfaces.surface_sweep({"profile_sketch": "P", "path_sketch": "Path", **(options or {})}), feature, definition

    def test_endpoint_direction_sentinel_does_not_confirm_bidirectional_request(self):
        self.assertTrue(self.invoke()[0]["ok"])
        payload, _, _ = self.invoke({"direction": "both"})
        self.assertFalse(payload["ok"])
        self.assertIn("Direction", payload["message"])

    def test_signed_twist_requires_successful_geometry_rebuild(self):
        options = {"twist_control": "constant_twist", "twist_angle_deg": 90, "reverse_twist": True}
        payload, feature, definition = self.invoke(options, {"TwistControlType": 8})
        self.assertTrue(payload["ok"])
        feature.ModifyDefinition.assert_called_once()
        definition.ReleaseSelectionAccess.assert_called_once()
        self.assertFalse(self.invoke(options, {"TwistControlType": 8}, edit_success=False)[0]["ok"])

    def test_native_readback_mismatch_keeps_feature_but_reports_failure(self):
        payload, _, _ = self.invoke({"keep_tangency": True})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["data"]["feature"], "Sweep")
        self.assertIn("MaintainTangency", payload["message"])

    def test_second_direction_uses_its_own_twist_channel(self):
        payload, _, definition = self.invoke(
            {"direction": "second", "twist_control": "constant_twist", "twist_angle_deg": 45, "reverse_twist": True},
            {"Direction": 2, "TwistControlType": 8, "D2ReverseTwistDir": True})
        self.assertTrue(payload["ok"])
        definition.SetTwistAngle.assert_called_once_with(0)
        definition.SetD2TwistAngle.assert_called_once_with(math.pi / 4)
        self.assertEqual(payload["data"]["native_settings"]["twist_angle_deg"], 45)

    def test_guide_control_uses_first_guides_without_limiting_total_guide_count(self):
        for mode, control, names in (("first_guide", 2, ["G1", "G2"]),
                                     ("two_guides", 3, ["G1", "G2", "G3"])):
            with self.subTest(mode=mode):
                self.assertTrue(self.invoke({"twist_control": mode, "guide_sketches": names},
                                           {"TwistControlType": control, "GetGuideCurvesCount": lambda: len(names)})[0]["ok"])
