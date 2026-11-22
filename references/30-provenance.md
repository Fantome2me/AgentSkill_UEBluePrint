# Provenance and corrections (v2)

What v2 changed relative to v1, and where v2's new claims come from.

## v2 adds

| Addition | Basis | Confidence |
|---|---|---|
| `references/00-router.md` - route before acting | Four distinct graph subsystems exist in this engine, each with different libraries. The wrong-route symptom table is compiled from errors actually observed (`Cannot pythonize '28'`, `not a valid object path`, `no attribute 'BlueprintTools'`, widget-tree wipe). | High - subsystem separation is a fact of the engine |
| `references/40-hand-wired-style.md` - hand-wired layout style | **Measured** on `/Game/PluginsMakingAssets/Shaders/ParentsShaders/ObjectShader`, 71 nodes, via the project's own `Scripts/ProbeMaterialGraph.py` dump | High for this graph; see the single-sample caveat below |
| `references/45-material-and-other-graphs.md` | Material API from `unreal.MaterialEditingLibrary`; UMG and Niagara traps carried from the previously verified packages (Pre0/Pre3), which recorded them as `[实测]` | High, except where noted |
| `scripts/analyze_graph_layout.py` | Newly written; self-tested against injected defects | High for the checks it asserts; it was corrected twice during development (see below) |

## The measured numbers, and their one caveat

All layout numbers in `40-hand-wired-style.md` come from a **single 71-node material graph**:

```text
nodes=71  columns=40  widest column=19 (x=-4600)
x span=-4600..-800 (3800)   y span=-2200..1670 (3870)
adjacent-column gaps: min 4, median 88, mean 97, max 300, none over 400
node -> direct consumer distance: min 8, median 296, max 3200
exact collisions 0   empty isolated 0   broken inputs 0   dead ends 0
reachable from root pins 71/71
```

**Caveat, stated plainly:** one graph is a strong single sample, not a statistical study. The
*shape* rules (single far-left source column, left-to-right flow, no collisions, no abandoned nodes,
bounded spacing) are the durable conclusions. The exact deltas - 88, 296, 300 - are **calibratable**,
and should be re-measured as more graphs are audited. Do not treat 300 as a law of nature; treat it
as "the densest real example we have, and nothing in it ever needed more".

Corollary: the "spread too far apart" threshold is deliberately phrased as *adjacent-column gap over
400* (the reported defect) rather than *over 300* (a soft reference miss), so a graph slightly looser
than the reference is warned about rather than failed.

## Corrections made during v2 development

Recording these because each was a real bug in this package, caught by testing rather than review.

1. **The analyzer initially missed the user's main complaint.** v1's style rules bounded only the
   *minimum* spacing, so "nodes spread too far apart" was unconstrained. The user reported it; the
   measurement then showed adjacent-column gaps never exceed 300 in the reference graph, which turned
   a vague complaint into a checkable threshold (defect G7).

2. **First version miscounted reachability.** An early ad-hoc analysis reported 82%, while the
   finished tool reported 100% on the same dump. Cause: the earlier script crashed partway through
   building the edge map and its numbers were collected from a degraded run. The tool is correct;
   the earlier figure is void. Consequence: `REFERENCE["reachable_pct"]` is documented as *the
   threshold*, not as a claim about the reference graph's actual value (which is 100%).

3. **The analyzer falsely flagged a legitimate root node as "empty".** A texture sample wired
   directly into an `MP_*` pin has no inputs and no consumers - correct, but reported as an empty
   isolated node. Fixed by excluding root-pin nodes from that check, and a self-test assertion was
   added so the false positive cannot come back silently.

4. **The analyzer crashed on dumps without coordinates.** `ObjectGraph_NOW.json` records `x` but no
   `y`. Now it degrades: connectivity checks still run, layout checks are reported as `SKIPPED`, and
   reachability with zero root pins is reported as *not assessable* instead of a misleading 0%.

