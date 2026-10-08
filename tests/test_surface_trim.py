# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
from solidworks_mcp import sw_multibody as surfaces


LEFT = {"index": 0, "surface_body_index": 0, "area_mm2": 40., "box_mm": [0, 0, 0, 4, 10, 0], "selection_point_mm": [2, 5, 0]}
RIGHT = {"index": 1, "surface_body_index": 0, "area_mm2": 60., "box_mm": [4, 0, 0, 10, 10, 0], "selection_point_mm": [7, 5, 0]}


class SurfaceTrimContracts(unittest.TestCase):
    def test_invalid_region_indices_and_points_fail_before_com(self):
        for regions in ([], [True], [-1], [0, 0], [1.5]):
            with patch.object(surfaces, "require_part") as part:
                with self.assertRaises(RuntimeError):
                    surfaces.trim_surface({"region_indices": regions})
                part.assert_not_called()
        for points in ([], [[1, 2]], [[float("nan"), 0, 0]]):
            with patch.object(surfaces, "require_part") as part:
                with self.assertRaises(RuntimeError):
                    surfaces.trim_surface({"region_indices": [0], "picked_points_mm": points})
                part.assert_not_called()

    def test_preselection_precedes_native_setup_and_regions_have_stable_indices(self):
        events = []
        fm = SimpleNamespace(PreTrimSurface=lambda *args: events.append(("pre", args)) or True,
                             GetPreTrimmedBodies=lambda body: ["right", "left"])
        with patch.object(surfaces, "get_bodies", return_value=["source"]), \
             patch.object(surfaces, "require_selection", side_effect=lambda *a: events.append("select") or 1), \
             patch.object(surfaces, "feature_manager", return_value=fm), \
             patch.object(surfaces, "_trim_body_info", side_effect=lambda body: RIGHT if body == "right" else LEFT):
            _, _, pieces = surfaces._prepare_surface_trim(None, {"surface_body_indices": [0], "trim_selection": {"planes": ["Cutter"]}, "linear_extension": True, "remove_picked": True, "split_system": False})
        self.assertEqual(events, ["select", ("pre", (False, False, True, True))])
        self.assertEqual([body for body, _ in pieces], ["left", "right"])
        self.assertEqual([info["index"] for _, info in pieces], [0, 1])

    def test_invalid_targets_and_mutual_contract_never_prepare_trim(self):
        cases = [{"surface_body_indices": [True]}, {"surface_body_indices": [2]},
                 {"surface_body_indices": [0], "mode": "mutual"},
                 {"surface_body_indices": [0, 1], "mode": "mutual", "trim_selection": {"planes": ["Cutter"]}},
                 {"surface_body_indices": [0], "trim_selection": {"surface_bodies": [0]}}]
        for args in cases:
            with patch.object(surfaces, "get_bodies", return_value=["a", "b"]), patch.object(surfaces, "feature_manager") as manager:
                with self.assertRaises(RuntimeError):
                    surfaces._prepare_surface_trim(None, args)
                manager.assert_not_called()

    def test_preview_clears_selection_even_if_native_generation_fails(self):
        with patch.object(surfaces, "require_part", return_value=(None, "doc")), patch.object(surfaces, "exit_active_sketch"), \
             patch.object(surfaces, "_prepare_surface_trim", side_effect=RuntimeError("native error")), \
             patch.object(surfaces, "clear_selection") as clear:
            with self.assertRaisesRegex(RuntimeError, "native error"):
                surfaces.preview_surface_trim({})
            clear.assert_called_once_with("doc")

    def test_native_selection_point_and_geometry_verification(self):
        for remove in (False, True):
            for wrong_side in (False, True):
                with self.subTest(remove=remove, wrong_side=wrong_side), ExitStack() as stack:
                    data = SimpleNamespace()
                    select = Mock(return_value=True)
                    face = SimpleNamespace(GetClosestPointOn=lambda *p: p)
                    body = SimpleNamespace(Select2=select, GetFaces=lambda: [face])
                    fm = SimpleNamespace(PostTrimSurface=Mock(return_value="feature"))
                    doc = SimpleNamespace(SelectionManager=SimpleNamespace(CreateSelectData=lambda: data))
                    expected = RIGHT if remove else LEFT
                    actual = {**expected, "box_mm": [100, 0, 0, 104, 10, 0]} if wrong_side else expected
                    replacements = {"require_part": (None, doc), "_prepare_surface_trim": (fm, ["source"], [(body, LEFT), (body, RIGHT)]),
                                    "get_bodies": ["remaining"], "_trim_body_info": actual,
                                    "_surface_trim_info": {"type": 0, "pieces_to_keep_count": 1, "trim_tool_count": 1}}
                    for name, returned in replacements.items():
                        stack.enter_context(patch.object(surfaces, name, return_value=returned))
                    stack.enter_context(patch.object(surfaces, "_finish", side_effect=lambda *a, **kw: {"ok": True, "data": {"feature": "Trim", **kw}}))
                    stack.enter_context(patch.object(surfaces, "exit_active_sketch"))
                    clear = stack.enter_context(patch.object(surfaces, "clear_selection"))
                    result = surfaces.trim_surface({"surface_body_indices": [0], "region_indices": [0], "remove_picked": remove})
                    self.assertEqual(result["ok"], not wrong_side)
                    self.assertEqual(result["data"]["feature"], "Trim")
                    self.assertEqual((data.X, data.Y, data.Z, data.Mark), (.002, .005, 0., 0))
                    select.assert_called_once_with(True, data)
                    clear.assert_called()

    def test_native_definition_access_is_released_when_read_fails(self):
        release = Mock()
        definition = SimpleNamespace(AccessSelections=lambda *a: True, GetType=lambda: (_ for _ in ()).throw(RuntimeError("native error")), ReleaseSelectionAccess=lambda: release())
        feature = SimpleNamespace(GetTypeName2=lambda: "TrimRefSurface", GetDefinition=lambda: definition)
        with self.assertRaisesRegex(RuntimeError, "native error"):
            surfaces._surface_trim_info(None, feature)
        release.assert_called_once()

    def test_unrequested_knitting_cannot_be_reported_as_success(self):
        for knit in (False, True):
            with self.subTest(knit=knit), ExitStack() as stack:
                data = SimpleNamespace()
                face = SimpleNamespace(GetClosestPointOn=lambda *p: p)
                body = SimpleNamespace(Select2=lambda *a: True, GetFaces=lambda: [face])
                fm = SimpleNamespace(PostTrimSurface=lambda *a: "feature")
                doc = SimpleNamespace(SelectionManager=SimpleNamespace(CreateSelectData=lambda: data))
                right = {**RIGHT, "surface_body_index": 1}
                merged = {"area_mm2": 100., "box_mm": [0, 0, 0, 10, 10, 0]}
                values = {"require_part": (None, doc), "_prepare_surface_trim": (fm, ["a", "b"], [(body, LEFT), (body, right)]),
                          "get_bodies": ["merged"], "_trim_body_info": merged,
                          "_surface_trim_info": {"type": 1, "pieces_to_keep_count": 2, "trim_tool_count": 2}}
                for name, returned in values.items():
                    stack.enter_context(patch.object(surfaces, name, return_value=returned))
                stack.enter_context(patch.object(surfaces, "_finish", side_effect=lambda *a, **kw: {"ok": True, "data": {"feature": "Trim", **kw}}))
                stack.enter_context(patch.object(surfaces, "exit_active_sketch"))
                stack.enter_context(patch.object(surfaces, "clear_selection"))
                payload = surfaces.trim_surface({"surface_body_indices": [0, 1], "region_indices": [0, 1], "mode": "mutual", "knit": knit})
                self.assertEqual(payload["ok"], knit)
                self.assertEqual(payload["data"]["feature"], "Trim")
                if not knit:
                    self.assertIn("joined distinct", payload["message"])
