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

"""Shared foundation for the solidworks-mcp MCP bridge.

Everything that touches COM, converts units, walks the feature tree, or turns a
declarative selection specification into a real SOLIDWORKS selection lives here.

Unit contract at the MCP boundary
---------------------------------
Lengths are millimetres, angles are degrees, and every tool parameter carries
that in its name (``depth_mm``, ``angle_deg``).  The SOLIDWORKS COM API is
metres and radians throughout, so conversion happens once, at this boundary.
"""

from __future__ import annotations

import logging
import math
import os
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

import pythoncom
import win32com.client
from mcp.types import Tool


LOG_PATH = Path(__file__).with_name("server.log")
# Rotating rather than plain: this server runs for as long as the SOLIDWORKS
# session does and every COM failure logs a traceback, so an unbounded file
# just grows -- it had reached 1.6 MB before this was capped.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8"),
    ],
)
logger = logging.getLogger("solidworks-mcp")


# --------------------------------------------------------------------------
# Constants taken from the installed swconst.tlb
# --------------------------------------------------------------------------

DOC_TYPES = {0: "none", 1: "part", 2: "assembly", 3: "drawing"}

# swEndConditions_e
END_CONDITIONS = {
    "blind": 0,
    "through_all": 1,
    "through_next": 2,
    "up_to_vertex": 3,
    "up_to_surface": 4,
    "offset_from_surface": 5,
    "mid_plane": 6,
    "up_to_body": 7,
    "through_all_both": 9,
}

# swSurfaceTypes_e
SURFACE_TYPES = {
    4001: "plane", 4002: "cylinder", 4003: "cone", 4004: "sphere",
    4005: "torus", 4006: "bsurface", 4007: "blend", 4008: "offset",
    4009: "extruded", 4010: "revolved",
}

# swCurveTypes_e
CURVE_TYPES = {
    3001: "line", 3002: "circle", 3003: "ellipse", 3004: "intersection",
    3005: "bcurve", 3006: "spcurve", 3008: "constparam", 3009: "trimmed",
}

# swBodyType_e
BODY_SOLID = 0
BODY_SHEET = 1
BODY_ALL = -1

# swFeatureFilletType_e / swFeatureFilletOptions_e
FILLET_TYPE_SIMPLE = 0
FILLET_PROPAGATE = 1
FILLET_UNIFORM_RADIUS = 2
FILLET_KEEP_FEATURES = 128

# swChamferType_e
CHAMFER_ANGLE_DISTANCE = 1
CHAMFER_DISTANCE_DISTANCE = 2
CHAMFER_EQUAL_DISTANCE = 16
CHAMFER_TANGENT_PROPAGATION = 4  # swFeatureChamferOption_e

# swRefPlaneReferenceConstraints_e
PLANE_PARALLEL = 1
PLANE_PERPENDICULAR = 2
PLANE_COINCIDENT = 4
PLANE_DISTANCE = 8
PLANE_ANGLE = 16
PLANE_MIDPLANE = 128
PLANE_FLIP = 256

# swMateType_e
MATE_TYPES = {
    "coincident": 0, "concentric": 1, "perpendicular": 2, "parallel": 3,
    "tangent": 4, "distance": 5, "angle": 6, "symmetric": 8, "width": 11,
    "lock": 16, "slot": 21, "profile_center": 24,
}

# swMateAlign_e
MATE_ALIGNMENTS = {"aligned": 0, "anti_aligned": 1, "closest": 2}

# swSketchTrimChoice_e
TRIM_CHOICES = {
    "closest": 0, "corner": 1, "two_entities": 2, "entity_point": 3,
    "entities": 4, "outside": 5, "inside": 6,
}

# Selection type strings understood by ModelDocExtension::SelectByID2
SELECT_TYPE_FACE = "FACE"
SELECT_TYPE_EDGE = "EDGE"
SELECT_TYPE_VERTEX = "VERTEX"

# Every file this server writes lands under one directory, so a stray path in
# a tool call cannot scatter output across the user's disk.  Override it with
# SW_MCP_OUTPUT_ROOT.
OUTPUT_ROOT = Path(
    os.environ.get("SW_MCP_OUTPUT_ROOT") or (Path.home() / "Documents" / "solidworks-mcp")
).expanduser().resolve()

EXPORT_EXTENSIONS = {".step", ".stp", ".iges", ".igs", ".stl", ".x_t", ".x_b", ".png", ".jpg", ".bmp", ".3mf"}

TEMPLATE_SUFFIXES = {"part": ".prtdot", "assembly": ".asmdot", "drawing": ".drwdot"}


def discover_template(kind: str) -> Path | None:
    """Find a document template when SOLIDWORKS reports none configured.

    Template names are localized (gb_part.prtdot, Part.prtdot, ...) and the
    install directory carries the release year, so both are discovered rather
    than hard-coded.  SW_MCP_TEMPLATE_DIR overrides the search.
    """
    suffix = TEMPLATE_SUFFIXES.get(kind)
    if suffix is None:
        return None

    roots: list[Path] = []
    override = os.environ.get("SW_MCP_TEMPLATE_DIR")
    if override:
        roots.append(Path(override).expanduser())
    program_data = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "SOLIDWORKS"
    if program_data.is_dir():
        # Newest release first, so a 2026 install wins over a leftover 2023 one.
        roots.extend(sorted((p for p in program_data.glob("SOLIDWORKS *") if p.is_dir()), reverse=True))
    roots.append(Path.home() / "Documents" / "SOLIDWORKS Templates")

    for root in roots:
        if not root.is_dir():
            continue
        for candidate in sorted(root.rglob(f"*{suffix}")):
            if candidate.is_file():
                return candidate
    return None


# --------------------------------------------------------------------------
# Tool registry.  Every module registers into these two shared containers and
# server.py merges them, so adding a tool never means editing a dispatch table.
# --------------------------------------------------------------------------

TOOLS: list[Tool] = []
HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {}


def tool(
    name: str,
    description: str,
    properties: dict[str, Any] | None = None,
    required: Sequence[str] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register one MCP tool and its handler."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        TOOLS.append(
            Tool(
                name=name,
                description=description,
                inputSchema={
                    "type": "object",
                    "properties": properties or {},
                    "required": list(required or []),
                },
            )
        )
        HANDLERS[name] = fn
        return fn

    return decorator


# --------------------------------------------------------------------------
# Result envelope
# --------------------------------------------------------------------------


