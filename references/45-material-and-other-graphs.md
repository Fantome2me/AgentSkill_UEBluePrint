# Material, UMG and Niagara graphs

Route B / C / D specifics. Each is a different subsystem with different libraries.
See `references/00-router.md` for how to decide you are in one of them.

---

## ROUTE B - Material graphs

### The API surface

```python
MEL = unreal.MaterialEditingLibrary

MEL.get_material_expressions(material)                     # -> all expression nodes
MEL.get_inputs_for_material_expression(material, expr)     # -> upstream source expressions
MEL.get_material_property_input_node(material, property)   # -> node feeding one MP_* output
MEL.create_material_expression(material, expr_class, x, y) # -> new expression node
MEL.connect_material_expressions(from_expr, from_out, to_expr, to_in)
MEL.connect_material_property(from_expr, from_out, property)
MEL.recompile_material(material)
```

Positions live on the expression itself:

```python
expr.get_editor_property("material_expression_editor_x")
expr.get_editor_property("material_expression_editor_y")
```

**These two properties are the only reason a layout audit is possible on materials.** Read them
before and after any generated edit; if a generated graph has every node at `(0, 0)` or at one
identical coordinate, that is defect G1 from `references/40-hand-wired-style.md`.

### Which root pins exist

Enumerate rather than hard-code - the enum is your source of truth:

```python
for name in [p for p in dir(unreal.MaterialProperty) if p.startswith("MP_")]:
    member = getattr(unreal.MaterialProperty, name, None)
    if member is None:
        continue
    owner = MEL.get_material_property_input_node(material, member)
    if owner is not None:
        print(name, "->", owner.get_name())
```

### Producing a dump the audit tool understands

`Saved/ObjectGraph.json` in this project is the canonical shape, produced by
`Scripts/ProbeMaterialGraph.py`:

```json
{ "path": "/Game/.../Material",
  "root": { "MP_BASE_COLOR": "MaterialExpressionAdd_3", "...": "..." },
  "expressions": [ { "name": "...", "class": "MaterialExpressionMultiply",
                     "x": -1700, "y": -1750, "inputs": { "A": "...", "B": "..." } } ] }
```

Feed it straight to `scripts/analyze_graph_layout.py`.

### Trap: input pin order is not reliably readable

`get_inputs_for_material_expression` returns the connected **source** expressions, and the ordering
is only usable when the count matches the node's pin count. Where it does not, a dumper has to fall
back to a positional convention. This is exactly what the project's probe script does:

```python
PINS = {
    "MaterialExpressionMultiply": ["A", "B"],
    "MaterialExpressionLinearInterpolate": ["A", "B", "Alpha"],
    "MaterialExpressionClamp": ["Input", "Min", "Max"],
    "MaterialExpressionCustom": None,      # use the node's own declared inputs
    # unknown classes -> [] and the dump records "<order unknown: N>" instead of lying
}
```

**Do not treat a guessed pin name as fact.** If the count does not match, the dump deliberately
records an unknown-order bucket. The audit tool treats those as opaque (it will not claim a pin
name it does not know) - and connectivity is still correct because it only needs the edge, not the
pin label.

### Trap: the Python `MaterialProperty` enum is incomplete

17 of 36 entries are exposed. `MP_PixelDepthOffset` (index 28) is missing:

```text
TypeError: PythonizeEnumEntry: Cannot pythonize '28' (int64) as 'MaterialProperty'
TypeError: Cannot pythonize 'material_property' (ByteProperty)
MaterialProperty: Cannot create instances of enum types
```

Passing the raw int or the string does not help. **That one connection requires C++** - see
`references/21-adjacent-systems.md` §6. Every other property is reachable.

### Trap: a translucent material silently discards Pixel Depth Offset

```hlsl
// Engine/Shaders/Private/MaterialTemplate.ush
#define OUTPUT_PIXEL_DEPTH_OFFSET (WANT_PIXEL_DEPTH_OFFSET && !IS_VIRTUAL_TEXTURE_MATERIAL && !IS_NANITE_PASS \
    && ((MATERIALBLENDING_SOLID || MATERIALBLENDING_MASKED) || (TRANSLUCENT_WRITING_VELOCITY)))
```

