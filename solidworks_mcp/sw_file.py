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

"""Session status, document lifecycle, saving, exporting, and appearance."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pythoncom
import win32com.client

from .sw_core import (
    as_list,
    active_document,
    byref_long,
    clear_selection,
    document_info,
    document_type,
    double_array,
    EXPORT_EXTENSIONS,
    extension,
    invoke_no_arg,
    logger,
    nothing,
    OUTPUT_ROOT,
    rebuild,
    require_part,
    result,
    running_app,
    safe,
    discover_template,
    tool,
    value,
    whats_wrong,
)


def validated_output_path(path: str, allowed_extensions: set[str], allow_overwrite: bool = False) -> Path:
    candidate = Path(path).expanduser()
    # A relative path means "inside the output root", which is where everything
    # has to land anyway; the server has no meaningful working directory.
    if not candidate.is_absolute():
        candidate = OUTPUT_ROOT / candidate
    output = candidate.resolve()
    if output.suffix.lower() not in allowed_extensions:
        extensions = ", ".join(sorted(allowed_extensions))
        raise RuntimeError(f"Unsupported output extension. Allowed: {extensions}.")
    try:
        output.relative_to(OUTPUT_ROOT)
    except ValueError as exc:
        raise RuntimeError(f"For safety, output path must be under {OUTPUT_ROOT}.") from exc
    if output.exists() and not allow_overwrite:
        raise RuntimeError(f"Refusing to overwrite existing output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def _save_as(doc: Any, path: str) -> bool:
    output = Path(path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    errors, warnings = byref_long(0), byref_long(0)

    # SOLIDWORKS refuses SaveAs onto a path that is already open — including
    # this very document's own path — so that case is a plain Save.
    current = str(value(doc, "GetPathName") or "")
    if current and Path(current).resolve() == output:
        try:
            if bool(doc.Save3(1, errors, warnings)) and int(errors.value) == 0:
                return True
        except Exception:
            logger.info("Save3 on the document's own path failed; trying SaveAs")

    try:
        if bool(extension(doc).SaveAs(str(output), 0, 0, nothing(), errors, warnings)) and int(errors.value) == 0:
            return True
    except Exception:
        logger.info("Extension.SaveAs failed; falling back to ModelDoc2.SaveAs")
    try:
        return bool(doc.SaveAs(str(output)))
    except Exception:
        return False


@tool(
    "solidworks_status",
    "Read-only: verify attachment to the running SOLIDWORKS instance and report active-document status.",
    {},
)
def solidworks_status(args: dict[str, Any]) -> dict[str, Any]:
    app = running_app()
    active = app.ActiveDoc
    payload = result(
        True,
        "Attached to the running SOLIDWORKS session.",
        version=str(value(app, "RevisionNumber")),
        document_count=int(value(app, "GetDocumentCount")),
        output_root=str(OUTPUT_ROOT),
    )
    if active is not None:
        payload["data"]["active_document"] = document_info(active)
    return payload


@tool(
    "get_active_document_info",
    "Read-only: return the active SOLIDWORKS document title, path, type, and unsaved state.",
    {},
)
def get_active_document_info(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    return result(True, "Read active document.", document=document_info(doc))


@tool(
    "create_new_document",
    "Create a new SOLIDWORKS part, assembly, or drawing from the configured default template.",
    {"kind": {"type": "string", "enum": ["part", "assembly", "drawing"]}},
    ["kind"],
)
def create_new_document(args: dict[str, Any]) -> dict[str, Any]:
    return new_document(str(args["kind"]))


def new_document(kind: str) -> dict[str, Any]:
    app = running_app()
    preference = {"part": 1, "assembly": 2, "drawing": 3}.get(kind)
    if preference is None:
        return result(False, "kind must be part, assembly, or drawing.")
    template = app.GetUserPreferenceStringValue(preference)
    if not template or not Path(str(template)).is_file():
        fallback = discover_template(kind)
        if fallback is None:
            return result(
                False,
                f"No default {kind} template is configured in SOLIDWORKS and none could be found on disk. "
                "Set one under Tools > Options > File Locations, or point SW_MCP_TEMPLATE_DIR at your templates.",
            )
        template = str(fallback)
    doc = app.NewDocument(template, 0, 0.0, 0.0)
    if doc is None:
        return result(False, f"SOLIDWORKS did not create a {kind} document.")
    return result(True, f"Created new {kind} document.", document=document_info(doc))


@tool(
    "open_document",
    "Open an existing .sldprt, .sldasm, or .slddrw file and make it the active document.",
    {"path": {"type": "string"}},
    ["path"],
)
def open_document(args: dict[str, Any]) -> dict[str, Any]:
    app = running_app()
    path = Path(str(args["path"])).expanduser().resolve()
    if not path.is_file():
        return result(False, f"No such file: {path}")
    doc_type = {".sldprt": 1, ".sldasm": 2, ".slddrw": 3}.get(path.suffix.lower())
    if doc_type is None:
        return result(False, "Only .sldprt, .sldasm, and .slddrw files can be opened.")
    errors, warnings = byref_long(0), byref_long(0)
    doc = app.OpenDoc6(str(path), doc_type, 0, "", errors, warnings)
    if doc is None:
        return result(False, f"SOLIDWORKS could not open {path}.", error_code=int(errors.value))
    # OpenDoc6 returns an already-open document without activating it.  That
    # left the next tool operating on whichever drawing or part happened to be
    # active, despite this tool promising otherwise.
    activation_errors = byref_long(0)
    activated = bool(app.ActivateDoc3(str(path), False, 0, activation_errors))
    if not activated:
        return result(
            False,
            f"Opened {path}, but SOLIDWORKS could not make it active.",
            error_code=int(activation_errors.value),
            document=document_info(doc),
        )
    return result(True, f"Opened and activated {path}.", document=document_info(app.ActiveDoc))


@tool(
    "save_document",
    "Save the active document as a new .sldprt, .sldasm, or .slddrw file under the designated "
    "outputs folder. Existing files are never overwritten unless overwrite is true.",
    {"path": {"type": "string"}, "overwrite": {"type": "boolean", "default": False}},
    ["path"],
)
def save_document(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    output = validated_output_path(
        str(args["path"]), {".sldprt", ".sldasm", ".slddrw"}, bool(args.get("overwrite", False))
    )
    clear_selection(doc)
    saved = _save_as(doc, str(output))
    if saved:
        return result(True, f"Saved document to {output}.", path=str(output))
    return result(False, _save_failure_reason(doc, output), path=str(output))


def _save_failure_reason(doc: Any, output: Path) -> str:
    """SOLIDWORKS refuses a save-as onto a file it already has open elsewhere."""
    try:
        for other in as_list(value(running_app(), "GetDocuments")):
            if other is doc:
                continue
            existing = str(value(other, "GetPathName") or "")
            if existing and Path(existing).resolve() == output:
                title = str(value(other, "GetTitle"))
                return (
                    f"SOLIDWORKS already has '{title}' open from {output}, and will not save over "
                    "a document that is open. Close that document first, or save to another name."
                )
    except Exception:
        pass
    return "SOLIDWORKS failed to save the document."


@tool(
    "save_active_document",
    "Save changes to the active document's existing file path. This cannot choose another path.",
    {},
)
def save_active_document(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    path = str(value(doc, "GetPathName") or "")
    if not path:
        return result(False, "The active document has no saved path; use save_document first.")
    errors, warnings = byref_long(0), byref_long(0)
    saved = bool(doc.Save3(1, errors, warnings)) and int(errors.value) == 0
    return result(
        saved,
        f"Saved active document to {path}." if saved else "SOLIDWORKS failed to save the active document.",
        path=path,
    )


@tool(
    "export_document",
    "Export the active document to STEP/STP, IGES/IGS, STL, Parasolid X_T/X_B, 3MF, or an image of "
    "the current view, under the designated outputs folder.",
    {"path": {"type": "string"}, "overwrite": {"type": "boolean", "default": False}},
    ["path"],
)
def export_document(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    output = validated_output_path(str(args["path"]), EXPORT_EXTENSIONS, bool(args.get("overwrite", False)))
    clear_selection(doc)
    exported = _save_as(doc, str(output))
    return result(
        exported,
        f"Exported document to {output}." if exported else "SOLIDWORKS failed to export the document.",
        path=str(output),
    )


@tool(
    "rebuild_document",
    "Force a rebuild of the active document and report any feature that fails.",
    {"force_all": {"type": "boolean", "default": False, "description": "Rebuild every feature, not just the stale ones."}},
)
def rebuild_document(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = active_document()
    if bool(args.get("force_all", False)):
        try:
            rebuilt = bool(extension(doc).ForceRebuildAll())
        except Exception:
            rebuilt = rebuild(doc)
    else:
        rebuilt = rebuild(doc)
    problems = whats_wrong(doc)
    return result(
        rebuilt and not problems,
        "Rebuilt the active document." if rebuilt and not problems else "The rebuild reported problems.",
        problems=problems,
    )


@tool(
    "set_appearance",
    "Set the display colour of the active part, or of the selected faces or features. "
    "RGB components are 0-255.",
    {
        "red": {"type": "integer", "minimum": 0, "maximum": 255},
        "green": {"type": "integer", "minimum": 0, "maximum": 255},
        "blue": {"type": "integer", "minimum": 0, "maximum": 255},
        "transparency": {"type": "number", "minimum": 0, "maximum": 1, "default": 0},
        "selection": {
            "type": "object",
            "description": "Optional: colour only these faces. Omit to colour the whole model.",
            "properties": {"faces": {"type": "array", "items": {"type": "integer"}}},
        },
    },
    ["red", "green", "blue"],
)
def set_appearance(args: dict[str, Any]) -> dict[str, Any]:
    from .sw_core import as_list, enumerate_faces, get_bodies, safe

    _, doc = require_part()
    values = (
        int(args["red"]) / 255.0, int(args["green"]) / 255.0, int(args["blue"]) / 255.0,
        1.0, 1.0, 0.4, 0.3, float(args.get("transparency", 0)), 0.0,
    )
    face_indices = (args.get("selection") or {}).get("faces")
    if face_indices:
        faces = [f for body in get_bodies(doc) for f in as_list(safe(body, "GetFaces"))]
        for raw_index in face_indices:
            index = int(raw_index)
            if not 0 <= index < len(faces):
                return result(False, f"Face index {index} is out of range (0..{len(faces) - 1}).")
            faces[index].MaterialPropertyValues = double_array(values)
        target = f"{len(face_indices)} faces"
    else:
        doc.MaterialPropertyValues = double_array(values)
        target = "the whole model"
    try:
        invoke_no_arg(doc, "GraphicsRedraw2")
    except Exception:
        pass
    return result(True, f"Applied the colour to {target}.", rgb=[args["red"], args["green"], args["blue"]])


def _material_readback(doc: Any, database: str) -> str:
    """Read the assigned material name back, across the API's several shapes.

    GetMaterialPropertyName2 takes the database as an [in, out] string.  Given
    a plain Python string, SOLIDWORKS 2016 returns nothing even though the
    material was applied (the mass changes), so the typed by-reference form is
    tried first.  MaterialIdName, stable since 2008, reports "database|name"
    and is the last resort.
    """
    for database_arg in (
        win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, database),
        database,
    ):
        try:
            raw = doc.GetMaterialPropertyName2("", database_arg)
        except Exception:
            continue
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else ""
        name = str(raw or "")
        if name:
            return name
    ident = str(safe(doc, "MaterialIdName", "") or "")
    return ident.split("|", 1)[1] if "|" in ident else ident


@tool(
    "set_material",
    "Assign a SOLIDWORKS material to the active part, which is what makes get_mass_properties "
    "report a real mass. Example: database 'SOLIDWORKS Materials', name 'AISI 1020'.",
    {
        "name": {"type": "string", "description": "Material name exactly as it appears in the material library."},
        "database": {"type": "string", "default": "SOLIDWORKS Materials"},
    },
    ["name"],
)
def set_material(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_part()
    name = str(args["name"])
    database = str(args.get("database", "SOLIDWORKS Materials"))
    try:
        doc.SetMaterialPropertyName2("", database, name)
    except Exception as exc:
        return result(False, f"SOLIDWORKS rejected that material: {exc}")
    applied = _material_readback(doc, database)
    rebuild(doc)
    # An unknown material name is accepted silently and leaves the part
    # unassigned, so treat anything but an exact read-back as a failure.
    if applied != name:
        return result(
            False,
            f"SOLIDWORKS did not apply '{name}'; the part reports "
            f"'{applied or '<unspecified>'}'. Check the exact name in the material library.",
            material=applied,
        )
    return result(True, f"Applied material '{name}'.", material=applied)
