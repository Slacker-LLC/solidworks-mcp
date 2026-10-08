# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for details.

import unittest
from unittest.mock import patch
from solidworks_mcp import sw_multibody


class SurfaceConstructionBoundaries(unittest.TestCase):
    def test_revolve_cannot_silently_discard_the_second_angle(self):
        for args in ({"angle_deg": 361}, {"angle_deg": float("nan")}, {"angle2_deg": 10},
                     {"mode": "two_directions", "angle_deg": 300, "angle2_deg": 90}):
            with self.subTest(args=args), patch.object(sw_multibody, "require_part") as require:
                with self.assertRaises(RuntimeError):
                    sw_multibody.surface_revolve(args)
                require.assert_not_called()

    def test_loft_rejects_invalid_profiles_and_missing_vectors_before_com(self):
        for args in ({"profile_sketches": ["A", "A"]}, {"profile_sketches": ["A", "B"], "closed": True},
                     {"profile_sketches": ["A", "B"], "start_tangency": "vector"}):
            with self.subTest(args=args), patch.object(sw_multibody, "require_part") as require:
                with self.assertRaises(RuntimeError):
                    sw_multibody.surface_loft(args)
                require.assert_not_called()

    def test_cut_scope_is_validated_before_any_selection(self):
        for indices in ([], [0, 0], [-1], [True]):
            with self.subTest(indices=indices), patch.object(sw_multibody, "require_part") as require:
                with self.assertRaises(RuntimeError):
                    sw_multibody.cut_with_surface({"selection": {"planes": ["Plane"]}, "body_indices": indices})
                require.assert_not_called()


if __name__ == "__main__":
    unittest.main()
