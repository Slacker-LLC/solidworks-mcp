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
from unittest.mock import patch
from solidworks_mcp import sw_core, sw_multibody


class SurfaceTopologyContract(unittest.TestCase):
    def test_component_fallback_does_not_leak_solid_bodies_into_surface_indices(self):
        body = SimpleNamespace(GetType=lambda: sw_core.BODY_SOLID)
        component = SimpleNamespace(GetBody=lambda: body)
        self.assertEqual(sw_core.component_bodies(component), [body])
        self.assertEqual(sw_core.component_bodies(component, sw_core.BODY_SHEET), [])
        self.assertEqual(sw_core.component_bodies(component, sw_core.BODY_ALL), [body])

    def test_surface_indices_are_separate_and_match_measurement_order(self):
        solid = SimpleNamespace(GetFaces=lambda: ["solid-face"], GetEdges=lambda: ["solid-edge"])
        sheets = [SimpleNamespace(GetFaces=lambda: ["sheet-face-a", "sheet-face-b"], GetEdges=lambda: ["sheet-edge-a"]),
                  SimpleNamespace(GetFaces=lambda: ["sheet-face-c"], GetEdges=lambda: ["sheet-edge-b", "sheet-edge-c"])]
        def contexts(doc, kind=sw_core.BODY_SOLID):
            return [(body, "body", None) for body in (sheets if kind == sw_core.BODY_SHEET else [solid])]
        with patch.object(sw_core, "iter_body_context", side_effect=contexts):
            self.assertEqual([f["_obj"] for f in sw_core.enumerate_faces(None)], ["solid-face"])
            self.assertEqual([e["_obj"] for e in sw_core.enumerate_edges(None)], ["solid-edge"])
            for measured, walk in ((sw_core.enumerate_faces, sw_core.iter_face_objects),
                                   (sw_core.enumerate_edges, sw_core.iter_edge_objects)):
                self.assertEqual([e["_obj"] for e in measured(None, sw_core.BODY_SHEET)],
                                 [obj for obj, _ in walk(None, sw_core.BODY_SHEET)])
            with patch.object(sw_core, "clear_selection"), patch.object(sw_core, "select_object", return_value=True) as select:
                self.assertEqual(sw_core.apply_selection(None, {"surface_faces": [2], "surface_edges": [1]}, mark=4), 2)
                self.assertEqual([c.args[1] for c in select.call_args_list], ["sheet-face-c", "sheet-edge-b"])
                self.assertTrue(all(c.args[2:] == (4, True) for c in select.call_args_list))

    def test_invalid_boundaries_fail_before_com(self):
        for selection in ({}, {"faces": [0]}, {"surface_edges": [-1]}, {"surface_faces": [True]},
                          {"surface_edges": [0, 0]}, {"surface_faces": []}):
            for function in (sw_multibody.extend_surface, sw_multibody.untrim_surface):
                with self.subTest(selection=selection, function=function.__name__), patch.object(sw_multibody, "require_part") as require:
                    with self.assertRaises(RuntimeError):
                        function({"selection": selection, "distance_mm": 1})
                    require.assert_not_called()

    def test_invalid_end_conditions_and_untrim_options_fail_before_com(self):
        cases = [(sw_multibody.extend_surface, {"distance_mm": float("nan")}),
                 (sw_multibody.extend_surface, {"end_condition": "up_to_point"}),
                 (sw_multibody.extend_surface, {"end_condition": "distance", "distance_mm": 1, "target_selection": {"faces": [0]}}),
                 (sw_multibody.untrim_surface, {"extend_percent": -1}),
                 (sw_multibody.untrim_surface, {"extend_percent": 20}),
                 (sw_multibody.untrim_surface, {"trim_opposite_side": True})]
        for function, options in cases:
            with self.subTest(options=options), patch.object(sw_multibody, "require_part") as require:
                with self.assertRaises(RuntimeError):
                    function({"selection": {"surface_faces": [0]}, **options})
                require.assert_not_called()

    def test_old_untrim_api_does_not_discard_trim_opposite_side(self):
        from unittest.mock import Mock
        old = Mock(return_value="feature")
        manager = SimpleNamespace(InsertUntrimSurface=old)
        with patch.object(sw_multibody, "require_part", return_value=(None, object())), \
             patch.object(sw_multibody, "exit_active_sketch"), patch.object(sw_multibody, "require_selection"), \
             patch.object(sw_multibody, "feature_manager", return_value=manager), \
             patch.object(sw_multibody, "_finish", return_value={"ok": True}):
            sw_multibody.untrim_surface({"selection": {"surface_faces": [0]}, "merge": False})
            old.assert_called_once_with(0, 2, 0.0, False)
            old.reset_mock()
            sw_multibody.untrim_surface({"selection": {"surface_edges": [0]}, "extend_percent": 20})
            old.assert_called_once_with(0, 2, 20.0, True)
            old.reset_mock()
            with self.assertRaises(RuntimeError):
                sw_multibody.untrim_surface({"selection": {"surface_faces": [0]}, "merge": False, "trim_opposite_side": True})
            old.assert_not_called()


if __name__ == "__main__":
    unittest.main()
