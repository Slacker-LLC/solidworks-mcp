# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for details.

"""Structural members checked by bounding length, volume ratio and cut-list grouping."""

import math
from pathlib import Path
from live_expansion_regression import call, near, volume
from solidworks_mcp.sw_core import as_list, byref_long, running_app, safe, value


def member(path, configuration, length):
    call("create_new_document", kind="part")
    call("create_weldment")
    call("create_sketch", plane="front", name="MemberPath")
    call("draw_line", x1_mm=0, y1_mm=0, x2_mm=length, y2_mm=0)
    call("close_sketch")
    call("insert_structural_member", profile_path=path, configuration=configuration,
         groups=[{"sketch_name": "MemberPath", "segments": [0]}])
    near(call("get_bounding_box")["size_mm"][0], length, "structural member length")
    return volume()


def run():
    app = running_app()
    initial = {str(value(d, "GetTitle")) for d in as_list(value(app, "GetDocuments"))}
    original = str(value(app.ActiveDoc, "GetTitle")) if app.ActiveDoc is not None else ""
    try:
        profiles = call("list_weldment_profiles", query="pipe", limit=100)["profiles"]
        assert profiles, "No fixture profile available; select another installed native profile."
        path = next(p["path"] for p in profiles if Path(p["path"]).name.lower() == "pipe.sldlfp")
        names = call("get_weldment_profile_configurations", path=path)["configurations"]
        assert names, "Profile contains no configurations."
        first = member(path, names[0], 100)
        assert first > 0
        if names[0] == "21.3 x 2.3":
            near(first, math.pi / 4 * (21.3**2 - (21.3 - 2 * 2.3)**2) * 100, "pipe analytic volume")
        call("update_cut_list")
        cut_list = call("list_cut_list")["cut_list"]
        assert sum(entry["body_count"] for entry in cut_list) == 1, cut_list
        second = member(path, names[0], 200)
        near(second, 2 * first, "structural member volume ratio")
        print("Structural profile, member length/volume and cut-list grouping verified.", flush=True)
    finally:
        titles = [str(safe(doc, "GetTitle", "")) for doc in as_list(value(app, "GetDocuments"))]
        for title in titles:
            if title and title not in initial:
                app.CloseDoc(title)
        if original:
            app.ActivateDoc3(original, False, 0, byref_long())
        assert {str(value(d, "GetTitle")) for d in as_list(value(app, "GetDocuments"))} == initial


if __name__ == "__main__":
    run()
