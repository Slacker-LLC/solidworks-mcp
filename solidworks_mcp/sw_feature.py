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

"""Solid features: extrude, revolve, fillet, chamfer, shell, sweep, loft, rib,
draft, holes, patterns, and mirror.

Every feature that needs topology takes the same declarative ``selection``
object, so the agent never has to reason about SOLIDWORKS' implicit global
selection set or about which mark a given API expects.
"""

from __future__ import annotations

import math
from typing import Any

from .sw_core import (
    active_document,
    apply_selection,
    call_versioned,
    CHAMFER_ANGLE_DISTANCE,
    CHAMFER_DISTANCE_DISTANCE,
    CHAMFER_EQUAL_DISTANCE,
    CHAMFER_TANGENT_PROPAGATION,
    clear_selection,
    empty_variant,
    END_CONDITIONS,
    exit_active_sketch,
    extension,
    feature_manager,
    feature_property,
    feature_result,
    FILLET_PROPAGATE,
    FILLET_TYPE_SIMPLE,
    FILLET_UNIFORM_RADIUS,
    find_feature,
    iter_features,
    iter_feature_objects,
    logger,
    rebuild,
    rename_feature,
    require_part,
    require_selection,
    result,
    selectable,
    SELECTION_SCHEMA,
    select_sketch_for_feature,
    tool,
    to_m,
    to_rad,
    value,
    whats_wrong,
)


def _feature_names(doc: Any) -> list[str]:
    return [f["name"] for f in iter_features(doc)]


def _feature_created_after(doc: Any, before: list[str]) -> Any | None:
    """Recover the new feature for the SOLIDWORKS APIs that return void."""
    previous = set(before)
    for feature in reversed(iter_feature_objects(doc)):
        name = str(feature_property(feature, "Name", ""))
        if name and name not in previous:
            return feature
    return None


_END_CONDITION_SCHEMA = {
    "type": "string",
    "enum": sorted(END_CONDITIONS),
    "default": "blind",
    "description": "End condition. blind uses depth_mm; through_all/through_next ignore it.",
}

_SKETCH_ARG = {
    "type": "string",
    "description": "Which sketch to use as the profile. Defaults to the most recently created sketch.",
}


def _two_direction_end_conditions(condition: int) -> tuple[bool, int, int]:
    """Translate the public ``through_all_both`` end condition for feature APIs.

    ``FeatureExtrusion3`` and ``FeatureCut4`` do not honour
    ``swEndCondThroughAllBoth`` when it is supplied as the first end condition
    of a single-ended feature.  They create only the forward half of the
    feature, without a rebuild error.  The API represents the intended result
    as a two-direction feature with ``through all`` on both sides instead.
    """
    if condition == END_CONDITIONS["through_all_both"]:
        through_all = END_CONDITIONS["through_all"]
        return False, through_all, through_all
    return True, condition, 0


# --------------------------------------------------------------------------
# Extrude and cut
# --------------------------------------------------------------------------


