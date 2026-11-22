# Hand-wired graph style - and the anti-patterns it avoids

Derived by measuring a real hand-wired material graph in this project, then contrasted with the
failure modes scripted generation actually produces.

**Evidence base.** `/Game/PluginsMakingAssets/Shaders/ParentsShaders/ObjectShader`, 71 expression
nodes, dumped with `Scripts/ProbeMaterialGraph.py` (which records
`material_expression_editor_x/y` per node). Measured results:

| Metric | Measured |
|---|---|
| nodes | 71 |
| distinct x columns | 40 |
| widest single column | **19 nodes** (at `x = -4600`) |
| x span | -4600 .. -800 (3800 units) |
| y span | -2200 .. 1670 (3870 units) |
| smallest nonzero `\|dx\|` | 4 |
| smallest nonzero `\|dy\|` | **10** |
| coordinate gcd | 2 (all coordinates even) |
| reachable from root pins | 58 / 71 = **82%** |
| **exact position collisions** | **0** |
| **empty isolated nodes** | **0** |
| dead ends (no consumer, not a root) | 2 |

Read that table as a specification. The hand-wired graph has **zero overlaps and zero empty
nodes** while still using tight 10-unit vertical packing. That combination is the whole point: it is
*dense* but *never ambiguous*.

**Scope honesty.** This is one 71-node material graph, not a statistical study of every graph the
user ever drew. Treat the numbers as a strong single-sample guide, not a universal constant. The
*shape* rules below (input column, left-to-right, no collisions, no orphans) are the durable part;
the exact pixel deltas are calibratable.

---

## Part 1 - The seven rules

### HW1 - One deep input column on the far left

Parameters, textures, constants and other sources all live in a **single leftmost column**, stacked
vertically. The measured graph puts 19 of its 71 nodes in one column at the far left.

Why this matters: sources have no inputs and are the natural starting points of every trace. Grouping
them means a reader finds any input by scanning one column, and re-ordering a parameter never
requires rewiring a graph.

Anti-pattern: scattering texture samples and constants wherever the cursor happened to be. That is
what produces a graph you cannot read even though it compiles.

### HW2 - Flow left to right, one consumer per column where possible

After the input column, each processing step advances rightward. 40 distinct columns across 71
nodes means the measured graph averages **under two nodes per column**. Nodes are roughly
single-file, not clustered.

Rule: **x strictly increases along the data path.** A node that consumes something never sits to the
left of its source.

### HW3 - Vertical stacking is tight; horizontal spacing is bounded on BOTH ends

Measured on the reference graph:

| Quantity | Measured |
|---|---|
| gap between **adjacent columns** | min 4, median **88**, mean 97, **max 300** |
| gaps exceeding 400 units | **none** |
| distance from a node to its direct consumer | min 8, median **296** |
| widest-column vertical span (19 nodes) | 3470 |

So horizontal spacing has a **floor and a ceiling**, and both matter:

- **Floor (min gap):** measured min is 4-10. Below ~40 units two nodes visually touch and their
  in/out wire stubs collide, which reads as a broken connection. Keep at least ~50.
- **Ceiling (max gap):** **no adjacent-column gap in the reference exceeds 300 units.** This is the
  rule that "spread out too far" violates.

**The distinction that matters - two different kinds of long wire:**

| Wire kind | How long is acceptable | Why |
|---|---|---|
| processing node -> its consumer (stage to stage) | **short**: median 296, keep under ~300 | the reader follows it step by step; a long one means you skipped a stage position |
| far-left source (parameter/texture) -> a late consumer | **long is normal**: measured up to 3200 | the source column is a shared pool on purpose (HW1); its wires necessarily reach across |

So "node spacing too far apart" specifically means **excessive gaps between gear stages**, not long
wires from the input column. Do not "fix" the second kind by moving sources out of the input column -
that trades a cosmetic problem for a structural one.

Anti-pattern: computing positions as `anchor_x + (stage * 600)` or larger. Anything above ~300 per
stage pushes a 6-stage graph past a 1920-pixel viewport, so the user has to pan to read one chain.
The measured reference compresses **40 columns into a 3800-unit span** - about 97 units per step.

### HW4 - Zero exact collisions

Measured: **0**. Two nodes at the identical `(x, y)` are never acceptable. They render on top of each
other, you cannot see or select the lower one, and a later automated pass cannot tell which is which.

This is the single defect that most clearly separates hand-wiring from naive generation, because
generated code frequently has no position logic at all and therefore drops every node at the origin.

### HW5 - Every node must participate

Measured: **0 empty isolated nodes**, and 82% reachable from the root pins. Every node in a
hand-wired graph is either on a path from a source to a root pin, or is a recognisable
source/constant waiting to be used.

Rule: **create-then-connect, never create-and-abandon.** If you cannot say which pin consumes a node
you just made, you should not have made it. Scripted generation is prone to leaving orphan nodes
behind after a failed connection attempt or a revised plan.

### HW6 - No broken inputs on non-source nodes

