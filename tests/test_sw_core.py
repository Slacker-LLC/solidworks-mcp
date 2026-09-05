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

"""Offline tests for sw_core.

These run without SOLIDWORKS: they cover the parts that are plain Python --
the method-flagging control flow, unit conversion, and the assembly transform
maths.  Anything that needs a live COM session belongs in live_p0_regression.py.

    ..\\.venv\\Scripts\\python.exe -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from solidworks_mcp import sw_core


class FakeDispatch:
    """Stands in for pywin32's late-bound CDispatch.

    The real ``_FlagAsMethod`` loops over its arguments and resolves each one
    through ``GetIDsOfNames``, so an unknown name raises and abandons every
    name after it.  That loop-and-raise behaviour is what these tests pin down,
    so it is reproduced here exactly rather than approximated.
    """

    def __init__(self, known: set[str]) -> None:
        self.known = set(known)
        self.flagged: list[str] = []

    def _FlagAsMethod(self, *names: str) -> None:
        for name in names:
            if name not in self.known:
                raise AttributeError(f"{name} is not a member of this interface")
            self.flagged.append(name)


class FlagMethodsTests(unittest.TestCase):
    def setUp(self) -> None:
        # The warn-once set is module state; keep tests independent of order.
        self._saved = set(sw_core._UNFLAGGABLE_NAMES)
        sw_core._UNFLAGGABLE_NAMES.clear()

    def tearDown(self) -> None:
        sw_core._UNFLAGGABLE_NAMES.clear()
        sw_core._UNFLAGGABLE_NAMES.update(self._saved)

    def test_all_known_names_are_flagged(self) -> None:
        obj = FakeDispatch({"CreateLine", "CreateArc", "CreateSpline"})
        sw_core.flag_methods(obj, "CreateLine", "CreateArc", "CreateSpline")
        self.assertEqual(obj.flagged, ["CreateLine", "CreateArc", "CreateSpline"])

    def test_a_missing_name_does_not_strand_the_names_after_it(self) -> None:
        """The regression this change exists for.

        Passing the whole list to _FlagAsMethod meant one member absent from
        this SOLIDWORKS release left every later member unflagged, and an
        unflagged argument-taking member is what silently returns None or takes
        SOLIDWORKS down.
        """
        obj = FakeDispatch({"Save3", "GetBodies2", "GetComponents"})
        sw_core.flag_methods(obj, "Save3", "RenamedAwayInThisRelease", "GetBodies2", "GetComponents")
        self.assertEqual(obj.flagged, ["Save3", "GetBodies2", "GetComponents"])

    def test_a_missing_name_is_not_raised_to_the_caller(self) -> None:
        obj = FakeDispatch(set())
        self.assertIs(sw_core.flag_methods(obj, "Nope", "AlsoNope"), obj)
        self.assertEqual(obj.flagged, [])

    def test_an_unexpected_absence_is_reported_once_and_by_name(self) -> None:
        obj = FakeDispatch({"Save3"})
        with self.assertLogs(sw_core.logger, level="WARNING") as captured:
            sw_core.flag_methods(obj, "Save3", "RenamedAwayInThisRelease")
            sw_core.flag_methods(obj, "Save3", "RenamedAwayInThisRelease")
        self.assertEqual(len(captured.records), 1)
        self.assertIn("RenamedAwayInThisRelease", captured.output[0])

    def test_an_expected_absence_stays_out_of_the_warning_log(self) -> None:
        """Members only some document types carry must not look like defects.

        Every part document is offered the assembly members, so warning on them
        would bury the one warning that does mean something.
        """
        obj = FakeDispatch({"Select2"})
        with self.assertNoLogs(sw_core.logger, level="WARNING"):
            sw_core.flag_methods(obj, "Select2", "Select4")
        self.assertEqual(obj.flagged, ["Select2"])

    def test_expected_absences_are_drawn_from_the_real_flag_lists(self) -> None:
        """Stops the quiet set drifting away from the lists it is meant to cover."""
        listed = (
            set(sw_core._SKETCH_MANAGER_METHODS)
            | set(sw_core._FEATURE_MANAGER_METHODS)
            | set(sw_core._EXTENSION_METHODS)
            | set(sw_core._MODEL_DOC_METHODS)
            | set(sw_core._APP_METHODS)
            | {"Select2", "Select4"}  # selectable()
        )
        self.assertLessEqual(set(sw_core._DOCUMENT_SPECIFIC_METHODS), listed)

    def test_flagging_is_retried_on_later_objects(self) -> None:
        """Select4 is absent on some interfaces and present on others.

        selectable() is handed faces, edges, sketch segments and components
        alike, so a name that failed once must still be attempted next time.
        """
        without = FakeDispatch({"Select2"})
        sw_core.flag_methods(without, "Select2", "Select4")
        with_it = FakeDispatch({"Select2", "Select4"})
        sw_core.flag_methods(with_it, "Select2", "Select4")
        self.assertEqual(with_it.flagged, ["Select2", "Select4"])

    def test_the_flag_lists_are_non_empty_and_free_of_duplicates(self) -> None:
        lists = {
            "_SKETCH_MANAGER_METHODS": sw_core._SKETCH_MANAGER_METHODS,
            "_FEATURE_MANAGER_METHODS": sw_core._FEATURE_MANAGER_METHODS,
            "_EXTENSION_METHODS": sw_core._EXTENSION_METHODS,
            "_MODEL_DOC_METHODS": sw_core._MODEL_DOC_METHODS,
            "_APP_METHODS": sw_core._APP_METHODS,
        }
        for label, names in lists.items():
            with self.subTest(list=label):
                self.assertTrue(names)
                self.assertEqual(len(names), len(set(names)), f"{label} repeats a name")


class FakeVersionedDispatch:
    """A dispatch whose members resolve the way pywin32's dynamic lookup does.

    Attribute access on a name the build does not expose raises AttributeError
    from ``__getattr__``, exactly as ``CDispatch`` does when ``GetIDsOfNames``
    fails; a known name returns a callable that records the call.
    """

    def __init__(self, known: set[str]) -> None:
        self.known = set(known)
        self.attempted: list[str] = []
        self.calls: list[tuple[str, tuple]] = []

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        self.attempted.append(name)
        if name not in self.known:
            raise AttributeError(f"<unknown>.{name}")

        def method(*args):
            self.calls.append((name, args))
            return f"{name} feature"

        return method


class CallVersionedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = set(sw_core._UNFLAGGABLE_NAMES)
        sw_core._UNFLAGGABLE_NAMES.clear()

    def tearDown(self) -> None:
        sw_core._UNFLAGGABLE_NAMES.clear()
        sw_core._UNFLAGGABLE_NAMES.update(self._saved)

    def test_the_newest_name_wins_when_the_build_has_it(self) -> None:
        obj = FakeVersionedDispatch({"FeatureCut4", "FeatureCut3"})
        outcome = sw_core.call_versioned(obj, ("FeatureCut4", (1, 2, 3)), ("FeatureCut3", (1, 2)))
        self.assertEqual(outcome, "FeatureCut4 feature")
        self.assertEqual(obj.calls, [("FeatureCut4", (1, 2, 3))])

    def test_an_older_build_gets_the_older_name_with_its_own_arguments(self) -> None:
        """The case this helper exists for: SOLIDWORKS 2016 has no FeatureCut4."""
        obj = FakeVersionedDispatch({"FeatureCut3"})
        outcome = sw_core.call_versioned(obj, ("FeatureCut4", (1, 2, 3)), ("FeatureCut3", (1, 2)))
        self.assertEqual(outcome, "FeatureCut3 feature")
        self.assertEqual(obj.calls, [("FeatureCut3", (1, 2))])
        self.assertIn("FeatureCut4", sw_core._UNFLAGGABLE_NAMES)

    def test_a_name_known_to_be_absent_is_not_looked_up_again(self) -> None:
        """Each lookup is a cross-process GetIDsOfNames, so remember the misses."""
        sw_core._UNFLAGGABLE_NAMES.add("FeatureCut4")
        obj = FakeVersionedDispatch({"FeatureCut3"})
        sw_core.call_versioned(obj, ("FeatureCut4", ()), ("FeatureCut3", ()))
        self.assertEqual(obj.attempted, ["FeatureCut3"])

    def test_no_candidate_at_all_is_a_clear_error_naming_every_candidate(self) -> None:
        obj = FakeVersionedDispatch(set())
        with self.assertRaises(RuntimeError) as raised:
            sw_core.call_versioned(obj, ("FeatureCut4", ()), ("FeatureCut3", ()))
        self.assertIn("FeatureCut4", str(raised.exception))
        self.assertIn("FeatureCut3", str(raised.exception))

    def test_every_fallback_name_is_flagged_as_a_method(self) -> None:
        """An unflagged argument-taking member is what silently returns None or
        takes SOLIDWORKS down, so the older variants must be in the flag lists
        like the current ones."""
        from solidworks_mcp import sw_drawing

        fallbacks = {
            "FeatureCut3", "InsertProtrusionSwept3", "InsertCutSwept4",
            "FeatureLinearPattern4", "FeatureCircularPattern4",
        }
        self.assertLessEqual(fallbacks, set(sw_core._FEATURE_MANAGER_METHODS))
        self.assertIn("CreateDetailViewAt3", sw_drawing._DRAWING_METHODS)


class UnitTests(unittest.TestCase):
    def test_millimetres_and_metres_round_trip(self) -> None:
        self.assertEqual(sw_core.to_m(1000), 1.0)
        self.assertEqual(sw_core.to_mm(1.0), 1000.0)
        self.assertAlmostEqual(sw_core.to_mm(sw_core.to_m(37.5)), 37.5, places=9)

    def test_degrees_and_radians_round_trip(self) -> None:
        self.assertAlmostEqual(sw_core.to_deg(sw_core.to_rad(45.0)), 45.0, places=9)

    def test_mm_point_converts_every_component(self) -> None:
        self.assertEqual(sw_core.mm_point([0.001, 0.002, -0.003]), [1.0, 2.0, -3.0])

    def test_as_list_normalises_the_shapes_solidworks_returns(self) -> None:
        self.assertEqual(sw_core.as_list(None), [])
        self.assertEqual(sw_core.as_list([]), [])
        self.assertEqual(sw_core.as_list("single"), ["single"])
        self.assertEqual(sw_core.as_list(("a", None, "b")), ["a", "b"])


class TransformTests(unittest.TestCase):
    IDENTITY = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1]

    def test_identity_transform_leaves_a_point_alone(self) -> None:
        self.assertEqual(sw_core.apply_transform([1.0, 2.0, 3.0], self.IDENTITY), [1.0, 2.0, 3.0])

    def test_no_matrix_means_no_transform(self) -> None:
        self.assertEqual(sw_core.apply_transform([1.0, 2.0, 3.0], None), [1.0, 2.0, 3.0])

    def test_translation_lives_in_elements_nine_to_eleven(self) -> None:
        matrix = list(self.IDENTITY)
        matrix[9:12] = [10.0, 20.0, 30.0]
        self.assertEqual(sw_core.apply_transform([1.0, 2.0, 3.0], matrix), [11.0, 22.0, 33.0])

    def test_scale_lives_in_element_twelve_and_misses_the_translation(self) -> None:
        matrix = list(self.IDENTITY)
        matrix[9:12] = [1.0, 0.0, 0.0]
        matrix[12] = 2.0
        self.assertEqual(sw_core.apply_transform([1.0, 1.0, 1.0], matrix), [3.0, 2.0, 2.0])

    def test_rotation_is_column_major(self) -> None:
        # 90 degrees about z: x -> y, y -> -x.  Stored column-major, so the
        # first column (elements 0,1,2) is where x lands.
        matrix = [0, 1, 0, -1, 0, 0, 0, 0, 1, 0, 0, 0, 1]
        rotated = sw_core.apply_transform([1.0, 0.0, 0.0], matrix)
        self.assertEqual([round(v, 9) for v in rotated], [0.0, 1.0, 0.0])

    def test_rotate_vector_ignores_translation(self) -> None:
        matrix = list(self.IDENTITY)
        matrix[9:12] = [100.0, 200.0, 300.0]
        self.assertEqual(sw_core.rotate_vector([0.0, 0.0, 1.0], matrix), [0.0, 0.0, 1.0])


class FakeBody:
    def __init__(self, faces: list[object], edges: list[object]) -> None:
        self._faces = faces
        self._edges = edges

    def GetFaces(self) -> list[object]:
        return self._faces

    def GetEdges(self) -> list[object]:
        return self._edges


class TopologyIndexOrderTests(unittest.TestCase):
    """The light-weight walks must agree with the measured ones, index for index.

    Callers pass indices taken from list_faces / list_edges, which come from
    enumerate_faces / enumerate_edges.  If the walk used for *selection* ordered
    bodies or their faces differently, a fillet would land on the wrong face and
    nothing in the result would say so.
    """

    def bodies(self) -> list[tuple[object, str, None]]:
        first = FakeBody(faces=["f0", "f1", "f2"], edges=["e0", "e1"])
        second = FakeBody(faces=["f3"], edges=["e2", "e3", "e4"])
        return [(first, "Body1", None), (second, "Body2", None)]

    def test_face_order_matches_the_measured_enumeration(self) -> None:
        with unittest.mock.patch.object(sw_core, "iter_body_context", return_value=self.bodies()):
            measured = [entry["_obj"] for entry in sw_core.enumerate_faces(None)]
            light = [obj for obj, _ in sw_core.iter_face_objects(None)]
        self.assertEqual(measured, ["f0", "f1", "f2", "f3"])
        self.assertEqual(light, measured)

    def test_edge_order_matches_the_measured_enumeration(self) -> None:
        with unittest.mock.patch.object(sw_core, "iter_body_context", return_value=self.bodies()):
            measured = [entry["_obj"] for entry in sw_core.enumerate_edges(None)]
            light = [obj for obj, _ in sw_core.iter_edge_objects(None)]
        self.assertEqual(measured, ["e0", "e1", "e2", "e3", "e4"])
        self.assertEqual(light, measured)

    def test_the_component_transform_travels_with_each_face(self) -> None:
        matrix = [1, 0, 0, 0, 1, 0, 0, 0, 1, 5, 0, 0, 1]
        contexts = [
            (FakeBody(["f0"], []), "Part1-1", None),
            (FakeBody(["f1"], []), "Part2-1", matrix),
        ]
        with unittest.mock.patch.object(sw_core, "iter_body_context", return_value=contexts):
            walked = sw_core.iter_face_objects(None)
        self.assertEqual([m for _, m in walked], [None, matrix])


class SelectionSpecTests(unittest.TestCase):
    def test_split_selection_yields_one_entity_per_sub_spec(self) -> None:
        singles = sw_core.split_selection({"faces": [1, 2], "planes": ["Front"]})
        self.assertEqual(singles, [{"faces": [1]}, {"faces": [2]}, {"planes": ["Front"]}])

    def test_split_selection_carries_the_sketch_name_into_each_sub_spec(self) -> None:
        singles = sw_core.split_selection({"sketch_segments": [0, 1], "sketch_name": "Sketch1"})
        self.assertTrue(all(sub["sketch_name"] == "Sketch1" for sub in singles))
        self.assertEqual(len(singles), 2)

    def test_split_selection_of_nothing_is_empty(self) -> None:
        self.assertEqual(sw_core.split_selection(None), [])
        self.assertEqual(sw_core.split_selection({}), [])
        self.assertEqual(sw_core.split_selection({"faces": []}), [])


if __name__ == "__main__":
    unittest.main()
