# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for details.

"""Native MotionManager acceptance on scratch documents, restoring the session."""

from live_expansion_regression import call, near
from solidworks_mcp.sw_core import as_list, byref_long, running_app, value


def run():
    app = running_app()
    initial = {str(value(d, "GetTitle")) for d in as_list(value(app, "GetDocuments"))}
    original = str(value(app.ActiveDoc, "GetTitle")) if app.ActiveDoc is not None else ""
    try:
        for kind in ("part", "assembly"):
            call("create_new_document", kind=kind)
            baseline = call("list_motion_studies")["count"]
            study = call("create_motion_study", name="MCP Timeline")["study"]
            assert study["type"] == "animation" and "animation" in study["supported_types"], study
            assert call("list_motion_studies")["count"] == baseline + 1
            assert call("activate_motion_study", name="MCP Timeline")["study"]["active"]
            call("set_motion_study_type", name="MCP Timeline", type="animation")
            timing = call("set_motion_study_timing", name="MCP Timeline", duration_sec=6, time_sec=2)["study"]
            near(timing["duration_sec"], 6, "motion duration")
            near(timing["time_sec"], 2, "motion cursor")
            duplicate = call("duplicate_motion_study", name="MCP Timeline", new_name="MCP Copy")["study"]
            near(duplicate["duration_sec"], 6, "duplicated duration")
            assert duplicate["type"] == "animation", duplicate
            assert call("list_motion_studies")["count"] == baseline + 2
            call("delete_motion_study", name="MCP Copy")
            call("delete_motion_study", name="MCP Timeline")
            assert call("list_motion_studies")["count"] == baseline
            print(f"Native MotionManager lifecycle/type/timeline verified on {kind}.", flush=True)
    finally:
        titles = [str(value(d, "GetTitle")) for d in as_list(value(app, "GetDocuments"))]
        for title in titles:
            if title not in initial:
                app.CloseDoc(title)
        if original:
            app.ActivateDoc3(original, False, 0, byref_long())
        assert {str(value(d, "GetTitle")) for d in as_list(value(app, "GetDocuments"))} == initial


if __name__ == "__main__":
    run()
