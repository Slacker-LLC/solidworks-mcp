# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for details.

"""Native weldments, structural-member groups, profiles and cut lists."""

import math
import os
from pathlib import Path

from .sw_core import (
    as_list, clear_selection, dispatch_array, exit_active_sketch, feature_manager,
    feature_property, feature_result, flag_methods, get_bodies, iter_feature_objects,
    rebuild, rename_feature, require_part, result, running_app, safe,
    sketch_segment_objects, to_rad, tool, value, whats_wrong,
)
from .sw_manage import _property


def _walk_features(doc):
    seen = set()
    pending = list(iter_feature_objects(doc))
    while pending:
        feature = pending.pop(0)
        key = (str(feature_property(feature, "Name", "")), str(feature_property(feature, "GetTypeName2", "")))
        if key in seen:
            continue
        seen.add(key)
        yield feature
        child = safe(feature, "GetFirstSubFeature")
        siblings = set()
        while child is not None:
            child_name = str(feature_property(child, "Name", ""))
            if child_name in siblings:
                break
            siblings.add(child_name)
            pending.append(child)
            child = safe(child, "GetNextSubFeature")


@tool("create_weldment", "Enable native weldment features on the active part; verifies IsWeldment afterwards.")
def create_weldment(args):
    _, doc = require_part()
    if bool(value(doc, "IsWeldment")):
        return result(True, "The active part is already a weldment.")
    manager = flag_methods(feature_manager(doc), "InsertWeldmentFeature")
    feature = manager.InsertWeldmentFeature()
    payload = feature_result(doc, feature, "weldment")
    if payload["ok"] and not bool(value(doc, "IsWeldment")):
        payload.update(ok=False, message="The part did not become a weldment.")
    return payload


@tool("list_weldment_profiles", "Read-only: find local native .sldlfp profiles under the supplied root or installed ProgramData profile folders.",
      {"root": {"type": "string"}, "query": {"type": "string", "default": ""}, "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100}})
def list_weldment_profiles(args):
    limit = int(args.get("limit", 100))
    if not 1 <= limit <= 500:
        raise RuntimeError("limit must be between 1 and 500.")
    if args.get("root"):
        roots = [Path(str(args["root"])).expanduser().resolve()]
        if not roots[0].is_dir():
            return result(False, "Profile root is not a directory.")
    else:
        data = Path(os.environ.get("ProgramData", "C:/ProgramData")) / "SOLIDWORKS"
        roots = sorted(data.glob("SOLIDWORKS */weldment profiles"), reverse=True)
    query = str(args.get("query", "")).casefold()
    paths = sorted({p.resolve() for root in roots for p in root.rglob("*.sldlfp") if p.is_file() and query in str(p).casefold()})
    return result(True, "Read local weldment profiles.", profiles=[{"name": p.stem, "path": str(p)} for p in paths[:limit]], total_matches=len(paths), truncated=len(paths) > limit)


def _profile_path(raw):
    path = Path(str(raw)).expanduser().resolve()
    if path.suffix.lower() != ".sldlfp" or not path.is_file():
        raise RuntimeError("Select an existing .sldlfp weldment profile.")
    return path


@tool("get_weldment_profile_configurations", "Read-only: list configurations stored in a native weldment profile, including size configurations.", {"path": {"type": "string"}}, ["path"])
def get_weldment_profile_configurations(args):
    path = _profile_path(args["path"])
    app = flag_methods(running_app(), "GetConfigurationNames")
    names = [str(n) for n in as_list(app.GetConfigurationNames(str(path)))]
    return result(True, "Read profile configurations.", path=str(path), configurations=names)


@tool("insert_structural_member", "Create native structural members using a local .sldlfp profile and explicit sketch-segment groups. Angles are degrees; profile configuration selects size.",
      {"profile_path": {"type": "string"}, "configuration": {"type": "string", "default": ""}, "name": {"type": "string"},
       "groups": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
           "sketch_name": {"type": "string"}, "segments": {"type": "array", "items": {"type": "integer", "minimum": 0}, "minItems": 1, "uniqueItems": True},
           "angle_deg": {"type": "number", "default": 0}}, "required": ["sketch_name", "segments"], "additionalProperties": False}}}, ["profile_path", "groups"])