Measured: 3 nodes have empty input pins despite not being sources. Treat that as the acceptable
upper bound for work-in-progress, not a target — every one of those is a place where a reader cannot
tell whether the author forgot or intended it.

A non-source node with **all** inputs empty is almost always a bug: either the connection failed
silently (`try_create_connection` returned `False` and nothing checked) or the node was left over
from an abandoned edit.

### HW7 - Sources and root-pin nodes are the only unconnected-allowed nodes

Constants, parameters, texture samples and function inputs legitimately have no inputs. Root material
pins legitimately have no consumers. **Nothing else does.**

### HW8 - User-authored and unrecognised nodes are OPAQUE, never "dead"

This is the rule that a naive analyzer breaks most damagingly, so it gets its own entry.

Nodes you cannot classify are **not** defects. Specifically, these legitimately have **zero inputs at
their call site**, and must never be reported as broken, deprecated or orphaned:

| Node | Why it may have no visible inputs |
|---|---|
| `MaterialFunctionCall` | a function may take no arguments, and its real wiring lives **inside the MaterialFunction asset**, not in this graph |
| `MaterialExpressionCustom` | a Custom HLSL node may declare no inputs |
| `MaterialExpressionFunctionInput` | by definition an input |
| `MaterialExpressionStaticSwitchParameter` | a parameter node, no upstream |
| `MaterialExpressionPerInstanceCustomData` | engine-provided data, no upstream |
| anything whose class you do not recognise | by definition you do not know its rules |

**Rule: classify, do not judge.** An unrecognised class is recorded as *opaque* and reported as
information ("N user-authored nodes, treated as opaque"). It is never counted toward broken inputs,
dead ends or orphans.

Corollary for the analyzer's default direction: the set of classes *known to take inputs* is a short
allow-list, and **the default for an unlisted class is opaque, not broken**. An exhaustive "these
classes are fine" list is impossible to maintain, and any gap in it becomes a false accusation
against valid user work. A false "your node is broken" is far more expensive than a missed finding,
because the user cannot tell a tool bug from a real defect.

Two related honesty rules for the dump format:

- **An unknown pin-order bucket is not a pin name.** When a dumper cannot map pins to names it records
  something like `<order unknown: 2 sources>`. The source list is a real edge and must be kept for
  connectivity; the bucket key is not a name and must never be treated as one. Nodes in that state
  are reported as *wiring not verifiable* and are excluded from the broken-input check rather than
  quietly blessed or quietly accused.
- **Nodes missing from the dump entirely cannot be audited at all.** The project's
  `ProbeMaterialGraph.py` only knows the classes in its `PINS` table, so a graph containing
  `MaterialFunctionCall` nodes dumps **without them** - they are silently absent, not present-and-clean.
  Before trusting a PASS on such a graph, confirm the dump contains the node classes you expect.
  `references/45-material-and-other-graphs.md` has the corrected dumper that records every expression.


---

## Part 2 - Measured anti-patterns of scripted generation

These are the defects the user reported, each matched to its cause. The point is that every one is
**preventable by a rule above**, not by "being more careful".

| # | Defect | Why it happens | Rule that prevents it | Detection |
|---|---|---|---|---|
| G1 | **Nodes overlap / stack on each other** | The node factories take no position. `add_call_function_node(path)` has no location parameter, so every generated node defaults to the same place unless the caller sets it. | HW3, HW4 | exact-collision count > 0 |
| G2 | **Empty nodes** | A node created in a batch whose connection later failed, or a revised plan that superseded it, is never removed. | HW5 | no inputs AND no consumers |
| G3 | **Broken connections** | `try_create_connection` returns `False` and the return value was not checked; or pins were re-resolved after a compile and stale handles silently no-op'd. | HW6 + always check the bool | non-source node with all inputs empty |
| G4 | **Dangling / interrupted wires** | `break_pin_links()` severs *every* link on a pin, not just the one intended. Reconnecting elsewhere then orphans whatever it used to drive. | verify the whole exec chain after any rewire | node with a consumer that no longer has its own consumer |
| G5 | **Unreadable layout** | Positions set to a fixed grid offset from an anchor that was itself auto-placed, so the whole block lands somewhere arbitrary. | HW1, HW2 | column count ≈ node count with no left input column |
| G7 | **Nodes spread too far apart** | Stage step chosen generously ("600 looks tidy") or scaled by node count / subgraph depth. Nothing caps it, so a 6-stage graph spans past a screen and the user must pan to read one chain. | HW3 ceiling | adjacent-column gap > 300, or x-span > ~100 per stage |
| G8 | **A tool calls valid user-authored nodes "broken" or "deprecated"** | The checker used an allow-list of "classes allowed to have no inputs" and judged everything else by that list, so `MaterialFunctionCall` and Custom nodes were accused. The worst failure mode in this whole table: it destroys trust in every other finding. | HW8 | run the analyzer's `--self-test`, which asserts user-authored nodes are not called broken |
| G6 | **Graph compiles but is unusable** | Nothing above is checked; only `compile_blueprint`'s return value was read. | the layout audit below | run the analyzer |

