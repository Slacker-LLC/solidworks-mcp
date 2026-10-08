# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for details.
# limitations under the License.

"""Sweep surface area, twist and direction checks on disposable parts."""
import math
from live_expansion_regression import call, near
from live_surface_construction_regression import area
from solidworks_mcp.sw_core import BODY_SHEET, get_bodies, require_part, value, as_list, flag_methods


def twisted_area(angle):
    k = math.radians(angle) / 10
    return 100 if k == 0 else 10 * (5 * math.sqrt(1 + (5 * k) ** 2) + math.asinh(5 * k) / k)


def integrated_area():
    # Independently integrate the native surface Jacobian; GetArea is approximate
    # on twisted spline surfaces. These fixtures have rectangular UV domains.
    _, doc = require_part()
    total = 0
    n = 24
    for body in get_bodies(doc, BODY_SHEET):
        for face in as_list(value(body, "GetFaces")):
            u0, u1, v0, v1 = value(face, "GetUVBounds")
            surface = flag_methods(value(face, "GetSurface"), "Evaluate")
            partial = 0
            for i in range(n + 1):
                wi = 1 if i in (0, n) else 4 if i % 2 else 2
                for j in range(n + 1):
                    wj = 1 if j in (0, n) else 4 if j % 2 else 2
                    sample = surface.Evaluate(u0 + (u1 - u0) * i / n, v0 + (v1 - v0) * j / n, 1, 1)
                    du, dv = sample[3:6], sample[6:9]
                    cross = [du[1] * dv[2] - du[2] * dv[1], du[2] * dv[0] - du[0] * dv[2], du[0] * dv[1] - du[1] * dv[0]]
                    partial += wi * wj * math.sqrt(sum(x * x for x in cross))
            total += partial * (u1 - u0) * (v1 - v0) / (9 * n * n) * 1e6
    return total


def verify_twist_coordinates(options):
    _, doc = require_part()
    for body in get_bodies(doc, BODY_SHEET):
        for face in as_list(value(body, "GetFaces")):
            u0, u1, v0, v1 = value(face, "GetUVBounds")
            surface = flag_methods(value(face, "GetSurface"), "Evaluate")
            samples = (0, 0.5, 1) if options.get("direction") == "both" else (0, 0.25, 0.5, 0.75, 1)
            along_v = abs(surface.Evaluate(u1, v1, 0, 0)[2] - surface.Evaluate(u1, v0, 0, 0)[2]) > 0.005
            def point(t):
                return surface.Evaluate(u1 if along_v else u0 + t * (u1 - u0), v0 + t * (v1 - v0) if along_v else v1, 0, 0)[:3]
            start, end = point(0), point(1)
            for t in samples:
                x, y, z = point(t)
                assert math.isclose(math.hypot(x, y), 0.005, abs_tol=1e-6)
                if options.get("direction") == "both":
                    angle = options.get("twist_angle_deg", 0) if z >= 0 else options.get("second_twist_angle_deg", 0)
                    reverse = options.get("reverse_twist", False) if z >= 0 else options.get("reverse_second_twist", False)
                else:
                    angle, reverse = options.get("twist_angle_deg", 0), options.get("reverse_twist", False)
                fraction = abs(z) / 0.01 if options.get("direction") in ("both", "second") else (z - start[2]) / (end[2] - start[2])
                origin = 0 if options.get("direction") in ("both", "second") else math.atan2(start[1], start[0])
                expected = origin + math.radians(angle) * fraction * (-1 if reverse else 1)
                delta = math.atan2(y, x) - expected
                error = abs(math.atan2(math.sin(2 * delta), math.cos(2 * delta)) / 2)
                # Compare positional deviation in mm; sweep splines approximate
                # constant twist between sections rather than exact helicoids.
                assert abs(math.sin(error)) * math.hypot(x, y) * 1000 < 0.01, (x, y, z, expected, error)