def insert_structural_member(args):
    path = _profile_path(args["profile_path"])
    if not args.get("groups"):
        raise RuntimeError("Specify at least one structural-member group.")
    _, doc = require_part()
    exit_active_sketch(doc)
    resolved = []
    for spec in args["groups"]:
        segments = sketch_segment_objects(doc, str(spec["sketch_name"]))
        indices = spec["segments"]
        angle = float(spec.get("angle_deg", 0))
        if not indices or len(indices) != len(set(indices)) or any(not isinstance(i, int) or i < 0 or i >= len(segments) for i in indices):
            raise RuntimeError("Structural-member segment indices are invalid; list_sketch_segments first.")
        if not math.isfinite(angle):
            raise RuntimeError("Group rotation angle must be finite.")
        resolved.append(([segments[i] for i in indices], angle))
    clear_selection(doc)
    manager = flag_methods(feature_manager(doc), "CreateStructuralMemberGroup", "InsertStructuralWeldment5")
    groups = []
    for segments, angle in resolved:
        group = manager.CreateStructuralMemberGroup()
        group.Segments = dispatch_array(segments)
        group.Angle = to_rad(angle)
        group.ApplyCornerTreatment = True
        group.CornerTreatmentType = 1
        groups.append(group)
    before = len(get_bodies(doc))
    feature = manager.InsertStructuralWeldment5(str(path), 1, False, dispatch_array(groups), str(args.get("configuration", "")))
    rename_feature(feature, args.get("name"))
    payload = feature_result(doc, feature, "structural member")
    if payload["ok"] and len(get_bodies(doc)) <= before:
        payload.update(ok=False, message="Structural member did not add a solid body.")
    return payload


def _cut_list(doc):
    entries = []
    for feature in _walk_features(doc):
        if str(feature_property(feature, "GetTypeName2", "")) != "CutListFolder":
            continue
        folder = value(feature, "GetSpecificFeature2")
        bodies = as_list(value(folder, "GetBodies"))
        if not bodies:
            continue
        manager = flag_methods(value(feature, "CustomPropertyManager"), "Get6", "GetType2")
        entries.append({"name": str(value(feature, "Name")), "body_count": len(bodies),
                        "bodies": [str(value(b, "Name")) for b in bodies],
                        "properties": [_property(manager, str(n)) for n in as_list(value(manager, "GetNames"))]})
    return entries


@tool("list_cut_list", "Read-only: list weldment/sheet-metal cut-list folders, grouped bodies and resolved custom properties. Call update_cut_list after geometry changes.")
def list_cut_list(args):
    _, doc = require_part()
    return result(True, "Read cut list.", cut_list=_cut_list(doc))


@tool("update_cut_list", "Generate/update automatic native cut lists for the active weldment or sheet-metal part, then read folder/body grouping back.")
def update_cut_list(args):
    _, doc = require_part()
    found = False
    ok = True
    for feature in iter_feature_objects(doc):
        if str(feature_property(feature, "GetTypeName2", "")) != "SolidBodyFolder":
            continue
        folder = flag_methods(value(feature, "GetSpecificFeature2"), "SetAutomaticCutList", "SetAutomaticUpdate", "UpdateCutList")
        folder.SetAutomaticCutList(True)
        folder.SetAutomaticUpdate(True)
        ok = bool(folder.UpdateCutList()) and ok
        found = True
    rebuild(doc)
    problems = whats_wrong(doc)
    entries = _cut_list(doc)
    return result(found and ok and not problems and bool(entries), "Updated native cut list." if entries else "No cut-list folders were generated.", cut_list=entries, problems=problems)
