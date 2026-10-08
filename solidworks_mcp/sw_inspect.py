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

"""Feedback channel: topology listings, screenshots, mass properties, measurement,
bounding boxes, and rebuild-error readback.

Without this an agent builds blind.  capture_screenshot returns the picture as
real image content, so the model can actually look at what it made.
"""

from __future__ import annotations

import base64
import io
import time
from pathlib import Path
from typing import Any

from .sw_core import (
    BODY_SHEET,
    active_document,
    apply_selection,
    apply_transform,
    byref_long,
    clear_selection,
    document_type,
    enumerate_edges,
    enumerate_faces,
    enumerate_vertices,
    extension,
    flag_methods,
    invoke_no_arg,
    iter_body_context,
    iter_features,
    logger,
    mm_point,
    nothing,
    OUTPUT_ROOT,
    rebuild,
    require_part,
    result,
    safe,
    SELECTION_SCHEMA,
    tool,
    to_deg,
    to_mm,
    value,
    whats_wrong,
)


# swStandardViews_e. Addressing views by number rather than by the "*Front"
# style name matters: on a localized SOLIDWORKS those names are translated, and
# ShowNamedView2 silently does nothing when it cannot resolve the name.
NAMED_VIEWS = {
    "front": 1, "back": 2, "left": 3, "right": 4, "top": 5,
    "bottom": 6, "isometric": 7, "trimetric": 8, "dimetric": 9,
}


def _orient(doc: Any, view: str) -> None:
    doc.ShowNamedView2("", NAMED_VIEWS[view])