def run(cases=None):
    original = call("list_open_documents")["active_title"]
    scratch = []
    try:
        cases = cases or [{}, {"twist_control": "keep_normal"},
                 {"twist_control": "constant_twist", "twist_angle_deg": 90},
                 {"twist_control": "constant_twist", "twist_angle_deg": 90, "reverse_twist": True},
                 {"direction": "both", "twist_control": "constant_twist", "twist_angle_deg": 45, "second_twist_angle_deg": 90},
                 {"direction": "both", "twist_control": "constant_twist", "twist_angle_deg": 45, "second_twist_angle_deg": 45,
                  "reverse_twist": True, "reverse_second_twist": True},
                 {"direction": "second"}, {"direction": "second", "twist_control": "constant_twist", "twist_angle_deg": 45, "reverse_twist": True},
                 {"interior": True, "direction": "first"},
                 {"interior": True, "direction": "first", "twist_control": "constant_twist", "twist_angle_deg": 45, "reverse_twist": True},
                 {"circular_diameter_mm": 10}, {"closed": True},
                 {"closed": True, "keep_tangency": True, "advanced_smoothing": True, "merge_smooth_faces": True},
                 {"twist_control": "first_guide"}, {"twist_control": "two_guides"},
                 {"twist_control": "first_guide", "extra_guide": True}]
        for options in cases:
            scratch.append(call("create_new_document", kind="part")["document"]["title"])
            closed = options.pop("closed", False)
            interior = options.pop("interior", False) or options.get("direction") in ("both", "second")
            extra_guide = options.pop("extra_guide", False)
            if "circular_diameter_mm" not in options:
                call("create_sketch", plane="front", name="Profile")
                if closed:
                    call("draw_circle", x_mm=0, y_mm=0, radius_mm=5)
                else:
                    call("draw_line", x1_mm=-5, y1_mm=0, x2_mm=5, y2_mm=0)
                call("close_sketch")
                options["profile_sketch"] = "Profile"
            call("create_sketch", plane="right", name="Path")
            call("draw_line", x1_mm=10 if interior else 0, y1_mm=0, x2_mm=-10, y2_mm=0)
            call("close_sketch")
            if options.get("twist_control") in ("first_guide", "two_guides"):
                options["guide_sketches"] = []
                for i in range(1 if options["twist_control"] == "first_guide" and not extra_guide else 2):
                    call("create_plane", mode="offset", selection={"planes": ["right"]}, distance_mm=5, flip=bool(i), name=f"GuidePlane{i}")
                    call("create_sketch", plane_name=f"GuidePlane{i}", name=f"Guide{i}")
                    call("draw_line", x1_mm=0, y1_mm=0, x2_mm=-10, y2_mm=0)
                    call("close_sketch")
                    options["guide_sketches"].append(f"Guide{i}")
            data = call("surface_sweep", path_sketch="Path", name="SweepSurface", **options)
            expected = 2 * math.pi * 5 * 10 if closed or "circular_diameter_mm" in options else twisted_area(options.get("twist_angle_deg", 0))
            if options.get("direction") == "both":
                expected += twisted_area(options.get("second_twist_angle_deg", 0))
            if options.get("twist_control") == "constant_twist":
                verify_twist_coordinates(options)
                integrated = integrated_area()
                if options.get("direction") != "both":
                    near(integrated, expected, "native surface integral vs analytic helicoid")
                reported = area()
                assert math.isclose(reported, integrated, rel_tol=0.001), (reported, integrated)
                print(f"Native area={reported}, integrated={integrated}" + (f", analytic={expected}" if options.get("direction") != "both" else "; bidirectional boundary twists verified"), flush=True)
            else:
                near(area(), expected, "swept area")
            assert len(call("list_surface_bodies")["surface_bodies"]) == 1
            assert call("list_faces")["total_faces"] == 0
            edges = call("list_edges", body_type="surface")["edges"]
            points = [edge[key] for edge in edges for key in ("point_mm", "start_mm", "end_mm") if edge.get(key)]
            z0, z1 = min(p[2] for p in points), max(p[2] for p in points)
            near(z0, -10 if options.get("direction") in ("both", "second") else 0, "sweep start extent")
            near(z1, 0 if options.get("direction") == "second" else 10, "sweep end extent")
            print(data["native_settings"], flush=True)
        print("Open/closed/circular sweep areas and twist/direction controls verified.", flush=True)
    finally:
        opened = {doc["title"] for doc in call("list_open_documents")["documents"]}
        for title in scratch:
            if title in opened:
                call("close_document", name=title, discard_changes=True)
        if original and original in opened:
            call("activate_document", name=original)


if __name__ == "__main__":
    run()
