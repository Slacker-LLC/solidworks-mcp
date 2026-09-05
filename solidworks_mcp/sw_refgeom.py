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

"""Reference geometry: planes and axes.

Without these anything beyond a single-plane part stalls, so the plane tool
covers the four constructions that actually come up: offset, angle, midplane,
and three points.
"""

from __future__ import annotations

from typing import Any

from .sw_core import (
    select_in_order,
    active_document,
    clear_selection,
    exit_active_sketch,
    feature_manager,
    feature_result,
    PLANE_ANGLE,
    PLANE_COINCIDENT,
    PLANE_DISTANCE,
    PLANE_FLIP,
    PLANE_MIDPLANE,
    PLANE_PARALLEL,
    reference_axes,
    reference_planes,
    rename_feature,
    require_part,
    require_selection,
    result,
    SELECTION_SCHEMA,
    tool,
    to_m,
    to_rad,
)


@tool(
    "list_reference_planes",
    "Read-only: list the actual localized reference-plane names in the active part, so a sketch or "
    "plane can target one precisely.",
    {},
)
def list_reference_planes(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_part()
    return result(True, "Read reference planes.", planes=reference_planes(doc), axes=reference_axes(doc))


@tool(
    "create_plane",
    "Create a reference plane. mode=offset needs one plane or planar face and distance_mm; "
    "mode=angle needs a plane plus an axis or straight edge and angle_deg; mode=midplane needs two "
    "planes or planar faces; mode=three_points needs three vertices or sketch points. "
    "References are consumed in the order they appear in selection, so for mode=angle write the "
    "plane before the axis.",
    {
        "mode": {"type": "string", "enum": ["offset", "angle", "midplane", "three_points", "parallel_through_point"], "default": "offset"},
        "selection": SELECTION_SCHEMA,
        "distance_mm": {"type": "number", "description": "Offset distance for mode=offset."},
        "angle_deg": {"type": "number", "description": "Angle for mode=angle."},
        "flip": {"type": "boolean", "default": False, "description": "Flip to the other side."},
        "name": {"type": "string"},
    },
    ["mode", "selection"],
)
def create_plane(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_part()
    exit_active_sketch(doc)
    mode = str(args["mode"])
    # InsertRefPlane reads its first/second/third reference from marks 0/1/2,
    # not from selection order, so each one is selected with its own mark.
    count = select_in_order(doc, args["selection"])
    flip = PLANE_FLIP if bool(args.get("flip", False)) else 0

    if mode == "offset":
        if args.get("distance_mm") is None:
            return result(False, "mode=offset needs distance_mm.")
        constraints = (PLANE_DISTANCE | flip, to_m(args["distance_mm"]), 0, 0.0, 0, 0.0)
    elif mode == "angle":
        if args.get("angle_deg") is None:
            return result(False, "mode=angle needs angle_deg.")
        if count < 2:
            return result(False, "mode=angle needs two references: a plane and an axis or straight edge.")
        constraints = (PLANE_ANGLE | flip, to_rad(args["angle_deg"]), PLANE_COINCIDENT, 0.0, 0, 0.0)
    elif mode == "midplane":
        if count < 2:
            return result(False, "mode=midplane needs two planes or planar faces.")
        constraints = (PLANE_MIDPLANE, 0.0, PLANE_MIDPLANE, 0.0, 0, 0.0)
    elif mode == "three_points":
        if count < 3:
            return result(False, "mode=three_points needs three vertices or sketch points.")
        constraints = (PLANE_COINCIDENT, 0.0, PLANE_COINCIDENT, 0.0, PLANE_COINCIDENT, 0.0)
    else:  # parallel_through_point
        if count < 2:
            return result(False, "mode=parallel_through_point needs a plane and a point.")
        constraints = (PLANE_PARALLEL, 0.0, PLANE_COINCIDENT, 0.0, 0, 0.0)

    feature = feature_manager(doc).InsertRefPlane(*constraints)
    rename_feature(feature, args.get("name"))
    payload = feature_result(doc, feature, f"{mode} reference plane", references=count)
    if payload["ok"]:
        payload["data"]["planes"] = reference_planes(doc)
    return payload


@tool(
    "create_axis",
    "Create a reference axis from the selected references: a cylindrical or conical face, a "
    "straight edge, two planes, or two points. Two planes give their line of intersection, so "
    "pick the pair deliberately: front+right is the Y axis, top+right is the Z axis, and "
    "front+top is the X axis. For a circular pattern through a plate, you want the axis normal "
    "to the plate, which is often easier to get from the part's own cylindrical face.",
    {
        "selection": SELECTION_SCHEMA,
        "name": {"type": "string"},
    },
    ["selection"],
)
def create_axis(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_part()
    exit_active_sketch(doc)
    from .sw_core import find_feature, iter_features

    before = {f["name"] for f in iter_features(doc)}
    count = require_selection(doc, args["selection"])
    if not bool(doc.InsertAxis2(True)):
        clear_selection(doc)
        return result(False, "SOLIDWORKS could not build an axis from that selection.", references=count)

    created = [f["name"] for f in iter_features(doc) if f["name"] not in before]
    feature = find_feature(doc, created[-1]) if created else None
    rename_feature(feature, args.get("name"))
    clear_selection(doc)
    name = str(args.get("name") or (created[-1] if created else ""))
    return result(True, f"Created reference axis '{name}'.", feature=name, axes=reference_axes(doc))
