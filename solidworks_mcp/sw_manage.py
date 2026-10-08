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

"""Documents, configurations, custom properties, equations and material discovery."""

from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import pythoncom
import win32com.client
from . import sw_core

from .sw_core import (
    active_document, as_list, byref_long, document_info, document_type,
    extension, flag_methods, rebuild, result, running_app, safe, tool,
    value, whats_wrong,
)

NAME = {"type": "string", "minLength": 1}
CONFIG = {"type": "string", "default": "", "description": "Empty means document-level properties; otherwise an existing configuration name."}
PROPERTY_TYPES = {"text": 30, "number": 3, "date": 64, "yes_or_no": 11}


@tool("list_solidworks_sessions", "Read registered, already-running SolidWorks instances and their documents. Readability does not imply that modeling commands are healthy. Never starts an application.")
def list_solidworks_sessions(args):
    configured = sw_core.selected_session_pid()
    current = None
    try:
        current = int(value(flag_methods(running_app(), "GetProcessID"), "GetProcessID"))
    except Exception:
        pass
    entries = []
    for pid, _ in sw_core.session_monikers():
        entry = {"process_id": pid, "selected": pid == current}
        try:
            app = sw_core.session_app(pid)
            active = app.ActiveDoc
            entry.update(readable=True, version=str(value(app, "RevisionNumber")),
                         documents=[document_info(doc) for doc in as_list(value(app, "GetDocuments"))],
                         active_title=str(value(active, "GetTitle")) if active is not None else None)
        except Exception as exc:
            entry.update(readable=False, error=str(exc))
        entries.append(entry)
    return result(True, "Read registered SolidWorks sessions.", sessions=entries,
                  configured_process_id=configured, attached_process_id=current)


@tool("select_solidworks_session", "Route subsequent tools in this MCP server to an already-running SolidWorks process. process_id=0 restores the default COM registration. Does not activate windows, move documents, launch or terminate applications. If a selected process exits, calls fail rather than silently switching to another document session.",
      {"process_id": {"type": "integer", "minimum": 0}}, ["process_id"])
def select_solidworks_session(args):
    pid = args["process_id"]
    if isinstance(pid, bool) or not isinstance(pid, int) or pid < 0:
        raise RuntimeError("process_id must be a non-negative integer.")
    app = flag_methods(sw_core.session_app(pid), "GetProcessID")
    attached = int(value(app, "GetProcessID"))
    if pid and attached != pid:
        raise RuntimeError("The registered SolidWorks session does not match the requested process ID.")
    sw_core._SESSION_PID = pid
    return result(True, "Selected SolidWorks connection for this MCP server.",
                  configured_process_id=pid, attached_process_id=attached)


def _nonempty(raw: Any, label: str) -> str:
    name = str(raw).strip()
    if not name:
        raise RuntimeError(f"{label} must not be empty.")
    return name


def _changed(doc: Any, message: str, **data: Any) -> dict[str, Any]:
    rebuild(doc)
    problems = whats_wrong(doc)
    return result(not problems, message, problems=problems, **data)


@tool("list_open_documents", "Read-only: list all open documents, including unsaved state and the active title.")
def list_open_documents(args):
    app = running_app()
    active = app.ActiveDoc
    return result(True, "Read open documents.", documents=[document_info(d) for d in as_list(value(app, "GetDocuments"))],
                  active_title=str(value(active, "GetTitle")) if active is not None else None)


def _open_doc(app, name):
    for doc in as_list(value(app, "GetDocuments")):
        if name in (str(value(doc, "GetTitle")), str(value(doc, "GetPathName") or "")):
            return doc
    raise RuntimeError(f"No open document matches '{name}'. Use list_open_documents first.")


