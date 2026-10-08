# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for details.

"""Native sheet metal creation, parameters, flattening and DXF export."""

from pathlib import Path
import math

from .sw_core import (
    as_list, clear_selection, empty_variant, extension, feature_manager, feature_property,
    feature_result, find_feature, flag_methods, get_bodies, iter_feature_objects,
    nothing, rebuild, rename_feature, require_part, result,
    select_sketch_for_feature, to_m, to_mm, tool, value, whats_wrong,
)
from .sw_file import validated_output_path
from .sw_multibody import _positive


def _sheet_features(doc):
    return [f for f in iter_feature_objects(doc) if str(feature_property(f, "GetTypeName2", "")) == "SheetMetal"]


def _flat_features(doc):
    return [f for f in iter_feature_objects(doc) if str(feature_property(f, "GetTypeName2", "")) == "FlatPattern"]


def _suppressed(feature):
    flags = as_list(flag_methods(feature, "IsSuppressed2").IsSuppressed2(1, empty_variant()))
    if len(flags) != 1:
        raise RuntimeError("Could not read feature suppression in the current configuration.")
    return bool(flags[0])


def _read_parameters(feature):
    definition = value(feature, "GetDefinition")
    return {"feature": str(value(feature, "Name")), "thickness_mm": to_mm(float(value(definition, "Thickness"))),
            "bend_radius_mm": to_mm(float(value(definition, "BendRadius"))), "k_factor": float(value(definition, "KFactor"))}


@tool("sheet_metal_base_flange", "Create a native sheet-metal base flange from a closed or open sketch. Thickness, radius and extrusion depth are mm; K-factor is dimensionless.",
      {"sketch_name": {"type": "string"}, "thickness_mm": {"type": "number", "exclusiveMinimum": 0},
       "bend_radius_mm": {"type": "number", "minimum": 0, "default": 1}, "depth_mm": {"type": "number", "exclusiveMinimum": 0, "default": 50},
       "k_factor": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
       "reverse_thickness": {"type": "boolean", "default": False}, "reverse": {"type": "boolean", "default": False}, "name": {"type": "string"}}, ["thickness_mm"])
def sheet_metal_base_flange(args):
    thickness = _positive(args["thickness_mm"], "thickness_mm")
    depth = _positive(args.get("depth_mm", 50), "depth_mm")
    radius = float(args.get("bend_radius_mm", 1))
    factor = float(args.get("k_factor", 0.5))
    if not math.isfinite(radius) or radius < 0 or not math.isfinite(factor) or not 0 <= factor <= 1:
        raise RuntimeError("Radius must be finite and nonnegative; K-factor must be between 0 and 1.")
    _, doc = require_part()
    sketch = select_sketch_for_feature(doc, args.get("sketch_name"))
    manager = flag_methods(feature_manager(doc), "CreateCustomBendAllowance", "InsertSheetMetalBaseFlange2")
    allowance = manager.CreateCustomBendAllowance()
    allowance.Type = 2
    allowance.KFactor = factor
    feature = manager.InsertSheetMetalBaseFlange2(to_m(thickness), bool(args.get("reverse_thickness", False)), to_m(radius),
        to_m(depth), to_m(depth), bool(args.get("reverse", False)), 0, 0, 1, allowance,
        False, 2, to_m(thickness), to_m(thickness), 0.5, True, False, True, True)
    rename_feature(feature, args.get("name"))
    payload = feature_result(doc, feature, "sheet-metal base flange", sketch=sketch)
    if payload["ok"] and not any(bool(value(b, "IsSheetMetal")) for b in get_bodies(doc)):
        payload.update(ok=False, message="Base flange feature did not create a sheet-metal body.")
    return payload


@tool("list_sheet_metal_features", "Read-only: list native sheet-metal definitions, flat patterns and current suppression states.")
def list_sheet_metal_features(args):
    _, doc = require_part()
    return result(True, "Read sheet-metal features.", sheet_metal=[_read_parameters(f) for f in _sheet_features(doc)],
                  flat_patterns=[{"feature": str(value(f, "Name")), "suppressed": _suppressed(f)} for f in _flat_features(doc)])


def _sheet_feature(doc, name):
    if not name:
        template = value(extension(doc), "GetTemplateSheetMetal")
        if template is None:
            raise RuntimeError("The active part has no sheet-metal defaults.")
        return template
    available = _sheet_features(doc)
    if name:
        available = [f for f in available if str(value(f, "Name")) == name]
    if len(available) != 1:
        raise RuntimeError("Select an exact sheet-metal feature name from list_sheet_metal_features.")
    return available[0]


@tool("get_sheet_metal_parameters", "Read-only: read sheet-metal thickness, bend radius and K-factor. Omit feature_name for document defaults, or specify a body definition from list_sheet_metal_features.", {"feature_name": {"type": "string"}})
def get_sheet_metal_parameters(args):
    _, doc = require_part()
    return result(True, "Read sheet-metal parameters.", parameters=_read_parameters(_sheet_feature(doc, args.get("feature_name"))))