For `BLEND_TRANSLUCENT`, PDO is compiled out entirely unless the material writes velocity. The
symptom is nasty: you wire the chain, everything verifies, and the render never changes - including
a gain 0-vs-1 A/B test. Fix:

```python
mat.set_editor_property("bOutputTranslucentVelocity", True)
```

It is a plain `UPROPERTY`, readable and writable from Python.

### Rule: material pins are TYPED, and the type is enforced at compile time

Every pin in a material graph has a component count: 1 (scalar), 2, 3 or 4. Wiring is not
duck-typed - the compiler refuses a connection it cannot cast, and the failure is a
material compile error, not a Python exception. That asymmetry is what makes this the
most expensive class of material bug: **the API call succeeds, the asset saves, and the
material is red.**

Two directions, and only one of them is implicit:

| Cast | Allowed? | What happens |
|---|---|---|
| scalar -> 2/3/4 | **yes, implicit** | broadcast into every channel |
| 2 -> 3/4, 3 -> 4 | **yes, implicit** | the missing channels are filled with 0 |
| 4 -> 3 -> 2 -> 1 | **NO** | `Cannot cast from larger float type to smaller float type` |

So the rule is one line long:

> **Downcast needs an explicit mask. Upcast does not.**

Before wiring, know the width of both ends. The widths that come up constantly:

```text
MaterialExpressionTime                1
MaterialExpressionFresnel             1
MaterialExpressionScalarParameter     1
MaterialExpressionTextureCoordinate   2
MaterialExpressionPanner             2 in, 2 out
TextureSample / TextureSampleParameter2D   UV 2, RGB 3, RGBA 4, R 1
MaterialExpressionVertexColor         4
MaterialExpressionCameraVectorWS      3
MaterialExpressionWorldPosition       3
MaterialExpressionComponentMask       N out, where N = number of enabled channels
```

A float4 that is about to feed a float3 pin needs `ComponentMask(RGB)` in between. Wiring
it straight and hoping the tool reports a useful error is how a graph ends up with a
red node that someone later "fixes" by deleting the branch.

### Rule: ComponentMask is a cast, not a comment

`MaterialExpressionComponentMask` is the only sanctioned way to change a pin's width or
channel order, and it has three behaviours that catch people:

1. **The output width is the number of enabled channels, not 1.** A mask with R and G
   enabled outputs a float2. Feed that into a scalar input and it is the same downcast
   error as above. If a mask is there to "take R", exactly one flag may be on.
2. **The default is R and G, not R.** A freshly created mask silently produces a float2
   until `r/g/b/a` are set explicitly. Always set all four flags at creation.
   ```python
   m = MEL.create_material_expression(mat, unreal.MaterialExpressionComponentMask, x, y)
   m.set_editor_property("r", True)
   m.set_editor_property("g", False)   # without this the default leaves G on
   m.set_editor_property("b", False)
   m.set_editor_property("a", False)
   ```
3. **It cannot reorder.** A mask preserves the original channel order, so BGR is not
   expressible with one mask. Reordering needs two masks and an `AppendVector`, and each
   half must be a single channel or the append produces the wrong width.

Corollary: never use a mask to "clean up" a graph visually. Every mask is a real cast and
changes the type flowing downstream.

### Rule: a material edit is not finished until it compiles clean

`recompile_material` returns a bool, and a `False` there is a hard failure - the material
did not compile and the render will fall back. Treat it exactly like a failed build:

```python
ok = MEL.recompile_material(mat)
if not ok:
    raise RuntimeError("material did not compile - do not save, do not report success")
```

Then confirm from a place that cannot lie. The editor log records the real reason:

```powershell
Select-String -Path "<Project>/Saved/Logs/*.log" -Pattern "Error.*<MaterialName>|Cannot cast|Missing input"
```

Why this is a rule rather than advice: in this engine a shader compile failure surfaces
as `Shader compilation failures are Fatal` at PSO creation, long after the edit looked
successful, and the trace points at the renderer rather than at the graph that caused it.

The order is fixed and not negotiable:

```text
wire  ->  recompile  ->  assert the returned bool  ->  scan the log  ->  save  ->  prove
```

Saving before the compile is asserted is how a broken graph reaches disk with a green
checkmark next to it.

