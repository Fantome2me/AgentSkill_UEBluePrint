# Domain router - decide BEFORE you act

The single most expensive mistake in UE automation is **reaching for the wrong editor's API**.
There are four distinct graph surfaces in this engine, each with its own subsystem, its own
libraries, and its own failure modes. They look similar in conversation ("add a node and wire it")
and share almost nothing in code.

**Route first. Then act.** Thirty seconds of routing saves an hour of writing calls against the
wrong subsystem.

---

## Step 1 - Classify the target (topology detection)

### 1a. Ask these four questions in order

| # | Question | If YES | If NO |
|---|---|---|---|
| 1 | Is the target an asset path ending in `.uasset` that is a **Material / MaterialFunction / MaterialInstance**? | **MATERIAL** route | next |
| 2 | Is it a **NiagaraSystem / NiagaraEmitter**, or does the task mention particles, emitters, renderers, modules? | **NIAGARA** route | next |
| 3 | Is it a **WidgetBlueprint**, or does the task mention UMG, panels, buttons, layout, slots? | **UMG** route | next |
| 4 | Is it a **Blueprint** (actor/component/anim), or does the task mention events, functions, variables, casting, gameplay logic? | **BLUEPRINT** route | next |
| 5 | Is it a **Level / map / actor in a level / WorldSettings**, or project config? | **LEVEL+CONFIG** route | escalate: ask which asset exactly |

### 1b. Classify by file evidence when the name is ambiguous

Do not guess from the asset name. Inspect it. Class names are plain ASCII inside the `.uasset`:

```powershell
$b = [IO.File]::ReadAllBytes($asset)
$head = [Text.Encoding]::ASCII.GetString($b, 0, [Math]::Min(4000, $b.Length))
# which of these appear tells you the asset kind
$head -match 'MaterialExpression'   # -> material graph
$head -match 'Niagara'              # -> niagara
$head -match 'WidgetTree'           # -> UMG
$head -match 'K2Node|EdGraph'       # -> blueprint graph
$head -match 'StaticMesh|SkeletalMesh'
```

For live, authoritative classification, load it and ask the engine instead of the bytes:

```python
obj = unreal.EditorAssetLibrary.load_asset(path)
print(type(obj).__name__)     # Material / MaterialInstanceConstant / NiagaraSystem / WidgetBlueprint / Blueprint
```

**Never infer the route from the asset's folder path.** In real projects MaterialFunctions live in
`Shaders/`, Blueprints live in `Blueprints/`, and both are routinely misfiled.

### 1c. Classify by the *operation*, not just the asset

The same asset can need two different routes:

| Operation on a Material | Route |
|---|---|
| add/modify an expression node in the graph | MATERIAL-graph |
| change a scalar/vector/texture parameter **value** on an instance | MATERIAL-instance (different API: `MaterialInstanceConstant`, `set_editor_property`) |
| swap which master an instance derives from | MATERIAL-instance |
| change shading model / blend mode flags | MATERIAL-flags (`set_editor_property` on the material, no graph work) |

Routing by asset alone is not enough. **Route by (asset kind x operation).**

---

## Step 2 - Dispatch table

Each route names its own libraries, its own reference files, and its own dominant trap. Load only
the rows you need; do not read all four.

### ROUTE A - BLUEPRINT graph

| | |
|---|---|
| **Libraries** | `unreal.BlueprintGraphEditor`, `unreal.BlueprintEditorLibrary`, `unreal.BlueprintGraphPinLibrary` |
| **Entry** | `BlueprintGraphEditor.get_graph_editor_by_name(bp, "EventGraph")` or `create_and_edit_function_graph` |
| **References** | `references/10-api.md`, `references/11-parameter-formats.md`, `references/12-templates.md`, `references/40-hand-wired-style.md` |
| **Dominant trap** | pin/function names are not what you would guess; probe the palette first |
| **Verification** | `list_nodes_with_errors()` empty, then `compile_blueprint`, then `bp.status` contains `BS_UP_TO_DATE` |

### ROUTE B - MATERIAL graph

