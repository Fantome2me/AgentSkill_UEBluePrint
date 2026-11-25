---
name: ue-bp-skill-v2
description: Route and execute Unreal Engine 5.8 graph work from Python - first classify the target as Blueprint, Material, UMG, Niagara or Level, then apply the right library; build and rewire Blueprint graphs (functions, events, nodes, pins, variables); audit any graph for the defects scripted generation produces (overlapping nodes, empty nodes, broken and interrupted wires, wasteful spacing); and lay out graphs in the measured hand-wired style instead of dumping nodes at the origin. Use whenever a task means "modify a Blueprint", "add a node or function", "rewire this graph", "why does this graph look wrong", "the nodes overlap", "spread too far apart", "clean up this graph", or when a script claims success but the asset changed nothing.
---

# UE graph operations - routed (v2)

Engine: **Unreal Engine 5.8** (`5.8.0-55116800+++UE5+Release-5.8`).

v2 adds two capabilities over v1 and keeps everything v1 verified:

1. **A router.** Decide *which* graph subsystem you are in before writing a single call.
2. **Layout discipline.** Encode the *measured* style of a real hand-wired graph, plus a tool that
   detects the defects scripted generation produces.

## Step 0 - ALWAYS route first

There are four distinct graph surfaces in this engine. They share a conversational shape ("add a node
and wire it") and almost no code. Routing by asset name alone is not enough - route by
**(asset kind x operation)**.

```text
Target is a Material / MaterialFunction / MaterialInstance?
    -> ROUTE B  (references/45-material-and-other-graphs.md, references/10-api.md not applicable)
Target is a NiagaraSystem / NiagaraEmitter?
    -> ROUTE D  (ToolsetRegistry)
Target is a WidgetBlueprint, or mentions UMG / panels / layout?
    -> ROUTE C  (WidgetBlueprintLibrary - NOT BlueprintGraphEditor)
Target is a Blueprint, or mentions events / functions / variables / casting?
    -> ROUTE A  (BlueprintGraphEditor)
Target is a Level / map / actor / WorldSettings / config?
    -> ROUTE E  (EditorActorSubsystem, EditorLoadingAndSavingUtils, ini)
```

Full decision table, byte-level classification evidence, per-route libraries, and the
wrong-route symptom table: **`references/00-router.md`**.

State the routing record in your report - it is four lines and it makes a wrong route obvious
immediately instead of three debugging cycles later.

## Step 1 - Then follow the loop

```text
0. PRE-FLIGHT   editor closed / process list clean / asset writable / commit headroom
1. BACKUP       copy the .uasset out, record size + SHA256
2. PROBE        learn real node ids, function paths, pin names - never guess
3. MUTATE       route-specific calls; create graphs and nodes, wire pins
                (materials: no edge until both ends' component count is known - see R12)
4. LAYOUT       position everything on the hand-wired grid (references/40-hand-wired-style.md)
5. ASSERT       error-node list empty, then compile, then status is BS_UP_TO_DATE
                (materials: assert recompile_material() returned True AND the log is clean)
6. SAVE         in the SAME run
7. PROVE        disk SHA256 changed AND mtime changed AND a FRESH process reads it back
8. AUDIT        run scripts/analyze_graph_layout.py on a dump of the result
9. REPORT       what you verified, and explicitly what you could not
```

**Step 4 and step 8 are what v2 adds.** A graph that compiles but is unreadable, overlapping, or
scattered is a defective deliverable.

## The layout rules, measured not guessed

Derived by measuring a real 71-node hand-wired material graph in this project:

| Rule | Measured basis |
|---|---|
| **HW1** sources stack in ONE far-left column | 19 of 71 nodes in a single column at `x=-4600` |
| **HW2** flow left to right, ~1-2 nodes per column | 40 columns for 71 nodes |
| **HW3** spacing is bounded BOTH ways | adjacent-column gaps 4..300, median 88; **none over 400** |
| **HW4** zero exact position collisions | measured 0 |
| **HW5** every node participates | measured 0 empty isolated nodes |
| **HW6** no broken inputs on non-source nodes | every non-source node has its inputs wired |
| **HW7** only sources and root-pin nodes may be unconnected | - |

### The defect this most often catches: nodes spread too far apart

Two long-wire cases that look alike and are **not** the same:

| Wire | Acceptable length | Why |
|---|---|---|
| processing node -> its consumer (stage to stage) | short, median **296**, keep under ~300 | a long one means you skipped a stage position |
| far-left source -> a late consumer | long is normal, measured up to **3200** | the source column is a deliberate shared pool |

So do **not** "fix" long source wires by moving sources out of the input column - that trades a
cosmetic problem for a structural one. The real defect is excessive gaps between processing stages:
if your stage step is 600+, a 6-stage chain exceeds a 1920-pixel viewport and the user must pan to
read one chain. Measured mean is **97 units per stage**; the reference fits 40 columns into 3800 units.

Full rules, the measured numbers, the anti-pattern table (G1-G7), acceptance thresholds, and a
drop-in `Layouter` class: **`references/40-hand-wired-style.md`**.

## Audit tool

```powershell
<python> <skill>/scripts/analyze_graph_layout.py <graph-dump.json>
<python> <skill>/scripts/analyze_graph_layout.py --self-test
```

Reports exact collisions, empty isolated nodes, broken inputs, dead ends, reachability, and
column-gap spacing, then PASS/FAIL. It was self-tested by injecting every defect it claims to detect
(`--self-test` asserts each one is caught, including a false-positive guard that a legitimate
root-pin node is not called "empty"). It degrades instead of crashing on dumps that lack coordinates.

Reference run on the real hand-wired graph:

```text
nodes=71 columns=40 widest=19 (at x=-4600)
reachable=71/71 (100.0%)  roots=7
EXACT COLLISIONS=0  empty_isolated=0  broken_inputs=0  dead_ends=0
spacing: median gap=88  max gap=300  (reference median=88, max=300)
VERDICT: PASS (no blocking defect)
```

## Reference files

| I need to... | Read |
|---|---|
| decide which subsystem I am in | `references/00-router.md` |
| the exact Blueprint method and signature | `references/10-api.md`, `references/blueprint_api.json` |
| get a parameter format right | `references/11-parameter-formats.md` |
| copy a working procedure | `references/12-templates.md` |
| drive the editor / debug a silent no-op | `references/13-driver.md` |
| the hard rules and the 50-entry trap catalogue | `references/20-rules.md` |
| the measured hand-wired layout style | `references/40-hand-wired-style.md` |
| Material / UMG / Niagara graph specifics | `references/45-material-and-other-graphs.md` |
| reach Niagara, materials, maps, actors, C++ bridge | `references/21-adjacent-systems.md` |
| know what came from where | `references/30-provenance.md` |

## Non-negotiable rules (unchanged from v1)

1. **A successful API return is not a successful edit.** Prove every write with changed disk hash +
   changed mtime + fresh-process readback.
2. **The editor must be closed for any write.**
3. **Back up before anything destructive.** `<Asset><GUID>.tmp` under `Saved/` is a valid package and
   your recovery path.
4. **Never invent a node id, function path, pin name, or palette category.** Probe first.
5. **Check the error-node list after every write**, then compile, then assert `BS_UP_TO_DATE`.
6. **Never round-trip a readback** (`write(read(g))`) - a readback is a rendering, not source.
7. **Verify in a fresh process.** Same-session checks lie.
8. **Lay out what you create.** An unreadable graph is a defective deliverable.
9. **Say what you could not verify.** Rendering, real input and built Slate widgets are not
     checkable headlessly.
10. **Type every pin before wiring it.** Material pins carry a component count and only
    the upcast is implicit. A float4 into a float3 input needs an explicit `ComponentMask`
    or the material compiles red while the Python call reports success. `ComponentMask`
    defaults to R+G, so set all four flags at creation.
11. **A graph you cannot compile is not a deliverable.** For materials that means
    `recompile_material()` returning True *and* a clean `Saved/Logs` scan, before the save
    and not after it.