def result(ok: bool, message: str, **data: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": ok, "message": message}
    if data:
        payload["data"] = data
    return payload


def com_error(exc: Exception) -> dict[str, Any]:
    logger.exception("SOLIDWORKS COM failure")
    return result(False, f"SOLIDWORKS COM error: {exc}")


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------


def to_m(value: float) -> float:
    return float(value) / 1000.0


def to_mm(value: float) -> float:
    return float(value) * 1000.0


def to_rad(value: float) -> float:
    return math.radians(float(value))


def to_deg(value: float) -> float:
    return math.degrees(float(value))


def mm_point(point: Iterable[float]) -> list[float]:
    return [round(to_mm(v), 6) for v in point]


# --------------------------------------------------------------------------
# Late-binding helpers
# --------------------------------------------------------------------------


def value(obj: Any, member: str) -> Any:
    """Read a zero-argument SOLIDWORKS COM member through late binding.

    This type library exposes such members inconsistently: pywin32 surfaces
    some as plain properties and others as bound methods, and which is which
    varies by interface (``IBody2.GetFaces`` is a method, ``IFace2.GetArea`` a
    property).  Reading the attribute without invoking it silently yields a
    bound-method object that then looks like a one-element result, so always
    invoke ordinary callables here.  COM dispatch objects are themselves
    callable, hence the ``_oleobj_`` guard.
    """
    member_value = getattr(obj, member)
    if callable(member_value) and not hasattr(member_value, "_oleobj_"):
        return member_value()
    return member_value


# Retained under its original name: the two access paths are now identical.
invoke_no_arg = value


# Members that only some document types carry, per the installed sldworks.tlb.
# They are still offered to every document, because attempting one costs a
# single failed lookup while skipping one that turned out to be present costs a
# silent None or a crashed SOLIDWORKS.  Their absence is expected, so it must
# not be reported as a defect -- otherwise every part document would warn about
# the assembly members and the log would stop being worth reading.
_DOCUMENT_SPECIFIC_METHODS = frozenset(
    {
        # IPartDoc
        "SetMaterialPropertyName2", "GetMaterialPropertyName2", "GetPartBox", "GetBodies2",
        # IAssemblyDoc
        "GetBox", "AddComponent5", "AddMate5", "GetComponents",
        # Absent from some selectable interfaces
        "Select4",
    }
)

# Names this build would not resolve.  Held only to keep the warning to once
# per name: flagging is still attempted on every call, because the same name
# can be absent on one interface and present on another.
_UNFLAGGABLE_NAMES: set[str] = set()


def flag_methods(obj: Any, *names: str) -> Any:
    """Force pywin32 to treat these members as methods, not properties.

    Most SOLIDWORKS members are declared in the type library as PROPGET *with
    arguments*.  Late-bound pywin32 is free to resolve such a member on plain
    attribute access by invoking it with no arguments, and SOLIDWORKS does not
    defend against that: ``SketchManager.CreateSpline`` quietly evaluates to
    None, and ``ModelDocExtension.AddDimension`` takes the whole application
    down.  Flagging is one GetIDsOfNames per name and removes the ambiguity, so
    every member we call with arguments goes through here first.

    Each name is flagged on its own.  ``_FlagAsMethod`` loops over its arguments
    and resolves each one through GetIDsOfNames, so the first name a given build
    does not expose raises and abandons every name after it in the list -- and
    the exception was swallowed here without naming anything.  One member
    renamed between releases would then leave the tail of a twenty-name list
    unflagged, producing exactly the silent-None and crash-SOLIDWORKS failures
    this function exists to prevent.  Flagging one at a time costs the same
    round trips as the batched call, since the batch looped anyway.
    """
    for name in names:
        try:
            obj._FlagAsMethod(name)
        except Exception:
            # Deliberately not logging the object: repr() on a COM proxy can
            # itself dispatch, and this runs on the failure path.
            if name in _DOCUMENT_SPECIFIC_METHODS:
                logger.debug("'%s' is not exposed by this object, as expected.", name)
            elif name not in _UNFLAGGABLE_NAMES:
                _UNFLAGGABLE_NAMES.add(name)
                logger.warning(
                    "Could not flag '%s' as a method. Calls passing arguments to it may return "
                    "None or destabilise SOLIDWORKS. Check it against the installed type library "
                    "with tools/tlb_probe.py.",
                    name,
                )
    return obj


_SKETCH_MANAGER_METHODS = (
    "CreateLine", "CreateCenterLine", "CreateCircleByRadius", "CreateArc", "Create3PointArc",
    "CreateTangentArc", "CreateEllipse", "CreatePolygon", "CreateSketchSlot", "CreatePoint",
    "CreateSpline", "CreateCornerRectangle", "InsertSketch", "SketchTrim", "SketchUseEdge3",
)

_FEATURE_MANAGER_METHODS = (
    "FeatureExtrusion3", "FeatureCut4", "FeatureRevolve2", "FeatureFillet3",
    "InsertFeatureChamfer", "InsertRib", "InsertMultiFaceDraft", "SimpleHole2",
    "InsertProtrusionSwept4", "InsertCutSwept5", "InsertProtrusionBlend2", "InsertCutBlend",
    "FeatureLinearPattern5", "FeatureCircularPattern5", "InsertMirrorFeature2", "InsertRefPlane",
    # Older variants that call_versioned falls back to on builds without the
    # newer names above (SOLIDWORKS 2016 has FeatureCut3 but no FeatureCut4).
    # Newer builds keep the old names, so flagging them costs nothing there.
    "FeatureCut3", "InsertProtrusionSwept3", "InsertCutSwept4",
    "FeatureLinearPattern4", "FeatureCircularPattern4",
)

_EXTENSION_METHODS = (
    "SelectByID2", "SelectByRay", "AddDimension", "DeleteSelection2", "SaveAs", "SaveAs3",
    "GetMassProperties2", "GetWhatsWrong",
)

# Grouped by the interface the installed sldworks.tlb declares them on.  The
# whole list is offered to every document regardless: the type library describes
# interfaces, not what a given SOLIDWORKS dispatch actually exposes, and a name
# that is present but unflagged is the expensive mistake here, not a name that
# is absent and fails a lookup.  Names in the two document-specific groups are
# also listed in _DOCUMENT_SPECIFIC_METHODS so their absence stays quiet.
_MODEL_DOC_METHODS = (
    # IModelDoc2
    "ClearSelection2", "InsertSketch2", "SketchFillet2", "SketchChamfer", "SketchMirror", "SketchOffset2",
    "InsertFeatureShell", "InsertAxis2", "ShowNamedView2", "Parameter", "Save3", "SaveAs",
    "Insert3DSketch2", "Insert3DSketch",
    # IPartDoc
    "SetMaterialPropertyName2", "GetMaterialPropertyName2", "GetPartBox", "GetBodies2",
    # IAssemblyDoc
    "GetBox", "AddComponent5", "AddMate5", "GetComponents",
)

_APP_METHODS = ("NewDocument", "OpenDoc6", "ActivateDoc3", "CloseDoc", "GetUserPreferenceStringValue")


def sketch_manager(doc: Any) -> Any:
    return flag_methods(doc.SketchManager, *_SKETCH_MANAGER_METHODS)


def feature_manager(doc: Any) -> Any:
    return flag_methods(doc.FeatureManager, *_FEATURE_MANAGER_METHODS)


def extension(doc: Any) -> Any:
    return flag_methods(doc.Extension, *_EXTENSION_METHODS)


def selectable(obj: Any) -> Any:
    return flag_methods(obj, "Select2", "Select4")


def call_versioned(obj: Any, *candidates: tuple[str, Sequence[Any]]) -> Any:
    """Call the first member of ``obj`` that this SOLIDWORKS build exposes.

    Each release adds numbered variants -- FeatureCut4 over FeatureCut3 -- whose
    argument list extends the older one, while the older name keeps working.
    List the newest first with its full arguments and the older ones with the
    prefix they take; a build that lacks the newer name (SOLIDWORKS 2016 has
    no FeatureCut4) silently gets the older call, and a build that has it never
    pays for the fallback. Method availability is checked on this object:
    a failed flag on another interface does not prove this member is absent.
    """
    last: Exception | None = None
    for name, args in candidates:
        try:
            method = getattr(obj, name)
        except AttributeError as exc:
            _UNFLAGGABLE_NAMES.add(name)
            last = exc
            continue
        return method(*args)
    names = ", ".join(name for name, _ in candidates)
    raise RuntimeError(f"This SOLIDWORKS build exposes none of: {names}.") from last


def safe(obj: Any, member: str, default: Any = None) -> Any:
    try:
        return value(obj, member)
    except Exception:
        return default


def nothing() -> Any:
    """A typed VT_DISPATCH null, which SOLIDWORKS requires for optional objects."""
    return win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)


