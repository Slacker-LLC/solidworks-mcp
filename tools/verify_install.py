# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for details.

"""Verify an isolated wheel install, offline tests and actual MCP stdio discovery.

SOLIDWORKS must already be running. --scratch exercises geometry calls on a new
part and closes it afterwards; default calls only read the active document.
"""

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import sys
import unittest


async def protocol(install, expected, catalog, scratch):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ, PYTHONPATH=str(install))
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "solidworks_mcp.server"],
                                       env=env, cwd=str(install.parent))
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            response = await session.list_tools()
            if len(response.tools) != expected or len({tool.name for tool in response.tools}) != expected:
                raise RuntimeError("MCP tool count or uniqueness differs from the expected release.")
            async def call(name, arguments=None):
                output = await session.call_tool(name, arguments or {})
                if output.isError:
                    raise RuntimeError(f"Installed MCP protocol error: {name}: {output.content[0].text}")
                payload = json.loads(output.content[0].text)
                if not payload["ok"]:
                    raise RuntimeError(f"Installed MCP call failed: {name}: {payload}")
                return payload.get("data", {})
            async def rejected_call(name, arguments):
                output = await session.call_tool(name, arguments)
                if output.isError:
                    raise RuntimeError(f"Expected a structured native failure, got a protocol error: {name}.")
                payload = json.loads(output.content[0].text)
                if payload["ok"]:
                    raise RuntimeError(f"Installed MCP falsely accepted an ignored native edit: {name}.")
                return payload.get("data", {})
            connections = await call("list_solidworks_sessions")
            attached = connections["attached_process_id"]
            if attached is None:
                raise RuntimeError("Cannot identify the installed MCP SolidWorks connection.")
            selected = await call("select_solidworks_session", {"process_id": attached})
            if selected["attached_process_id"] != attached:
                raise RuntimeError("Installed MCP instance selection differs.")
            initial = await call("list_open_documents")
            original = initial["active_title"]
            title = None
            wrap_title = None
            dome_title = None
            sketch3d_title = None
            dimension_title = None
            try:
                if scratch:
                    title = (await call("create_new_document", {"kind": "part"}))["document"]["title"]
                    points = [[0, 0, 0], [10, 5, 2], [20, 0, 4]]
                    await call("create_curve_through_points", {"name": "InstalledCurve", "points_mm": points})
                    actual = await call("get_curve_points", {"name": "InstalledCurve"})
                    if actual["curve"]["points_mm"] != points:
                        raise RuntimeError("Installed MCP curve coordinate readback differs.")
                    await call("create_coordinate_system", {"name": "InstalledCS", "origin_mm": [2, 3, 4]})
                    await call("create_sketch", {"plane": "front", "name": "InstalledRevolveProfile"})
                    await call("draw_line", {"x1_mm": 5, "y1_mm": 0, "x2_mm": 5, "y2_mm": 10})
                    await call("draw_centerline", {"x1_mm": 0, "y1_mm": 0, "x2_mm": 0, "y2_mm": 10})
                    await call("close_sketch")
                    await call("surface_revolve", {"sketch_name": "InstalledRevolveProfile"})
                    surfaces = await call("list_surface_bodies")
                    area = sum(body["area_mm2"] for body in surfaces["surface_bodies"])
                    if not math.isclose(area, 2 * math.pi * 5 * 10, rel_tol=1e-7):
                        raise RuntimeError("Installed MCP revolved surface area differs.")
                    edges = await call("list_edges", {"body_type": "surface"})
                    if len(edges["edges"]) != 2:
                        raise RuntimeError("Installed MCP surface-edge enumeration differs.")
                    await call("extend_surface", {"selection": {"surface_edges": [edges["edges"][0]["index"]]}, "distance_mm": 5})
                    surfaces = await call("list_surface_bodies")
                    actual_area = sum(body["area_mm2"] for body in surfaces["surface_bodies"])
                    if not math.isclose(actual_area, 2 * math.pi * 5 * 15, rel_tol=1e-6, abs_tol=0.05):
                        raise RuntimeError(f"Installed MCP surface extension area differs: {actual_area}; edges={edges['edges']}.")
                    await call("create_sketch", {"plane": "front", "name": "InstalledHoleProfile"})
                    await call("draw_rectangle", {"x1_mm": 20, "y1_mm": 20, "x2_mm": 30, "y2_mm": 30})
                    await call("draw_circle", {"x_mm": 25, "y_mm": 25, "radius_mm": 1})
                    await call("close_sketch")
                    await call("planar_surface", {"sketch_name": "InstalledHoleProfile"})
                    faces = await call("list_faces", {"body_type": "surface", "surface_type": "plane"})
                    if len(faces["faces"]) != 1:
                        raise RuntimeError("Installed MCP surface-face enumeration differs.")
                    await call("untrim_surface", {"selection": {"surface_faces": [faces["faces"][0]["index"]]}, "face_mode": "internal"})
                    faces = await call("list_faces", {"body_type": "surface", "surface_type": "plane"})
                    if not math.isclose(faces["faces"][0]["area_mm2"], 100, rel_tol=1e-7):
                        raise RuntimeError("Installed MCP internal untrim area differs.")
                    await call("create_sketch", {"plane": "front", "name": "InstalledFillProfile"})
                    await call("draw_rectangle", {"x1_mm": 60, "y1_mm": 60, "x2_mm": 70, "y2_mm": 70})
                    await call("close_sketch")
                    filled = await call("fill_surface", {"boundaries": [{"selection": {"sketches": ["InstalledFillProfile"]}}]})
                    if filled["native_continuity_controls"] != [0]:
                        raise RuntimeError("Installed MCP filled-surface continuity readback differs.")
                    faces = await call("list_faces", {"body_type": "surface", "surface_type": "plane"})
                    filled_face = next(face for face in faces["faces"] if face["point_mm"][0] >= 50)
                    if not math.isclose(filled_face["area_mm2"], 100, rel_tol=1e-7):
                        raise RuntimeError("Installed MCP filled-surface area differs.")
                    edges = await call("list_edges", {"body_type": "surface"})
                    boundary = next(edge["index"] for edge in edges["edges"] if edge["body_index"] == filled_face["body_index"])
                    await call("ruled_surface", {"selection": {"surface_edges": [boundary]}, "mode": "tapered",
                               "length_mm": 5, "angle_deg": 30, "direction_selection": {"planes": ["front"]}})
                    faces = await call("list_faces", {"body_type": "surface"})
                    patch = next(face for face in faces["faces"] if math.isclose(face["area_mm2"], 50, rel_tol=1e-7))
                    if not math.isclose(abs(patch["normal"][2]), 0.5, abs_tol=1e-6):
                        raise RuntimeError("Installed MCP ruled-surface taper inclination differs.")
                    await call("create_sketch", {"plane": "front", "name": "InstalledSelectiveHoles"})
                    await call("draw_rectangle", {"x1_mm": 90, "y1_mm": 90, "x2_mm": 100, "y2_mm": 100})
                    for x in (93, 97):
                        await call("draw_circle", {"x_mm": x, "y_mm": 95, "radius_mm": 1})
                    await call("close_sketch")
                    await call("planar_surface", {"sketch_name": "InstalledSelectiveHoles"})
                    faces = (await call("list_faces", {"body_type": "surface"}))["faces"]
                    hole_face = next(face for face in faces if face["point_mm"][0] >= 90)
                    edges = (await call("list_edges", {"body_type": "surface"}))["edges"]
                    holes = [edge["index"] for edge in edges
                             if edge["body_index"] == hole_face["body_index"] and edge["curve_type"] == "circle"]
                    if len(holes) != 2:
                        raise RuntimeError("Installed MCP hole fixture topology differs.")
                    await call("delete_surface_holes", {"selection": {"surface_edges": [holes[0]]}})
                    faces = (await call("list_faces", {"body_type": "surface"}))["faces"]
                    hole_face = next(face for face in faces if face["point_mm"][0] >= 90)
                    if not math.isclose(hole_face["area_mm2"], 100 - math.pi, rel_tol=1e-7):
                        raise RuntimeError("Installed MCP selective hole removal changed an unselected hole.")
                    await call("create_sketch", {"plane": "front", "name": "InstalledSweepProfile"})
                    await call("draw_line", {"x1_mm": -5, "y1_mm": 200, "x2_mm": 5, "y2_mm": 200})
                    await call("close_sketch")
                    await call("create_sketch", {"plane": "right", "name": "InstalledSweepPath"})
                    await call("draw_line", {"x1_mm": 0, "y1_mm": 200, "x2_mm": -10, "y2_mm": 200})
                    await call("close_sketch")
                    swept = await call("surface_sweep", {"profile_sketch": "InstalledSweepProfile", "path_sketch": "InstalledSweepPath",
                                       "twist_control": "constant_twist", "twist_angle_deg": 90, "reverse_twist": True})
                    if not math.isclose(swept["native_settings"]["twist_angle_deg"], -90, abs_tol=1e-8):
                        raise RuntimeError("Installed MCP signed sweep twist readback differs.")
                    edges = (await call("list_edges", {"body_type": "surface"}))["edges"]
                    midpoints = [edge["point_mm"] for edge in edges if edge.get("point_mm")
                                 and edge["point_mm"][1] > 190 and math.isclose(edge["point_mm"][2], 5, abs_tol=0.02)]
                    if len(midpoints) != 2 or any(p[0] * (p[1] - 200) >= 0 for p in midpoints):
                        raise RuntimeError(f"Installed MCP reversed sweep geometry differs: {midpoints}.")
                    await call("create_sketch", {"plane": "front", "name": "InstalledMidProfile"})
                    await call("draw_rectangle", {"x1_mm": 300, "y1_mm": 300, "x2_mm": 320, "y2_mm": 310})
                    await call("close_sketch")
                    await call("boss_extrude", {"sketch_name": "InstalledMidProfile", "depth_mm": 2})
                    middle = (await call("mid_surface", {"name": "InstalledMid"}))["midsurface"]
                    if middle["face_pair_count"] != 1 or middle["sheet_count"] != 1:
                        raise RuntimeError("Installed MCP midsurface topology differs.")
                    if not math.isclose(middle["area_mm2"], 200, abs_tol=1e-7) or not math.isclose(middle["face_pairs"][0]["thickness_mm"], 2, abs_tol=1e-7):
                        raise RuntimeError("Installed MCP midsurface area/thickness differs.")
                    if not math.isclose(middle["faces"][0]["point_mm"][2], 1, abs_tol=1e-7):
                        raise RuntimeError("Installed MCP midsurface placement differs.")
                    if (await call("get_mid_surface_data", {"name": "InstalledMid"}))["midsurface"] != middle:
                        raise RuntimeError("Installed MCP read-only midsurface inspection differs.")
                    await call("create_sketch", {"plane": "front", "name": "InstalledTrimProfile"})
                    await call("draw_rectangle", {"x1_mm": 400, "y1_mm": 400, "x2_mm": 410, "y2_mm": 410})
                    await call("close_sketch")
                    await call("planar_surface", {"sketch_name": "InstalledTrimProfile", "name": "InstalledTrimSource"})
                    bodies = (await call("list_surface_bodies"))["surface_bodies"]
                    source = next(body["index"] for body in bodies if body["name"] == "InstalledTrimSource")
                    await call("create_plane", {"mode": "offset", "selection": {"planes": ["right"]}, "distance_mm": 404, "name": "InstalledKnife"})
                    trim_args = {"surface_body_indices": [source], "trim_selection": {"planes": ["InstalledKnife"]}}
                    before = await call("list_features")
                    regions = (await call("preview_surface_trim", trim_args))["regions"]
                    if before != await call("list_features") or [round(r["area_mm2"]) for r in regions] != [40, 60]:
                        raise RuntimeError("Installed MCP trim preview changed features or returned wrong regions.")
                    trimmed = await call("trim_surface", {**trim_args, "region_indices": [0], "name": "InstalledTrim"})
                    bodies = (await call("list_surface_bodies"))["surface_bodies"]
                    actual = next(body for body in bodies if body["name"] == "InstalledTrim")
                    if not math.isclose(actual["area_mm2"], 40, abs_tol=1e-7):
                        raise RuntimeError("Installed MCP trim retained the wrong sheet area.")
                    if (await call("get_surface_trim_data", {"name": "InstalledTrim"}))["trim"] != trimmed["trim"]:
                        raise RuntimeError("Installed MCP trim definition readback differs.")
                    for name, coords in (("BoundaryBottom", [500, 500, 510, 500]), ("BoundaryTop", [500, 510, 510, 510]),
                                         ("BoundaryLeft", [500, 500, 500, 510]), ("BoundaryRight", [510, 500, 510, 510])):
                        await call("create_sketch", {"plane": "front", "name": name})
                        await call("draw_line", dict(zip(("x1_mm", "y1_mm", "x2_mm", "y2_mm"), coords)))
                        await call("close_sketch")
                    boundary = (await call("boundary_surface", {
                        "direction1": [{"selection": {"sketches": [n]}} for n in ("BoundaryBottom", "BoundaryTop")],
                        "direction2": [{"selection": {"sketches": [n]}} for n in ("BoundaryLeft", "BoundaryRight")],
                        "name": "InstalledBoundary"}))["boundary"]
                    if not math.isclose(boundary["feature_face_area_mm2"], 100, abs_tol=1e-7) or [d["curve_count"] for d in boundary["directions"]] != [2, 2]:
                        raise RuntimeError("Installed MCP boundary area or curve groups differ.")
                    if (await call("get_boundary_feature_data", {"name": "InstalledBoundary"}))["boundary"] != boundary:
                        raise RuntimeError("Installed MCP boundary definition query differs.")
                await call("list_reference_points")
                systems = await call("list_coordinate_systems")
                if scratch and systems["coordinate_systems"][0]["origin_mm"] != [2, 3, 4]:
                    raise RuntimeError("Installed MCP coordinate-system origin readback differs.")
                if scratch:
                    wrap_title = (await call("create_new_document", {"kind": "part"}))["document"]["title"]
                    await call("create_sketch", {"plane": "front", "name": "WrapBase"})
                    await call("draw_circle", {"x_mm": 0, "y_mm": 0, "radius_mm": 10})
                    await call("close_sketch")
                    await call("boss_extrude", {"sketch_name": "WrapBase", "depth_mm": 30})
                    await call("create_plane", {"mode": "offset", "selection": {"planes": ["right"]}, "distance_mm": 10, "name": "WrapPlane"})
                    await call("create_sketch", {"plane_name": "WrapPlane", "name": "WrapProfile"})
                    await call("draw_rectangle", {"x1_mm": -20, "y1_mm": -2, "x2_mm": -10, "y2_mm": 2})
                    await call("close_sketch")
                    await call("create_sketch", {"plane_name": "WrapPlane", "name": "WideWrapProfile"})
                    await call("draw_rectangle", {"x1_mm": -20, "y1_mm": -4, "x2_mm": -10, "y2_mm": 4})
                    await call("close_sketch")
                    index = next(f["index"] for f in (await call("list_faces"))["faces"] if f["surface_type"] == "cylinder")
                    wrapped = await call("wrap_sketch", {"sketch_name": "WrapProfile", "face_index": index, "name": "InstalledWrap"})
                    if not math.isclose(wrapped["volume_change_mm3"], 42, abs_tol=1e-6):
                        raise RuntimeError("Installed MCP wrap volume differs.")
                    if (await call("get_wrap_data", {"name": "InstalledWrap"}))["wrap"] != wrapped["wrap"]:
                        raise RuntimeError("Installed MCP wrap definition differs.")
                    source_edit = await call("set_wrap_parameters", {"name": "InstalledWrap", "source_sketch_name": "WideWrapProfile"})
                    if not source_edit["wrap"]["source_reference_confirmed"] or not math.isclose(source_edit["volume_change_from_input_mm3"], 84, abs_tol=1e-6):
                        raise RuntimeError("Installed MCP wrap source reference or geometry differs.")
                    target_data = source_edit["wrap"]["target_face"]
                    if target_data.get("radius_mm") != 10 or not math.isclose(target_data["area_mm2"], 600 * math.pi, abs_tol=1e-6):
                        raise RuntimeError("Installed MCP wrap original-target geometry differs.")
                    await call("set_wrap_parameters", {"name": "InstalledWrap", "source_sketch_name": "WrapProfile"})
                    edited = await call("set_wrap_parameters", {"name": "InstalledWrap", "thickness_mm": 2})
                    if not math.isclose(edited["volume_change_from_input_mm3"], 88, abs_tol=1e-6) or edited["wrap"]["thickness_mm"] != 2:
                        raise RuntimeError("Installed MCP wrap thickness edit differs.")
                    directed = await call("set_wrap_parameters", {"name": "InstalledWrap", "pull_selection": {"planes": ["right"]}})
                    if not directed["wrap"]["pull_reference_confirmed"] or not math.isclose(directed["volume_change_from_input_mm3"], 79.9100175565, abs_tol=.001):
                        raise RuntimeError("Installed MCP wrap pull identity or geometry differs.")
                    await call("set_wrap_parameters", {"name": "InstalledWrap", "mode": "scribe"})
                    await call("create_sketch", {"plane_name": "WrapPlane", "name": "MultiProfile"})
                    await call("draw_rectangle", {"x1_mm": -25, "y1_mm": -4, "x2_mm": -5, "y2_mm": 4})
                    await call("close_sketch")
                    indices = [f["index"] for f in (await call("list_faces"))["faces"] if f["surface_type"] == "cylinder"]
                    multi = await call("wrap_sketch", {"sketch_name": "MultiProfile", "face_indices": indices, "method": "spline", "name": "InstalledMultiWrap"})
                    if len(indices) != 2 or not math.isclose(multi["volume_change_mm3"], 168, abs_tol=.1):
                        raise RuntimeError("Installed MCP multiple-face wrap geometry differs.")
                if scratch:
                    dome_title = (await call("create_new_document", {"kind": "part"}))["document"]["title"]
                    await call("create_sketch", {"plane": "front", "name": "DomeBase"})
                    await call("draw_circle", {"x_mm": 0, "y_mm": 0, "radius_mm": 10})
                    await call("close_sketch")
                    await call("boss_extrude", {"sketch_name": "DomeBase", "depth_mm": 30})
                    await call("create_plane", {"mode": "offset", "selection": {"planes": ["front"]}, "distance_mm": 35, "name": "InstalledApexPlane"})
                    await call("create_sketch", {"plane_name": "InstalledApexPlane", "name": "InstalledApex"})
                    await call("draw_point", {"x_mm": 3, "y_mm": 0})
                    await call("draw_point", {"x_mm": -3, "y_mm": 0})
                    await call("close_sketch")
                    index = next(f["index"] for f in (await call("list_faces"))["faces"] if f.get("normal", [0, 0, 0])[2] > .99)
                    created = await call("dome", {"face_index": index, "height_mm": 5, "name": "InstalledDome"})
                    if not math.isclose(created["volume_change_mm3"], math.pi * 5 * 325 / 6, abs_tol=.001):
                        raise RuntimeError("Installed MCP dome spherical volume differs.")
                    queried = (await call("get_dome_data", {"name": "InstalledDome"}))["dome"]
                    if queried["height_mm"] != 5 or queried["reverse_direction"]:
                        raise RuntimeError("Installed MCP dome definition differs.")
                    await call("set_dome_parameters", {"name": "InstalledDome", "height_mm": 8})
                    await call("set_dome_parameters", {"name": "InstalledDome", "elliptical": True})
                    edited = await call("set_dome_parameters", {"name": "InstalledDome", "reverse_direction": True})
                    if not edited["dome"]["ellipsoid_check"]["confirmed"] or not math.isclose(edited["volume_change_from_input_mm3"], -2 * math.pi * 100 * 8 / 3, abs_tol=.2):
                        raise RuntimeError("Installed MCP dome ellipsoid geometry differs.")
                    await call("set_dome_parameters", {"name": "InstalledDome", "height_mm": 5, "reverse_direction": False, "elliptical": False})
                    constrained = await call("set_dome_parameters", {"name": "InstalledDome", "constraint_sketch_name": "InstalledApex"})
                    check = constrained["dome"]["constraint_check"]
                    if not constrained["dome"]["constraint_reference_confirmed"] or not check["confirmed"] or check["sample_count"] != 2:
                        raise RuntimeError("Installed MCP whole point-sketch constraint differs.")
                    if sorted(round(p[0]) for p in check["samples_mm"]) != [-3, 3] or any(abs(p[2] - 35) > .001 for p in check["samples_mm"]):
                        raise RuntimeError("Installed MCP constraint model coordinates differ.")
                    rejected = await rejected_call("set_dome_parameters", {"name": "InstalledDome", "clear_constraint": True, "height_mm": 5})
                    if rejected["parameters_confirmed"] or not rejected["dome"]["has_constraint"] or rejected["dome"]["constraint_reference_confirmed"]:
                        raise RuntimeError("Installed MCP ignored-constraint-clear readback differs.")
                if scratch:
                    sketch3d_title = (await call("create_new_document", {"kind": "part"}))["document"]["title"]
                    await call("create_3d_sketch", {"name": "InstalledSpatial"})
                    await call("draw_line", {"x1_mm": 0, "y1_mm": 0, "x2_mm": 10, "y2_mm": 20, "z2_mm": 30})
                    point = await call("draw_point", {"x_mm": 5, "y_mm": -4, "z_mm": 7})
                    if math.dist(point["point_mm"], [5, -4, 7]) > .001:
                        raise RuntimeError("Installed MCP XYZ point differs.")
                    await call("draw_spline", {"points": [{"x_mm": 20, "y_mm": 0, "z_mm": 0},
                                                           {"x_mm": 25, "y_mm": 5, "z_mm": 8}, {"x_mm": 30, "y_mm": 0, "z_mm": 15}]})
                    segments = (await call("list_sketch_segments"))["segments"]
                    line = next(s for s in segments if s["type"] == "line")
                    if math.dist(line["end_mm"], [10, 20, 30]) > .001 or abs(line["length_mm"] - math.sqrt(1400)) > .001:
                        raise RuntimeError("Installed MCP XYZ line geometry differs.")
                    points = await call("list_sketch_points")
                    if not points["is_3d"] or points["sketch"] != "InstalledSpatial" or len(points["points"]) != 6:
                        raise RuntimeError("Installed MCP 3D sketch point enumeration differs.")
                    if not (await call("close_sketch"))["is_3d"]:
                        raise RuntimeError("Installed MCP closed the wrong sketch mode.")
                    await call("create_sketch", {"plane": "front", "name": "InstalledPlanar"})
                    await call("draw_point", {"x_mm": 1, "y_mm": 2})
                    await call("close_sketch")
                    reopened = await call("edit_sketch", {"sketch_name": "InstalledSpatial"})
                    if not reopened["is_3d"] or not reopened["reference_confirmed"] or (await call("list_sketch_points"))["sketch"] != "InstalledSpatial":
                        raise RuntimeError("Installed MCP reopened-sketch identity or name differs.")
                    await rejected_call("create_3d_sketch", {"name": "Unexpected"})
                    await call("close_sketch")
                    await call("create_3d_sketch", {"name": "InstalledTubePath"})
                    await call("draw_line", {"x1_mm": 0, "y1_mm": 0, "x2_mm": 10, "y2_mm": 20, "z2_mm": 30})
                    await call("surface_sweep", {"path_sketch": "InstalledTubePath", "circular_diameter_mm": 2})
                    area = sum(b["area_mm2"] for b in (await call("list_surface_bodies"))["surface_bodies"])
                    if abs(area - 2 * math.pi * math.sqrt(1400)) > .01 or (await call("list_sketches"))["sketch_open"]:
                        raise RuntimeError("Installed MCP 3D path sweep geometry or automatic sketch exit differs.")
                    await call("create_3d_sketch", {"name": "InstalledSpatialArcs"})
                    await call("draw_centerline", {"x1_mm": 80, "y1_mm": 0, "x2_mm": 90, "y2_mm": 20, "z2_mm": 30})
                    construction = (await call("list_sketch_segments"))["segments"][0]
                    if not construction["construction"] or math.dist(construction["end_mm"], [90,20,30]) > .001:
                        raise RuntimeError("Installed MCP spatial construction line differs.")
                    for start, end, middle, angle in (([100,0,0],[100,10,0],[100,5,5],math.pi),
                                                      ([120,0,0],[120,5,5],[120,-5,5],1.5*math.pi)):
                        coordinates = {f"{k}{i}_mm": v for i,p in enumerate((start,end,middle),1) for k,v in zip("xyz",p)}
                        arc = await call("draw_3point_arc", coordinates)
                        if abs(arc["radius_mm"]-5) > .01 or abs(arc["length_mm"]-5*angle) > .01 or arc["max_point_gap_mm"] > .01:
                            raise RuntimeError("Installed MCP spatial arc geometry differs.")
                    await call("close_sketch")
                    await call("create_sketch", {"plane": "right", "name": "InstalledSpatialSource"})
                    await call("draw_circle", {"x_mm": 240, "y_mm": 20, "radius_mm": 2})
                    ellipse = await call("draw_ellipse", {"center_x_mm": 210, "center_y_mm": 20, "major_x_mm": 220, "major_y_mm": 20, "minor_x_mm": 210, "minor_y_mm": 25})
                    if abs(ellipse["length_mm"]-48.4422411027) > .01 or ellipse["max_point_gap_mm"] > .01:
                        raise RuntimeError("Installed MCP ellipse geometry differs.")
                    await call("close_sketch")
                    await call("create_3d_sketch", {"name": "InstalledSpatialConverted"})
                    await call("draw_line", {"x1_mm": 100, "y1_mm": 100, "z1_mm": 100, "x2_mm": 110, "y2_mm": 100, "z2_mm": 100})
                    converted = await call("convert_entities", {"selection": {"sketch_name": "InstalledSpatialSource", "sketch_segments": [0,1]}, "chain": False})
                    if converted["native_types"] != [1,3] or not converted["geometry_correspondence_confirmed"] or converted["max_source_point_gap_mm"] > .01:
                        raise RuntimeError("Installed MCP spatial circle/ellipse conversion differs.")
                    await call("close_sketch")
                    await call("create_3d_sketch", {"name": "InstalledAxisDimensions"})
                    await call("draw_line", {"x1_mm":0,"y1_mm":0,"x2_mm":10,"y2_mm":20,"z2_mm":30})
                    refused=await rejected_call("add_3d_dimension", {"axis":"x","point_indices":[0,1],"value_mm":15,"place_x_mm":50,"place_y_mm":50,"place_z_mm":50})
                    if not refused["selected_references_confirmed"] or refused["projected_before_mm"]!=10 or (await call("list_dimensions", {"feature_name":"InstalledAxisDimensions"}))["dimensions"]:
                        raise RuntimeError("Installed MCP converted-model refusal differs or created an unreported dimension.")
                    await call("close_sketch")
                    dimension_title=(await call("create_new_document", {"kind":"part"}))["document"]["title"]
                    await call("create_3d_sketch", {"name":"InstalledAxisDimensions"})
                    await call("draw_line", {"x1_mm":0,"y1_mm":0,"x2_mm":10,"y2_mm":20,"z2_mm":30})
                    names=[]
                    for axis,target in zip("xyz",(15,25,35)):
                        added=await call("add_3d_dimension", {"axis":axis,"point_indices":[0,1],"value_mm":target,"place_x_mm":50,"place_y_mm":50,"place_z_mm":50})
                        if not added["edit_context_restored"] or not added["point_references_confirmed"] or abs(added["projected_distance_mm"]-target)>.01:
                            raise RuntimeError("Installed MCP axis dimension or driven geometry differs.")
                        names.append(added["full_name"])
                    dimension_info=(await call("list_dimensions", {"feature_name":"InstalledAxisDimensions"}))["dimensions"]
                    if len(dimension_info)!=3 or any(d["driven"] or d["driven_state"]!=2 for d in dimension_info):
                        raise RuntimeError("Installed MCP driving dimension state readback differs.")
                    await call("close_sketch")
                    await call("set_dimension", {"full_name":names[0],"value_mm":18})
                    positions=(await call("list_sketch_points", {"sketch_name":"InstalledAxisDimensions"}))["points"]
                    if abs(abs(positions[1]["model_point_mm"][0]-positions[0]["model_point_mm"][0])-18)>.01:
                        raise RuntimeError("Installed MCP later dimension edit did not drive spatial geometry.")
            finally:
                if dimension_title:
                    await call("close_document", {"name":dimension_title,"discard_changes":True})
                if sketch3d_title:
                    await call("close_document", {"name": sketch3d_title, "discard_changes": True})
                if dome_title:
                    await call("close_document", {"name": dome_title, "discard_changes": True})
                if wrap_title:
                    await call("close_document", {"name": wrap_title, "discard_changes": True})
                if title:
                    await call("close_document", {"name": title, "discard_changes": True})
                if scratch and original:
                    await call("activate_document", {"name": original})
            if catalog:
                catalog.parent.mkdir(parents=True, exist_ok=True)
                catalog.write_text(json.dumps([tool.model_dump(mode="json") for tool in response.tools],
                                              ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", type=Path, required=True)
    parser.add_argument("--tool-count", type=int, required=True)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--scratch", action="store_true", help="Verify geometry calls on a new temporary part, then close it.")
    args = parser.parse_args()
    install = args.install.resolve()
    sys.path.insert(0, str(install))
    from solidworks_mcp import server

    if not Path(server.__file__).resolve().is_relative_to(install):
        raise RuntimeError("Imported source checkout instead of the isolated installation.")
    if len(server.TOOLS) != args.tool_count:
        raise RuntimeError("Installed tool count differs from the expected release.")
    repo = Path(__file__).resolve().parents[1]
    suite = unittest.defaultTestLoader.discover(str(repo / "tests"), pattern="test_*.py")
    checks = unittest.TextTestRunner(verbosity=1).run(suite)
    if not checks.wasSuccessful():
        raise RuntimeError("Installed-package offline checks failed.")
    asyncio.run(protocol(install, args.tool_count, args.catalog, args.scratch))
    print(f"Installed wheel: {checks.testsRun} offline checks and MCP stdio discovery/native calls passed; {args.tool_count} tools.")


if __name__ == "__main__":
    main()
