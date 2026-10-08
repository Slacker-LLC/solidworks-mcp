# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

"""Native surface trim geometry on newly-created documents only."""
import math
from live_expansion_regression import call, near
from solidworks_mcp.sw_core import HANDLERS, selected_session_pid


def run():
    configured = selected_session_pid()
    sessions = call("list_solidworks_sessions")
    attached = sessions["attached_process_id"]
    call("select_solidworks_session", process_id=attached)
    assert call("list_solidworks_sessions")["attached_process_id"] == attached
    original = call("list_open_documents")["active_title"]
    scratch = []

    def part():
        scratch.append(call("create_new_document", kind="part")["document"]["title"])

    def plate(name="BaseSurface", y=0):
        call("create_sketch", plane="front", name=f"{name}Profile")
        call("draw_rectangle", x1_mm=0, y1_mm=y, x2_mm=10, y2_mm=y + 10)
        call("close_sketch")
        call("planar_surface", sketch_name=f"{name}Profile", name=name)

    def knife():
        call("create_plane", mode="offset", selection={"planes": ["right"]}, distance_mm=4, name="KnifePlane")

    def index(name):
        return next(b["index"] for b in call("list_surface_bodies")["surface_bodies"] if b["name"] == name)

    def preview(args):
        features = call("list_features")
        bodies = call("list_surface_bodies")
        regions = call("preview_surface_trim", **args)["regions"]
        assert call("preview_surface_trim", **args)["regions"] == regions
        assert call("list_features") == features
        assert call("list_surface_bodies") == bodies
        return regions

    def finish(args, selected, expected, **extra):
        result = call("trim_surface", **args, region_indices=selected, name="VerifiedTrim", **extra)
        near(result["actual_sheet_area_mm2"], expected, "trimmed sheet area")
        assert result["actual_sheet_box_mm"] == result["expected_sheet_box_mm"]
        before = call("list_features")
        assert call("get_surface_trim_data", name="VerifiedTrim")["trim"] == result["trim"]
        assert call("list_features") == before
        return result

    try:
        for region, remove, linear, split in ((0, False, False, True), (1, False, False, True),
                                             (1, True, False, True), (0, False, True, False)):
            part(); plate(); knife()
            args = {"surface_body_indices": [index("BaseSurface")], "trim_selection": {"planes": ["KnifePlane"]},
                    "remove_picked": remove, "linear_extension": linear, "split_system": split}
            regions = preview(args)
            assert [round(r["area_mm2"]) for r in regions] == [40, 60]
            result = finish(args, [region], 40 if remove or region == 0 else 60)
            assert result["trim"] == {"type": 0, "trim_tool_count": 1, "pieces_to_keep_count": 1}
            near(result["actual_sheet_box_mm"][0], 4 if region == 1 and not remove else 0, "retained side")

        for tool_kind in ("surface_bodies", "surface_faces", "mutual_separate", "mutual_knit"):
            part(); plate(); knife()
            call("create_sketch", plane="KnifePlane", name="KnifeProfile")
            call("draw_rectangle", x1_mm=-5, y1_mm=0, x2_mm=5, y2_mm=10)
            call("close_sketch"); call("planar_surface", sketch_name="KnifeProfile", name="KnifeSurface")
            base, cutter = index("BaseSurface"), index("KnifeSurface")
            mutual = tool_kind.startswith("mutual")
            args = {"surface_body_indices": [base, cutter] if mutual else [base], "mode": "mutual" if mutual else "standard"}
            if not mutual:
                if tool_kind == "surface_bodies": args["trim_selection"] = {tool_kind: [cutter]}
                else:
                    faces = call("list_faces", body_type="surface")["faces"]
                    face_index = next(f["index"] for f in faces if abs(f["point_mm"][0] - 4) < 1e-6)
                    args["trim_selection"] = {tool_kind: [face_index]}
            regions = preview(args)
            selected = [r["index"] for r in regions if (r["surface_body_index"] == base and r["box_mm"][3] < 5)
                        or (mutual and r["surface_body_index"] == cutter and r["box_mm"][2] > -1)]
            if tool_kind == "mutual_separate":
                payload = HANDLERS["trim_surface"]({**args, "region_indices": selected, "knit": False, "name": "UnconfirmedSeparate"})
                assert not payload["ok"] and payload["data"]["feature"] == "UnconfirmedSeparate"
                near(payload["data"]["actual_sheet_area_mm2"], 90, "mutual geometry retained")
                assert payload["data"]["sheet_body_count"] == 1
                print("Native mutual trim joins the sheets despite knit=false; failure preserves feature evidence.", flush=True)
                continue
            result = finish(args, selected, 90 if mutual else 140, knit=tool_kind == "mutual_knit")
            assert result["trim"]["type"] == int(mutual)
            if mutual:
                assert len(call("list_surface_bodies")["surface_bodies"]) == 1

        for keep_inside in (True, False):
            part(); plate()
            call("create_sketch", plane="front", name="CircleTool")
            call("draw_circle", x_mm=5, y_mm=5, radius_mm=2); call("close_sketch")
            args = {"surface_body_indices": [index("BaseSurface")], "trim_selection": {"sketches": ["CircleTool"]}}
            regions = preview(args)
            selected = next(r["index"] for r in regions if (r["area_mm2"] < 50) == keep_inside)
            finish(args, [selected], 4 * math.pi if keep_inside else 100 - 4 * math.pi,
                   picked_points_mm=[[5, 5, 0] if keep_inside else [1, 1, 0]])

        part(); plate(); plate("OtherSurface", y=20); knife()
        args = {"surface_body_indices": [index("BaseSurface"), index("OtherSurface")], "trim_selection": {"planes": ["KnifePlane"]}}
        regions = preview(args)
        finish(args, [r["index"] for r in regions if r["box_mm"][3] < 5], 80)

        part()
        call("create_sketch", plane="front", name="CylinderProfile")
        call("draw_line", x1_mm=5, y1_mm=0, x2_mm=5, y2_mm=10)
        call("draw_centerline", x1_mm=0, y1_mm=0, x2_mm=0, y2_mm=10); call("close_sketch")
        call("surface_revolve", sketch_name="CylinderProfile", name="Cylinder")
        call("create_plane", mode="offset", selection={"planes": ["top"]}, distance_mm=4, name="CutHeight")
        args = {"surface_body_indices": [0], "trim_selection": {"planes": ["CutHeight"]}}
        regions = preview(args)
        finish(args, [min(regions, key=lambda r: r["area_mm2"])["index"]], 2 * math.pi * 5 * 4)
        print("Verified plane/sketch/surface/face tools, both retained sides, removal, mutual trim/knit, multiple targets and cylinder area on 12 scratch parts; knit=false remains a native gap.", flush=True)
    finally:
        opened = {d["title"] for d in call("list_open_documents")["documents"]}
        for title in scratch:
            if title in opened: call("close_document", name=title, discard_changes=True)
        if original in opened: call("activate_document", name=original)
        call("select_solidworks_session", process_id=configured)


if __name__ == "__main__":
    run()