def empty_variant() -> Any:
    return win32com.client.VARIANT(pythoncom.VT_EMPTY, None)


def double_array(values: Sequence[float]) -> Any:
    """Pass doubles as a VT_R8 SAFEARRAY rather than a loosely typed tuple.

    Late-bound pywin32 otherwise marshals them incorrectly for several
    SOLIDWORKS setters (the appearance setter being the classic offender).
    """
    return win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, tuple(float(v) for v in values))


def dispatch_array(values: Sequence[Any]) -> Any:
    return win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, tuple(values))


def integer_array(values: Sequence[int]) -> Any:
    return win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_I4, tuple(int(v) for v in values))


def byref_long(initial: int = 0) -> Any:
    return win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, initial)


def byref_variant() -> Any:
    return win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_VARIANT, None)


def byref_double(initial: float = 0) -> Any:
    return win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_R8, float(initial))


def byref_dispatch() -> Any:
    return win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_DISPATCH, None)


def persistent_reference_id(doc: Any, obj: Any) -> bytes:
    raw = flag_methods(value(doc, "Extension"), "GetPersistReference3").GetPersistReference3(obj)
    # Freeze native memoryviews before a rollback, rebuild or edit changes them.
    return bytes(raw) if raw is not None else b""


def as_list(com_array: Any) -> list[Any]:
    """Normalise the several shapes SOLIDWORKS uses for 'array or nothing'."""
    if com_array is None:
        return []
    if isinstance(com_array, (list, tuple)):
        return [item for item in com_array if item is not None]
    return [com_array]


# --------------------------------------------------------------------------
# Application and documents
# --------------------------------------------------------------------------


_SESSION_PID: int | None = None


def selected_session_pid() -> int:
    if _SESSION_PID is not None:
        return _SESSION_PID
    raw = os.environ.get("SW_MCP_SESSION_PID", "0")
    try:
        pid = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("SW_MCP_SESSION_PID must be a non-negative process ID.") from exc
    if pid < 0:
        raise RuntimeError("SW_MCP_SESSION_PID must be a non-negative process ID.")
    return pid


def session_monikers() -> list[tuple[int, Any]]:
    pythoncom.CoInitialize()
    rot = pythoncom.GetRunningObjectTable()
    context = pythoncom.CreateBindCtx(0)
    sessions = []
    for moniker in rot:
        try:
            name = moniker.GetDisplayName(context, None)
        except pythoncom.com_error:
            continue
        prefix = "SolidWorks_PID_"
        if name.startswith(prefix) and name[len(prefix):].isdigit():
            sessions.append((int(name[len(prefix):]), moniker))
    return sorted(sessions, key=lambda item: item[0])


def session_app(pid: int) -> Any:
    pythoncom.CoInitialize()
    if pid == 0:
        return flag_methods(win32com.client.GetActiveObject("SldWorks.Application"), *_APP_METHODS)
    for candidate, moniker in session_monikers():
        if candidate == pid:
            dispatch = pythoncom.GetRunningObjectTable().GetObject(moniker).QueryInterface(pythoncom.IID_IDispatch)
            return flag_methods(win32com.client.Dispatch(dispatch), *_APP_METHODS)
    raise RuntimeError(f"SolidWorks session {pid} is no longer registered. List sessions and select an existing process ID.")


def running_app() -> Any:
    """Attach only to an already-running SOLIDWORKS session; never start one."""
    pythoncom.CoInitialize()
    pid = selected_session_pid()
    try:
        app = session_app(pid)
    except Exception as exc:
        if pid:
            raise RuntimeError(f"Cannot attach to selected SolidWorks session {pid}; no other session was substituted.") from exc
        raise RuntimeError(
            "No running SOLIDWORKS session is available. Open SOLIDWORKS and finish any modal dialogs first."
        ) from exc
    return flag_methods(app, *_APP_METHODS)


# swUserPreferenceToggle_e.swInputDimValOnCreate.  When this is on — and it is
# on by default — every dimension SOLIDWORKS creates pops a modal "Modify" box.
# A modal dialog blocks the COM call that opened it, which would wedge this
# server permanently, so dimension tools turn it off and put it back.
SW_INPUT_DIM_VAL_ON_CREATE = 10


@contextmanager
def dimension_dialog_suppressed(app: Any) -> Iterator[None]:
    previous: Any = None
    try:
        previous = bool(app.GetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE))
        app.SetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE, False)
    except Exception:
        logger.info("Could not suppress the SOLIDWORKS dimension-input dialog.")
        previous = None
    try:
        yield
    finally:
        if previous is not None:
            try:
                app.SetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE, previous)
            except Exception:
                logger.info("Could not restore the SOLIDWORKS dimension-input dialog setting.")


def active_document() -> tuple[Any, Any]:
    app = running_app()
    doc = app.ActiveDoc
    if doc is None:
        raise RuntimeError("SOLIDWORKS has no active document. Create or open a part/assembly first.")
    return app, flag_methods(doc, *_MODEL_DOC_METHODS)


def document_type(doc: Any) -> int:
    return int(value(doc, "GetType"))


def require_part() -> tuple[Any, Any]:
    app, doc = active_document()
    if document_type(doc) != 1:
        raise RuntimeError("This tool requires an active part document.")
    return app, doc


def require_assembly() -> tuple[Any, Any]:
    app, doc = active_document()
    if document_type(doc) != 2:
        raise RuntimeError("This tool requires an active assembly document.")
    return app, doc


def document_info(doc: Any) -> dict[str, Any]:
    doc_type_num = document_type(doc)
    try:
        dirty = bool(value(doc, "GetSaveFlag"))
    except Exception:
        dirty = False
    return {
        "title": str(value(doc, "GetTitle")),
        "path": str(value(doc, "GetPathName") or ""),
        "document_type": DOC_TYPES.get(doc_type_num, f"unknown ({doc_type_num})"),
        "dirty": dirty,
    }


def rebuild(doc: Any) -> bool:
    try:
        return bool(invoke_no_arg(doc, "EditRebuild3"))
    except Exception:
        return False


def clear_selection(doc: Any) -> None:
    try:
        doc.ClearSelection2(True)
    except Exception:
        pass


# --------------------------------------------------------------------------
# Feature tree
# --------------------------------------------------------------------------


def feature_property(feature: Any, member: str, default: Any = "") -> Any:
    try:
        return value(feature, member)
    except Exception:
        return default


def iter_feature_objects(doc: Any) -> list[Any]:
    features: list[Any] = []
    try:
        feature = value(doc, "FirstFeature")
        while feature is not None:
            features.append(feature)
            feature = value(feature, "GetNextFeature")
    except Exception:
        logger.exception("Could not enumerate feature tree")
    return features


def iter_features(doc: Any) -> list[dict[str, str]]:
    return [
        {
            "name": str(feature_property(feature, "Name", "")),
            "type": str(feature_property(feature, "GetTypeName2", "")),
        }
        for feature in iter_feature_objects(doc)
    ]


def find_feature(doc: Any, name: str) -> Any | None:
    for feature in iter_feature_objects(doc):
        if str(feature_property(feature, "Name", "")) == name:
            return feature
    return None


def reference_planes(doc: Any) -> list[str]:
    """Return actual, localized reference-plane names in feature-tree order."""
    return [f["name"] for f in iter_features(doc) if f["type"] == "RefPlane" and f["name"]]


def reference_axes(doc: Any) -> list[str]:
    return [f["name"] for f in iter_features(doc) if f["type"] == "RefAxis" and f["name"]]


def sketch_features(doc: Any) -> list[Any]:
    return [f for f in iter_feature_objects(doc) if feature_property(f, "GetTypeName2", "") in ("ProfileFeature", "3DProfileFeature")]


