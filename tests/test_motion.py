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

from solidworks_mcp import sw_motion


class MotionBoundaryTests(unittest.TestCase):
    def test_invalid_timeline_is_rejected_before_com(self):
        for args in ({"duration_sec": 0}, {"time_sec": -1}, {"time_sec": float("nan")}, {}):
            with self.subTest(args=args), patch.object(sw_motion, "_manager") as manager:
                with self.assertRaises(RuntimeError):
                    sw_motion.set_motion_study_timing({"name": "study", **args})
                manager.assert_not_called()

    def test_out_of_bounds_cursor_cannot_change_duration(self):
        with patch.object(sw_motion, "_manager") as manager, patch.object(sw_motion, "_study") as study, \
                patch.object(sw_motion, "_info", return_value={"duration_sec": 5}):
            with self.assertRaises(RuntimeError):
                sw_motion.set_motion_study_timing({"name": "study", "duration_sec": 6, "time_sec": 7})
            manager.return_value.ActivateMotionStudy.assert_not_called()
            study.return_value.SetDuration.assert_not_called()

    def test_unavailable_solver_is_not_assigned(self):
        study = SimpleNamespace(StudyType=1)
        with patch.object(sw_motion, "_supported", return_value=1), patch.object(sw_motion, "_info", return_value={}):
            self.assertFalse(sw_motion._set_type(study, 4)["ok"])
        self.assertEqual(study.StudyType, 1)

    def test_supported_type_mask_reads_byref_output(self):
        def supported(mask):
            mask.value = 3
            return True
        self.assertEqual(sw_motion._supported(SimpleNamespace(GetSupportedStudyTypes=supported)), 3)


if __name__ == "__main__":
    unittest.main()