@tool("activate_document", "Activate an already-open document by its exact title or full path.", {"name": NAME}, ["name"])
def activate_document(args):
    app = running_app()
    doc = _open_doc(app, str(args["name"]))
    errors = byref_long()
    app.ActivateDoc3(str(value(doc, "GetTitle")), False, 0, errors)
    active = app.ActiveDoc
    ok = active is not None and str(value(active, "GetTitle")) == str(value(doc, "GetTitle"))
    return result(ok, "Activated document." if ok else "Document activation failed.", error_code=int(errors.value))


@tool("close_document", "Close an open document without saving. Refuses unsaved changes unless discard_changes is explicitly true.",
      {"name": NAME, "discard_changes": {"type": "boolean", "default": False}}, ["name"])
def close_document(args):
    app = running_app()
    doc = _open_doc(app, str(args["name"]))
    info = document_info(doc)
    if info["dirty"] and not args.get("discard_changes", False):
        return result(False, "Document has unsaved changes. Save it or explicitly set discard_changes=true.", document=info)
    app.CloseDoc(info["title"])
    remaining = [str(value(d, "GetTitle")) for d in as_list(value(app, "GetDocuments"))]
    return result(info["title"] not in remaining, "Requested document close.", document=info)


def _configuration_doc():
    _, doc = active_document()
    if document_type(doc) not in (1, 2):
        raise RuntimeError("Configurations require a part or assembly document.")
    return flag_methods(doc, "GetConfigurationByName", "ShowConfiguration2", "AddConfiguration3", "DeleteConfiguration2")


def _config_names(doc):
    return [str(n) for n in as_list(value(doc, "GetConfigurationNames"))]


def _config(doc, name):
    if name not in _config_names(doc):
        raise RuntimeError(f"No configuration named '{name}'.")
    return doc.GetConfigurationByName(name)


@tool("list_configurations", "Read-only: list configurations and identify the active configuration.")
def list_configurations(args):
    doc = _configuration_doc()
    active = doc.ConfigurationManager.ActiveConfiguration
    configs = []
    for name in _config_names(doc):
        cfg = _config(doc, name)
        configs.append({"name": name, "description": str(safe(cfg, "Description", "")), "active": name == str(value(active, "Name"))})
    return result(True, "Read configurations.", configurations=configs)


@tool("activate_configuration", "Switch the active part or assembly configuration and rebuild.", {"name": NAME}, ["name"])
def activate_configuration(args):
    doc = _configuration_doc()
    name = str(args["name"])
    _config(doc, name)
    doc.ShowConfiguration2(name)
    actual = str(value(doc.ConfigurationManager.ActiveConfiguration, "Name"))
    if actual != name:
        return result(False, "Configuration activation failed.", active_configuration=actual)
    return _changed(doc, "Activated configuration.", active_configuration=actual)


@tool("create_configuration", "Create and activate a configuration inheriting current document state.",
      {"name": NAME, "description": {"type": "string", "default": ""}}, ["name"])
def create_configuration(args):
    doc = _configuration_doc()
    name = _nonempty(args["name"], "Configuration name")
    if name in _config_names(doc):
        return result(False, "Configuration already exists.")
    doc.AddConfiguration3(name, str(args.get("description", "")), "", 0)
    if name not in _config_names(doc):
        return result(False, "SOLIDWORKS did not create the configuration.")
    _config(doc, name).Description = str(args.get("description", ""))
    return _changed(doc, "Created configuration.", name=name)


@tool("delete_configuration", "Delete a named inactive configuration. Refuses the active or last configuration.", {"name": NAME}, ["name"])
def delete_configuration(args):
    doc = _configuration_doc()
    name = str(args["name"])
    _config(doc, name)
    active = str(value(doc.ConfigurationManager.ActiveConfiguration, "Name"))
    if name == active or len(_config_names(doc)) <= 1:
        return result(False, "Activate another configuration before deleting this one.")
    doc.DeleteConfiguration2(name)
    if name in _config_names(doc):
        return result(False, "SOLIDWORKS did not delete the configuration.")
    return _changed(doc, "Deleted configuration.", name=name)