def sketch_names(doc: Any) -> list[str]:
    return [str(feature_property(f, "Name", "")) for f in sketch_features(doc)]


def latest_sketch(doc: Any) -> tuple[str, Any]:
    """Return the most recently created sketch feature in tree order."""
    sketches = sketch_features(doc)
    if not sketches:
        raise RuntimeError("No sketch was found in the feature tree.")
    feature = sketches[-1]
    return str(feature_property(feature, "Name", "")), feature


def sketch_name_for_object(doc: Any, sketch: Any) -> str:
    reference = persistent_reference_id(doc, sketch)
    if reference:
        for feature in sketch_features(doc):
            specific = value(feature, "GetSpecificFeature2")
            if specific is not None and persistent_reference_id(doc, specific) == reference:
                return str(value(feature, "Name"))
    raise RuntimeError("Cannot identify the active sketch feature from its native reference.")


def resolve_sketch(doc: Any, sketch_name: str | None) -> tuple[str, Any]:
    """Resolve an explicit sketch name, or fall back to the newest sketch."""
    if sketch_name:
        feature = find_feature(doc, sketch_name)
        if feature is None:
            raise RuntimeError(f"No feature named '{sketch_name}' exists in this document.")
        if feature_property(feature, "GetTypeName2", "") not in ("ProfileFeature", "3DProfileFeature"):
            raise RuntimeError(f"Feature '{sketch_name}' is not a sketch.")
        return sketch_name, feature
    return latest_sketch(doc)


def toggle_sketch(doc: Any, is_3d: bool) -> None:
    if is_3d:
        flag_methods(doc, "Insert3DSketch2", "Insert3DSketch")
        call_versioned(doc, ("Insert3DSketch2", (True,)), ("Insert3DSketch", ()))
    else:
        sketch_manager(doc).InsertSketch(True)


def exit_active_sketch(doc: Any) -> None:
    active = doc.SketchManager.ActiveSketch
    if active is not None:
        toggle_sketch(doc, bool(value(active, "Is3D")))
        if doc.SketchManager.ActiveSketch is not None:
            raise RuntimeError("SOLIDWORKS did not close the active sketch.")


def select_sketch_for_feature(doc: Any, sketch_name: str | None) -> str:
    """Close any open sketch and leave exactly the profile sketch selected."""
    exit_active_sketch(doc)
    name, feature = resolve_sketch(doc, sketch_name)
    clear_selection(doc)
    if not bool(selectable(feature).Select2(False, 0)):
        raise RuntimeError(f"Could not select sketch '{name}' for the feature operation.")
    return name


# --------------------------------------------------------------------------
# Named selection
# --------------------------------------------------------------------------


def select_by_id(
    doc: Any,
    name: str,
    object_type: str,
    mark: int = 0,
    append: bool = False,
    ext: Any = None,
) -> bool:
    """Select something by its name.

    Pass ``ext`` to reuse one accessor across a run of calls: obtaining it
    re-flags eight members, and each flag is a cross-process lookup.
    """
    target = ext if ext is not None else extension(doc)
    try:
        return bool(target.SelectByID2(name, object_type, 0, 0, 0, append, mark, nothing(), 0))
    except Exception:
        # Some builds reject the typed VT_DISPATCH null and want pythoncom.Nothing.
        return bool(target.SelectByID2(name, object_type, 0, 0, 0, append, mark, pythoncom.Nothing, 0))


def select_at_point(
    doc: Any,
    object_type: str,
    point_m: Sequence[float],
    mark: int = 0,
    append: bool = True,
) -> bool:
    """Select topology by a 3D model-space point that lies on the entity.

    This avoids QueryInterface-ing Face2/Edge to IEntity, which late-bound
    pywin32 cannot do without a generated type-library cache.
    """
    x, y, z = (float(c) for c in point_m[:3])
    ext = extension(doc)
    try:
        return bool(ext.SelectByID2("", object_type, x, y, z, append, mark, nothing(), 0))
    except Exception:
        return bool(ext.SelectByID2("", object_type, x, y, z, append, mark, pythoncom.Nothing, 0))


def select_object(doc: Any, obj: Any, mark: int = 0, append: bool = True) -> bool:
    """Select a COM object directly, preferring Select4 and falling back to Select2."""
    selectable(obj)
    try:
        select_data = doc.SelectionManager.CreateSelectData()
        select_data.Mark = mark
        if bool(obj.Select4(append, select_data)):
            return True
    except Exception:
        pass
    try:
        return bool(obj.Select2(append, mark))
    except Exception:
        return False


def select_body(doc: Any, body: Any, mark: int = 0, append: bool = True) -> bool:
    """IBody2.Select2 takes ISelectData, unlike IFeature.Select2's integer mark."""
    selection_manager = flag_methods(doc.SelectionManager, "CreateSelectData")
    data = selection_manager.CreateSelectData()
    data.Mark = mark
    return bool(flag_methods(body, "Select2").Select2(append, data))


def resolve_plane_name(doc: Any, name: str, planes: Sequence[str] | None = None) -> str:
    """Accept either an exact localized plane name or front/top/right.

    Pass ``planes`` to share one feature-tree walk across several lookups; the
    walk reads a name and a type off every feature in the document.
    """
    available = list(planes) if planes is not None else reference_planes(doc)
    if name in available:
        return name
    key = name.strip().lower()
    index = {"front": 0, "top": 1, "right": 2}.get(key)
    if index is not None and len(available) > index:
        return available[index]
    raise RuntimeError(
        f"'{name}' is not a reference plane of this document. Available: {available}"
    )


# --------------------------------------------------------------------------
# Topology enumeration
#
# Indices are positional within the current model state: enumeration walks
# solid bodies in GetBodies2 order and each body's faces/edges in COM order.
# They stay valid until geometry changes, so re-list after adding a feature.
# --------------------------------------------------------------------------


def component_transform(component: Any) -> list[float] | None:
    """Component-to-assembly transform as the 16 doubles of a MathTransform."""
    try:
        transform = value(component, "Transform2")
        data = value(transform, "ArrayData")
        if data is not None and len(data) >= 13:
            return [float(v) for v in data[:16]]
    except Exception:
        pass
    return None


def apply_transform(point: Sequence[float], matrix: Sequence[float] | None) -> list[float]:
    """Map a component-space point into assembly space.

    SOLIDWORKS stores a MathTransform column-major: 0-8 rotation, 9-11
    translation, 12 scale.
    """
    if matrix is None:
        return [float(c) for c in point[:3]]
    x, y, z = (float(c) for c in point[:3])
    scale = float(matrix[12]) if len(matrix) > 12 and matrix[12] else 1.0
    return [
        (matrix[0] * x + matrix[3] * y + matrix[6] * z) * scale + matrix[9],
        (matrix[1] * x + matrix[4] * y + matrix[7] * z) * scale + matrix[10],
        (matrix[2] * x + matrix[5] * y + matrix[8] * z) * scale + matrix[11],
    ]


def iter_components(doc: Any, top_level_only: bool = False) -> list[Any]:
    try:
        return as_list(doc.GetComponents(bool(top_level_only)))
    except Exception:
        return []


def component_bodies(component: Any, body_type: int = BODY_SOLID) -> list[Any]:
    try:
        bodies = as_list(flag_methods(component, "GetBodies3").GetBodies3(body_type, empty_variant()))
        if bodies:
            return bodies
    except Exception:
        pass
    try:
        bodies = as_list(safe(component, "GetBody"))
        return bodies if body_type == BODY_ALL else [body for body in bodies if safe(body, "GetType") == body_type]
    except Exception:
        return []


