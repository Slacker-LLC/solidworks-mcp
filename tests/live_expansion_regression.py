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

"""Live checks for expanded tools; creates scratch documents and restores originals.

Run with SOLIDWORKS already open. Only newly-created documents are changed.
Saved assembly fixtures stay under SW_MCP_OUTPUT_ROOT/.expansion-tests/<uuid>.
"""

import json
import math
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from solidworks_mcp import server
from solidworks_mcp.sw_core import (
    BODY_SHEET, HANDLERS, OUTPUT_ROOT, as_list, byref_long, get_bodies,
    require_part, running_app, safe, value,
)

CALLED = set()


def call(tool_name, **args):
    payload = HANDLERS[tool_name](args)
    if not payload["ok"]:
        raise AssertionError(f"{tool_name}: {payload}")
    CALLED.add(tool_name)
    print(f"PASS {tool_name}", flush=True)
    return payload.get("data", {})


def volume():
    return call("get_mass_properties")["volume_mm3"]


def near(actual, expected, label):
    if not math.isclose(actual, expected, rel_tol=1e-6, abs_tol=0.05):
        raise AssertionError(f"{label}: actual={actual}, expected={expected}")


def block():
    title = call("create_new_document", kind="part")["document"]["title"]
    call("create_sketch", plane="front", name="Base")
    call("draw_rectangle", x1_mm=0, y1_mm=0, x2_mm=10, y2_mm=10)
    call("close_sketch")
    call("boss_extrude", sketch_name="Base", depth_mm=10)
    near(volume(), 1000, "block volume")
    return title


def test_management():
    title = block()
    docs = call("list_open_documents")
    assert title in {d["title"] for d in docs["documents"]}
    call("activate_document", name=title)
    default = next(c["name"] for c in call("list_configurations")["configurations"] if c["active"])
    call("create_configuration", name="MCP_Alternate", description="MCP regression")
    call("activate_configuration", name=default)
    call("delete_configuration", name="MCP_Alternate")
    for scope in ("", default):
        call("set_custom_property", name="MCP_Test", value="Fixture", configuration=scope)
        read = call("get_custom_property", name="MCP_Test", configuration=scope)["property"]
        assert read["value"] == "Fixture", read
        listed = call("list_custom_properties", configuration=scope)["properties"]
        assert any(p["name"] == "MCP_Test" for p in listed)
        call("delete_custom_property", name="MCP_Test", configuration=scope)
        assert not any(p["name"] == "MCP_Test" for p in call("list_custom_properties", configuration=scope)["properties"])
    added = call("add_equation", equation='"MCP_Width" = 25mm')
    index = added["index"]
    entries = call("list_equations")["equations"]
    assert entries[index]["global_variable"], entries
    call("set_equation", index=index, equation='"MCP_Width" = 30mm')
    call("evaluate_equations")
    entries = call("list_equations")["equations"]
    assert "30" in entries[index]["equation"], entries
    call("delete_equation", index=index)
    assert not call("list_equations")["equations"]
    materials = call("list_materials", query="", limit=2)
    assert len(materials["materials"]) > 0, materials
    exact = materials["materials"][0]
    matched = call("list_materials", query=exact["name"], limit=500)
    assert exact in matched["materials"], matched
    refused = HANDLERS["close_document"]({"name": title})
    assert not refused["ok"], refused
    call("close_document", name=title, discard_changes=True)


def test_multibody():
    block()
    call("scale_bodies", selection={"bodies": [0]}, factor=2)
    near(volume(), 8000, "scaled volume")
    bounds = call("get_bounding_box")
    for actual in bounds["size_mm"]:
        near(actual, 20, "uniform scale bounds")
    block()
    call("scale_bodies", selection={"bodies": [0]}, factor=2, uniform=False, y_factor=3, z_factor=4, about="origin")
    near(volume(), 24000, "nonuniform scale volume")
    block()
    call("scale_bodies", selection={"bodies": [0]}, factor=1, uniform=False, y_factor=2)
    call("move_copy_bodies", selection={"bodies": [0]}, rotation_z_deg=90)
    bounds = call("get_bounding_box")
    near(bounds["size_mm"][0], 20, "rotated X size")
    near(bounds["size_mm"][1], 10, "rotated Y size")
    near(volume(), 2000, "rotation volume")
    block()
    call("move_copy_bodies", selection={"bodies": [0]}, x_mm=5, copy=True)
    assert len(call("list_bodies")["bodies"]) == 2
    near(volume(), 2000, "copied volume")
    call("combine_bodies", selection={"bodies": [0, 1]}, operation="add")
    near(volume(), 1500, "union volume")
    block()
    call("move_copy_bodies", selection={"bodies": [0]}, x_mm=5, copy=True)
    call("combine_bodies", selection={"bodies": [0, 1]}, operation="subtract")
    near(volume(), 500, "subtraction volume")
    block()
    call("move_copy_bodies", selection={"bodies": [0]}, x_mm=5, copy=True)
    call("combine_bodies", selection={"bodies": [0, 1]}, operation="intersect")
    near(volume(), 500, "intersection volume")
    block()
    call("move_copy_bodies", selection={"bodies": [0]}, x_mm=20, copy=True, copies=2)
    call("delete_bodies", selection={"bodies": [1]})
    near(volume(), 2000, "delete-body volume")
    call("delete_bodies", selection={"bodies": [0]}, keep_selected=True)
    near(volume(), 1000, "keep-body volume")


