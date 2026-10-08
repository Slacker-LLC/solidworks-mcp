# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
from solidworks_mcp import sw_wrap as wrap

ARGS = {"sketch_name": "Profile", "face_index": 0}
FACE = SimpleNamespace(GetSurface=lambda: SimpleNamespace(Identity=lambda: 4001, PlaneParams=[0, 0, 1, 0, 0, 0]),
                       GetArea=lambda: .0001, GetBox=lambda: [0, 0, 0, .01, .01, 0])


class WrapContracts(unittest.TestCase):
    def test_invalid_arguments_never_touch_com(self):
        for change in ({"mode": "other"}, {"method": "other"}, {"thickness_mm": float("nan")},
                       {"thickness_mm": .001}, {"mesh_factor": True}, {"mesh_factor": 11},
                       {"face_index": -1}, {"sketch_name": " "}, {"face_indices": [0, 0]},
                       {"reverse_direction": 1}, {"pull_selection": {"axes": ["Axis"]}},
                       {"pull_selection": {"planes": ["front", "right"]}},
                       {"mode": "scribe", "pull_selection": {"planes": ["right"]}}):
            with patch.object(wrap, "require_part") as part:
                with self.assertRaises(RuntimeError): wrap.wrap_sketch({**ARGS, **change})
                part.assert_not_called()

    def setup_calls(self, stack, manager, delta=2, faces=8):
        info = {"mode": "emboss", "source_sketch": "Profile", "has_target_face": True,
                "reverse_direction": False, "has_pull_direction": False, "thickness_mm": 1,
                "solid_volume_mm3": 100 + delta, "solid_face_count": faces}
        for name, returned in {"require_part": (None, "doc"), "feature_manager": manager,
                               "_feature_names": [], "_feature_created_after": "recovered", "_wrap_info": info,
                               "_geometry": {"solid_volume_mm3": 100, "solid_face_count": 3}}.items():
            stack.enter_context(patch.object(wrap, name, return_value=returned))
        for name in ("exit_active_sketch", "clear_selection"):
            stack.enter_context(patch.object(wrap, name))
        stack.enter_context(patch.object(wrap, "_finish", side_effect=lambda *a: {"ok": True, "data": {"feature": a[1]}}))
        return stack.enter_context(patch.object(wrap, "require_selection", return_value=1))

    def test_marks_units_and_void_feature_recovery(self):
        create = Mock(return_value=None)
        with ExitStack() as stack:
            select = self.setup_calls(stack, SimpleNamespace(InsertWrapFeature2=create))
            payload = wrap.wrap_sketch(ARGS)
        create.assert_called_once_with(0, .001, False, 0, 5)
        self.assertEqual([c.kwargs["mark"] for c in select.call_args_list], [4, 1])
        self.assertTrue(select.call_args_list[1].kwargs["append"])
        self.assertEqual(payload["data"]["feature"], "recovered")
        self.assertTrue(payload["ok"])

    def test_legacy_rejects_spline_before_mutation(self):
        create = Mock()
        with ExitStack() as stack:
            select = self.setup_calls(stack, SimpleNamespace(InsertWrapFeature=create))
            with self.assertRaisesRegex(RuntimeError, "older method"):
                wrap.wrap_sketch({**ARGS, "method": "spline"})
            select.assert_not_called(); create.assert_not_called()
            wrap.wrap_sketch(ARGS)
        create.assert_called_once_with(0, .001, False)

    def test_multiple_face_marks_and_reverse_forwarding(self):
        create = Mock(return_value="multi")
        with ExitStack() as stack:
            select = self.setup_calls(stack, SimpleNamespace(InsertWrapFeature2=create))
            payload = wrap.wrap_sketch({"sketch_name": "Profile", "face_indices": [2, 4], "method": "spline", "reverse_direction": True})
        create.assert_called_once_with(0, .001, True, 1, 5)
        self.assertEqual(select.call_args_list[1].args[1], {"faces": [2, 4]})
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["data"]["reverse_geometry_confirmed"])

    def test_legacy_does_not_silently_drop_multiple_targets(self):
        create = Mock()
        with ExitStack() as stack:
            select = self.setup_calls(stack, SimpleNamespace(InsertWrapFeature=create))
            with self.assertRaisesRegex(RuntimeError, "multiple-face"):
                wrap.wrap_sketch({"sketch_name": "Profile", "face_indices": [0, 1]})
            create.assert_not_called(); select.assert_not_called()

    def test_curved_pull_edge_is_rejected_before_creation(self):
        curved = SimpleNamespace(GetCurve=lambda: SimpleNamespace(IsLine=lambda: False))
        doc = SimpleNamespace(SelectionManager=SimpleNamespace(GetSelectedObject6=lambda *a: curved))
        with patch.object(wrap, "require_selection", return_value=1):
            with self.assertRaisesRegex(RuntimeError, "linear"):
                wrap._selected_pull(doc, {"edges": [0]})

    def test_modern_errors_are_not_retried(self):
        legacy = Mock()
        with ExitStack() as stack:
            self.setup_calls(stack, SimpleNamespace(InsertWrapFeature2=Mock(side_effect=RuntimeError("native failure")), InsertWrapFeature=legacy))
            with self.assertRaisesRegex(RuntimeError, "native failure"): wrap.wrap_sketch(ARGS)
        legacy.assert_not_called()

    def test_non_effective_feature_is_preserved_as_failure(self):
        with ExitStack() as stack:
            self.setup_calls(stack, SimpleNamespace(InsertWrapFeature2=lambda *a: "feature"), delta=0)
            payload = wrap.wrap_sketch(ARGS)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["data"]["feature"], "feature")

    def test_geometry_read_after_releasing_rollback(self):
        events = []
        definition = SimpleNamespace(AccessSelections=lambda *a: True, ReleaseSelectionAccess=lambda: events.append("release"),
                                     SourceSketch=SimpleNamespace(Name="Profile"), Type=2, Thickness=.001,
                                     ReverseDirection=False, Face=FACE, PullDirection=None)
        feature = SimpleNamespace(GetTypeName2=lambda: "Emboss", GetDefinition=lambda: definition, GetFaces=lambda: [])
        with patch.object(wrap, "_geometry", side_effect=lambda d: events.append("geometry") or {}):
            info = wrap._wrap_info("doc", feature)
        self.assertEqual(events, ["release", "geometry"])
        self.assertEqual(info["mode"], "scribe")
        self.assertIn("method", info["unreadable_parameters"])

    def test_pull_identity_uses_nonempty_native_persistent_ids(self):
        actual, expected = object(), object()
        definition = SimpleNamespace(AccessSelections=lambda *a: True, ReleaseSelectionAccess=lambda: None,
                                     SourceSketch=SimpleNamespace(Name="Profile"), Type=0, Thickness=.001,
                                     ReverseDirection=False, Face=FACE, PullDirection=actual)
        feature = SimpleNamespace(GetTypeName2=lambda: "Emboss", GetDefinition=lambda: definition, GetFaces=lambda: [])
        for refs, confirmed in (([(1, 2), (1, 2)], True), ([(1, 2), (2, 1)], False), ([()], False)):
            with patch.object(wrap, "_reference_id", side_effect=refs), patch.object(wrap, "_geometry", return_value={}):
                self.assertEqual(wrap._wrap_info("doc", feature, expected_pull=expected)["pull_reference_confirmed"], confirmed)

    def test_edit_invalid_requests_fail_before_com(self):
        for args in ({"name": "Wrap"}, {"name": "Wrap", "thickness_mm": 0}, {"name": "Wrap", "mode": "bad"},
                     {"name": "Wrap", "reverse_direction": 1}, {"name": "Wrap", "clear_pull_direction": False},
                     {"name": "Wrap", "pull_selection": {"planes": ["right"]}, "clear_pull_direction": True},
                     {"name": "Wrap", "source_sketch_name": " "}, {"name": "Wrap", "target_face_index": True},
                     {"name": "Wrap", "target_face_index": -1}):
            with patch.object(wrap, "require_part") as part:
                with self.assertRaises(RuntimeError): wrap.set_wrap_parameters(args)
                part.assert_not_called()

    def test_edit_converts_units_and_checks_rolled_back_geometry(self):
        events = []
        definition = SimpleNamespace(AccessSelections=lambda *a: events.append("access") or True,
                                     ReleaseSelectionAccess=lambda: events.append("release"))
        feature = SimpleNamespace(GetDefinition=lambda: definition,
                                  ModifyDefinition=lambda *a: events.append("modify") or True)
        info = {"mode": "emboss", "thickness_mm": 2, "reverse_direction": False, "has_pull_direction": False,
                "solid_volume_mm3": 188, "solid_face_count": 8}
        with ExitStack() as stack:
            for name, returned in {"require_part": (None, "doc"), "find_feature": feature, "_wrap_info": info,
                                   "whats_wrong": []}.items():
                stack.enter_context(patch.object(wrap, name, return_value=returned))
            for name in ("clear_selection", "exit_active_sketch", "rebuild"):
                stack.enter_context(patch.object(wrap, name))
            stack.enter_context(patch.object(wrap, "_geometry", side_effect=lambda *a: events.append("baseline") or {"solid_volume_mm3": 100, "solid_face_count": 3}))
            payload = wrap.set_wrap_parameters({"name": "Wrap", "thickness_mm": 2})
        self.assertTrue(payload["ok"])
        self.assertEqual(definition.Thickness, .002)
        self.assertEqual(events, ["access", "baseline", "modify"])
        self.assertEqual(payload["data"]["volume_change_from_input_mm3"], 88)

    def test_edit_exception_releases_selection_access(self):
        released = Mock()
        definition = SimpleNamespace(AccessSelections=lambda *a: True, ReleaseSelectionAccess=lambda: released())
        feature = SimpleNamespace(GetDefinition=lambda: definition, ModifyDefinition=Mock(side_effect=RuntimeError("native failure")))
        with ExitStack() as stack:
            for name, returned in {"require_part": (None, "doc"), "find_feature": feature, "_wrap_info": {"mode": "emboss"}, "_geometry": {}}.items():
                stack.enter_context(patch.object(wrap, name, return_value=returned))
            for name in ("clear_selection", "exit_active_sketch"):
                stack.enter_context(patch.object(wrap, name))
            with self.assertRaisesRegex(RuntimeError, "native failure"):
                wrap.set_wrap_parameters({"name": "Wrap", "thickness_mm": 2})
        released.assert_called_once()

    def test_persistent_reference_freezes_native_memoryview(self):
        buffer = bytearray([1, 2, 3])
        doc = SimpleNamespace(Extension=SimpleNamespace(GetPersistReference3=lambda obj: memoryview(buffer)))
        snapshot = wrap._reference_id(doc, object())
        buffer[0] = 9
        self.assertEqual(snapshot, b"\x01\x02\x03")

    def test_source_edit_passes_sketch_object_and_verifies_frozen_reference(self):
        definition = SimpleNamespace(AccessSelections=lambda *a: True, ReleaseSelectionAccess=lambda: None)
        feature = SimpleNamespace(GetDefinition=lambda: definition, ModifyDefinition=lambda *a: True)
        sketch = object()
        source_feature = SimpleNamespace(GetTypeName2=lambda: "ProfileFeature", GetSpecificFeature2=lambda: sketch)
        info = {"mode": "emboss", "source_sketch": "Wide", "source_reference_confirmed": True,
                "reverse_direction": False, "solid_volume_mm3": 184, "solid_face_count": 8}
        with ExitStack() as stack:
            stack.enter_context(patch.object(wrap, "find_feature", side_effect=[feature, source_feature]))
            for name, returned in {"require_part": (None, "doc"), "_wrap_info": info, "_reference_id": b"source",
                                   "_geometry": {"solid_volume_mm3": 100, "solid_face_count": 3}, "whats_wrong": []}.items():
                stack.enter_context(patch.object(wrap, name, return_value=returned))
            for name in ("clear_selection", "exit_active_sketch", "rebuild"):
                stack.enter_context(patch.object(wrap, name))
            payload = wrap.set_wrap_parameters({"name": "Wrap", "source_sketch_name": "Wide"})
        self.assertIs(definition.SourceSketch, sketch)
        self.assertTrue(payload["ok"])

    def test_ignored_target_edit_cannot_report_success(self):
        definition = SimpleNamespace(AccessSelections=lambda *a: True, ReleaseSelectionAccess=lambda: None)
        feature = SimpleNamespace(GetDefinition=lambda: definition, ModifyDefinition=lambda *a: True)
        target = object()
        doc = SimpleNamespace(SelectionManager=SimpleNamespace(GetSelectedObject6=lambda *a: target))
        info = {"mode": "emboss", "reverse_direction": False, "target_reference_confirmed": False,
                "solid_volume_mm3": 142, "solid_face_count": 8}
        with ExitStack() as stack:
            for name, returned in {"require_part": (None, doc), "find_feature": feature, "_wrap_info": info,
                                   "_reference_id": b"target", "require_selection": 1, "whats_wrong": [],
                                   "_geometry": {"solid_volume_mm3": 100, "solid_face_count": 3}}.items():
                stack.enter_context(patch.object(wrap, name, return_value=returned))
            for name in ("clear_selection", "exit_active_sketch", "rebuild"):
                stack.enter_context(patch.object(wrap, name))
            payload = wrap.set_wrap_parameters({"name": "Wrap", "target_face_index": 1})
        self.assertIs(definition.Face, target)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["data"]["parameters_confirmed"])


if __name__ == "__main__":
    unittest.main()