def iter_body_context(doc: Any, body_type: int = BODY_SOLID) -> list[tuple[Any, str, list[float] | None]]:
    """Yield (body, label, component transform) for parts and assemblies alike.

    In an assembly, body geometry is reported in component space, so the
    transform travels with the body and every point is mapped before use.
    """
    if document_type(doc) == 2:
        contexts: list[tuple[Any, str, list[float] | None]] = []
        for component in iter_components(doc):
            if bool(safe(component, "IsSuppressed", False)):
                continue
            label = str(safe(component, "Name2", "") or "")
            matrix = component_transform(component)
            for body in component_bodies(component, body_type):
                contexts.append((body, f"{label}/{safe(body, 'Name', '')}", matrix))
        return contexts
    try:
        return [(body, str(safe(body, "Name", "")), None) for body in as_list(doc.GetBodies2(body_type, False))]
    except Exception:
        return []


def get_bodies(doc: Any, body_type: int = BODY_SOLID) -> list[Any]:
    return [body for body, _, _ in iter_body_context(doc, body_type)]


def _point_on(entity: Any, box: Sequence[float] | None, matrix: Sequence[float] | None = None) -> list[float] | None:
    """Return a point guaranteed to lie on a face, for point-based selection."""
    if box is None or len(box) < 6:
        return None
    center = [(box[0] + box[3]) / 2.0, (box[1] + box[4]) / 2.0, (box[2] + box[5]) / 2.0]
    point = center
    try:
        flag_methods(entity, "GetClosestPointOn")
        closest = entity.GetClosestPointOn(center[0], center[1], center[2])
        if closest is not None and len(closest) >= 3:
            point = [float(closest[0]), float(closest[1]), float(closest[2])]
    except Exception:
        pass
    return apply_transform(point, matrix)


def _edge_point(edge: Any, matrix: Sequence[float] | None = None) -> list[float] | None:
    """Return a point on an edge.

    IEdge has no GetBox — that is IFace2 only — so evaluate the underlying
    curve at its mid parameter instead.  This also handles closed circular
    edges, which have no vertices to average.
    """
    try:
        curve = value(edge, "GetCurve")
        params = value(edge, "GetCurveParams3")
        middle = (float(params.UMinValue) + float(params.UMaxValue)) / 2.0
        point = flag_methods(curve, "Evaluate").Evaluate(middle)
        if point is not None and len(point) >= 3:
            return apply_transform([float(point[0]), float(point[1]), float(point[2])], matrix)
    except Exception:
        pass
    # Fall back to the chord midpoint snapped onto the curve.
    try:
        start = value(value(edge, "GetStartVertex"), "GetPoint")
        end = value(value(edge, "GetEndVertex"), "GetPoint")
        middle = [(float(start[i]) + float(end[i])) / 2.0 for i in range(3)]
        flag_methods(edge, "GetClosestPointOn")
        closest = edge.GetClosestPointOn(middle[0], middle[1], middle[2])
        if closest is not None and len(closest) >= 3:
            middle = [float(closest[0]), float(closest[1]), float(closest[2])]
        return apply_transform(middle, matrix)
    except Exception:
        return None


def _face_point(face: Any, matrix: Sequence[float] | None = None) -> list[float] | None:
    return _point_on(face, safe(face, "GetBox"), matrix)


def iter_face_objects(doc: Any, body_type: int = BODY_SOLID) -> list[tuple[Any, list[float] | None]]:
    """Every face as (object, component transform), in list_faces index order.

    Selecting a face needs the COM object, and a point on it only if selecting
    the object outright fails.  enumerate_faces additionally reads a box, a
    closest point, an area, a normal and the surface parameters for *every*
    face in the model -- six to ten cross-process calls each.  That is exactly
    what list_faces is for, and pure waste when the caller has already named
    three indices to fillet.  Iteration order matches enumerate_faces because
    the indices being passed in came from there.
    """
    return [
        (face, matrix)
        for body, _, matrix in iter_body_context(doc, body_type)
        for face in as_list(safe(body, "GetFaces"))
    ]


def iter_edge_objects(doc: Any, body_type: int = BODY_SOLID) -> list[tuple[Any, list[float] | None]]:
    """Every edge as (object, component transform), in list_edges index order."""
    return [
        (edge, matrix)
        for body, _, matrix in iter_body_context(doc, body_type)
        for edge in as_list(safe(body, "GetEdges"))
    ]


def _surface_details(surface: Any, matrix: Sequence[float] | None = None) -> dict[str, Any]:
    details: dict[str, Any] = {}
    try:
        identity = int(value(surface, "Identity"))
    except Exception:
        return details
    details["surface_type"] = SURFACE_TYPES.get(identity, f"type_{identity}")
    try:
        if identity == 4002:  # cylinder: root(3), axis(3), radius
            params = surface.CylinderParams
            details["axis"] = [round(v, 6) for v in rotate_vector(params[3:6], matrix)]
            details["radius_mm"] = round(to_mm(params[6]), 6)
        elif identity == 4004:  # sphere: center(3), radius
            params = surface.SphereParams
            details["center_mm"] = mm_point(apply_transform(params[0:3], matrix))
            details["radius_mm"] = round(to_mm(params[3]), 6)
        elif identity == 4003:  # cone
            params = surface.ConeParams
            details["axis"] = [round(v, 6) for v in rotate_vector(params[3:6], matrix)]
        elif identity == 4005:  # torus
            params = surface.TorusParams
            details["axis"] = [round(v, 6) for v in rotate_vector(params[3:6], matrix)]
            details["major_radius_mm"] = round(to_mm(params[6]), 6)
            details["minor_radius_mm"] = round(to_mm(params[7]), 6)
        elif identity == 4001:  # plane: normal(3), root(3)
            params = surface.PlaneParams
            # Deliberately not "normal": this is the underlying plane's normal,
            # which ignores whether the face uses that plane reversed. Face2.Normal
            # is the outward one, and overwriting it here silently inverted five
            # faces out of six on a simple slab.
            details["surface_normal"] = [round(v, 6) for v in rotate_vector(params[0:3], matrix)]
    except Exception:
        pass
    return details


def rotate_vector(vector: Sequence[float], matrix: Sequence[float] | None) -> list[float]:
    """Rotate a direction into assembly space, leaving translation and scale out."""
    if matrix is None:
        return [float(c) for c in vector[:3]]
    x, y, z = (float(c) for c in vector[:3])
    return [
        matrix[0] * x + matrix[3] * y + matrix[6] * z,
        matrix[1] * x + matrix[4] * y + matrix[7] * z,
        matrix[2] * x + matrix[5] * y + matrix[8] * z,
    ]


def enumerate_faces(doc: Any, body_type: int = BODY_SOLID) -> list[dict[str, Any]]:
    faces: list[dict[str, Any]] = []
    index = 0
    for body_index, (body, body_name, matrix) in enumerate(iter_body_context(doc, body_type)):
        for face in as_list(safe(body, "GetFaces")):
            entry: dict[str, Any] = {"index": index, "body_index": body_index, "body": body_name, "_obj": face}
            point = _point_on(face, safe(face, "GetBox"), matrix)
            if point is not None:
                entry["point_mm"] = mm_point(point)
                entry["_point_m"] = point
            try:
                entry["area_mm2"] = round(float(value(face, "GetArea")) * 1_000_000.0, 4)
            except Exception:
                pass
            try:
                normal = value(face, "Normal")
                if normal is not None:
                    # Face2.Normal already accounts for FaceInSurfaceSense, so it
                    # is the outward normal as-is; only the component transform
                    # still has to be applied.
                    entry["normal"] = [round(v, 6) for v in rotate_vector(normal[:3], matrix)]
            except Exception:
                pass
            surface = safe(face, "GetSurface")
            if surface is not None:
                entry.update(_surface_details(surface, matrix))
            faces.append(entry)
            index += 1
    return faces


