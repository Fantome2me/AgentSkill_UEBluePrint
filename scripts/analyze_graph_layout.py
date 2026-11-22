#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Layout audit for UE graph dumps - detects the defects scripted generation produces.

Checks (each maps to a rule in references/40-hand-wired-style.md):
  * exact position collisions      -> HW4 / defect G1   (must be 0)
  * empty isolated nodes           -> HW5 / defect G2   (must be 0)
  * broken inputs (non-source)     -> HW6 / defect G3
  * dead ends (no consumer)        -> HW4 .. G4
  * column structure + spacing     -> HW1/HW2/HW3 / G5
  * reachability from root pins    -> HW5

USAGE
    <python> analyze_graph_layout.py <dump.json> [<dump2.json> ...]
    <python> analyze_graph_layout.py --self-test
    <python> analyze_graph_layout.py --json <dump.json>      # machine-readable output

INPUT FORMAT (produced by the project's ProbeMaterialGraph.py, or by any dumper that
records positions - see references/13-driver.md for how to produce one):

    { "path": "/Game/.../Material", "root": {"MP_BASE_COLOR": "NodeName", ...},
      "expressions": [ {"name":..., "class":..., "x":int, "y":int,
                        "inputs": {"PinName": "SourceNodeName"|null, ...}}, ... ] }

`inputs` may also be a flat list of source names when pin order is unknown; both are accepted.

EXIT CODES
    0 = no blocking defect (collisions and orphans are both zero)
    1 = blocking defect found
    2 = bad usage / unreadable input