def _property_manager(doc, configuration):
    if configuration:
        flag_methods(doc, "GetConfigurationByName")
        _config(doc, configuration)
    ext = flag_methods(extension(doc), "CustomPropertyManager")
    return flag_methods(ext.CustomPropertyManager(configuration), "Get6", "GetType2", "Add3", "Set2", "Delete2")


def _property(manager, name):
    if name not in [str(n) for n in as_list(value(manager, "GetNames"))]:
        raise RuntimeError(f"No custom property named '{name}'.")
    raw = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, "")
    resolved = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, "")
    was_resolved = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BOOL, False)
    linked = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BOOL, False)
    manager.Get6(name, False, raw, resolved, was_resolved, linked)
    code = int(manager.GetType2(name))
    return {"name": name, "value": str(raw.value), "resolved_value": str(resolved.value),
            "type": next((k for k, v in PROPERTY_TYPES.items() if v == code), str(code)), "linked": bool(linked.value)}


@tool("list_custom_properties", "Read-only: list raw and resolved custom properties, optionally for a configuration.", {"configuration": CONFIG})
def list_custom_properties(args):
    _, doc = active_document()
    manager = _property_manager(doc, str(args.get("configuration", "")))
    return result(True, "Read custom properties.", properties=[_property(manager, str(n)) for n in as_list(value(manager, "GetNames"))])


@tool("get_custom_property", "Read-only: read one custom property with its resolved value and type.", {"name": NAME, "configuration": CONFIG}, ["name"])
def get_custom_property(args):
    _, doc = active_document()
    return result(True, "Read custom property.", property=_property(_property_manager(doc, str(args.get("configuration", ""))), str(args["name"])))


@tool("set_custom_property", "Add or replace a custom property, then read it back. Configuration may be omitted for document-level properties.",
      {"name": NAME, "value": {"type": "string"}, "type": {"type": "string", "enum": list(PROPERTY_TYPES), "default": "text"}, "configuration": CONFIG}, ["name", "value"])
def set_custom_property(args):
    _, doc = active_document()
    manager = _property_manager(doc, str(args.get("configuration", "")))
    name = _nonempty(args["name"], "Property name")
    text = str(args["value"])
    status = int(manager.Add3(name, PROPERTY_TYPES[str(args.get("type", "text"))], text, 1))
    if status != 0:
        return result(False, "SOLIDWORKS rejected the custom property.", status=status)
    prop = _property(manager, name)
    return _changed(doc, "Set custom property.", property=prop) if prop["value"] == text else result(False, "Property readback differs.", property=prop)


@tool("delete_custom_property", "Delete a named document-level or configuration-specific custom property and verify removal.", {"name": NAME, "configuration": CONFIG}, ["name"])
def delete_custom_property(args):
    _, doc = active_document()
    manager = _property_manager(doc, str(args.get("configuration", "")))
    name = str(args["name"])
    _property(manager, name)
    status = int(manager.Delete2(name))
    if name in [str(n) for n in as_list(value(manager, "GetNames"))]:
        return result(False, "Custom property was not removed.", status=status)
    return _changed(doc, "Deleted custom property.", name=name, status=status)


def _equation_manager(doc):
    if document_type(doc) not in (1, 2):
        raise RuntimeError("Equations require a part or assembly.")
    return flag_methods(value(doc, "GetEquationMgr"), "Equation", "Value", "GlobalVariable", "Add2", "Delete")


def _equations(manager):
    entries = []
    for i in range(int(value(manager, "GetCount"))):
        equation = str(manager.Equation(i))
        evaluated = float(manager.Value(i))
        status = int(value(manager, "Status"))
        entries.append({"index": i, "equation": equation, "value": evaluated,
                        "status": status, "global_variable": bool(manager.GlobalVariable(i))})
    return entries


def _equation_result(doc, manager, message, **data):
    value(manager, "EvaluateAll")
    entries = _equations(manager)
    payload = _changed(doc, message, equations=entries, **data)
    if any(entry["status"] != 0 for entry in entries):
        payload.update(ok=False, message="SOLIDWORKS reports an equation error; inspect equation status.")
    return payload