| | |
|---|---|
| **Libraries** | `unreal.MaterialEditingLibrary`; optionally `editor_toolset.toolsets.material.MaterialTools` via `ToolsetRegistry` |
| **Entry** | `MaterialEditingLibrary.get_material_expressions(material)` |
| **References** | `references/45-material-and-other-graphs.md` |
| **Dominant trap** | the Python `unreal.MaterialProperty` enum is **incomplete** - it declares 17 of 36 entries and `MP_PixelDepthOffset` (index 28) is missing, so `get_material_property_input_node(mat, MP_PixelDepthOffset)` raises `Cannot pythonize '28'`. That one connection needs C++. |
| **Verification** | expression count delta, then recompile, then read the connection back |

### ROUTE C - UMG / Widget Blueprint

| | |
|---|---|
| **Libraries** | `unreal.WidgetBlueprintLibrary` + the UMG editor subsystem |
| **References** | `references/45-material-and-other-graphs.md` (§ UMG) |
| **Dominant trap** | adding a widget with a **null parent** replaces the root and wipes the tree, leaving the Blueprint `BS_ERROR`. Always verify the parent is non-null and assert the widget count only grows. |
| **Verification** | widget-count invariant after every structural change |

**This is not the Blueprint route.** `BlueprintGraphEditor` cannot author a widget tree.

### ROUTE D - Niagara

| | |
|---|---|
| **Libraries** | `unreal.ToolsetRegistry` -> `NiagaraToolsets.NiagaraToolset_System` |
| **Entry** | `ToolsetRegistry.execute_tool("<toolset>", "<tool>", json.dumps(payload))` |
| **References** | `references/21-adjacent-systems.md` §1, `references/45-material-and-other-graphs.md` |
| **Dominant trap** | object references inside payloads need a **full object path** (`/Game/A/B.B`), not a package path |
| **Verification** | `result.get_editor_property("error")` must be `""`; `value` is a JSON string, parse it |

### ROUTE E - Level, actors, config

| | |
|---|---|
| **Libraries** | `unreal.EditorActorSubsystem`, `unreal.EditorLoadingAndSavingUtils`, plain-text `Config/DefaultEngine.ini` |
| **References** | `references/21-adjacent-systems.md` §2-3 |
| **Dominant trap** | load **exactly one** map per run - loading a second causes UE to revert the non-first-loaded level on exit, so your edit silently disappears even though `save_map` wrote a file |
| **Verification** | reload the map in a fresh process and read the value back |

---

## Step 3 - Cross-cutting rules (all routes)

These are not route-specific. They apply to every one of A-E.

1. **Editor closed for any write.** A running `UnrealEditor` holds the package open; its next save
   overwrites yours.
2. **Back up the `.uasset` / `.umap` / `.ini`** first, recording size + SHA256. UE also drops a
   recoverable `<Asset><GUID>.tmp` in `Saved/` on each overwrite.
3. **A `True` return is not a successful edit.** Prove it: disk hash changed + mtime changed +
   fresh-process readback.
4. **Probe, do not guess** - node ids, function paths, pin names, palette categories, dispatcher
   names, component event names. All of them.
5. **Lay out what you create.** A graph that compiles but is unreadable is a defective deliverable.
   See `references/40-hand-wired-style.md`.
6. **Say what you could not verify.** Rendering is never verifiable headlessly.

---

## Anti-patterns that indicate you routed wrong

| Symptom | You probably routed wrong because |
|---|---|
| `AttributeError: module 'unreal' has no attribute 'BlueprintTools'` | You copied a **project-private** helper from old material. It is not engine API. Use ROUTE A. |
| `find_input_pin` returns something invalid on a Material | Material graphs have no K2 pins. You are on ROUTE A against a material. |
| `get_material_expressions` returns empty on a Blueprint | You are on ROUTE B against a Blueprint. |
| `Cannot pythonize '28'` | ROUTE B, and you hit the incomplete enum. That specific connection needs C++. |
| Widget tree destroyed, `BS_ERROR` | ROUTE C, and you passed a null parent. |
| `not a valid object path for property 'System'` | ROUTE D, and you used a package path instead of a full object path. |
| Edit worked in-session but is gone after restart | ROUTE E, and you loaded two maps in one run - or you never saved. |

## Routing record (state this in your report)

```text
target asset   : /Game/.../X            (kind verified by: <class name / byte evidence>)
operation      : <what the user wants changed>
route          : A | B | C | D | E
libraries used : <the actual modules called>
references read: <the files you opened>
traps applied  : <which known traps you defended against>
```

Filling this in takes four lines and makes a wrong route obvious immediately instead of three
debugging cycles later.