5. **The self-test asserted a brittle exact count.** After adding a fixture node it asserted
   `empty_isolated == 1` where the correct value was 2. Now it asserts on the specific node name,
   which is what the check actually means.

6. **The analyzer misread valid user-authored nodes as broken.** Reported by the user, and the most
   serious defect found in this package. The checker used an allow-list of classes *permitted* to
   have no inputs and judged every other class by that list, so `MaterialFunctionCall` and
   `MaterialExpressionCustom` nodes - a user's own encapsulated work - were accused of being
   deprecated and disconnected. Fixed by inverting the default: an unlisted class is now **opaque**,
   reported as information and never counted as broken, dead or orphaned. A regression assertion was
   added to `--self-test` ("user-authored nodes NOT called broken") so it cannot silently return.

7. **The dump format could not have contained those nodes anyway.** `ProbeMaterialGraph.py` attaches
   pin names through a hand-written `PINS` table, so `MaterialFunctionCall` and `Custom` nodes were
   dumped without usable wiring - and a class absent from that table risks being skipped entirely,
   which means an audit could PASS on a dump that never saw the user's nodes. The corrected,
   read-only `scripts/DumpMaterialGraph.py` now records **every** expression whatever its class,
   marks unrecognised classes explicitly, and reports `unrecognised_classes` in the output so a
   downstream audit can never silently skip them.

8. **I previously described the reachability figure incorrectly.** An early ad-hoc script reported
   82%; I then called the higher 100% figure a "phantom edge" artifact. That was backwards. The
   engine really does report 2 connected sources on those texture-sample nodes (they expose both
   `Coordinates` and `TextureObject`); the old script crashed while building its edge map and lost
   those list-valued edges, which is why it under-counted. The correct value is 100%, and the real
   lesson is about the *pin names*, not the edges: a `<order unknown: N sources>` bucket is not a pin
   name and must never be treated as one, but its sources are genuine edges and must be kept.

   Consequence for the style guide: `REFERENCE["reachable_pct"]` is documented as a **threshold**
   (82% = "worse than this is worth investigating"), never as a claim about the reference graph's
   actual reachability, which is 100%.

## Carried from v1 unchanged

`10-api.md`, `11-parameter-formats.md`, `12-templates.md`, `13-driver.md`, `20-rules.md`,
`21-adjacent-systems.md`, `blueprint_api.json`, `scripts/run_one.py`.

These derive from Epic's Unreal Python 5.8 documentation, the engine headers on this machine, and a
previously verified real session. Their provenance is recorded in the v1 package
(`D:\UE_BP_Skill\ue-bp-skill-test\references\30-provenance.md`) and is not restated here.

Key v1 conclusions that v2 keeps:

- `unreal.BlueprintTools` / `write_graph_dsl` are **not engine API** - no `UBlueprintTools` UCLASS
  exists anywhere in the engine source tree. They belong to one specific project.
- Python **can** create Blueprint nodes, via `unreal.BlueprintGraphEditor`'s factories and
  `create_node_from_name` against the palette.
- Three layers: engine-native (portable) / editor toolset (experimental) / project-private (not
  portable). Build reusable procedures on layer 1 only.

## Still unverified

1. **T3-T6 in `12-templates.md` have never been executed end-to-end.** Written against documented
   APIs; no run confirms them.
2. **`SetPinValue` string forms** for struct, enum and object-reference pins follow the engine's
   `ImportText` convention but were not exercised on 5.8.
3. **`add_call_function_node` path spelling** - Epic's docs show `Module.Class.Function`, a real
   session used `Module.Class:Function`. Both may work; confirm before standardising.
4. **The layout rules have been measured on material graphs only.** Blueprint graph nodes have
   different default sizes and pin geometry, so the same deltas are plausible but not confirmed
   there. Re-measure before quoting 88/300 as Blueprint numbers.
5. **The open-source MCP projects** referenced in the v1 package were described from search results
   plus three fetched pages; no code from them is used here.
