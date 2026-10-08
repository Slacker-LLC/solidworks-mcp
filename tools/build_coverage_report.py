# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for details.

"""Join native feature types to actual registered tools; keep unimplemented types visible."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from solidworks_mcp import server


FEATURE_TOOLS = {
    "swFmChamfer": ["chamfer"], "swFmFillet": ["fillet"], "swFmDraft": ["draft"],
    "swFmCirPattern": ["circular_pattern"], "swFmLPattern": ["linear_pattern"],
    "swFmMirrorPattern": ["mirror_feature"], "swFmShell": ["shell"],
    "swFmBlend": ["loft"], "swFmBlendCut": ["loft"], "swFmExtrusion": ["boss_extrude"],
    "swFmBoss": ["boss_extrude"], "swFmCut": ["cut_extrude"], "swFmRevolution": ["revolve"],
    "swFmRevCut": ["revolve"], "swFmSweep": ["sweep"], "swFmSweepCut": ["sweep"],
    "swFmThicken": ["thicken_surface"], "swFmSheetMetal": ["sheet_metal_base_flange", "get_sheet_metal_parameters", "set_sheet_metal_parameters"],
    "swFmBaseFlange": ["sheet_metal_base_flange"], "swFmFlatPattern": ["set_flat_pattern", "export_flat_pattern"],
    "swFmCenterMark": ["insert_center_marks"], "swFmDrSheet": ["add_sheet", "activate_sheet"],
    "swFmAbsoluteView": ["insert_model_view"], "swFmDetailView": ["insert_detail_view"],
    "swFmSectionPartView": ["insert_section_view"], "swFmSectionAssemView": ["insert_section_view"],
    "swFmUnfoldedView": ["insert_projected_view"], "swFmRefPlane": ["create_plane"],
    "swFmRefAxis": ["create_axis"], "swFmRefSurface": ["surface_extrude", "surface_revolve", "surface_loft", "planar_surface", "offset_surface", "knit_surfaces", "extend_surface", "untrim_surface", "fill_surface", "ruled_surface", "delete_surface_holes", "surface_sweep", "mid_surface", "get_mid_surface_data", "preview_surface_trim", "trim_surface", "get_surface_trim_data", "boundary_surface", "get_boundary_feature_data"],
    "swFmProfileFeature": ["create_sketch", "create_3d_sketch", "list_sketch_points", "add_3d_dimension"], "swFmWeldMemberFeat": ["insert_structural_member"],
    "swFmCoordinateSystem": ["create_coordinate_system", "list_coordinate_systems"],
    "swFmRefCurve": ["create_curve_through_points", "get_curve_points", "set_curve_points", "composite_curve"],
}

MODULE_TOOLS = {"Motion": ["list_motion_studies", "create_motion_study", "activate_motion_study",
                           "duplicate_motion_study", "delete_motion_study", "set_motion_study_type",
                           "set_motion_study_timing"]}


def report(api):
    names = {tool.name for tool in server.TOOLS}
    unknown = {name for tools in FEATURE_TOOLS.values() for name in tools} - names
    if unknown:
        raise RuntimeError(f"Coverage mapping names tools that do not exist: {sorted(unknown)}")
    features = [{**feature, "tools": FEATURE_TOOLS.get(feature["name"], []),
                 "status": "implemented_partial" if feature["name"] in FEATURE_TOOLS else "unmapped"}
                for feature in api["feature_types"]]
    return {
        "objective": "覆盖 SolidWorks 原生及官方随附功能；不包含嘉立创等第三方插件",
        "goal_complete": False,
        "note": "已关联工具只代表该特征类别存在部分实现；不等于所有参数、子操作、版本或官方模块已完成。",
        "registered_tool_count": len(names),
        "feature_types": features,
        "official_modules": [{k: v for k, v in module.items() if k != "interfaces"} |
                             {"implementation_status": "implemented_partial" if module["module"] in MODULE_TOOLS else "pending",
                              "tools": MODULE_TOOLS.get(module["module"], [])}
                             for module in api.get("official_modules", [])],
        "tools": [tool.model_dump(mode="json", exclude_none=True) for tool in server.TOOLS],
        "completion_gates": {"all_required_operations_implemented": False, "all_required_operations_live_verified": False,
                             "official_modules_verified": False, "installation_and_protocol_verified_for_final_release": False},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = report(json.loads(args.inventory.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"tools={payload['registered_tool_count']}, feature_types={len(payload['feature_types'])}, goal_complete=False")


if __name__ == "__main__":
    main()
