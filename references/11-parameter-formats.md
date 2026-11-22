# Parameter formats

Wrong parameter *format* — not wrong logic — is the dominant failure when scripting Blueprints.
Every item below is either verified in the engine source, verified in Epic's Unreal Python 5.8
docs, or observed in a real session (marked accordingly).

---

## 1. Asset paths: two forms, not interchangeable

| Use | Form | Example |
|---|---|---|
| Asset APIs (`load_asset`, `EditorAssetLibrary`, `save_asset`) | package path, no suffix | `/Game/Final/Map/TestMap_Model/Djiang_Central_Ring1_UE_mesh_1098` |
| Object references inside toolset JSON payloads | **full object path** | `/Game/Dir/MySystem.MySystem` |
| Config files (`DefaultEngine.ini`) | `Package.Asset` | `/Game/Dir/MyMap.MyMap` |

- `load_asset` returns **`None`** on failure — it does not raise. Check for `None` and for the
  expected class before using the result.
- Inside a toolset payload, a package path fails with
  `not a valid object path for property 'X'`. Append the `.AssetName` suffix.
- Non-ASCII names (`陈千语`, `演示物体`, `发`) are legal in UE paths. Never transliterate them to make a
  script "safer" — the transliterated path does not exist.

## 2. Graph handles

A graph is an **object**, never a string.

```python
graph  = unreal.BlueprintEditorLibrary.find_graph(bp, "EventGraph")   # -> EdGraph | None
graph  = unreal.BlueprintEditorLibrary.find_event_graph(bp)           # only the graph named "EventGraph"
editor = unreal.BlueprintGraphEditor.get_graph_editor(graph)
editor = unreal.BlueprintGraphEditor.get_graph_editor_by_name(bp, "EventGraph")  # skips the find
```

`find_graph` / `find_event_graph` return `None` when absent — check before use. Do not cache a graph
handle across a `compile_blueprint` call; re-resolve it.

## 3. `FEdGraphPinType` — the type descriptor

Never pass `"float"`, `"int"`, `"bool"` or `"Vector"` where an `EdGraphPinType` is expected. Build it:

```python
L = unreal.BlueprintEditorLibrary

t_float = L.get_basic_type_by_name("float")
t_bool  = L.get_basic_type_by_name("bool")
t_int   = L.get_basic_type_by_name("int")

t_vec   = L.get_struct_type(unreal.Vector.static_struct())
t_actor = L.get_object_reference_type(unreal.Actor.static_class())
t_class = L.get_class_reference_type(unreal.Actor.static_class())

t_float_array = L.get_array_type(t_float)
t_float_set   = L.get_set_type(t_float)
t_name_to_vec = L.get_map_type(L.get_basic_type_by_name("name"), t_vec)
```

**`get_basic_type_by_name` silently returns an `int` type for an unrecognised name.** It does not
raise. When the type matters, confirm the descriptor:

```python
print(L.pin_type_to_json_schema(t_float, None))
```

Containers nest by wrapping: `get_array_type(get_struct_type(...))`.

Both `add_member_variable` and `add_local_variable` default the type to **boolean** when `pin_type` is
omitted. Omitting it is a silent way to get the wrong type.

## 4. Values are strings

`set_pin_value(value) -> bool`, `add_member_variable(..., value: str)`, and
`set_local_variable_default_value(name, value: str)` all take **strings**, parsed against the type.

| Type | String form |
|---|---|
| `bool` | `"true"` / `"false"` (lowercase) |
| `int` / `int64` | `"42"` |
| `float` / `double` | `"3.5"` — dot separator regardless of OS locale |
| `string` / `name` / `text` | raw text, no added quotes |
| object reference | object path, e.g. `/Game/A/B.B` |
| class reference | class path |
| struct (`Vector`) | UE literal, e.g. `"(X=1.0,Y=2.0,Z=3.0)"` — *unverified on this build* |
| enum | enumerator name — *unverified on this build* |

Rows marked *unverified* follow the engine's `ImportText` convention but were not exercised against
5.8. Confirm on a scratch Blueprint before relying on them.

**Always check the bool.** A `False` means the literal was rejected and the pin keeps its previous
value. Silent no-op is the normal failure mode here.

## 5. Function paths for `add_call_function_node`

Format: `/Script/<Module>.<Class>:<Function>`.

Verified working on UE 5.8:

| Purpose | Path |
|---|---|
| Print string | `/Script/Engine.KismetSystemLibrary.PrintString` |
| IsValid | `/Script/Engine.KismetSystemLibrary:IsValid` |
| Actor world offset | `/Script/Engine.Actor:K2_AddActorWorldOffset` |
| Actor location | `/Script/Engine.Actor:K2_GetActorLocation` |
| Player camera manager | `/Script/Engine.GameplayStatics:GetPlayerCameraManager` |
| Camera location / rotation | `/Script/Engine.PlayerCameraManager:GetCameraLocation` · `:GetCameraRotation` |
| Vector math | `/Script/Engine.KismetMathLibrary:Subtract_VectorVector` · `:Add_VectorVector` · `:Multiply_VectorFloat` · `:Normal` · `:MakeVector` |
| Rotator → axis | `/Script/Engine.KismetMathLibrary:GetUpVector` · `:GetRightVector` |