"""

import argparse
import collections
import itertools
import json
import sys

# Node classes that legitimately have no input pins (HW7).
SOURCE_CLASSES = {
    "MaterialExpressionScalarParameter",
    "MaterialExpressionVectorParameter",
    "MaterialExpressionTextureSampleParameter2D",
    "MaterialExpressionTextureObjectParameter",
    "MaterialExpressionStaticSwitchParameter",
    "MaterialExpressionFunctionInput",
    "MaterialExpressionConstant",
    "MaterialExpressionConstant2Vector",
    "MaterialExpressionConstant3Vector",
    "MaterialExpressionConstant4Vector",
    "MaterialExpressionTextureCoordinate",
    "MaterialExpressionTime",
    "MaterialExpressionWorldPosition",
    "MaterialExpressionCameraPositionWS",
    "MaterialExpressionVertexNormalWS",
    "MaterialExpressionPixelNormalWS",
    "MaterialExpressionPerInstanceCustomData",
    "MaterialExpressionSkyAtmosphereLightDirection",
}

# Classes KNOWN to take input pins, so that "no inbound edge" is genuinely suspicious.
#
# Keep this list SHORT and only add classes you have actually seen wired. The default for an
# unlisted class is OPAQUE, not broken - see the classification block in analyze(). That inversion
# is deliberate: an over-long "known" list would recreate exactly the false-positive problem this
# list is meant to avoid, because unlisted classes would be judged as if the list were exhaustive.
#
# Notably ABSENT on purpose (all legitimately have zero inputs at their call site):
#   MaterialFunctionCall                  - a function may take no arguments; its real wiring lives
#                                           inside the MaterialFunction asset, not in this graph
#   MaterialExpressionCustom              - a Custom HLSL node may declare no inputs
#   MaterialExpressionFunctionInput       - by definition an input
#   MaterialExpressionStaticSwitchParameter
#   MaterialExpressionPerInstanceCustomData
KNOWN_INPUT_TAKING_CLASSES = {
    # arithmetic / math (all take A/B or an input)
    "MaterialExpressionAdd", "MaterialExpressionSubtract", "MaterialExpressionMultiply",
    "MaterialExpressionDivide", "MaterialExpressionPower", "MaterialExpressionDotProduct",
    "MaterialExpressionLinearInterpolate", "MaterialExpressionClamp", "MaterialExpressionOneMinus",
    "MaterialExpressionNormalize", "MaterialExpressionComponentMask", "MaterialExpressionAppendVector",
    "MaterialExpressionSaturate", "MaterialExpressionAbs", "MaterialExpressionSine",
    "MaterialExpressionCosine", "MaterialExpressionFrac", "MaterialExpressionFloor",
    "MaterialExpressionCeil", "MaterialExpressionMin", "MaterialExpressionMax",
    "MaterialExpressionStep", "MaterialExpressionSmoothStep", "MaterialExpressionIf",
    "MaterialExpressionCrossProduct", "MaterialExpressionDistance", "MaterialExpressionLength",
    # transforms / space conversion
    "MaterialExpressionTransform", "MaterialExpressionTransformPosition",
    # texture sampling
    "MaterialExpressionTextureSample", "MaterialExpressionTextureSampleParameter2D",
    "MaterialExpressionTextureObject",
    # switches and reroutes
    "MaterialExpressionStaticSwitch", "MaterialExpressionReroute",
    # engine inputs that consume a coordinate or vector
    "MaterialExpressionPanner", "MaterialExpressionRotator", "MaterialExpressionTexCoord",
}


def _inputs_of(expr):
    """Return (known_sources, has_unknown_bucket).

    A dumper that cannot map pins to names records an opaque bucket such as
    "<order unknown: 2 sources>". Those sources are real edges, but the bucket key is NOT a pin
    name - treating it as one invents a phantom connection and inflates reachability. So the
    sources are returned separately and the caller marks the node as having uncertain wiring.
    """
    raw = expr.get("inputs") or {}
    known, unknown_bucket = [], False
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(k, str) and k.startswith("<"):
                unknown_bucket = True
            vals = v if isinstance(v, list) else [v]
            for s in vals:
                if isinstance(s, str) and s:
                    known.append(s)
    else:
        for s in raw:
            if isinstance(s, str) and s:
                known.append(s)
    return known, unknown_bucket


def analyze(dump):
    exprs = dump.get("expressions") or dump.get("nodes") or []
    roots = dump.get("root") or {}
    if isinstance(roots, dict):
        root_nodes = set(v for v in roots.values() if isinstance(v, str))
    else:
        root_nodes = set(roots)

    n = len(exprs)
    if n == 0:
        return {"label": dump.get("path", "?"), "error": "no expressions in dump"}

    # ---- positions (optional) ----
    # Some dumpers record only x, or omit coordinates entirely. Connectivity checks are still
    # valid in that case, so degrade instead of failing: report which checks were skipped.
    skipped = []
    have_xy = all(("x" in e and "y" in e) for e in exprs)
    if not have_xy:
        xs_ok = [e for e in exprs if "x" in e]
        if xs_ok:
            skipped.append("exact-collision check (dump lacks per-node y)")
            xs = [int(e["x"]) for e in xs_ok]
            columns = collections.Counter(xs)
            pts = [(int(e["x"]), 0, e["name"]) for e in xs_ok]
            dxs = [abs(a[0] - b[0]) for a, b in itertools.combinations(pts, 2) if a[0] != b[0]]
            collisions, dys = {}, []
            ys = []
        else:
            skipped.append("all layout checks (dump has no coordinates)")
            xs, ys, columns, collisions, dxs, dys = [], [], collections.Counter(), {}, [], []
    else:
        pos = collections.Counter((int(e["x"]), int(e["y"])) for e in exprs)
        collisions = {k: v for k, v in pos.items() if v > 1}
        xs = [int(e["x"]) for e in exprs]
        ys = [int(e["y"]) for e in exprs]
        columns = collections.Counter(xs)
        pts = [(int(e["x"]), int(e["y"]), e["name"]) for e in exprs]
        dxs = [abs(a[0] - b[0]) for a, b in itertools.combinations(pts, 2) if a[0] != b[0]]
        dys = [abs(a[1] - b[1]) for a, b in itertools.combinations(pts, 2) if a[1] != b[1]]

    # ---- connectivity ----
    inb = collections.defaultdict(list)
    outb = collections.defaultdict(list)
    uncertain_wiring = []            # nodes whose dump could not name the pins
    for e in exprs:
        srcs, has_unknown = _inputs_of(e)
        if has_unknown:
            uncertain_wiring.append(e["name"])
        for s in srcs:
            inb[e["name"]].append(s)
            outb[s].append(e["name"])
    uncertain_wiring = sorted(set(uncertain_wiring))

    seen, stack = set(), list(root_nodes)
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(inb.get(cur, []))

    def klass(e):
        return str(e.get("class", ""))

    # A node that IS a root pin's node is doing its job by definition, even when it has no
    # inputs and no consumers (e.g. a bare texture sample wired straight into MP_BASE_COLOR).
    # Excluding those avoids the false positive of calling a legitimate root "empty".
    empty_isolated = [e["name"] for e in exprs
                      if not inb.get(e["name"]) and not outb.get(e["name"])
                      and e["name"] not in root_nodes]

    # ---- user-authored / function nodes must NEVER be assumed dead ----
    # MaterialFunctionCall, MaterialExpressionCustom, function input/output and anything else not
    # recognised are OPAQUE. A function call with no arguments legitimately has no inputs, and a
    # MaterialFunction hides its wiring inside another asset, so a "no inputs == broken" rule
    # misreads user-authored nodes as deprecated/broken. Classify them instead of judging them.
    opaque, known_nonsource = [], set()
    for e in exprs:
        k = klass(e)
        if k in SOURCE_CLASSES:
            continue
        if k in KNOWN_INPUT_TAKING_CLASSES:
            known_nonsource.add(e["name"])
        else:
            opaque.append(e["name"])

    # ---- broken inputs: report ONLY when we are actually sure ----
    # All three must hold: the class is known to take inputs, the dump named the pins (no opaque
    # bucket), and there is no inbound edge. Anything else is reported as uncertain, not broken.
    # Deliberately conservative: a false "your node is broken" costs far more than a missed finding,
    # because the user cannot tell a tool bug from a real defect.
    broken_inputs = [e["name"] for e in exprs
                     if e["name"] in known_nonsource
                     and not inb.get(e["name"])
                     and e["name"] not in uncertain_wiring]

    opaque_unwired = [nm for nm in opaque
                      if not inb.get(nm) and not outb.get(nm) and nm not in root_nodes]

    dead_ends = [e["name"] for e in exprs
                 if not outb.get(e["name"]) and e["name"] not in root_nodes]
    unreachable = [e["name"] for e in exprs if e["name"] not in seen]

    return {
        "label": dump.get("path", "?"),
        "nodes": n,
        "columns": len(columns),
        "widest_column": max(columns.values()) if columns else 0,
        "widest_column_x": columns.most_common(1)[0][0] if columns else None,
        "min_dx": min(dxs) if dxs else None,
        "min_dy": min(dys) if dys else None,
        "x_span": [min(xs), max(xs)] if xs else None,
        "y_span": [min(ys), max(ys)] if ys else None,
        "exact_collisions": len(collisions),
        "collision_groups": [[list(k), v] for k, v in list(collisions.items())[:10]],
        "empty_isolated": len(empty_isolated),
        "empty_isolated_names": empty_isolated[:15],
        "broken_inputs": len(broken_inputs),
        "broken_input_names": broken_inputs[:15],
        "dead_ends": len(dead_ends),
        "dead_end_names": dead_ends[:15],
        "reachable": len(seen),
        "reachable_pct": round(100.0 * len(seen) / n, 1),
        "unreachable": len(unreachable),
        "root_nodes": len(root_nodes),
        "checks_skipped": skipped,
        "spacing": _spacing_stats(xs),
        "opaque_user_authored": len(opaque),
        "opaque_unwired": len(opaque_unwired),
        "opaque_unwired_names": opaque_unwired[:15],
        "uncertain_wiring": len(uncertain_wiring),
        "uncertain_wiring_names": uncertain_wiring[:15],
    }


# Reference values measured on a real 71-node hand-wired material graph.
REFERENCE = {"exact_collisions": 0, "empty_isolated": 0, "broken_inputs": 0,
             "reachable_pct": 82.0,        # measured on the real hand-wired reference graph
             "min_dy": 10,
             "max_adjacent_gap": 300,      # measured max; > 400 is the "spread too far" defect
             "median_gap": 88,             # measured median adjacent-column gap
             "stage_hop_median": 296}      # measured median node -> consumer distance


def _spacing_stats(xs):
    """Adjacent-column gaps, used to detect defect G7 (nodes spread too far apart)."""
    cols = sorted(set(xs))
    gaps = [cols[i + 1] - cols[i] for i in range(len(cols) - 1)]
    if not gaps:
        return {"max_gap": None, "median_gap": None, "over_300": 0, "over_400": 0, "big_gaps": []}
    gaps_sorted = sorted(gaps)
    mid = len(gaps_sorted) // 2
    median = (gaps_sorted[mid] if len(gaps_sorted) % 2
              else (gaps_sorted[mid - 1] + gaps_sorted[mid]) // 2)
    return {
        "max_gap": max(gaps),
        "median_gap": median,
        "over_300": sum(1 for g in gaps if g > REFERENCE["max_adjacent_gap"]),
        "over_400": sum(1 for g in gaps if g > 400),
        "big_gaps": sorted([g for g in gaps if g > REFERENCE["max_adjacent_gap"]], reverse=True)[:10],
    }


def report(res, out=sys.stdout):
    if "error" in res:
        print("!! %s: %s" % (res["label"], res["error"]), file=out)
        return 1
    print("== %s ==" % res["label"], file=out)
    print("  nodes=%d  columns=%d  widest=%d (at x=%s)"
          % (res["nodes"], res["columns"], res["widest_column"], res["widest_column_x"]), file=out)
    print("  x span=%s  y span=%s  min|dx|=%s  min|dy|=%s"
          % (res["x_span"], res["y_span"], res["min_dx"], res["min_dy"]), file=out)
    print("  reachable=%d/%d (%.1f%%)  roots=%d"
          % (res["reachable"], res["nodes"], res["reachable_pct"], res["root_nodes"]), file=out)
    print("  EXACT COLLISIONS=%d  empty_isolated=%d  broken_inputs=%d  dead_ends=%d"
          % (res["exact_collisions"], res["empty_isolated"],
             res["broken_inputs"], res["dead_ends"]), file=out)
    for s in res.get("checks_skipped") or []:
        print("  SKIPPED: %s" % s, file=out)

    # User-authored and unrecognised nodes: reported as INFORMATION, never as defects.
    if res.get("opaque_user_authored"):
        print("  user-authored / unrecognised nodes: %d (treated as opaque, NOT judged as broken)"
              % res["opaque_user_authored"], file=out)
        if res.get("opaque_unwired_names"):
            print("     %d of them have no visible edges in this dump - this may be normal"
                  % res["opaque_unwired"], file=out)
            print("     (a function call with no args, or wiring hidden inside its own asset):", file=out)
            print("     %s" % res["opaque_unwired_names"], file=out)
            print("     -> verify inside the asset before calling any of these dead.", file=out)
    if res.get("uncertain_wiring"):
        print("  wiring NOT verifiable for %d node(s): the dump could not name their input pins"
              % res["uncertain_wiring"], file=out)
        print("     so their inputs are excluded from the broken-input check:", file=out)
        print("     %s" % res["uncertain_wiring_names"], file=out)

    sp = res.get("spacing") or {}
    if sp.get("median_gap") is not None:
        print("  spacing: median gap=%d  max gap=%d  (reference median=%d, max=%d)"
              % (sp["median_gap"], sp["max_gap"],
                 REFERENCE["median_gap"], REFERENCE["max_adjacent_gap"]), file=out)
        if sp["over_400"]:
            print("   ! %d column gap(s) exceed 400 units -> defect G7 (spread too far apart)"
                  % sp["over_400"], file=out)
            print("     largest gaps: %s" % sp["big_gaps"], file=out)
        elif sp["over_300"]:
            print("   ~ %d column gap(s) exceed the measured reference max of %d"
                  % (sp["over_300"], REFERENCE["max_adjacent_gap"]), file=out)
            print("     largest gaps: %s" % sp["big_gaps"], file=out)

    blocking = []
    if res["exact_collisions"]:
        blocking.append("position collisions (defect G1)")
        print("   collisions at: %s" % res["collision_groups"], file=out)
    if res["empty_isolated"]:
        blocking.append("empty isolated nodes (defect G2)")
        print("   isolated: %s" % res["empty_isolated_names"], file=out)
    if res["broken_inputs"] > REFERENCE["broken_inputs"]:
        print("   ! broken inputs %d exceeds the hand-wired reference of %d (defect G3)"
              % (res["broken_inputs"], REFERENCE["broken_inputs"]), file=out)
        print("     %s" % res["broken_input_names"], file=out)
    if res["dead_ends"]:
        print("   ! dead ends (no consumer): %s" % res["dead_end_names"], file=out)
    if res["reachable_pct"] < REFERENCE["reachable_pct"]:
        if res["root_nodes"] == 0:
            print("   ~ reachability NOT ASSESSABLE: the dump records no root pins"
                  " (0% here means unknown, not broken)", file=out)
        else:
            print("   ! reachability %.1f%% is below the hand-wired reference of %.0f%%"
                  % (res["reachable_pct"], REFERENCE["reachable_pct"]), file=out)

    if blocking:
        print("  VERDICT: FAIL - " + "; ".join(blocking), file=out)
        return 1
    print("  VERDICT: PASS (no blocking defect)", file=out)
    return 0


def self_test():
    """Inject the defects this tool claims to detect, and confirm each is reported."""
    print("self-test: injecting 1 exact collision, 1 empty isolated node, 1 broken input, 1 dead end")
    dump = {
        "path": "SELFTEST",
        "root": {"MP_BASE_COLOR": "B"},
        "expressions": [
            # two nodes at the exact same position -> collision
            {"name": "A", "class": "MaterialExpressionConstant", "x": 0, "y": 0, "inputs": {}},
            {"name": "B", "class": "MaterialExpressionMultiply", "x": 0, "y": 0, "inputs": {"A": "A"}},
            # non-source node with an empty input -> broken input (C is never consumed -> dead end)
            {"name": "C", "class": "MaterialExpressionMultiply", "x": 300, "y": 0, "inputs": {}},
            # not wired to anything in or out, and not a root -> empty isolated
            {"name": "Z", "class": "MaterialExpressionAdd", "x": 600, "y": 400, "inputs": {}},
            # a legitimate root node with no inputs: must NOT be reported as empty
            {"name": "R", "class": "MaterialExpressionTextureSampleParameter2D",
             "x": 900, "y": 0, "inputs": {}},
            # a stage placed 1500 units away -> defect G7 (spread too far apart)
            {"name": "FAR", "class": "MaterialExpressionMultiply",
             "x": 2400, "y": 0, "inputs": {"R": "R"}},
            # user-authored nodes: MUST NOT be reported as broken/dead
            {"name": "FNCALL", "class": "MaterialFunctionCall", "x": 1200, "y": 0, "inputs": {}},
            {"name": "CUSTOM", "class": "MaterialExpressionCustom", "x": 1500, "y": 0, "inputs": {}},
            # a node whose dump could not name its pins: inputs must be treated as uncertain
            {"name": "MASK", "class": "MaterialExpressionComponentMask", "x": 1800, "y": 0,
             "inputs": {"<order unknown: 1 sources>": ["R"]}},
        ],
    }
    dump["root"]["MP_NORMAL"] = "R"
    dump["root"]["MP_EMISSIVE_COLOR"] = "FAR"
    dump["root"]["MP_SPECULAR"] = "FNCALL"
    dump["root"]["MP_ROUGHNESS"] = "CUSTOM"
    res = analyze(dump)
    ok = True
    checks = [
        ("exact collision detected", res["exact_collisions"] == 1),
        # Z is isolated (counted); R is isolated too but excluded because it is a root node,
        # so the assertion is on Z specifically rather than on a brittle exact count.
        ("isolated non-root node detected", "Z" in res["empty_isolated_names"]),
        ("root node NOT falsely flagged as empty", "R" not in res["empty_isolated_names"]),
        ("broken input detected", res["broken_inputs"] >= 1),
        ("dead end detected", res["dead_ends"] >= 1),
        ("excessive column gap detected (G7)", (res.get("spacing") or {}).get("over_400", 0) >= 1),
        # the regression guard for the reported bug:
        ("user-authored nodes NOT called broken",
         "FNCALL" not in res["broken_input_names"] and "CUSTOM" not in res["broken_input_names"]),
        ("user-authored nodes counted as opaque", res["opaque_user_authored"] >= 2),
        ("uncertain-pin node excluded from broken check", "MASK" not in res["broken_input_names"]),
        ("uncertain wiring reported", "MASK" in res["uncertain_wiring_names"]),
        ("verdict is FAIL", report(res, out=open(__import__("os").devnull, "w")) == 1),
    ]
    for label, passed in checks:
        print("  [%s] %s" % ("OK" if passed else "FAIL", label))
        ok = ok and passed
    print("self-test: %s" % ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dumps", nargs="*")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true", help="emit machine-readable results")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.dumps:
        ap.print_help()
        return 2

    worst = 0
    results = []
    for path in args.dumps:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                dump = json.load(fh)
        except Exception as exc:
            print("!! cannot read %s: %s" % (path, exc))
            worst = max(worst, 2)
            continue
        # a dump may contain several materials under "materials"
        items = dump.get("materials") if isinstance(dump, dict) and "materials" in dump else [dump]
        for item in items:
            res = analyze(item)
            results.append(res)
            if not args.json:
                worst = max(worst, report(res))
                print()
    if args.json:
        json.dump(results, sys.stdout, ensure_ascii=False, indent=1)
        print()
    return worst


if __name__ == "__main__":
    sys.exit(main())
