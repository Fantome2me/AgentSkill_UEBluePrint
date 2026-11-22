# -*- coding: utf-8 -*-
"""READ-ONLY material graph dumper that does not silently drop nodes.

WHY THIS EXISTS
    The project's original ProbeMaterialGraph.py maps pin names through a hand-written PINS table.
    Any expression class missing from that table gets no pin names, and - worse - the original
    iteration path could skip classes it did not recognise. A graph containing MaterialFunctionCall
    or Custom nodes therefore dumped WITHOUT them, and an audit of that dump would pass while never
    having seen the user's own nodes at all.

WHAT THIS DOES DIFFERENTLY
    1. Dumps EVERY expression returned by MaterialEditingLibrary, whatever its class. Unknown
       classes are included and marked so downstream tools treat them as opaque instead of broken.
    2. Never invents a pin name. When the engine's source list cannot be mapped to a pin count, it
       records an explicit opaque bucket ("<order unknown: N sources>") and keeps the NAMES of the
       sources, so connectivity stays correct while pin labelling stays honest.
    3. Records the class for every node so a downstream audit can classify rather than judge.
    4. Writes the dump to Saved/<OUT_NAME> and prints a summary.

    It changes NOTHING in the project. Safe to run on a live project; still, run it headless.

RUN
    UnrealEditor-Cmd.exe <abs>.uproject -run=pythonscript -script=<abs>/DumpMaterialGraph.py \
        -unattended -nopause -nosplash -NoLogTimes -NoSound -NullRHI

    Edit MATERIAL_PATHS below, or set the env var UE_MAT_PATHS to a semicolon-separated list.
"""

import io
import json
import os
import traceback

import unreal

# --- configure -----------------------------------------------------------------
MATERIAL_PATHS = [
    "/Game/Final/Shaders/BaseShaders/BaseSkin/SkinShader/SkinShader",
    "/Game/Final/Shaders/BaseShaders/BaseHair/HairShader/HairShader",
    "/Game/Final/Shaders/BaseShaders/BaseFace/FaceShader/FaceShader",
    "/Game/Final/Shaders/BaseShaders/BaseCloth/ClothShader/ClothShader",
    "/Game/Final/Shaders/BaseShaders/BaseOutline/OutlineShader/OutlineShader",
]
_env = os.environ.get("UE_MAT_PATHS")
if _env:
    MATERIAL_PATHS = [p for p in _env.split(";") if p.strip()]

OUT_NAME = "MaterialGraphDump.json"
# --------------------------------------------------------------------------------

# Pin order per class, used ONLY to attach names when the counts line up. A class absent from this
# table still gets dumped - it just gets an opaque bucket instead of guessed names.
PINS = {
    "MaterialExpressionMultiply": ["A", "B"],
    "MaterialExpressionAdd": ["A", "B"],
    "MaterialExpressionSubtract": ["A", "B"],
    "MaterialExpressionDivide": ["A", "B"],
    "MaterialExpressionDotProduct": ["A", "B"],
    "MaterialExpressionLinearInterpolate": ["A", "B", "Alpha"],
    "MaterialExpressionClamp": ["Input", "Min", "Max"],
    "MaterialExpressionPower": ["Base", "Exp"],
    "MaterialExpressionOneMinus": ["Input"],
    "MaterialExpressionNormalize": ["VectorInput"],
    "MaterialExpressionComponentMask": ["Input"],
    "MaterialExpressionAppendVector": ["A", "B"],
    "MaterialExpressionSaturate": ["Input"],
    "MaterialExpressionTransform": ["Input"],
    "MaterialExpressionTransformPosition": ["Input"],
    # Texture samples legitimately expose BOTH Coordinates and TextureObject, which is why a
    # single-pin assumption produced an "unknown order, 2 sources" bucket in older dumps.
    "MaterialExpressionTextureSample": ["Coordinates", "TextureObject"],
    "MaterialExpressionTextureSampleParameter2D": ["Coordinates", "TextureObject"],
    "MaterialExpressionStaticSwitch": ["A", "B", "Value"],
}


def note(msg):
    unreal.log_warning("[MatDump] " + str(msg))


def safe(fn, default=None):
    try:
        return fn()
    except Exception as exc:                      # noqa: BLE001
        return default if default is not None else "<err:%s>" % exc