### Verified example graph

`/Game/PluginsMakingAssets/Shaders/ParentsShaders/ObjectShader` - 71 nodes, 7 root pins
(`MP_BASE_COLOR`, `MP_METALLIC`, `MP_SPECULAR`, `MP_ROUGHNESS`, `MP_NORMAL`, `MP_EMISSIVE_COLOR`,
`MP_AMBIENT_OCCLUSION`), dumped to `Saved/ObjectGraph.json`. Use it as the reference for both the
dump format and the layout style.

---

## ROUTE C - UMG / Widget Blueprints

`BlueprintGraphEditor` **cannot** author a widget tree. Different subsystem. Keep these separate:

| Task | Correct API |
|---|---|
| add / remove / re-parent a widget in the tree | `unreal.WidgetBlueprintLibrary` + UMG editor subsystem |
| change a widget property | `widget.set_editor_property(...)` |
| put a **node** in a Widget Blueprint's event graph | this *is* ROUTE A - the graph part is an ordinary Blueprint graph |

### Traps

| Trap | Symptom | Fix |
|---|---|---|
| `AddWidgetToWidgetBlueprint` with a **null parent** | the new widget becomes the root and the whole existing tree is **wiped**; the Blueprint goes `BS_ERROR` | verify the parent is non-null, verify the returned name (a `TRASH_` prefix means failure), and assert the widget count only **grows** |
| pre-detaching before `RemoveWidget` | `RemoveWidget` returns `false` (it calls `Parent->RemoveChild` internally) | do not pre-detach; detaching does not delete the UObject either |
| a widget referenced from a graph but not marked a variable | no `Get<Widget>` node exists | set `bIsVariable` on the widget - a property edit, no tree change |
| `set_widget_property` (ImportText) silently not landing | value unchanged | also set it directly: `w.set_editor_property("width_override", 150.0)` |
| `UBorder::SynchronizeProperties` applies `BrushColor` as a **tint** | a dark slot colour multiplies a texture to near-black, so "nothing displays" | set `BrushColor` to white while showing a texture, restore it when empty |
| `SetBrushFromTexture` does not change `DrawAs` | the texture resource is set but nothing renders (`RoundedBox` draws a box, ignoring the resource) | also set the brush `DrawAs = Image` |

**Invariant to assert after every structural widget change:** the widget count. That single assert
catches the tree-wipe trap, which is otherwise only visible as a destroyed Blueprint.

---

## ROUTE D - Niagara via ToolsetRegistry

Plain Python bindings hide most of Niagara. Reach it through the toolset registry:

```python
import json, unreal
TR = unreal.ToolsetRegistry

res = TR.execute_tool("NiagaraToolsets.NiagaraToolset_System", "GetSystemSummary", json.dumps(payload))
err = res.get_editor_property("error")     # "" on success
val = res.get_editor_property("value")     # a JSON STRING - json.loads it
```

Verified tools: `GetSystemSummary`, `GetEmitterTopology`, `GetEmitterInputValues`,
`GetRendererData`, `SetRendererData`, `GetEmitterData`, `SetEmitterData`, `AddSetParametersModule`,
`RemoveModule`, `GetStackInputData`, `SetStackInputData`.

### Traps

| Trap | Symptom | Fix |
|---|---|---|
| package path in a payload | `not a valid object path for property 'System'` | use a **full object path**: `/Game/A/B.B` |
| expecting a `SetVariables` module to replace a previous one | each call **appends**; the stack accumulates dead modules | delete the old module with `RemoveModule` first, or accept last-wins |
| scaling the component of a **world-space** emitter | particle size never changes | check `bLocalSpace` via `GetEmitterData`; world-space emitters ignore component scale - change the particle attribute instead |

Niagara cannot be verified headlessly: in a commandlet world components never activate
(`is_active` stays `False` after `activate(True)`), `get_system_age()` is unavailable and particles
are `None`. Any claim about how it *looks* is a hypothesis - provide a measurable A/B switch instead
(see `references/20-rules.md` R8).

Also available for materials: `editor_toolset.toolsets.material.MaterialTools`
(`add_expression`, `connect_expressions`, `connect_to_output`, `recompile`, `get_property_input`).