@tool(
    "boss_extrude",
    "Extrude a sketch into a solid boss. Depth is millimetres, draft is degrees.",
    {
        "depth_mm": {"type": "number", "exclusiveMinimum": 0, "default": 10},
        "sketch_name": _SKETCH_ARG,
        "end_condition": _END_CONDITION_SCHEMA,
        "reverse": {"type": "boolean", "default": False, "description": "Extrude the other way."},
        "draft_deg": {"type": "number", "default": 0},
        "draft_outward": {"type": "boolean", "default": False},
        "merge": {"type": "boolean", "default": True, "description": "Merge with existing solid bodies."},
        "name": {"type": "string", "description": "Rename the resulting feature."},
    },
)
def boss_extrude(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_part()
    sketch = select_sketch_for_feature(doc, args.get("sketch_name"))
    condition = END_CONDITIONS[str(args.get("end_condition", "blind"))]
    single_direction, condition_1, condition_2 = _two_direction_end_conditions(condition)
    depth = to_m(args.get("depth_mm", 10))
    draft = to_rad(args.get("draft_deg", 0))
    has_draft = abs(draft) > 1e-12

    feature = feature_manager(doc).FeatureExtrusion3(
        single_direction, False, bool(args.get("reverse", False)),
        condition_1, condition_2, depth, depth,
        has_draft, False, bool(args.get("draft_outward", False)), False,
        draft, 0.0,
        False, False, False, False,
        bool(args.get("merge", True)), True, True,
        0, 0.0, False,
    )
    rename_feature(feature, args.get("name"))
    return feature_result(doc, feature, "boss extrusion", sketch=sketch, depth_mm=args.get("depth_mm", 10))


@tool(
    "cut_extrude",
    "Cut material using a sketch profile. Depth is millimetres, draft is degrees.",
    {
        "depth_mm": {"type": "number", "exclusiveMinimum": 0, "default": 10},
        "sketch_name": _SKETCH_ARG,
        "end_condition": _END_CONDITION_SCHEMA,
        "reverse": {"type": "boolean", "default": False},
        "flip_side": {"type": "boolean", "default": False, "description": "Cut everything outside the profile instead."},
        "draft_deg": {"type": "number", "default": 0},
        "name": {"type": "string"},
    },
)
def cut_extrude(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_part()
    condition = END_CONDITIONS[str(args.get("end_condition", "blind"))]
    single_direction, condition_1, condition_2 = _two_direction_end_conditions(condition)
    depth = to_m(args.get("depth_mm", 10))
    draft = to_rad(args.get("draft_deg", 0))
    has_draft = abs(draft) > 1e-12
    requested = bool(args.get("reverse", False))

    def attempt(reverse: bool) -> tuple[str, Any]:
        name = select_sketch_for_feature(doc, args.get("sketch_name"))
        cut_args = (
            single_direction, bool(args.get("flip_side", False)), reverse,
            condition_1, condition_2, depth, depth,
            has_draft, False, False, False,
            draft, 0.0,
            False, False, False, False,
            False, True, True,
            False, False, False,
            0, 0.0, False, False,
        )
        # FeatureCut3 (2014-2016 builds) takes the same list without the
        # trailing OptimizeGeometry flag.
        return name, call_versioned(
            feature_manager(doc),
            ("FeatureCut4", cut_args),
            ("FeatureCut3", cut_args[:26]),
        )

    sketch, feature = attempt(requested)
    reversed_automatically = False
    if feature is None:
        # Which way "forward" points depends on the sketch plane's orientation,
        # and a sketch lying on a boundary face of the body cuts into thin air.
        # A cut that removes nothing is unambiguous, so try the other way.
        sketch, feature = attempt(not requested)
        reversed_automatically = feature is not None

    rename_feature(feature, args.get("name"))
    payload = feature_result(doc, feature, "cut", sketch=sketch, depth_mm=args.get("depth_mm", 10))
    if reversed_automatically:
        payload["data"]["reversed_automatically"] = True
        payload["message"] += " The requested direction removed nothing, so it was cut the other way."
    elif feature is None:
        payload["message"] += (
            " Neither direction removed material: check that the profile actually overlaps the body."
        )
    return payload


@tool(
    "revolve",
    "Revolve a sketch profile about an axis. If the sketch contains exactly one centerline "
    "SOLIDWORKS uses it automatically; otherwise pass axis_selection (a sketch_segments index for "
    "the centerline, or an axes name). Angle is degrees.",
    {
        "angle_deg": {"type": "number", "default": 360, "exclusiveMinimum": 0, "maximum": 360},
        "sketch_name": _SKETCH_ARG,
        "axis_selection": SELECTION_SCHEMA,
        "cut": {"type": "boolean", "default": False, "description": "Remove material instead of adding it."},
        "reverse": {"type": "boolean", "default": False},
        "mid_plane": {"type": "boolean", "default": False, "description": "Revolve symmetrically about the sketch plane."},
        "merge": {"type": "boolean", "default": True},
        "name": {"type": "string"},
    },
)
def revolve(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_part()
    sketch = select_sketch_for_feature(doc, args.get("sketch_name"))
    axis_spec = args.get("axis_selection")
    if axis_spec:
        # Mark 4 is what FeatureRevolve2 reads the axis from; append so the
        # profile selection made above survives.
        require_selection(doc, axis_spec, mark=4, append=True)

    angle = to_rad(args.get("angle_deg", 360))
    mid_plane = bool(args.get("mid_plane", False))
    single_direction = not mid_plane
    direction_type = 4 if mid_plane else 0  # swRevolveType_e: 4 = mid plane

    feature = feature_manager(doc).FeatureRevolve2(
        single_direction, True, False, bool(args.get("cut", False)),
        bool(args.get("reverse", False)), False,
        direction_type, 0, angle, 0.0,
        False, False, 0.0, 0.0,
        0, 0.0, 0.0,
        bool(args.get("merge", True)), True, True,
    )
    rename_feature(feature, args.get("name"))
    return feature_result(doc, feature, "revolve", sketch=sketch, angle_deg=args.get("angle_deg", 360))


# --------------------------------------------------------------------------
# Dress-up features
# --------------------------------------------------------------------------


@tool(
    "fillet",
    "Round the selected edges or faces with a constant radius, in millimetres. "
    "Use list_edges to find the edge indices, or selection.face_edges to fillet every edge of a face.",
    {
        "radius_mm": {"type": "number", "exclusiveMinimum": 0},
        "selection": SELECTION_SCHEMA,
        "propagate": {"type": "boolean", "default": True, "description": "Continue across tangent faces."},
        "keep_features": {"type": "boolean", "default": False},
        "name": {"type": "string"},
    },
    ["radius_mm", "selection"],
)
def fillet(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_part()
    exit_active_sketch(doc)
    count = require_selection(doc, args["selection"])
    options = FILLET_UNIFORM_RADIUS
    if bool(args.get("propagate", True)):
        options |= FILLET_PROPAGATE

    feature = feature_manager(doc).FeatureFillet3(
        options, to_m(args["radius_mm"]), 0.0, 0.0,
        FILLET_TYPE_SIMPLE, 0, 0,
        empty_variant(), empty_variant(), empty_variant(),
        empty_variant(), empty_variant(), empty_variant(), empty_variant(),
    )
    rename_feature(feature, args.get("name"))
    return feature_result(doc, feature, "fillet", radius_mm=args["radius_mm"], entities=count)


@tool(
    "chamfer",
    "Chamfer the selected edges or faces. equal_distance uses distance_mm on both sides; "
    "angle_distance uses distance_mm and angle_deg; distance_distance uses both distances.",
    {
        "distance_mm": {"type": "number", "exclusiveMinimum": 0},
        "selection": SELECTION_SCHEMA,
        "mode": {
            "type": "string",
            "enum": ["equal_distance", "angle_distance", "distance_distance"],
            "default": "equal_distance",
        },
        "angle_deg": {"type": "number", "default": 45},
        "other_distance_mm": {"type": "number", "description": "Second distance for distance_distance."},
        "propagate": {"type": "boolean", "default": True},
        "flip": {"type": "boolean", "default": False},
        "name": {"type": "string"},
    },
    ["distance_mm", "selection"],
)
def chamfer(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_part()
    exit_active_sketch(doc)
    count = require_selection(doc, args["selection"])
    mode = str(args.get("mode", "equal_distance"))
    chamfer_type = {
        "equal_distance": CHAMFER_EQUAL_DISTANCE,
        "angle_distance": CHAMFER_ANGLE_DISTANCE,
        "distance_distance": CHAMFER_DISTANCE_DISTANCE,
    }[mode]
    options = CHAMFER_TANGENT_PROPAGATION if bool(args.get("propagate", True)) else 0
    if bool(args.get("flip", False)):
        options |= 1  # swFeatureChamferFlipDirection
    other = to_m(args.get("other_distance_mm", args["distance_mm"]))

    feature = feature_manager(doc).InsertFeatureChamfer(
        options, chamfer_type, to_m(args["distance_mm"]),
        to_rad(args.get("angle_deg", 45)), other,
        0.0, 0.0, 0.0,
    )
    rename_feature(feature, args.get("name"))
    return feature_result(doc, feature, "chamfer", distance_mm=args["distance_mm"], entities=count)


@tool(
    "shell",
    "Hollow the part to a wall thickness in millimetres. Pass selection with the faces to remove; "
    "omit it to hollow the body with no opening.",
    {
        "thickness_mm": {"type": "number", "exclusiveMinimum": 0},
        "selection": SELECTION_SCHEMA,
        "outward": {"type": "boolean", "default": False, "description": "Thicken outward instead of inward."},
        "name": {"type": "string"},
    },
    ["thickness_mm"],
)
def shell(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_part()
    exit_active_sketch(doc)
    before = _feature_names(doc)
    removed = 0
    if args.get("selection"):
        removed = apply_selection(doc, args["selection"])
    else:
        clear_selection(doc)

    doc.InsertFeatureShell(to_m(args["thickness_mm"]), bool(args.get("outward", False)))
    feature = _feature_created_after(doc, before)
    rename_feature(feature, args.get("name"))
    return feature_result(doc, feature, "shell", thickness_mm=args["thickness_mm"], removed_faces=removed)


@tool(
    "draft",
    "Apply draft to the selected faces about a neutral plane or face. Angle is degrees. "
    "Put the faces to draft in selection and the neutral reference in neutral_selection.",
    {
        "angle_deg": {"type": "number", "exclusiveMinimum": 0, "default": 3},
        "selection": SELECTION_SCHEMA,
        "neutral_selection": SELECTION_SCHEMA,
        "reverse": {
            "type": "boolean",
            "default": False,
            "description": "False tapers the face outward, adding material below the neutral plane; "
                           "True tapers it inward and removes material. For a moulded part you "
                           "normally want True.",
        },
        "name": {"type": "string"},
    },
    ["angle_deg", "selection", "neutral_selection"],
)
def draft(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_part()
    exit_active_sketch(doc)
    # Mark 1 is the neutral plane, mark 2 the faces being drafted.
    require_selection(doc, args["neutral_selection"], mark=1)
    faces = require_selection(doc, args["selection"], mark=2, append=True)

    feature = feature_manager(doc).InsertMultiFaceDraft(
        to_rad(args["angle_deg"]), bool(args.get("reverse", False)), False, 0, False, False
    )
    rename_feature(feature, args.get("name"))
    return feature_result(doc, feature, "draft", angle_deg=args["angle_deg"], faces=faces)


@tool(
    "rib",
    "Turn an open sketch profile into a rib of the given thickness in millimetres. The usual setup "
    "is a single line drawn on the cross-section plane, bridging two existing faces — the profile "
    "must reach material at both ends or SOLIDWORKS creates nothing.",
    {
        "thickness_mm": {"type": "number", "exclusiveMinimum": 0},
        "sketch_name": _SKETCH_ARG,
        "two_sided": {"type": "boolean", "default": True, "description": "Thicken symmetrically about the sketch."},
        "reverse_material": {"type": "boolean", "default": False, "description": "Flip which way the rib grows."},
        "extrude_direction": {
            "type": "string",
            "enum": ["parallel_to_sketch", "normal_to_sketch"],
            "default": "parallel_to_sketch",
            "description": "How the profile reaches the material. A cross-section profile wants "
                           "parallel_to_sketch; the other is tried automatically if this one builds nothing.",
        },
        "draft_deg": {"type": "number", "default": 0},
        "name": {"type": "string"},
    },
    ["thickness_mm"],
)
def rib(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_part()
    draft_deg = float(args.get("draft_deg", 0))
    requested = str(args.get("extrude_direction", "parallel_to_sketch")) == "normal_to_sketch"

    def attempt(normal_to_sketch: bool) -> tuple[str, Any]:
        name = select_sketch_for_feature(doc, args.get("sketch_name"))
        before = _feature_names(doc)
        feature_manager(doc).InsertRib(
            bool(args.get("two_sided", True)), False, to_m(args["thickness_mm"]), 0,
            bool(args.get("reverse_material", False)),
            abs(draft_deg) > 1e-12, False, to_rad(draft_deg),
            normal_to_sketch, False,
        )
        return name, _feature_created_after(doc, before)

    sketch, feature = attempt(requested)
    flipped = False
    if feature is None:
        # InsertRib returns void and simply builds nothing when the extrusion
        # direction cannot reach material, so the other one is worth a try.
        sketch, feature = attempt(not requested)
        flipped = feature is not None

    rename_feature(feature, args.get("name"))
    payload = feature_result(doc, feature, "rib", sketch=sketch, thickness_mm=args["thickness_mm"])
    if flipped:
        used = "normal_to_sketch" if not requested else "parallel_to_sketch"
        payload["data"]["extrude_direction"] = used
        payload["message"] += f" The requested direction built nothing, so {used} was used."
    elif feature is None:
        payload["message"] += (
            " Neither extrusion direction reached material: check that the profile spans between "
            "two existing faces."
        )
    return payload


@tool(
    "simple_hole",
    "Cut a straight hole of a given diameter into a face. Put the face and the hole centre in "
    "selection, most simply as a points entry with type FACE at the hole location.",
    {
        "diameter_mm": {"type": "number", "exclusiveMinimum": 0},
        "selection": SELECTION_SCHEMA,
        "depth_mm": {"type": "number", "exclusiveMinimum": 0, "default": 10},
        "end_condition": _END_CONDITION_SCHEMA,
        "reverse": {"type": "boolean", "default": False},
        "name": {"type": "string"},
    },
    ["diameter_mm", "selection"],
)
def simple_hole(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_part()
    exit_active_sketch(doc)
    require_selection(doc, args["selection"])
    condition = END_CONDITIONS[str(args.get("end_condition", "blind"))]
    depth = to_m(args.get("depth_mm", 10))

    feature = feature_manager(doc).SimpleHole2(
        to_m(args["diameter_mm"]), True, False, bool(args.get("reverse", False)),
        condition, 0, depth, 0.0,
        False, False, False, False, 0.0, 0.0,
        False, False, False, False,
        True, True, False, False, False,
    )
    rename_feature(feature, args.get("name"))
    return feature_result(doc, feature, "hole", diameter_mm=args["diameter_mm"])


# --------------------------------------------------------------------------
# Swept and lofted features
# --------------------------------------------------------------------------


@tool(
    "sweep",
    "Sweep a closed profile sketch along a path sketch.",
    {
        "profile_sketch": {"type": "string", "description": "Sketch feature name of the closed profile."},
        "path_sketch": {"type": "string", "description": "Sketch feature name of the path."},
        "cut": {"type": "boolean", "default": False},
        "merge": {"type": "boolean", "default": True},
        "keep_tangency": {"type": "boolean", "default": False},
        "twist_angle_deg": {"type": "number", "default": 0},
        "name": {"type": "string"},
    },
    ["profile_sketch", "path_sketch"],
)
def sweep(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_part()
    exit_active_sketch(doc)
    # The sweep APIs read the profile from mark 1 and the path from mark 4.
    require_selection(doc, {"sketches": [str(args["profile_sketch"])]}, mark=1)
    require_selection(doc, {"sketches": [str(args["path_sketch"])]}, mark=4, append=True)

    manager = feature_manager(doc)
    keep_tangency = bool(args.get("keep_tangency", False))
    twist = to_rad(args.get("twist_angle_deg", 0))
    if bool(args.get("cut", False)):
        # InsertCutSwept5 has no Merge flag but does carry assembly scope flags.
        # InsertCutSwept4 (2016) is the same list without the last three.
        cut_args = (
            False, True, 0, keep_tangency, False,
            0, 0, False, 0.0, 0.0, 0, 0,
            True, True, twist, False,
            False, False, False,
            False, 0.0, 0,
        )
        feature = call_versioned(
            manager,
            ("InsertCutSwept5", cut_args),
            ("InsertCutSwept4", cut_args[:19]),
        )
    else:
        # InsertProtrusionSwept3 (2016) stops after BMergeSmoothFaces.
        boss_args = (
            False, True, 0, keep_tangency, False,
            0, 0, False, 0.0, 0.0, 0, 0,
            bool(args.get("merge", True)), True, True,
            twist, False, False, 0.0, 0,
        )
        feature = call_versioned(
            manager,
            ("InsertProtrusionSwept4", boss_args),
            ("InsertProtrusionSwept3", boss_args[:17]),
        )
    rename_feature(feature, args.get("name"))
    return feature_result(
        doc, feature, "sweep",
        profile=str(args["profile_sketch"]), path=str(args["path_sketch"]),
    )


@tool(
    "loft",
    "Loft a solid through two or more profile sketches, in the order given.",
    {
        "profile_sketches": {
            "type": "array",
            "minItems": 2,
            "items": {"type": "string"},
            "description": "Sketch feature names, ordered from one end of the loft to the other.",
        },
        "cut": {"type": "boolean", "default": False},
        "closed": {"type": "boolean", "default": False},
        "keep_tangency": {"type": "boolean", "default": False},
        "merge": {"type": "boolean", "default": True},
        "name": {"type": "string"},
    },
    ["profile_sketches"],
)
def loft(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_part()
    exit_active_sketch(doc)
    profiles = [str(name) for name in args["profile_sketches"]]
    clear_selection(doc)
    for index, name in enumerate(profiles):
        require_selection(doc, {"sketches": [name]}, mark=1, append=index > 0)

    manager = feature_manager(doc)
    closed = bool(args.get("closed", False))
    keep_tangency = bool(args.get("keep_tangency", False))
    if bool(args.get("cut", False)):
        # InsertCutBlend takes neither tangent-length nor merge arguments.
        feature = manager.InsertCutBlend(
            closed, keep_tangency, False, 1.0,
            0, 0, False, 0.0, 0.0, 0, True, True,
        )
    else:
        feature = manager.InsertProtrusionBlend2(
            closed, keep_tangency, False, 1.0,
            0, 0, 0.0, 0.0, True, True,
            False, 0.0, 0.0, 0,
            bool(args.get("merge", True)), True, True, 0,
        )
    rename_feature(feature, args.get("name"))
    return feature_result(doc, feature, "loft", profiles=profiles)


# --------------------------------------------------------------------------
# Patterns and mirror
# --------------------------------------------------------------------------


@tool(
    "linear_pattern",
    "Repeat features along one or two directions. Put the features to repeat in selection and the "
    "direction reference (an edge, axis, or plane) in direction1_selection. Spacing is millimetres.",
    {
        "selection": SELECTION_SCHEMA,
        "direction1_selection": SELECTION_SCHEMA,
        "count1": {"type": "integer", "minimum": 2, "default": 2},
        "spacing1_mm": {"type": "number", "exclusiveMinimum": 0},
        "reverse1": {"type": "boolean", "default": False},
        "direction2_selection": SELECTION_SCHEMA,
        "count2": {"type": "integer", "minimum": 1, "default": 1},
        "spacing2_mm": {"type": "number", "default": 0},
        "reverse2": {"type": "boolean", "default": False},
        "geometry_pattern": {"type": "boolean", "default": False},
        "name": {"type": "string"},
    },
    ["selection", "direction1_selection", "spacing1_mm"],
)
def linear_pattern(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_part()
    exit_active_sketch(doc)
    # Marks: 1 = direction 1, 2 = direction 2, 4 = the features being patterned.
    require_selection(doc, args["direction1_selection"], mark=1)
    has_second = bool(args.get("direction2_selection")) and int(args.get("count2", 1)) > 1
    if has_second:
        require_selection(doc, args["direction2_selection"], mark=2, append=True)
    require_selection(doc, args["selection"], mark=4, append=True)

    pattern_args = (
        int(args.get("count1", 2)), to_m(args["spacing1_mm"]),
        int(args.get("count2", 1)) if has_second else 1,
        to_m(args.get("spacing2_mm", 0)) if has_second else 0.0,
        bool(args.get("reverse1", False)), bool(args.get("reverse2", False)),
        "", "",
        bool(args.get("geometry_pattern", False)), False,
        False, False, True, True, False, False, False, False, 0.0, 0.0,
        False, False,
    )
    # FeatureLinearPattern4 (2016) ends at Offset2, i.e. the first 20 arguments.
    feature = call_versioned(
        feature_manager(doc),
        ("FeatureLinearPattern5", pattern_args),
        ("FeatureLinearPattern4", pattern_args[:20]),
    )
    rename_feature(feature, args.get("name"))
    return feature_result(doc, feature, "linear pattern", count1=int(args.get("count1", 2)))


@tool(
    "circular_pattern",
    "Repeat features around an axis. Put the features in selection and the axis (a reference axis, "
    "a cylindrical face, or a straight edge) in axis_selection. Angle is degrees.",
    {
        "selection": SELECTION_SCHEMA,
        "axis_selection": SELECTION_SCHEMA,
        "count": {"type": "integer", "minimum": 2, "default": 4},
        "angle_deg": {"type": "number", "default": 360, "description": "Total spread when equal_spacing is true, otherwise the step angle."},
        "equal_spacing": {"type": "boolean", "default": True},
        "reverse": {"type": "boolean", "default": False},
        "geometry_pattern": {"type": "boolean", "default": False},
        "name": {"type": "string"},
    },
    ["selection", "axis_selection"],
)
def circular_pattern(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_part()
    exit_active_sketch(doc)
    # Marks: 1 = the pattern axis, 4 = the features being patterned.
    require_selection(doc, args["axis_selection"], mark=1)
    require_selection(doc, args["selection"], mark=4, append=True)

    # SOLIDWORKS always stores a per-instance step; for equal spacing it wants
    # the full spread, which it then divides internally.
    spacing = to_rad(args.get("angle_deg", 360))
    pattern_args = (
        int(args.get("count", 4)), spacing,
        bool(args.get("reverse", False)), "",
        bool(args.get("geometry_pattern", False)),
        bool(args.get("equal_spacing", True)), False, False,
        False, False, 1, 0.0, "", False,
    )
    # FeatureCircularPattern4 (2016) ends at VaryInstance, the first 7 arguments.
    feature = call_versioned(
        feature_manager(doc),
        ("FeatureCircularPattern5", pattern_args),
        ("FeatureCircularPattern4", pattern_args[:7]),
    )
    rename_feature(feature, args.get("name"))
    return feature_result(doc, feature, "circular pattern", count=int(args.get("count", 4)))


@tool(
    "mirror_feature",
    "Mirror features (or whole bodies) about a plane or planar face. Put the features in selection "
    "and the mirror plane in plane_selection.",
    {
        "selection": SELECTION_SCHEMA,
        "plane_selection": SELECTION_SCHEMA,
        "mirror_body": {"type": "boolean", "default": False, "description": "Mirror the whole body instead of features."},
        "merge": {"type": "boolean", "default": True},
        "geometry_pattern": {"type": "boolean", "default": False},
        "name": {"type": "string"},
    },
    ["selection", "plane_selection"],
)
def mirror_feature(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_part()
    exit_active_sketch(doc)
    # Marks: 2 = the mirror plane, 1 = the features being mirrored.
    require_selection(doc, args["plane_selection"], mark=2)
    require_selection(doc, args["selection"], mark=1, append=True)

    feature = feature_manager(doc).InsertMirrorFeature2(
        bool(args.get("mirror_body", False)),
        bool(args.get("geometry_pattern", False)),
        bool(args.get("merge", True)),
        False, 0,
    )
    rename_feature(feature, args.get("name"))
    return feature_result(doc, feature, "mirror")


# --------------------------------------------------------------------------
# Feature-tree maintenance
# --------------------------------------------------------------------------


@tool(
    "delete_feature",
    "Delete named features from the feature tree, together with anything that depends on them.",
    {"feature_names": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
    ["feature_names"],
)
def delete_feature(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    exit_active_sketch(doc)
    clear_selection(doc)
    names = [str(name) for name in args["feature_names"]]
    selected: list[str] = []
    for name in names:
        feature = find_feature(doc, name)
        if feature is None:
            return result(False, f"No feature named '{name}' exists in this document.")
        if bool(selectable(feature).Select2(True, 0)):
            selected.append(name)
    if not selected:
        return result(False, "Could not select any of the named features.")
    deleted = bool(extension(doc).DeleteSelection2(0))
    rebuild(doc)
    clear_selection(doc)
    return result(
        deleted,
        f"Deleted {len(selected)} features." if deleted else "SOLIDWORKS refused to delete the selection.",
        deleted=selected,
    )


@tool(
    "rename_feature",
    "Rename a feature so later tool calls can refer to it stably.",
    {"feature_name": {"type": "string"}, "new_name": {"type": "string"}},
    ["feature_name", "new_name"],
)
def rename_feature_tool(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    feature = find_feature(doc, str(args["feature_name"]))
    if feature is None:
        return result(False, f"No feature named '{args['feature_name']}' exists in this document.")
    feature.Name = str(args["new_name"])
    return result(True, f"Renamed to '{args['new_name']}'.", feature=str(args["new_name"]))


@tool(
    "set_feature_suppression",
    "Suppress or unsuppress named features.",
    {
        "feature_names": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "suppressed": {"type": "boolean", "default": True},
    },
    ["feature_names"],
)
def set_feature_suppression(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    exit_active_sketch(doc)
    suppressed = bool(args.get("suppressed", True))
    changed: list[str] = []
    for name in args["feature_names"]:
        feature = find_feature(doc, str(name))
        if feature is None:
            return result(False, f"No feature named '{name}' exists in this document.")
        clear_selection(doc)
        if not bool(selectable(feature).Select2(False, 0)):
            return result(False, f"Could not select feature '{name}'.")
        # These take no arguments, and pywin32 may already have evaluated them
        # on attribute access, so go through value() rather than calling.
        ok = bool(value(doc, "EditSuppress2" if suppressed else "EditUnsuppress2"))
        if ok:
            changed.append(str(name))
    rebuild(doc)
    clear_selection(doc)
    return result(
        bool(changed),
        f"{'Suppressed' if suppressed else 'Unsuppressed'} {len(changed)} features.",
        changed=changed,
        problems=whats_wrong(doc),
    )