### Why generation fails here and hand-wiring does not

A human placing a node **sees** the collision and moves it. A script has no such feedback unless you
compute it. Generation therefore needs the layout discipline *encoded as code*, which is what
`scripts/analyze_graph_layout.py` is for.

---

## Part 3 - The layout audit (run it on generated graphs)

`scripts/analyze_graph_layout.py` takes a graph dump and reports exactly the six defects above.

```powershell
<python> <skill>/scripts/analyze_graph_layout.py <graph-dump.json>
```

Expected output on a clean graph, using the measured hand-wired graph as the reference:

```text
nodes=71 columns=40 widest=19  min|dx|=4 min|dy|=10
reachable=58/71 (82%)
EXACT COLLISIONS=0  orphans=0  broken_inputs=3  dead_ends=2
```

**Acceptance gate for generated work:**

| Check | Threshold |
|---|---|
| exact collisions | **must be 0** |
| empty isolated nodes | **must be 0** |
| broken inputs (non-source) | target 0; > 3 means connections are failing silently |
| dead ends | every one must be explainable, or removed |
| reachable fraction | aim for the measured 82% or better on a finished graph |
| widest input column | sources grouped, not scattered |
| **max adjacent-column gap** | **<= 300** (measured reference max); > 400 is the reported "too far apart" defect |
| **x span per column** | ~100 units per stage (measured mean 97, median 88) |
| **stage-to-stage hop** | median ~300; a hop far above that between two *processing* nodes means a missing stage position |
| long wires from the far-left source column | **legitimate**, up to the measured 3200 - do not count these as defects |

The analyzer was self-tested by injecting the defects it claims to detect (an exact collision, a
broken input, a dead end) and confirming it reports them; see `scripts/analyze_graph_layout.py`
`--self-test`.

---

## Part 4 - Positioning code that produces this shape

The concrete algorithm, ready to adapt. It enforces HW1-HW4 mechanically.

```python
# Measured from the reference hand-wired graph: adjacent-column gaps run 4..300 (median 88).
# 320 sits just above the measured max, so it is dense enough to read AND still collision-free.
COLUMN_STEP_X = 320     # HW2/HW3 ceiling - do NOT inflate this "for tidiness"
ROW_STEP_Y    = 90      # vertical distance between siblings (measured min gap was 10)
SOURCE_X      = -2600   # far-left input column, matching the measured convention
SOURCE_Y0     = -1800

# Budget check: 6 stages * 320 = 1920 units, which still fits a 1080p viewport.
# The reference graph fits 40 columns into 3800 units. If your step pushes a 6-stage
# chain past ~2000 units, you have defect G7.

class Layouter:
    """Assigns non-colliding positions in the hand-wired style."""

    def __init__(self):
        self.occupied = set()          # (x, y) already used - HW4
        self.col_y = {}                # next free y per column
        self.stage = 0

    def _claim(self, x, y):
        while (x, y) in self.occupied:  # never return a taken slot
            y += ROW_STEP_Y
        self.occupied.add((x, y))
        return int(x), int(y)

    def source(self):
        """HW1: sources stack in the single far-left column."""
        y = self.col_y.get(SOURCE_X, SOURCE_Y0)
        x, y = self._claim(SOURCE_X, y)
        self.col_y[SOURCE_X] = y + ROW_STEP_Y
        return unreal.IntPoint(x, y)

    def stage_pos(self, col):
        """HW2/HW3: successive stages advance rightward from SOURCE_X."""
        x = SOURCE_X + (col + 1) * COLUMN_STEP_X
        y = self.col_y.get(x, 0)
        x, y = self._claim(x, y)
        self.col_y[x] = y + ROW_STEP_Y
        return unreal.IntPoint(x, y)
```

Skeleton of a compliant build:

```python
def build(editor, lay):
    entry = editor.find_graph_entry_pin()
    entry_pos = entry.get_owning_node().get_node_pos()

    made = []                                   # HW5: track what we create
    node = editor.add_call_function_node(
        "/Script/Engine.KismetSystemLibrary.PrintString")
    node.set_node_pos(lay.stage_pos(0)); made.append(node)

    # connect, and CHECK - never assume
    if not BPL.can_create_connection(entry, node.find_execute_pin()):
        raise RuntimeError("not connectable")
    if not BPL.try_create_connection(entry, node.find_execute_pin()):
        raise RuntimeError("connection failed")   # G3 defended here

    # re-position AFTER creation, because factories ignore location
    node.set_node_pos(unreal.IntPoint(entry_pos.x + COLUMN_STEP_X, entry_pos.y))

    # HW5: nothing abandoned
    assert all(n is not None for n in made)
    return made
```

Two rules make this work in practice:

- **Position after connect, not before.** A failed connection may abort the build; laying out a node
  you then discard wastes the slot and leaves a hole in the column.
- **Track created nodes in a list.** That is what makes "did I abandon anything?" an answerable
  question at the end instead of a guess. See `references/12-templates.md` T3 for the full build,
  and `references/12-templates.md` T4 for verified connection helpers.