Note the two spellings in the wild: `Module.Class.Function` (dots) and `Module.Class:Function`
(colon). Both appear in Epic's own documentation examples. If one form returns null, try the other
before concluding the function does not exist.

**Discovering an unknown path.** Enumerate the palette once:

```python
avail = editor.list_available_nodes([])     # ~41k strings, shaped "<localised category>|<NodeName>"
```

Search by the **English node-name suffix**, never by the category prefix — categories are localised.

## 6. Palette names for `create_node_from_name`

Documented shapes:

```
'Development|PrintString'
'Utilities|Operators|Add'
'Utilities|FlowControl|Branch'
'Math|Vector|VectorLength'
```

Two traps, both observed:

- **`location` must be a 2-D type.** `unreal.Vector2D(x, y)` works.
  `unreal.IntPoint(x, y)` raises `Failed to convert parameter 'location'`. (Node *position* setting,
  by contrast, uses `IntPoint` — see §8. The two are different parameters with different types.)
- **Never retype the category prefix.** It is localised, and a retyped non-ASCII prefix has produced a
  string that matched nothing and returned `None` **without raising**. Take the exact string out of
  `list_available_nodes()`:

```python
avail   = editor.list_available_nodes([])
node_id = next(n for n in avail if n.endswith("CreateDynamicMaterialInstance"))
editor.create_node_from_name(node_id, unreal.Vector2D(-600, 300), [])
```

`context_pins` is an `Array[BlueprintGraphPin]` used to disambiguate actions that depend on what they
are wired to. Pass `[]` when you have no context. When several actions share one `Category|Name`,
pass `declaring_class` to pick the right one.

## 7. Pin lookup

Pins are found **by name**, and by-name is preferred over by-index — indices shift whenever a node's
signature changes between engine versions.

```python
node.find_input_pin("InString")
node.find_output_pin("ReturnValue")
node.find_execute_pin()
node.list_all_pins()
```

- Pin names are the **internal** names and frequently differ from the displayed label. Observed real
  cases: `InWidthOverride` not `InValue`; `NewScale` not `Scale`; `InText` not `Text`.
- For a branch node, the exec output is the **`then`** pin; use `find_then_pin()`, and
  `find_condition_pin()` for the condition input.
- A failed `find_*_pin` returns an **invalid handle**, not an exception. Check
  `unreal.BlueprintGraphPinLibrary.is_valid(pin)`.
- When unsure, dump the pins first and read the real names:

```python
for p in node.list_all_pins():
    print(p.get_pin_name(), p.get_pin_direction(), p.get_pin_value())
```

**Event nodes resolve by member name, not display name:** `ReceiveBeginPlay`, not `"Begin Play"`.

## 8. Node positions

Integer graph coordinates:

```python
node.set_node_pos(unreal.IntPoint(x, y))
pos = node.get_node_pos()          # -> IntPoint
```

Lay nodes on a readable grid (+300 x per column, +150 y per row) rather than stacking them at one
point. Overlapping nodes compile fine and are unusable for the human reviewing the graph. The node
factories do not take a position, so you must set it yourself.

## 9. Return values you must not ignore

| Call | Failure signal |
|---|---|
| `load_asset` | returns `None` |
| `find_graph` / `find_event_graph` | returns `None` |
| `create_node_from_name` | returns `None` (e.g. a localised-prefix mismatch) |
| `add_dispatcher_event_node` | returns null if the graph is not an event graph, no skeleton class, or no such dispatcher |
| `add_member_variable` / `add_local_variable` / `remove_*_variable` | returns `False` |
| `add_node_pin` / `remove_node_pin` | returns `False` — node kind does not support dynamic pins |
| `retarget_node_class` | returns `False` — unsupported node kind or class mismatch |
| `is_valid(pin)` | `False` — do not use the handle |
| `can_create_connection` | `False` — do not proceed to connect |
| `try_create_connection` | `False` — report, do not assume success |
| `set_pin_value` / `set_local_variable_default_value` | `False` — value rejected, pin unchanged |
| `compile_blueprint` | `True` = compilation **ran**; still scan the log |
| `remove_unused_variables` | returns the `int32` count actually removed |

Silence is not success. In this API a rejection is normally a `None`/`False` return, not an exception.

## 10. Two 2-D types, do not mix them up

This trips people constantly because both are "a coordinate pair":

| Parameter | Correct type |
|---|---|
| `create_node_from_name(location=...)`, `add_comment_node(location=...)` | **`unreal.Vector2D`** |
| `node.set_node_pos(...)`, `node.get_node_pos()` | **`unreal.IntPoint`** |

Comment node size is also `Vector2D`, defaulting to `(400, 200)`.