def _strip_internal(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop the raw metre-space pick points; the mm ones are the public contract."""
    return [{k: v for k, v in entry.items() if not k.startswith("_")} for entry in entries]


# --------------------------------------------------------------------------
# Topology listings — the input side of every selection spec
# --------------------------------------------------------------------------


@tool(
    "list_faces",
    "Read-only: list the faces of the active part with surface type, area, a point on the face, and "
    "the index that selection.faces takes. Re-list after any geometry change, because indices move.",
    {
        "body_type": {"type": "string", "enum": ["solid", "surface"], "default": "solid", "description": "Surface results use selection.surface_faces; solid results use selection.faces."},
        "surface_type": {
            "type": "string",
            "enum": ["plane", "cylinder", "cone", "sphere", "torus", "bsurface"],
            "description": "Only return faces of this surface type.",
        },
        "min_area_mm2": {"type": "number", "description": "Only return faces at least this large."},
        "normal": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 3, "maxItems": 3,
            "description": "Only return planar faces whose normal points this way, e.g. [0,0,1] for up.",
        },
        "tolerance": {"type": "number", "default": 0.02, "description": "Direction tolerance for the normal filter."},
    },
)
def list_faces(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    kind = args.get("body_type", "solid")
    if kind not in {"solid", "surface"}:
        raise RuntimeError("body_type must be solid or surface.")
    faces = enumerate_faces(doc, BODY_SHEET) if kind == "surface" else enumerate_faces(doc)
    total = len(faces)

    wanted_type = args.get("surface_type")
    if wanted_type:
        faces = [f for f in faces if f.get("surface_type") == wanted_type]
    min_area = args.get("min_area_mm2")
    if min_area is not None:
        faces = [f for f in faces if f.get("area_mm2", 0.0) >= float(min_area)]
    direction = args.get("normal")
    if direction:
        tolerance = float(args.get("tolerance", 0.02))
        target = [float(c) for c in direction]
        magnitude = sum(c * c for c in target) ** 0.5 or 1.0
        target = [c / magnitude for c in target]
        faces = [
            f for f in faces
            if f.get("normal") and abs(sum(a * b for a, b in zip(f["normal"], target)) - 1.0) <= tolerance
        ]

    return result(
        True,
        f"Read {len(faces)} of {total} faces.",
        faces=_strip_internal(faces),
        total_faces=total,
        body_type=kind,
    )


@tool(
    "list_edges",
    "Read-only: list the edges of the active part with curve type, length, and the index that "
    "selection.edges takes. Re-list after any geometry change.",
    {
        "body_type": {"type": "string", "enum": ["solid", "surface"], "default": "solid", "description": "Surface results use selection.surface_edges; solid results use selection.edges."},
        "curve_type": {
            "type": "string",
            "enum": ["line", "circle", "ellipse", "bcurve"],
            "description": "Only return edges of this curve type.",
        },
        "min_length_mm": {"type": "number"},
        "max_length_mm": {"type": "number"},
    },
)
def list_edges(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    kind = args.get("body_type", "solid")
    if kind not in {"solid", "surface"}:
        raise RuntimeError("body_type must be solid or surface.")
    edges = enumerate_edges(doc, BODY_SHEET) if kind == "surface" else enumerate_edges(doc)
    total = len(edges)

    wanted = args.get("curve_type")
    if wanted:
        edges = [e for e in edges if e.get("curve_type") == wanted]
    if args.get("min_length_mm") is not None:
        edges = [e for e in edges if e.get("length_mm", 0.0) >= float(args["min_length_mm"])]
    if args.get("max_length_mm") is not None:
        edges = [e for e in edges if e.get("length_mm", 1e18) <= float(args["max_length_mm"])]

    return result(True, f"Read {len(edges)} of {total} edges.", edges=_strip_internal(edges), total_edges=total, body_type=kind)


@tool(
    "list_vertices",
    "Read-only: list the vertices of the active part with the index that selection.vertices takes.",
    {},
)
def list_vertices(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    vertices = enumerate_vertices(doc)
    return result(True, f"Read {len(vertices)} vertices.", vertices=_strip_internal(vertices))


@tool("list_bodies", "Read-only: list the solid bodies of the active part or assembly.", {})
def list_bodies(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    bodies = []
    for index, (body, label, matrix) in enumerate(iter_body_context(doc)):
        entry: dict[str, Any] = {"index": index, "name": label or f"body{index}"}
        box = safe(body, "GetBodyBox")
        if box is not None and len(box) >= 6:
            entry["min_mm"] = mm_point(apply_transform(box[0:3], matrix))
            entry["max_mm"] = mm_point(apply_transform(box[3:6], matrix))
        bodies.append(entry)
    return result(True, f"Read {len(bodies)} solid bodies.", bodies=bodies)


@tool("list_features", "Read-only: list the active document feature tree in order.", {})
def list_features(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    return result(True, "Read feature tree.", features=iter_features(doc))


# --------------------------------------------------------------------------
# Measurement and properties
# --------------------------------------------------------------------------


@tool(
    "get_bounding_box",
    "Read-only: overall bounding box of the active part, in millimetres.",
    {},
)
def get_bounding_box(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    box = None
    try:
        # NoConversion=True keeps GetPartBox in system units (metres); passing
        # False returns document units, which would be silently 1000x off here.
        box = doc.GetPartBox(True) if document_type(doc) == 1 else doc.GetBox(0)
    except Exception:
        pass
    if box is None or len(box) < 6:
        return result(False, "The active document has no geometry to bound.")
    low, high = mm_point(box[0:3]), mm_point(box[3:6])
    return result(
        True,
        "Read the bounding box.",
        min_mm=low,
        max_mm=high,
        size_mm=[round(high[i] - low[i], 6) for i in range(3)],
    )


@tool(
    "get_mass_properties",
    "Read-only: mass, volume, surface area, and centre of mass of the active part, in millimetre "
    "and gram units.",
    {},
)
def get_mass_properties(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    status = byref_long(0)
    try:
        values = extension(doc).GetMassProperties2(2, status, False)
    except Exception as exc:
        return result(False, f"SOLIDWORKS could not compute mass properties: {exc}")
    if values is None or len(values) < 6:
        return result(False, "SOLIDWORKS returned no mass properties. Does the document contain a solid body?")

    volume_m3 = float(values[3])
    payload = {
        "center_of_mass_mm": mm_point(values[0:3]),
        "volume_mm3": round(volume_m3 * 1e9, 4),
        "surface_area_mm2": round(float(values[4]) * 1e6, 4),
        "mass_g": round(float(values[5]) * 1000.0, 6),
    }
    try:
        payload["density_g_per_cm3"] = round(float(values[5]) / volume_m3 / 1000.0, 6) if volume_m3 else None
    except Exception:
        pass
    if len(values) >= 15:
        payload["moments_of_inertia_kg_m2"] = [round(float(v), 12) for v in values[6:15]]
    return result(True, "Read mass properties.", **payload)


@tool(
    "measure",
    "Read-only: measure the selected entities. One face gives area and perimeter, one edge gives "
    "length, two entities give the distance and delta between them. Millimetres and degrees.",
    {"selection": SELECTION_SCHEMA},
    ["selection"],
)
def measure(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    count = apply_selection(doc, args["selection"])
    if count == 0:
        return result(False, "The selection specification did not resolve to anything to measure.")

    # CreateMeasure takes no arguments, so pywin32 has already evaluated it on
    # attribute access; calling the result would invoke IMeasure's default
    # member and fail with "member not found".
    measurement = value(extension(doc), "CreateMeasure")
    if measurement is None:
        clear_selection(doc)
        return result(False, "SOLIDWORKS did not provide a measure object.")
    try:
        measurement.ArcOption = 0  # centre-to-centre for circular entities
    except Exception:
        pass
    # IMeasure::Calculate takes no arguments in this type library.
    if not bool(value(measurement, "Calculate")):
        clear_selection(doc)
        return result(False, "SOLIDWORKS could not measure that selection.", entities=count)

    area = lambda v: v * 1e6  # noqa: E731 - square metres to square millimetres
    readings: dict[str, Any] = {}
    for member, key, convert in (
        ("Length", "length_mm", to_mm),
        ("TotalLength", "total_length_mm", to_mm),
        ("ArcLength", "arc_length_mm", to_mm),
        ("ChordLength", "chord_length_mm", to_mm),
        ("Perimeter", "perimeter_mm", to_mm),
        ("Area", "area_mm2", area),
        ("TotalArea", "total_area_mm2", area),
        ("Distance", "distance_mm", to_mm),
        ("NormalDistance", "normal_distance_mm", to_mm),
        ("CenterDistance", "center_distance_mm", to_mm),
        ("DeltaX", "delta_x_mm", to_mm),
        ("DeltaY", "delta_y_mm", to_mm),
        ("DeltaZ", "delta_z_mm", to_mm),
        ("Angle", "angle_deg", to_deg),
        ("Diameter", "diameter_mm", to_mm),
        ("Radius", "radius_mm", to_mm),
    ):
        raw = safe(measurement, member)
        # SOLIDWORKS reports -1 for readings that do not apply to the selection.
        if raw is None:
            continue
        try:
            number = float(raw)
        except (TypeError, ValueError):
            continue
        if number < 0:
            continue
        readings[key] = round(convert(number), 6)

    clear_selection(doc)
    return result(True, f"Measured {count} entities.", entities=count, **readings)


@tool(
    "check_errors",
    "Read-only: rebuild the active document and report every feature that SOLIDWORKS flags. "
    "The COM API returns null far more often than it raises, so call this after a suspicious build.",
    {"rebuild_first": {"type": "boolean", "default": True}},
)
def check_errors(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    rebuilt = rebuild(doc) if bool(args.get("rebuild_first", True)) else None
    problems = whats_wrong(doc)
    return result(
        not problems,
        "No feature errors or warnings." if not problems else f"{len(problems)} features are flagged.",
        problems=problems,
        rebuilt=rebuilt,
    )


# --------------------------------------------------------------------------
# Views and screenshots
# --------------------------------------------------------------------------


@tool(
    "set_view",
    "Orient the graphics area to a named view and optionally zoom to fit. View only; no geometry changes.",
    {
        "view": {"type": "string", "enum": sorted(NAMED_VIEWS), "default": "isometric"},
        "zoom_to_fit": {"type": "boolean", "default": True},
        "shaded_with_edges": {"type": "boolean", "default": True},
    },
)
def set_view(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    view = str(args.get("view", "isometric"))
    _orient(doc, view)
    if bool(args.get("shaded_with_edges", True)):
        try:
            # swViewDisplayMode_e: 4 = shaded with edges
            doc.ViewDisplayShadedwithedges()
        except Exception:
            pass
    if bool(args.get("zoom_to_fit", True)):
        try:
            invoke_no_arg(doc, "ViewZoomtofit2")
        except Exception:
            pass
    return result(True, f"Set the {view} view.", view=view)


def _capture_png(doc: Any, width: int) -> bytes:
    directory = OUTPUT_ROOT / "screenshots"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"capture_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000}.png"

    errors, warnings = byref_long(0), byref_long(0)
    saved = False
    try:
        saved = bool(extension(doc).SaveAs(str(path), 0, 0, nothing(), errors, warnings))
    except Exception:
        logger.exception("Extension.SaveAs refused the screenshot path")
    if not saved:
        try:
            saved = bool(doc.SaveAs(str(path)))
        except Exception:
            logger.exception("ModelDoc2.SaveAs refused the screenshot path")
    if not saved or not path.is_file():
        raise RuntimeError("SOLIDWORKS did not write a PNG of the current view.")

    data = path.read_bytes()
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            image = image.convert("RGB")
            if image.width > width:
                height = round(image.height * width / image.width)
                image = image.resize((width, height), Image.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG", optimize=True)
            data = buffer.getvalue()
    except Exception:
        logger.info("Returning the raw SOLIDWORKS PNG without resizing.")
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    return data


@tool(
    "capture_screenshot",
    "Take a picture of the current SOLIDWORKS view and return it as an image, so you can actually "
    "see the model you just built. Optionally reorients the view first.",
    {
        "view": {
            "type": "string",
            "enum": sorted(NAMED_VIEWS) + ["current"],
            "default": "isometric",
            "description": "Orient the view before capturing; 'current' leaves it alone.",
        },
        "zoom_to_fit": {"type": "boolean", "default": True},
        "width_px": {"type": "integer", "default": 1100, "minimum": 200, "maximum": 2000},
    },
)
def capture_screenshot(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    view = str(args.get("view", "isometric"))
    clear_selection(doc)
    if view != "current":
        _orient(doc, view)
    if bool(args.get("zoom_to_fit", True)):
        try:
            invoke_no_arg(doc, "ViewZoomtofit2")
        except Exception:
            pass
    # The PNG comes from the graphics buffer, so let SOLIDWORKS finish drawing
    # the new orientation before asking for it.
    try:
        invoke_no_arg(doc, "GraphicsRedraw2")
    except Exception:
        pass
    time.sleep(0.35)

    data = _capture_png(doc, int(args.get("width_px", 1100)))
    payload = result(True, f"Captured the {view} view.", view=view, bytes=len(data))
    payload["_image_png_base64"] = base64.b64encode(data).decode("ascii")
    return payload