def _curve_details(curve: Any, matrix: Sequence[float] | None = None) -> dict[str, Any]:
    details: dict[str, Any] = {}
    try:
        identity = int(value(curve, "Identity"))
    except Exception:
        return details
    details["curve_type"] = CURVE_TYPES.get(identity, f"type_{identity}")
    try:
        if identity == 3002:  # circle: center(3), axis(3), radius
            params = curve.CircleParams
            details["center_mm"] = mm_point(apply_transform(params[0:3], matrix))
            details["axis"] = [round(v, 6) for v in rotate_vector(params[3:6], matrix)]
            details["radius_mm"] = round(to_mm(params[6]), 6)
        elif identity == 3001:  # line: root(3), direction(3)
            params = curve.LineParams
            details["direction"] = [round(v, 6) for v in rotate_vector(params[3:6], matrix)]
    except Exception:
        pass
    return details


def _edge_endpoints(edge: Any, matrix: Sequence[float] | None = None) -> dict[str, Any]:
    endpoints: dict[str, Any] = {}
    for member, key in (("GetStartVertex", "start_mm"), ("GetEndVertex", "end_mm")):
        try:
            vertex = value(edge, member)
            if vertex is not None:
                point = value(vertex, "GetPoint")
                endpoints[key] = mm_point(apply_transform(point[:3], matrix))
        except Exception:
            pass
    return endpoints


def enumerate_edges(doc: Any, body_type: int = BODY_SOLID) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    index = 0
    for body_index, (body, body_name, matrix) in enumerate(iter_body_context(doc, body_type)):
        for edge in as_list(safe(body, "GetEdges")):
            entry: dict[str, Any] = {"index": index, "body_index": body_index, "body": body_name, "_obj": edge}
            point = _edge_point(edge, matrix)
            if point is not None:
                entry["point_mm"] = mm_point(point)
                entry["_point_m"] = point
            curve = safe(edge, "GetCurve")
            if curve is not None:
                entry.update(_curve_details(curve, matrix))
                try:
                    params = value(edge, "GetCurveParams3")
                    entry["length_mm"] = round(
                        to_mm(flag_methods(curve, "GetLength2").GetLength2(params.UMinValue, params.UMaxValue)), 6
                    )
                except Exception:
                    pass
            entry.update(_edge_endpoints(edge, matrix))
            edges.append(entry)
            index += 1
    return edges


def enumerate_vertices(doc: Any) -> list[dict[str, Any]]:
    vertices: list[dict[str, Any]] = []
    index = 0
    for body_index, (body, _, matrix) in enumerate(iter_body_context(doc)):
        seen: set[tuple[float, float, float]] = set()
        for edge in as_list(safe(body, "GetEdges")):
            for member in ("GetStartVertex", "GetEndVertex"):
                try:
                    vertex = value(edge, member)
                    if vertex is None:
                        continue
                    point = apply_transform(value(vertex, "GetPoint")[:3], matrix)
                    vertex_obj = vertex
                except Exception:
                    continue
                key = (round(point[0], 9), round(point[1], 9), round(point[2], 9))
                if key in seen:
                    continue
                seen.add(key)
                vertices.append(
                    {
                        "index": index,
                        "body_index": body_index,
                        "point_mm": mm_point(point),
                        "_point_m": point,
                        "_obj": vertex_obj,
                    }
                )
                index += 1
    return vertices


def enumerate_sketch_segments(doc: Any, sketch_name: str | None = None) -> tuple[str, list[dict[str, Any]]]:
    """List the segments of an open or named sketch, with selectable indices."""
    manager = sketch_manager(doc)
    sketch = manager.ActiveSketch
    resolved_name = ""
    if document_type(doc) == 3:
        # A drawing always has a live sketch — the active view's, or the
        # sheet's — and none of it lives in a feature tree to look up by name.
        if sketch is None:
            raise RuntimeError("The drawing has no active sketch. Activate a view first.")
        resolved_name = "<active drawing view>"
    elif sketch is not None and not sketch_name:
        resolved_name = sketch_name_for_object(doc, sketch)
    else:
        resolved_name, feature = resolve_sketch(doc, sketch_name)
        sketch = value(feature, "GetSpecificFeature2")
    if sketch is None:
        raise RuntimeError("No sketch is open and no sketch name was supplied.")

    segments: list[dict[str, Any]] = []
    for index, segment in enumerate(as_list(safe(sketch, "GetSketchSegments"))):
        # swSketchLINE is 0, so this must not go through a truthiness test.
        raw_type = safe(segment, "GetType")
        entry: dict[str, Any] = {
            "index": index,
            "type": {0: "line", 1: "arc", 2: "ellipse", 3: "spline", 4: "text", 5: "parabola"}.get(
                int(raw_type) if raw_type is not None else -1, "unknown"
            ),
            "construction": bool(safe(segment, "ConstructionGeometry", False)),
            "id": str(safe(segment, "GetID", "")),
        }
        try:
            entry["length_mm"] = round(to_mm(float(value(segment, "GetLength"))), 6)
        except Exception:
            pass
        seg_type = entry["type"]
        try:
            if seg_type == "line":
                start = value(segment, "GetStartPoint2")
                end = value(segment, "GetEndPoint2")
                entry["start_mm"] = mm_point([start.X, start.Y, start.Z])
                entry["end_mm"] = mm_point([end.X, end.Y, end.Z])
            elif seg_type == "arc":
                center = value(segment, "GetCenterPoint2")
                entry["center_mm"] = mm_point([center.X, center.Y, center.Z])
                entry["radius_mm"] = round(to_mm(float(value(segment, "GetRadius"))), 6)
        except Exception:
            pass
        segments.append(entry)
    return resolved_name, segments


def sketch_segment_objects(doc: Any, sketch_name: str | None = None) -> list[Any]:
    manager = sketch_manager(doc)
    sketch = manager.ActiveSketch
    if document_type(doc) == 3:
        # Drawing sketches belong to a view, not to a named feature.
        if sketch is None:
            raise RuntimeError("The drawing has no active sketch. Activate a view first.")
    elif sketch is None or sketch_name:
        _, feature = resolve_sketch(doc, sketch_name)
        sketch = value(feature, "GetSpecificFeature2")
    if sketch is None:
        raise RuntimeError("No sketch is open and no sketch name was supplied.")
    return as_list(safe(sketch, "GetSketchSegments"))


def sketch_point_objects(doc: Any, sketch_name: str | None = None) -> list[Any]:
    manager = sketch_manager(doc)
    sketch = manager.ActiveSketch
    if sketch is None or sketch_name:
        _, feature = resolve_sketch(doc, sketch_name)
        sketch = value(feature, "GetSpecificFeature2")
    if sketch is None:
        raise RuntimeError("No sketch is open and no sketch name was supplied.")
    return as_list(safe(sketch, "GetSketchPoints2"))


# --------------------------------------------------------------------------
# Declarative selection
# --------------------------------------------------------------------------