def _eq_index(manager, raw):
    index = int(raw)
    if index < 0 or index >= int(value(manager, "GetCount")):
        raise RuntimeError("Equation index is out of range. Use list_equations first.")
    return index


@tool("list_equations", "Read-only: list equations/global variables with current indices and evaluated values.")
def list_equations(args):
    _, doc = active_document()
    return result(True, "Read equations.", equations=_equations(_equation_manager(doc)))


@tool("add_equation", 'Append an equation/global variable, e.g. "Width" = 25mm. Uses SOLIDWORKS equation syntax.', {"equation": NAME}, ["equation"])
def add_equation(args):
    _, doc = active_document()
    manager = _equation_manager(doc)
    before = int(value(manager, "GetCount"))
    index = int(manager.Add2(-1, _nonempty(args["equation"], "Equation"), True))
    if index < 0 or int(value(manager, "GetCount")) != before + 1:
        return result(False, "SOLIDWORKS rejected the equation.", index=index)
    return _equation_result(doc, manager, "Added equation.", index=index)


@tool("set_equation", "Replace an equation by current index, preserving its existing configuration scope, and solve it.", {"index": {"type": "integer", "minimum": 0}, "equation": NAME}, ["index", "equation"])
def set_equation(args):
    _, doc = active_document()
    manager = _equation_manager(doc)
    index = _eq_index(manager, args["index"])
    text = _nonempty(args["equation"], "Equation")
    # Indexed PROPSET needs an explicit dispatch invocation: Equation(i) is
    # a flagged PROPGET, and ordinary Python assignment cannot carry i.
    dispid = manager._oleobj_.GetIDsOfNames("Equation")
    manager._oleobj_.Invoke(dispid, 0, pythoncom.DISPATCH_PROPERTYPUT, 0, index, text)
    value(manager, "EvaluateAll")
    actual = str(manager.Equation(index))
    if actual != text:
        return result(False, "Equation readback differs.", equation=actual)
    return _equation_result(doc, manager, "Updated equation.")


@tool("delete_equation", "Delete an equation/global variable by index. Re-list afterwards because indices shift.", {"index": {"type": "integer", "minimum": 0}}, ["index"])
def delete_equation(args):
    _, doc = active_document()
    manager = _equation_manager(doc)
    index = _eq_index(manager, args["index"])
    before = int(value(manager, "GetCount"))
    manager.Delete(index)
    if int(value(manager, "GetCount")) != before - 1:
        return result(False, "Equation was not deleted.")
    return _equation_result(doc, manager, "Deleted equation.")


@tool("evaluate_equations", "Solve all equations and rebuild the active part or assembly, reporting equation status.")
def evaluate_equations(args):
    _, doc = active_document()
    manager = _equation_manager(doc)
    return _equation_result(doc, manager, "Evaluated equations.")


@tool("list_materials", "Read-only: search actual installed material databases for exact names usable by set_material. Returns database paths.",
      {"query": {"type": "string", "default": ""}, "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100}})
def list_materials(args):
    app = running_app()
    paths = [Path(str(p)) for p in as_list(value(app, "GetMaterialDatabases"))]
    query = str(args.get("query", "")).casefold()
    limit = int(args.get("limit", 100))
    if not 1 <= limit <= 500:
        raise RuntimeError("limit must be between 1 and 500.")
    matches, failures = [], []
    total = 0
    for path in paths:
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError) as exc:
            failures.append({"database": str(path), "error": str(exc)})
            continue
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1].lower() != "material":
                continue
            name = node.get("name", "")
            if name and query in name.casefold():
                total += 1
                if len(matches) < limit:
                    matches.append({"name": name, "database": str(path)})
    return result(not failures, "Read material databases." if not failures else "Some material databases could not be read.", materials=matches, total_matches=total, truncated=total > limit, failures=failures)
