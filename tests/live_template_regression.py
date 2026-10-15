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

"""Live regression for explicit document templates.

Set all three variables before running this test against an existing SOLIDWORKS
session. The test creates only disposable unsaved documents and closes them:

    SW_MCP_LIVE_PART_TEMPLATE
    SW_MCP_LIVE_A4_DRAWING_TEMPLATE
    SW_MCP_LIVE_A3_DRAWING_TEMPLATE
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from solidworks_mcp.sw_core import running_app, value
from solidworks_mcp.sw_drawing import create_drawing
from solidworks_mcp.sw_file import create_new_document
from solidworks_mcp.sw_manage import activate_document, close_document


def require(payload: dict[str, Any], context: str) -> dict[str, Any]:
    if not payload.get("ok"):
        raise AssertionError(f"{context} failed: {payload.get('message')}")
    return payload["data"]


def required_path(variable: str) -> str:
    path = os.environ.get(variable, "")
    if not path:
        raise RuntimeError(f"Set {variable} to an explicit local template path.")
    return path


def verify_template_result(data: dict[str, Any], expected_type: str, expected_path: str) -> None:
    template = data["template"]
    verification = data["verification"]
    if template["source"] != "explicit":
        raise AssertionError(template)
    if Path(template["resolved_path"]) != Path(expected_path).expanduser().resolve():
        raise AssertionError(template)
    if verification["expected_document_type"] != expected_type:
        raise AssertionError(verification)
    if verification["actual_document_type"] != expected_type or not verification["document_created"]:
        raise AssertionError(verification)
    if verification["source_template_readback_available"]:
        raise AssertionError("The connector must not claim unavailable native source-template readback.")


def main() -> None:
    part_template = required_path("SW_MCP_LIVE_PART_TEMPLATE")
    drawing_templates = [
        (required_path("SW_MCP_LIVE_A4_DRAWING_TEMPLATE"), [297.0, 210.0]),
        (required_path("SW_MCP_LIVE_A3_DRAWING_TEMPLATE"), [420.0, 297.0]),
    ]
    app = running_app()
    original = str(value(app.ActiveDoc, "GetTitle")) if app.ActiveDoc is not None else ""
    created_titles: list[str] = []
    try:
        part = require(
            create_new_document({"kind": "part", "template_path": part_template}),
            "create explicit-template part",
        )
        created_titles.append(part["document"]["title"])
        verify_template_result(part, "part", part_template)

        for template, expected_size in drawing_templates:
            drawing = require(
                create_drawing({"template_path": template}),
                f"create drawing from {Path(template).name}",
            )
            created_titles.append(drawing["document"]["title"])
            verify_template_result(drawing, "drawing", template)
            actual_size = drawing.get("sheet_size_mm")
            if actual_size is None or any(
                not math.isclose(actual, expected, rel_tol=0, abs_tol=0.5)
                for actual, expected in zip(actual_size, expected_size)
            ):
                raise AssertionError(f"Unexpected sheet size for {Path(template).name}: {actual_size}")
            for field in ("scale", "first_angle"):
                if field not in drawing:
                    raise AssertionError(f"Drawing readback omitted {field}: {drawing}")
        print("Explicit template live regression passed.")
    finally:
        for title in reversed(created_titles):
            outcome = close_document({"name": title, "discard_changes": True})
            if not outcome.get("ok"):
                print(f"WARNING: could not close disposable document {title}: {outcome.get('message')}")
        if original:
            outcome = activate_document({"name": original})
            if not outcome.get("ok"):
                print(f"WARNING: could not restore original active document {original}.")


if __name__ == "__main__":
    main()