@tool("set_sheet_metal_parameters", "Edit sheet-metal thickness/radius/K-factor and read values back. Omit feature_name to edit document defaults; specify a body definition to override its defaults. Lengths are mm.",
      {"feature_name": {"type": "string"}, "thickness_mm": {"type": "number", "exclusiveMinimum": 0},
       "bend_radius_mm": {"type": "number", "minimum": 0}, "k_factor": {"type": "number", "minimum": 0, "maximum": 1}})
def set_sheet_metal_parameters(args):
    changes = {}
    for key, member in (("thickness_mm", "Thickness"), ("bend_radius_mm", "BendRadius"), ("k_factor", "KFactor")):
        if key not in args:
            continue
        raw = float(args[key])
        if not math.isfinite(raw) or (raw <= 0 if key == "thickness_mm" else raw < 0) or (key == "k_factor" and raw > 1):
            raise RuntimeError(f"Invalid {key}.")
        changes[member] = to_m(raw) if key.endswith("_mm") else raw
    if not changes:
        raise RuntimeError("Specify at least one sheet-metal parameter to change.")
    _, doc = require_part()
    feature = _sheet_feature(doc, args.get("feature_name"))
    definition = flag_methods(value(feature, "GetDefinition"), "AccessSelections", "ReleaseSelectionAccess")
    if not definition.AccessSelections(doc, nothing()):
        return result(False, "Could not access the sheet-metal definition.")
    modified = False
    try:
        if args.get("feature_name"):
            flag_methods(definition, "SetOverrideDefaultParameter2")
            if "Thickness" in changes or "BendRadius" in changes:
                definition.SetOverrideDefaultParameter2(0, True)
            if "KFactor" in changes:
                definition.SetOverrideDefaultParameter2(1, True)
        for member, raw in changes.items():
            setattr(definition, member, raw)
        modified = bool(flag_methods(feature, "ModifyDefinition").ModifyDefinition(definition, doc, nothing()))
    finally:
        if not modified:
            value(definition, "ReleaseSelectionAccess")
    rebuild(doc)
    actual = _read_parameters(feature)
    correct = all(math.isclose(actual[key], float(args[key]), rel_tol=1e-6, abs_tol=1e-6) for key in ("thickness_mm", "bend_radius_mm", "k_factor") if key in args)
    problems = whats_wrong(doc)
    return result(modified and correct and not problems, "Updated sheet-metal parameters." if modified and correct else "Sheet-metal parameter readback differs or update failed.", parameters=actual, problems=problems)


@tool("set_flat_pattern", "Flatten or refold sheet metal by changing native FlatPattern suppression in the current configuration. Re-list topology afterwards.",
      {"flat": {"type": "boolean"}, "feature_name": {"type": "string"}}, ["flat"])
def set_flat_pattern(args):
    _, doc = require_part()
    features = _flat_features(doc)
    if args.get("feature_name"):
        features = [f for f in features if str(value(f, "Name")) == str(args["feature_name"])]
    if not features:
        return result(False, "No matching FlatPattern feature exists.")
    desired = not bool(args["flat"])
    actual = []
    for feature in features:
        flag_methods(feature, "SetSuppression2").SetSuppression2(0 if desired else 1, 1, empty_variant())
        rebuild(doc)
        actual.append({"feature": str(value(feature, "Name")), "suppressed": _suppressed(feature)})
    clear_selection(doc)
    problems = whats_wrong(doc)
    return result(all(item["suppressed"] == desired for item in actual) and not problems, "Updated flat-pattern state.", flat_patterns=actual, problems=problems)


@tool("export_flat_pattern", "Export a saved sheet-metal part's flat pattern as DXF under the output root. Optionally include bend lines. The source part must already be saved.",
      {"path": {"type": "string"}, "bend_lines": {"type": "boolean", "default": True}, "overwrite": {"type": "boolean", "default": False}}, ["path"])
def export_flat_pattern(args):
    _, doc = require_part()
    model = str(value(doc, "GetPathName") or "")
    if not model or not Path(model).is_file():
        return result(False, "Save the sheet-metal part before exporting its flat pattern.")
    if not any(bool(value(body, "IsSheetMetal")) for body in get_bodies(doc)):
        return result(False, "The active part has no sheet-metal bodies.")
    output = validated_output_path(str(args["path"]), {".dxf"}, bool(args.get("overwrite", False)))
    options = 1 | (4 if args.get("bend_lines", True) else 0)
    clear_selection(doc)
    exported = bool(flag_methods(doc, "ExportToDWG2").ExportToDWG2(str(output), model, 1, True, empty_variant(), False, False, options, empty_variant()))
    valid = output.is_file() and output.stat().st_size > 0
    if valid:
        text = output.read_text(encoding="ascii", errors="ignore")
        valid = "ENTITIES" in text and any(entity in text for entity in ("LINE", "POLYLINE", "ARC", "CIRCLE", "SPLINE"))
    return result(exported and valid, "Exported flat-pattern DXF." if exported and valid else "Flat-pattern export did not produce a valid DXF entity section.", path=str(output))