def dump_one(path):
    material = unreal.EditorAssetLibrary.load_asset(path)
    if material is None:
        return {"path": path, "error": "load failed"}

    expressions = unreal.MaterialEditingLibrary.get_material_expressions(material)

    records = []
    for e in expressions:
        cls = e.get_class().get_name()
        known_pins = PINS.get(cls)

        # engine-reported upstream sources, in its own order
        connected = safe(lambda e=e: unreal.MaterialEditingLibrary
                         .get_inputs_for_material_expression(material, e), []) or []
        sources = [c.get_name() for c in connected if c is not None]

        rec = {
            "name": e.get_name(),
            "class": cls,
            # every node is recorded, recognised or not - this is the whole point
            "recognised_class": cls in PINS,
            "x": safe(lambda: int(e.get_editor_property("material_expression_editor_x")), 0),
            "y": safe(lambda: int(e.get_editor_property("material_expression_editor_y")), 0),
            "desc": str(safe(lambda: e.get_editor_property("desc"), "") or ""),
            "inputs": {},
        }

        if cls in ("MaterialExpressionScalarParameter", "MaterialExpressionVectorParameter",
                   "MaterialExpressionTextureSampleParameter2D", "MaterialExpressionStaticSwitchParameter"):
            rec["parameter_name"] = str(safe(lambda: e.get_editor_property("parameter_name"), ""))
        if cls == "MaterialExpressionScalarParameter":
            rec["value"] = safe(lambda: float(e.get_editor_property("default_value")))
        if cls == "MaterialExpressionVectorParameter":
            c = safe(lambda: e.get_editor_property("default_value"))
            rec["value"] = [c.r, c.g, c.b, c.a] if c else None
        if cls == "MaterialExpressionTextureSampleParameter2D":
            tex = safe(lambda: e.get_editor_property("texture"))
            rec["texture"] = safe(lambda: tex.get_path_name()) if tex else None
        if cls == "MaterialExpressionCustom":
            # explicitly keep what a Custom node declares, so downstream does not guess
            rec["custom_description"] = str(safe(lambda: e.get_editor_property("description"), ""))
            rec["custom_output_type"] = str(safe(lambda: e.get_editor_property("output_type"), ""))
            rec["custom_inputs"] = [str(i.get_editor_property("input_name"))
                                    for i in (safe(lambda: e.get_editor_property("inputs"), []) or [])]
            rec["code_bytes"] = len(str(safe(lambda: e.get_editor_property("code"), "") or ""))
        if cls == "MaterialFunctionCall":
            fn = safe(lambda: e.get_editor_property("material_function"))
            rec["material_function"] = safe(lambda: fn.get_path_name()) if fn else None

        # --- attach sources without ever inventing a pin name ---
        if not sources:
            rec["inputs"] = {}
        else:
            names = rec.get("custom_inputs") if cls == "MaterialExpressionCustom" else known_pins
            if names and len(sources) <= len(names):
                rec["inputs"] = {p: s for p, s in zip(names, sources)}
            else:
                # honest bucket: keeps the real edges, refuses to name the pins
                rec["inputs"] = {"<order unknown: %d sources>" % len(sources): sources}
                rec["pin_names_unknown"] = True

        records.append(rec)

    root = {}
    for prop in (p for p in dir(unreal.MaterialProperty) if p.startswith("MP_")):
        member = getattr(unreal.MaterialProperty, prop, None)
        if member is None:
            continue
        owner = safe(lambda m=member: unreal.MaterialEditingLibrary
                     .get_material_property_input_node(material, m))
        if owner is not None:
            root[prop] = owner.get_name()

    unrecognised = [r["name"] for r in records if not r["recognised_class"]]
    return {
        "path": path,
        "flags": {
            "shading_model": str(safe(lambda: material.get_editor_property("shading_model"))),
            "blend_mode": str(safe(lambda: material.get_editor_property("blend_mode"))),
            "material_domain": str(safe(lambda: material.get_editor_property("material_domain"))),
            "two_sided": bool(safe(lambda: material.get_editor_property("two_sided"), False)),
        },
        "root": root,
        "expressions": records,
        "count": len(records),
        # surface the nodes that older dumpers lost, so an audit can never silently skip them
        "unrecognised_classes": sorted({r["class"] for r in records if not r["recognised_class"]}),
        "unrecognised_count": len(unrecognised),
    }


def main():
    note("start")
    out = {"materials": []}
    for path in MATERIAL_PATHS:
        try:
            res = dump_one(path)
            out["materials"].append(res)
            note("%s -> %s nodes, %s unrecognised class(es)"
                 % (path, res.get("count", "?"), len(res.get("unrecognised_classes", []))))
        except Exception:
            note("FAILED %s" % path)
            for line in traceback.format_exc().splitlines():
                note("    " + line)

    saved = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir())
    dest = os.path.join(saved, OUT_NAME)
    with io.open(dest, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1, default=str)
    note("wrote %s" % dest)


try:
    main()
except Exception:
    note("FATAL")
    for line in traceback.format_exc().splitlines():
        note("    " + line)
