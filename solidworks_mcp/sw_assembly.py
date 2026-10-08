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

"""Assembly work: components and mates.

create_new_document could already make an assembly, which was not much use
without a way to put anything in it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .sw_core import (
    MATE_ALIGNMENTS,
    MATE_TYPES,
    SELECTION_SCHEMA,
    apply_transform,
    byref_long,
    clear_selection,
    component_transform,
    document_info,
    flag_methods,
    iter_components,
    logger,
    mm_point,
    rebuild,
    require_assembly,
    require_selection,
    result,
    running_app,
    safe,
    to_deg,
    to_m,
    to_rad,
    to_mm,
    tool,
    value,
    whats_wrong,
)


ADD_MATE_ERRORS = {
    0: "unknown error",
    1: "no error",
    2: "incorrect mate type for that selection",
    3: "incorrect alignment",
    4: "incorrect selections",
    5: "the mate would over-define the assembly",
    6: "incorrect gear ratios",
}


@tool(
    "list_components",
    "Read-only: list the components of the active assembly with their file paths and positions.",
    {"top_level_only": {"type": "boolean", "default": True}},
)
def list_components(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_assembly()
    components = []
    for component in iter_components(doc, bool(args.get("top_level_only", True))):
        matrix = component_transform(component)
        entry: dict[str, Any] = {
            "name": str(safe(component, "Name2", "")),
            "path": str(safe(component, "GetPathName", "")),
            "suppressed": bool(safe(component, "IsSuppressed", False)),
            "fixed": bool(safe(component, "IsFixed", False)),
        }
        if matrix is not None:
            entry["origin_mm"] = mm_point(apply_transform([0.0, 0.0, 0.0], matrix))
        components.append(entry)
    return result(True, f"Read {len(components)} components.", components=components)


@tool(
    "insert_component",
    "Insert a part or sub-assembly file into the active assembly at the given position. "
    "Position is millimetres in assembly space.",
    {
        "path": {"type": "string", "description": "Full path to the .sldprt or .sldasm file."},
        "x_mm": {"type": "number", "default": 0},
        "y_mm": {"type": "number", "default": 0},
        "z_mm": {"type": "number", "default": 0},
        "configuration": {"type": "string", "description": "Named configuration to insert. Defaults to the active one."},
    },
    ["path"],
)
def insert_component(args: dict[str, Any]) -> dict[str, Any]:
    app, doc = require_assembly()
    path = Path(str(args["path"])).expanduser().resolve()
    if not path.is_file():
        return result(False, f"No such file: {path}")
    doc_type = {".sldprt": 1, ".sldasm": 2}.get(path.suffix.lower())
    if doc_type is None:
        return result(False, "Only .sldprt and .sldasm files can be inserted as components.")

    # AddComponent5 needs the referenced document loaded; open it silently first.
    errors, warnings = byref_long(0), byref_long(0)
    try:
        app.OpenDoc6(str(path), doc_type, 1, str(args.get("configuration", "") or ""), errors, warnings)
    except Exception:
        logger.info("Silent open of %s failed; letting AddComponent5 resolve it.", path)

    existing = str(args.get("configuration", "") or "")
    component = doc.AddComponent5(
        str(path), 0, "", False, existing,
        to_m(args.get("x_mm", 0)), to_m(args.get("y_mm", 0)), to_m(args.get("z_mm", 0)),
    )
    if component is None:
        return result(False, f"SOLIDWORKS did not insert {path.name} into the assembly.")
    rebuild(doc)
    return result(
        True,
        f"Inserted {path.name}.",
        component=str(safe(component, "Name2", "")),
        path=str(path),
    )


@tool(
    "add_mate",
    "Mate two entities in the active assembly. Select exactly two faces, edges, planes, or "
    "vertices via selection, then name the mate type. Distance is millimetres, angle is degrees.",
    {
        "mate_type": {"type": "string", "enum": sorted(MATE_TYPES)},
        "selection": SELECTION_SCHEMA,
        "alignment": {"type": "string", "enum": sorted(MATE_ALIGNMENTS), "default": "closest"},
        "distance_mm": {"type": "number", "default": 0, "description": "Used by mate_type=distance."},
        "angle_deg": {"type": "number", "default": 0, "description": "Used by mate_type=angle."},
        "flip": {"type": "boolean", "default": False},
        "lock_rotation": {"type": "boolean", "default": False},
    },
    ["mate_type", "selection"],
)
def add_mate(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_assembly()
    count = require_selection(doc, args["selection"])
    mate_type = MATE_TYPES[str(args["mate_type"])]
    alignment = MATE_ALIGNMENTS[str(args.get("alignment", "closest"))]
    status = byref_long(0)

    mate = doc.AddMate5(
        mate_type, alignment, bool(args.get("flip", False)),
        to_m(args.get("distance_mm", 0)), to_m(args.get("distance_mm", 0)), to_m(args.get("distance_mm", 0)),
        1.0, 1.0,
        to_rad(args.get("angle_deg", 0)), to_rad(args.get("angle_deg", 0)), to_rad(args.get("angle_deg", 0)),
        False, bool(args.get("lock_rotation", False)), 0, status,
    )
    code = int(status.value)
    clear_selection(doc)
    rebuild(doc)
    if mate is None:
        return result(
            False,
            f"SOLIDWORKS refused the mate: {ADD_MATE_ERRORS.get(code, f'status {code}')}.",
            entities=count,
            status=code,
        )
    if code != 1:
        # An over-defining mate is still added to the tree, so reporting a
        # plain failure here would contradict what list_mates shows.
        return result(
            True,
            f"Added a {args['mate_type']} mate, but SOLIDWORKS flagged it: "
            f"{ADD_MATE_ERRORS.get(code, f'status {code}')}.",
            mate=str(safe(mate, "Name", "")),
            entities=count,
            warning=ADD_MATE_ERRORS.get(code, f"status {code}"),
            status=code,
        )
    return result(
        True,
        f"Added a {args['mate_type']} mate.",
        mate=str(safe(mate, "Name", "")),
        entities=count,
    )


@tool(
    "list_mates",
    "Read-only: list the mates of the active assembly.",
    {},
)
def list_mates(args: dict[str, Any]) -> dict[str, Any]:
    from .sw_core import as_list, feature_property, iter_feature_objects

    _, doc = require_assembly()
    reverse_types = {v: k for k, v in MATE_TYPES.items()}
    mates = []
    for feature in iter_feature_objects(doc):
        if str(feature_property(feature, "GetTypeName2", "")) != "MateGroup":
            continue
        child = safe(feature, "GetFirstSubFeature")
        while child is not None:
            definition = safe(child, "GetSpecificFeature2")
            entry: dict[str, Any] = {"name": str(feature_property(child, "Name", ""))}
            if definition is not None:
                code = safe(definition, "Type")
                if code is not None:
                    entry["mate_type"] = reverse_types.get(int(code), f"type_{int(code)}")
                distance = safe(definition, "Distance")
                if distance is not None:
                    entry["distance_mm"] = round(to_mm(float(distance)), 6)
                angle = safe(definition, "Angle")
                if angle is not None:
                    entry["angle_deg"] = round(to_deg(float(angle)), 6)
            mates.append(entry)
            child = safe(child, "GetNextSubFeature")
    return result(True, f"Read {len(mates)} mates.", mates=mates)


@tool(
    "set_component_fixed",
    "Fix or float the named components in the active assembly.",
    {
        "component_names": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "fixed": {"type": "boolean", "default": True},
    },
    ["component_names"],
)
def set_component_fixed(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_assembly()
    names = [str(name) for name in args["component_names"]]
    require_selection(doc, {"components": names})
    if bool(args.get("fixed", True)):
        doc.FixComponent()
    else:
        doc.UnfixComponent()
    clear_selection(doc)
    rebuild(doc)
    return result(True, f"Set fixed={args.get('fixed', True)} on {len(names)} components.", components=names)


def _named_component(doc: Any, name: str) -> Any:
    for component in iter_components(doc):
        if str(safe(component, "Name2", "")) == name:
            return component
    raise RuntimeError(f"No component named '{name}'. Use list_components first.")


def _component_result(doc: Any, ok: bool, message: str, **data: Any) -> dict[str, Any]:
    rebuild(doc)
    problems = whats_wrong(doc)
    return result(ok and not problems, message, problems=problems, **data)


@tool("set_component_visibility", "Show or hide a named assembly component without suppressing it.",
      {"name": {"type": "string"}, "visible": {"type": "boolean"}}, ["name", "visible"])
def set_component_visibility(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_assembly()
    component = _named_component(doc, str(args["name"]))
    desired = 1 if args["visible"] else 0
    component.Visible = desired
    actual = int(value(component, "Visible"))
    return _component_result(doc, actual == desired, "Updated component visibility.", name=args["name"], visible=actual == 1)


@tool("set_component_suppression", "Suppress or fully resolve a named assembly component in the active configuration.",
      {"name": {"type": "string"}, "suppressed": {"type": "boolean"}}, ["name", "suppressed"])
def set_component_suppression(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_assembly()
    component = flag_methods(_named_component(doc, str(args["name"])), "SetSuppression2")
    code = int(component.SetSuppression2(0 if args["suppressed"] else 2))
    actual = bool(value(component, "IsSuppressed"))
    return _component_result(doc, actual == bool(args["suppressed"]), "Updated component suppression.", name=args["name"], suppressed=actual, status=code)


@tool("set_component_configuration", "Switch a component to an existing configuration in its referenced part/subassembly, then rebuild.",
      {"name": {"type": "string"}, "configuration": {"type": "string", "minLength": 1}}, ["name", "configuration"])
def set_component_configuration(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_assembly()
    component = _named_component(doc, str(args["name"]))
    model = value(component, "GetModelDoc2")
    if model is None:
        return result(False, "Resolve the component before changing its configuration.")
    name = str(args["configuration"])
    names = [str(n) for n in value(model, "GetConfigurationNames") or []]
    if name not in names:
        return result(False, "Referenced configuration does not exist.", available_configurations=names)
    component.ReferencedConfiguration = name
    rebuild(doc)
    actual = str(value(component, "ReferencedConfiguration"))
    return _component_result(doc, actual == name, "Updated component configuration.", name=args["name"], configuration=actual)
