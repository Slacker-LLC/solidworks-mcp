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

"""A local, stdio-only MCP bridge for a running SOLIDWORKS instance.

The bridge does not launch SOLIDWORKS, execute arbitrary code, access the
network, or register an add-in.  It attaches to a session the user already has
open and drives it through the documented COM API.

Unit contract: every length is millimetres and every angle is degrees at the
MCP boundary, which is why the parameters are named ``*_mm`` and ``*_deg``.
The COM API underneath is metres and radians; conversion happens once, in
sw_core.

Layout
------
sw_core      COM attachment, units, feature tree, topology, selection engine
sw_file      session status, documents, saving, exporting, appearance, material
sw_refgeom   reference planes and axes
sw_sketch    sketches, sketch geometry, relations, dimensions
sw_feature   solid features
sw_inspect   topology listings, measurement, mass properties, screenshots
sw_assembly  components and mates
sw_drawing   drawing sheets, views, and annotation
sw_demo      the basketball demo, registered only when SW_MCP_DEMO_TOOLS is set
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import ImageContent, TextContent, Tool

from . import sw_core
from .sw_core import HANDLERS, TOOLS, com_error, logger, result

# Importing these modules is what registers their tools into sw_core.TOOLS.
from . import sw_file  # noqa: F401
from . import sw_refgeom  # noqa: F401
from . import sw_sketch  # noqa: F401
from . import sw_feature  # noqa: F401
from . import sw_inspect  # noqa: F401
from . import sw_assembly  # noqa: F401
from . import sw_drawing  # noqa: F401
from . import sw_demo  # noqa: F401
from . import sw_manage  # noqa: F401
from . import sw_multibody  # noqa: F401
from . import sw_boundary  # noqa: F401
from . import sw_wrap  # noqa: F401
from . import sw_dome  # noqa: F401
from . import sw_sheetmetal  # noqa: F401
from . import sw_weldment  # noqa: F401
from . import sw_motion  # noqa: F401
from . import sw_geometry  # noqa: F401


server = Server("solidworks-mcp")

# One SOLIDWORKS session, one COM apartment: serialise tool calls so a slow
# rebuild cannot interleave with the next call's selection.
COM_LOCK = asyncio.Lock()


@server.list_tools()
async def list_tools() -> list[Tool]:
    return list(TOOLS)


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent | ImageContent]:
    handler = HANDLERS.get(name)
    if handler is None:
        payload: dict[str, Any] = result(False, f"Unknown tool: {name}")
    else:
        async with COM_LOCK:
            try:
                payload = handler(arguments or {})
            except RuntimeError as exc:
                payload = result(False, str(exc))
            except KeyError as exc:
                payload = result(False, f"Missing required argument: {exc}")
            except Exception as exc:
                payload = com_error(exc)

    image_base64 = payload.pop("_image_png_base64", None) if isinstance(payload, dict) else None
    content: list[TextContent | ImageContent] = [
        TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    ]
    if image_base64:
        content.append(ImageContent(type="image", data=image_base64, mimeType="image/png"))
    return content


async def main() -> None:
    logger.info("solidworks-mcp starting with %d tools", len(TOOLS))
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def run() -> None:
    """Console-script entry point."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
