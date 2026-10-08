# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for details.

"""Native MotionManager study management; availability comes from the live study."""

import math

from .sw_core import (
    active_document, as_list, byref_long, document_type, extension,
    flag_methods, result, tool, value,
)

TYPES = {"animation": 1, "basic_motion": 2, "motion_analysis": 4}
NAME = {"type": "string", "minLength": 1}
TYPE = {"type": "string", "enum": list(TYPES), "default": "animation"}


def _name(raw):
    name = str(raw).strip()
    if not name:
        raise RuntimeError("Motion study name must not be empty.")
    return name


def _manager():
    _, doc = active_document()
    if document_type(doc) not in (1, 2):
        raise RuntimeError("Motion studies require an active part or assembly.")
    manager = value(extension(doc), "GetMotionStudyManager")
    if manager is None:
        raise RuntimeError("The native MotionManager is unavailable for this document.")
    return flag_methods(manager, "GetMotionStudy", "ActivateMotionStudy", "DeleteMotionStudy")


def _names(manager):
    return [str(name) for name in as_list(value(manager, "GetMotionStudyNames"))]


def _study(manager, name):
    if name not in _names(manager):
        raise RuntimeError(f"No motion study named '{name}'. Use list_motion_studies first.")
    study = manager.GetMotionStudy(name)
    if study is None:
        raise RuntimeError(f"MotionManager could not access '{name}'.")
    return flag_methods(study, "GetSupportedStudyTypes", "SetDuration", "SetTime")


def _supported(study):
    mask = byref_long()
    if not study.GetSupportedStudyTypes(mask):
        raise RuntimeError("MotionManager could not read the supported study types.")
    return int(mask.value)


def _info(study):
    mask = _supported(study)
    kind = int(value(study, "StudyType"))
    return {"name": str(value(study, "Name")), "type": next((name for name, code in TYPES.items() if code == kind), str(kind)),
            "type_code": kind, "active": bool(value(study, "IsActive")),
            "duration_sec": float(value(study, "GetDuration")), "time_sec": float(value(study, "GetTime")),
            "supported_types": [name for name, code in TYPES.items() if mask & code], "supported_type_mask": mask}


def _type(raw):
    if raw not in TYPES:
        raise RuntimeError(f"Unknown motion study type '{raw}'.")
    return TYPES[raw]


def _set_type(study, code):
    if not _supported(study) & code:
        return result(False, "Requested study type is unavailable in the current installed/loaded/licensed session.", study=_info(study))
    study.StudyType = code
    info = _info(study)
    return result(info["type_code"] == code, "Read back motion study type.", study=info)


@tool("list_motion_studies", "Read-only: list native MotionManager studies, timeline times and actually supported types.")
def list_motion_studies(args):
    manager = _manager()
    names = _names(manager)
    studies = [_info(_study(manager, name)) for name in names]
    count = int(value(manager, "GetMotionStudyCount"))
    return result(count == len(studies), "Read motion studies.", studies=studies, count=count)


@tool("create_motion_study", "Create and name a native motion study. Defaults to animation; unsupported simulation types are reported as unavailable.",
      {"name": NAME, "type": TYPE}, ["name"])
def create_motion_study(args):
    name = _name(args["name"])
    code = _type(args.get("type", "animation"))
    manager = _manager()
    if name in _names(manager):
        raise RuntimeError(f"Motion study '{name}' already exists.")
    study = value(manager, "CreateMotionStudy")
    if study is None:
        return result(False, "Native motion study creation failed.")
    study = flag_methods(study, "GetSupportedStudyTypes", "SetDuration", "SetTime")
    study.Name = name
    output = _set_type(study, code)
    if str(value(study, "Name")) != name or name not in _names(manager):
        return result(False, "Created study, but the requested name was not applied.", study=_info(study))
    return output


@tool("activate_motion_study", "Activate an existing native motion study and read back its active state.", {"name": NAME}, ["name"])
def activate_motion_study(args):
    manager = _manager()
    name = _name(args["name"])
    study = _study(manager, name)
    accepted = bool(manager.ActivateMotionStudy(name))
    info = _info(study)
    return result(accepted and info["active"], "Read back motion study activation.", study=info)


@tool("delete_motion_study", "Delete the named motion study and its animation/simulation data; verify it disappears from MotionManager.", {"name": NAME}, ["name"])
def delete_motion_study(args):
    manager = _manager()
    name = _name(args["name"])
    _study(manager, name)
    accepted = bool(manager.DeleteMotionStudy(name))
    names = _names(manager)
    return result(accepted and name not in names, "Read back motion study deletion.", remaining_names=names)


@tool("duplicate_motion_study", "Duplicate an existing motion study, assign a new unique name, and read back timeline/type state.",
      {"name": NAME, "new_name": NAME}, ["name", "new_name"])
def duplicate_motion_study(args):
    manager = _manager()
    name, new_name = _name(args["name"]), _name(args["new_name"])
    if new_name in _names(manager):
        raise RuntimeError(f"Motion study '{new_name}' already exists.")
    study = value(_study(manager, name), "Duplicate")
    if study is None:
        return result(False, "Native motion study duplication failed.")
    study = flag_methods(study, "GetSupportedStudyTypes")
    study.Name = new_name
    info = _info(study)
    return result(info["name"] == new_name and new_name in _names(manager), "Read back duplicated motion study.", study=info)


@tool("set_motion_study_type", "Set animation, Basic Motion or Motion Analysis only when native MotionManager reports that type is supported.",
      {"name": NAME, "type": TYPE}, ["name", "type"])
def set_motion_study_type(args):
    code = _type(args["type"])
    manager = _manager()
    return _set_type(_study(manager, _name(args["name"])), code)


@tool("set_motion_study_timing", "Set native timeline duration and/or cursor time in seconds, checking bounds before any mutation and reading values back.",
      {"name": NAME, "duration_sec": {"type": "number", "exclusiveMinimum": 0}, "time_sec": {"type": "number", "minimum": 0}}, ["name"])
def set_motion_study_timing(args):
    updates = {key: float(args[key]) for key in ("duration_sec", "time_sec") if key in args}
    if not updates or any(not math.isfinite(v) or v < 0 or (k == "duration_sec" and v == 0) for k, v in updates.items()):
        raise RuntimeError("Provide a finite positive duration and/or a finite nonnegative cursor time.")
    manager = _manager()
    study = _study(manager, _name(args["name"]))
    before = _info(study)
    duration = updates.get("duration_sec", before["duration_sec"])
    if updates.get("time_sec", 0) > duration:
        raise RuntimeError("Cursor time must not exceed the study duration.")
    if not manager.ActivateMotionStudy(before["name"]):
        return result(False, "Could not activate the study before setting its timeline.", study=before)
    accepted = True
    if "duration_sec" in updates:
        accepted = bool(study.SetDuration(duration))
    if accepted and "time_sec" in updates:
        accepted = bool(study.SetTime(updates["time_sec"]))
    info = _info(study)
    matches = all(math.isclose(info[k], v, rel_tol=1e-7, abs_tol=1e-7) for k, v in updates.items())
    return result(accepted and matches, "Read back motion study timeline.", study=info)