SELECTION_SCHEMA = {
    "type": "object",
    "description": (
        "What to operate on. Indices come from list_faces / list_edges / list_vertices / "
        "list_sketch_segments and are only valid for the current model state, so re-list "
        "after every geometry change."
    ),
    "properties": {
        "faces": {"type": "array", "items": {"type": "integer"}, "description": "Face indices from list_faces."},
        "edges": {"type": "array", "items": {"type": "integer"}, "description": "Edge indices from list_edges."},
        "surface_faces": {"type": "array", "items": {"type": "integer", "minimum": 0}, "description": "Face indices from list_faces(body_type=surface), separate from solid faces."},
        "surface_edges": {"type": "array", "items": {"type": "integer", "minimum": 0}, "description": "Edge indices from list_edges(body_type=surface), separate from solid edges."},
        "vertices": {"type": "array", "items": {"type": "integer"}, "description": "Vertex indices from list_vertices."},
        "face_edges": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Face indices whose every edge should be selected (handy for filleting a whole face).",
        },
        "feature_faces": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Feature names whose faces should be selected.",
        },
        "feature_edges": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Feature names whose edges should be selected.",
        },
        "features": {"type": "array", "items": {"type": "string"}, "description": "Feature names from list_features."},
        "planes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Reference planes: exact localized names, or front/top/right.",
        },
        "axes": {"type": "array", "items": {"type": "string"}, "description": "Reference-axis names."},
        "sketches": {"type": "array", "items": {"type": "string"}, "description": "Sketch feature names."},
        "sketch_segments": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Segment indices in the open (or named) sketch, from list_sketch_segments.",
        },
        "sketch_points": {"type": "array", "items": {"type": "integer"}, "description": "Sketch point indices."},
        "sketch_name": {"type": "string", "description": "Which sketch sketch_segments/sketch_points refer to. Defaults to the open sketch."},
        "bodies": {"type": "array", "items": {"type": "integer"}, "description": "Solid-body indices."},
        "surface_bodies": {"type": "array", "items": {"type": "integer", "minimum": 0}, "description": "Surface-body indices from list_surface_bodies; separate from solid-body indices."},
        "components": {"type": "array", "items": {"type": "string"}, "description": "Assembly component names."},
        "points": {
            "type": "array",
            "description": "Raw point picks, for entities you already know the coordinates of.",
            "items": {
                "type": "object",
                "properties": {
                    "x_mm": {"type": "number"}, "y_mm": {"type": "number"}, "z_mm": {"type": "number"},
                    "type": {"type": "string", "enum": ["FACE", "EDGE", "VERTEX"], "default": "FACE"},
                },
                "required": ["x_mm", "y_mm", "z_mm"],
            },
        },
    },
}


def _select_topology(
    doc: Any,
    obj: Any,
    point: Sequence[float] | None,
    select_type: str,
    mark: int,
) -> bool:
    """Select one face/edge/vertex, by COM object first and by point second.

    Selecting the object directly is unambiguous.  Point picking is the
    fallback because SelectByID2 refuses a point that another entity — a
    sketch segment lying under the edge, say — also claims.
    """
    if obj is not None and select_object(doc, obj, mark, True):
        return True
    return bool(point is not None and select_at_point(doc, select_type, point, mark, True))


def _select_indexed(
    doc: Any,
    entries: list[dict[str, Any]],
    indices: Sequence[int],
    select_type: str,
    mark: int,
    label: str,
) -> int:
    count = 0
    for raw_index in indices:
        index = int(raw_index)
        if not 0 <= index < len(entries):
            raise RuntimeError(f"{label} index {index} is out of range (0..{len(entries) - 1}).")
        entry = entries[index]
        if not _select_topology(doc, entry.get("_obj"), entry.get("_point_m"), select_type, mark):
            raise RuntimeError(f"SOLIDWORKS refused to select {label.lower()} index {index}.")
        count += 1
    return count


def _select_indexed_objects(
    doc: Any,
    entries: list[tuple[Any, Sequence[float] | None]],
    indices: Sequence[int],
    select_type: str,
    mark: int,
    label: str,
    point_of: Callable[[Any, Sequence[float] | None], list[float] | None],
) -> int:
    """Select topology by index, working out a pick point only when one is needed.

    Selecting the COM object is unambiguous and succeeds in the ordinary case,
    so the point stays lazy: a healthy selection now costs no geometry queries
    at all, where it used to measure every face in the model first.
    """
    count = 0
    for raw_index in indices:
        index = int(raw_index)
        if not 0 <= index < len(entries):
            raise RuntimeError(f"{label} index {index} is out of range (0..{len(entries) - 1}).")
        obj, matrix = entries[index]
        if obj is not None and select_object(doc, obj, mark, True):
            count += 1
            continue
        point = point_of(obj, matrix) if obj is not None else None
        if point is None or not select_at_point(doc, select_type, point, mark, True):
            raise RuntimeError(f"SOLIDWORKS refused to select {label.lower()} index {index}.")
        count += 1
    return count


