# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for details.

"""Inventory native SOLIDWORKS APIs without loading any add-in or invoking members."""

import argparse
import json
from pathlib import Path

import pythoncom
from tlb_probe import find_library


def read_interfaces(library):
    interfaces = []
    for index in range(library.GetTypeInfoCount()):
        info = library.GetTypeInfo(index)
        attr = info.GetTypeAttr()
        name = library.GetDocumentation(index)[0]
        members = []
        for slot in range(attr.cFuncs):
            descriptor = info.GetFuncDesc(slot)
            names = info.GetNames(descriptor.memid)
            if not names or names[0] in {"QueryInterface", "AddRef", "Release", "GetTypeInfoCount", "GetTypeInfo", "GetIDsOfNames", "Invoke"}:
                continue
            members.append({"name": names[0], "parameters": list(names[1:]), "parameter_count": len(descriptor.args),
                            "invocation_kind": descriptor.invkind, "flags": descriptor.wFuncFlags})
        if members:
            interfaces.append({"name": name, "members": members})
    return interfaces


def inventory(include_official=False):
    path = find_library("sldworks.tlb")
    interfaces = read_interfaces(pythoncom.LoadTypeLib(str(path)))
    constants = pythoncom.LoadTypeLib(str(find_library("swconst.tlb")))
    feature_types = []
    for index in range(constants.GetTypeInfoCount()):
        if constants.GetDocumentation(index)[0] != "swFeatureNameID_e":
            continue
        info = constants.GetTypeInfo(index)
        for slot in range(info.GetTypeAttr().cVars):
            descriptor = info.GetVarDesc(slot)
            feature_types.append({"name": info.GetNames(descriptor.memid)[0], "value": descriptor.value})
    modules = []
    if include_official:
        for name, relative in (
            ("Motion", "swmotionstudy.tlb"), ("Simulation", "Simulation/cosworks.dll"),
            ("Routing", "SWRoutingLib.tlb"), ("Costing", "sldcostingapi.tlb"),
            ("DimXpert", "swdimxpert.tlb"), ("Inspection", "swinspectionAddIn.tlb"),
            ("Sustainability", "sustainability.tlb"), ("DFMXpress", "DFMXpress/dfmaddin.tlb"),
            ("ScanTo3D", "swscanto3d.tlb"), ("CAM", "CAMWorks/SWCAM.tlb"),
        ):
            target = path.parent / relative
            entry = {"module": name, "library": str(target)}
            if not target.is_file():
                entry.update(status="library_not_found", note="该候选路径未找到类型库，不等同于模块未安装。")
            else:
                try:
                    extra = read_interfaces(pythoncom.LoadTypeLib(str(target)))
                    entry.update(status="inventoried", interface_count=len(extra),
                                 member_count=sum(len(i["members"]) for i in extra), interfaces=extra)
                except Exception as exc:
                    entry.update(status="unreadable_library", error=str(exc))
            modules.append(entry)
    return {"scope": "SOLIDWORKS 核心与明确列出的官方随附类型库；第三方插件排除", "library": str(path),
            "interface_count": len(interfaces), "member_count": sum(len(i["members"]) for i in interfaces),
            "feature_types": feature_types, "interfaces": interfaces, "official_modules": modules,
            "note": "API 清单用于发现缺口，不是功能覆盖率，也不表示这些 API 已封装或验证。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--official-modules", action="store_true", help="Also inventory known official module type libraries; never load add-ins.")
    args = parser.parse_args()
    payload = inventory(args.official_modules)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("interface_count", "member_count")}, ensure_ascii=False))
    print(f"原生特征类型数：{len(payload['feature_types'])}")
    for module in payload["official_modules"]:
        print(json.dumps({k: v for k, v in module.items() if k not in {"interfaces", "library"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