def test_surfaces_and_helix():
    call("create_new_document", kind="part")
    call("create_sketch", plane="front", name="SurfaceLine")
    call("draw_line", x1_mm=0, y1_mm=0, x2_mm=10, y2_mm=0)
    call("close_sketch")
    call("surface_extrude", sketch_name="SurfaceLine", depth_mm=20)
    near(sum(b["area_mm2"] for b in call("list_surface_bodies")["surface_bodies"]), 200, "surface tool area")
    _, doc = require_part()
    bodies = get_bodies(doc, BODY_SHEET)
    near(sum(float(value(face, "GetArea")) for b in bodies for face in as_list(value(b, "GetFaces"))) * 1e6, 200, "surface area")
    call("create_new_document", kind="part")
    call("create_sketch", plane="front", name="SurfaceRectangle")
    call("draw_rectangle", x1_mm=0, y1_mm=0, x2_mm=10, y2_mm=10)
    call("close_sketch")
    call("planar_surface", sketch_name="SurfaceRectangle")
    _, doc = require_part()
    near(sum(float(value(face, "GetArea")) for b in get_bodies(doc, BODY_SHEET) for face in as_list(value(b, "GetFaces"))) * 1e6, 100, "planar area")
    call("create_new_document", kind="part")
    call("create_sketch", plane="front", name="HelixCircle")
    call("draw_circle", x_mm=0, y_mm=0, radius_mm=5)
    call("close_sketch")
    helix = call("create_helix", sketch_name="HelixCircle", pitch_mm=2, revolutions=3)
    near(helix["height_mm"], 6, "helix height")
    features = call("list_features")["features"]
    assert any(f["type"] == "Helix" for f in features), features


def test_components():
    part_title = block()
    default = next(c["name"] for c in call("list_configurations")["configurations"] if c["active"])
    call("create_configuration", name="MCP_Alternate")
    call("activate_configuration", name=default)
    fixture = OUTPUT_ROOT / ".expansion-tests" / uuid.uuid4().hex / "block.SLDPRT"
    call("save_document", path=str(fixture))
    assembly_title = call("create_new_document", kind="assembly")["document"]["title"]
    inserted = call("insert_component", path=str(fixture))
    call("activate_document", name=assembly_title)
    name = inserted["component"]
    call("set_component_visibility", name=name, visible=False)
    call("set_component_visibility", name=name, visible=True)
    call("set_component_configuration", name=name, configuration="MCP_Alternate")
    call("set_component_suppression", name=name, suppressed=True)
    call("set_component_suppression", name=name, suppressed=False)


def main():
    app = running_app()
    initial = {str(value(d, "GetTitle")) for d in as_list(value(app, "GetDocuments"))}
    original = str(value(app.ActiveDoc, "GetTitle")) if app.ActiveDoc is not None else ""
    try:
        test_management()
        test_multibody()
        test_surfaces_and_helix()
        test_components()
        expected = {t.name for t in server.TOOLS if HANDLERS[t.name].__module__ == "solidworks_mcp.sw_manage"} | {
            "scale_bodies", "move_copy_bodies", "combine_bodies", "delete_bodies", "surface_extrude",
            "planar_surface", "create_helix", "list_surface_bodies",
            "set_component_visibility", "set_component_suppression", "set_component_configuration"}
        assert expected <= CALLED, expected - CALLED
        print(f"Expanded tools verified: {len(expected)}; total tools: {len(server.TOOLS)}", flush=True)
    finally:
        # Assemblies first, so their referenced scratch parts can also close.
        docs = as_list(value(app, "GetDocuments"))
        docs.sort(key=lambda d: int(value(d, "GetType")) != 2)
        for doc in docs:
            title = str(value(doc, "GetTitle"))
            if title not in initial:
                app.CloseDoc(title)
        if original:
            app.ActivateDoc3(original, False, 0, byref_long())
        assert {str(value(d, "GetTitle")) for d in as_list(value(app, "GetDocuments"))} == initial
        print("Scratch documents closed; original documents restored.", flush=True)


if __name__ == "__main__":
    main()