def apply_selection(doc: Any, spec: dict[str, Any] | None, mark: int = 0, append: bool = False) -> int:
    """Turn a selection spec into a real SOLIDWORKS selection; return the count."""
    if not spec:
        return 0
    if not append:
        clear_selection(doc)

    count = 0
    sketch_name = spec.get("sketch_name") or None

    # extension(doc) re-flags eight members on every call and each flag is a
    # cross-process GetIDsOfNames, so eight named selections used to spend 64 of
    # them on nothing.  Taken once here, and only when something needs it.
    ext = extension(doc) if any(
        spec.get(key) for key in ("features", "planes", "axes", "sketches", "components")
    ) else None

    if spec.get("faces"):
        count += _select_indexed_objects(
            doc, iter_face_objects(doc), spec["faces"], SELECT_TYPE_FACE, mark, "Face", _face_point
        )
    if spec.get("edges"):
        count += _select_indexed_objects(
            doc, iter_edge_objects(doc), spec["edges"], SELECT_TYPE_EDGE, mark, "Edge", _edge_point
        )
    if spec.get("surface_faces"):
        count += _select_indexed_objects(
            doc, iter_face_objects(doc, BODY_SHEET), spec["surface_faces"], SELECT_TYPE_FACE, mark, "Surface face", _face_point
        )
    if spec.get("surface_edges"):
        count += _select_indexed_objects(
            doc, iter_edge_objects(doc, BODY_SHEET), spec["surface_edges"], SELECT_TYPE_EDGE, mark, "Surface edge", _edge_point
        )
    if spec.get("vertices"):
        # Vertices keep the measured enumeration: it deduplicates by coordinate,
        # which means reading each point anyway.
        count += _select_indexed(doc, enumerate_vertices(doc), spec["vertices"], SELECT_TYPE_VERTEX, mark, "Vertex")

    if spec.get("face_edges"):
        indexed_faces = iter_face_objects(doc)
        for raw_index in spec["face_edges"]:
            index = int(raw_index)
            if not 0 <= index < len(indexed_faces):
                raise RuntimeError(f"Face index {index} is out of range (0..{len(indexed_faces) - 1}).")
            face, matrix = indexed_faces[index]
            for edge in as_list(safe(face, "GetEdges")):
                if select_object(doc, edge, mark, True):
                    count += 1
                    continue
                point = _edge_point(edge, matrix)
                if point is not None and select_at_point(doc, SELECT_TYPE_EDGE, point, mark, True):
                    count += 1

    for key, select_type in (
        ("feature_faces", SELECT_TYPE_FACE),
        ("feature_edges", SELECT_TYPE_EDGE),
    ):
        for feature_name in spec.get(key, []):
            feature = find_feature(doc, str(feature_name))
            if feature is None:
                raise RuntimeError(f"No feature named '{feature_name}' exists in this document.")
            wants_faces = select_type == SELECT_TYPE_FACE
            point_of = _face_point if wants_faces else _edge_point
            for face in as_list(safe(feature, "GetFaces")):
                for target in [face] if wants_faces else as_list(safe(face, "GetEdges")):
                    if select_object(doc, target, mark, True):
                        count += 1
                        continue
                    point = point_of(target, None)
                    if point is not None and select_at_point(doc, select_type, point, mark, True):
                        count += 1

    for feature_name in spec.get("features", []):
        if not select_by_id(doc, str(feature_name), "BODYFEATURE", mark, True, ext):
            raise RuntimeError(f"Could not select feature '{feature_name}'.")
        count += 1

    plane_names = spec.get("planes", [])
    # One feature-tree walk for the whole run rather than one per plane.
    available_planes = reference_planes(doc) if len(plane_names) > 1 else None
    for plane_name in plane_names:
        resolved = resolve_plane_name(doc, str(plane_name), available_planes)
        if not select_by_id(doc, resolved, "PLANE", mark, True, ext):
            raise RuntimeError(f"Could not select reference plane '{resolved}'.")
        count += 1

    for axis_name in spec.get("axes", []):
        if not select_by_id(doc, str(axis_name), "AXIS", mark, True, ext):
            raise RuntimeError(f"Could not select reference axis '{axis_name}'.")
        count += 1

    for name in spec.get("sketches", []):
        if not select_by_id(doc, str(name), "SKETCH", mark, True, ext):
            raise RuntimeError(f"Could not select sketch '{name}'.")
        count += 1

    if spec.get("sketch_segments"):
        segments = sketch_segment_objects(doc, sketch_name)
        for raw_index in spec["sketch_segments"]:
            index = int(raw_index)
            if not 0 <= index < len(segments):
                raise RuntimeError(f"Sketch segment index {index} is out of range (0..{len(segments) - 1}).")
            if not select_object(doc, segments[index], mark, True):
                raise RuntimeError(f"Could not select sketch segment {index}.")
            count += 1

    if spec.get("sketch_points"):
        points = sketch_point_objects(doc, sketch_name)
        for raw_index in spec["sketch_points"]:
            index = int(raw_index)
            if not 0 <= index < len(points):
                raise RuntimeError(f"Sketch point index {index} is out of range (0..{len(points) - 1}).")
            if not select_object(doc, points[index], mark, True):
                raise RuntimeError(f"Could not select sketch point {index}.")
            count += 1

    bodies = get_bodies(doc) if spec.get("bodies") else []
    for raw_index in spec.get("bodies", []):
        index = int(raw_index)
        if not 0 <= index < len(bodies):
            raise RuntimeError(f"Body index {index} is out of range (0..{len(bodies) - 1}).")
        if not select_body(doc, bodies[index], mark, True):
            raise RuntimeError(f"Could not select body {index}.")
        count += 1

    surface_bodies = get_bodies(doc, BODY_SHEET) if spec.get("surface_bodies") else []
    for raw_index in spec.get("surface_bodies", []):
        index = int(raw_index)
        if not 0 <= index < len(surface_bodies):
            raise RuntimeError(f"Surface-body index {index} is out of range (0..{len(surface_bodies) - 1}).")
        if not select_body(doc, surface_bodies[index], mark, True):
            raise RuntimeError(f"Could not select surface body {index}.")
        count += 1

    if spec.get("components"):
        found = {
            str(safe(component, "Name2", "")): component
            for component in iter_components(doc)
        }
        for component_name in spec["components"]:
            component = found.get(str(component_name))
            if component is None:
                raise RuntimeError(
                    f"No component named '{component_name}'. Available: {sorted(found)}"
                )
            # Component names need the assembly-qualified form for SelectByID2,
            # which the component itself knows; fall back to selecting it directly.
            identifier = str(safe(component, "GetSelectByIDString", "") or "")
            selected = bool(identifier and select_by_id(doc, identifier, "COMPONENT", mark, True, ext))
            if not selected:
                selected = select_object(doc, component, mark, True)
            if not selected:
                raise RuntimeError(f"Could not select component '{component_name}'.")
            count += 1

    for pick in spec.get("points", []):
        point = [to_m(pick["x_mm"]), to_m(pick["y_mm"]), to_m(pick["z_mm"])]
        select_type = str(pick.get("type", "FACE")).upper()
        if not select_at_point(doc, select_type, point, mark, True):
            raise RuntimeError(f"Nothing of type {select_type} was found at {pick}.")
        count += 1

    return count


def split_selection(spec: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Break a selection spec into one sub-spec per entity, in written order.

    Some APIs distinguish their references by mark rather than by selection
    order — InsertRefPlane wants first/second/third as marks 0/1/2 — so the
    caller needs to select them one at a time.
    """
    if not spec:
        return []
    sketch_name = spec.get("sketch_name")
    singles: list[dict[str, Any]] = []
    for key, items in spec.items():
        if key == "sketch_name" or not items:
            continue
        for item in items:
            sub: dict[str, Any] = {key: [item]}
            if sketch_name:
                sub["sketch_name"] = sketch_name
            singles.append(sub)
    return singles


def select_in_order(doc: Any, spec: dict[str, Any] | None) -> int:
    """Select each reference with its own mark, 0 upwards, in written order."""
    count = 0
    for index, sub in enumerate(split_selection(spec)):
        count += apply_selection(doc, sub, mark=index, append=index > 0)
    if count == 0:
        raise RuntimeError(
            "The selection specification did not resolve to anything. "
            "Call list_faces/list_edges/list_reference_planes first."
        )
    return count


def require_selection(doc: Any, spec: dict[str, Any] | None, mark: int = 0, append: bool = False) -> int:
    count = apply_selection(doc, spec, mark, append)
    if count == 0:
        raise RuntimeError(
            "The selection specification did not resolve to anything. "
            "Call list_faces/list_edges first and pass the indices you want."
        )
    return count


# --------------------------------------------------------------------------
# Rebuild-error readback.  SOLIDWORKS returns null far more often than it
# raises, so every mutating tool reports what the feature tree thinks.
# --------------------------------------------------------------------------


def whats_wrong(doc: Any) -> list[dict[str, Any]]:
    problems: list[dict[str, Any]] = []
    try:
        outcome = extension(doc).GetWhatsWrong(empty_variant(), empty_variant(), empty_variant())
    except Exception:
        return problems
    if not isinstance(outcome, (list, tuple)) or len(outcome) < 4:
        return problems
    _, features, error_codes, warnings = outcome[0], outcome[1], outcome[2], outcome[3]
    names = as_list(features)
    codes = as_list(error_codes)
    flags = as_list(warnings)
    for index, name in enumerate(names):
        problems.append(
            {
                "feature": str(name),
                "code": int(codes[index]) if index < len(codes) else None,
                "is_warning": bool(flags[index]) if index < len(flags) else None,
            }
        )
    return problems


def feature_result(doc: Any, feature: Any, action: str, **data: Any) -> dict[str, Any]:
    """Standard post-feature envelope: name it, rebuild, and report real errors."""
    if feature is None:
        problems = whats_wrong(doc)
        return result(
            False,
            f"SOLIDWORKS did not create the {action}. Check that the required selection was valid.",
            problems=problems,
            **data,
        )
    name = str(feature_property(feature, "Name", ""))
    rebuild(doc)
    problems = whats_wrong(doc)
    clear_selection(doc)
    payload = result(
        not problems,
        f"Created {action} '{name}'." if not problems else f"Created {action} '{name}', but the model has errors.",
        feature=name,
        **data,
    )
    if problems:
        payload["data"]["problems"] = problems
    return payload


def rename_feature(feature: Any, name: str | None) -> None:
    if not name:
        return
    try:
        feature.Name = name
    except Exception:
        logger.info("Could not rename feature to %s", name)
