# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for details.

"""Sheet-metal live checks using scratch documents only, restoring the session."""

import uuid
from live_expansion_regression import call, near, volume
from solidworks_mcp.sw_core import OUTPUT_ROOT, as_list, byref_long, running_app, value


def run():
    app = running_app()
    initial = {str(value(d, "GetTitle")) for d in as_list(value(app, "GetDocuments"))}
    original = str(value(app.ActiveDoc, "GetTitle")) if app.ActiveDoc is not None else ""
    try:
        call("create_new_document", kind="part")
        call("create_sketch", plane="front", name="Plate")
        call("draw_rectangle", x1_mm=0, y1_mm=0, x2_mm=100, y2_mm=50)
        call("close_sketch")
        call("sheet_metal_base_flange", sketch_name="Plate", thickness_mm=2, bend_radius_mm=1, k_factor=0.5)
        near(volume(), 10000, "flat sheet volume")
        definitions = call("list_sheet_metal_features")
        assert len(definitions["sheet_metal"]) == 1, definitions
        parameters = call("get_sheet_metal_parameters")["parameters"]
        near(parameters["thickness_mm"], 2, "sheet thickness")
        call("set_sheet_metal_parameters", thickness_mm=3, bend_radius_mm=2, k_factor=0.4)
        near(volume(), 15000, "updated sheet volume")
        parameters = call("get_sheet_metal_parameters")["parameters"]
        near(parameters["thickness_mm"], 3, "updated thickness")
        near(parameters["bend_radius_mm"], 2, "updated radius")
        near(parameters["k_factor"], 0.4, "updated K-factor")
        call("set_flat_pattern", flat=True)
        assert all(not f["suppressed"] for f in call("list_sheet_metal_features")["flat_patterns"])
        near(volume(), 15000, "flattened sheet volume")
        call("set_flat_pattern", flat=False)
        assert all(f["suppressed"] for f in call("list_sheet_metal_features")["flat_patterns"])
        folder = OUTPUT_ROOT / ".sheetmetal-tests" / uuid.uuid4().hex
        call("save_document", path=str(folder / "plate.SLDPRT"))
        call("export_flat_pattern", path=str(folder / "plate.dxf"))
        body_feature = call("list_sheet_metal_features")["sheet_metal"][0]["feature"]
        call("set_sheet_metal_parameters", feature_name=body_feature, thickness_mm=4, bend_radius_mm=3, k_factor=0.3)
        near(volume(), 20000, "body sheet thickness override volume")
        print("Sheet-metal creation, parameter edit, flatten/refold and DXF verified.", flush=True)
    finally:
        for title in [str(value(doc, "GetTitle")) for doc in as_list(value(app, "GetDocuments"))]:
            if title not in initial:
                app.CloseDoc(title)
        if original:
            app.ActivateDoc3(original, False, 0, byref_long())
        assert {str(value(d, "GetTitle")) for d in as_list(value(app, "GetDocuments"))} == initial


if __name__ == "__main__":
    run()
