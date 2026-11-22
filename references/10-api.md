# API reference — portable Blueprint surface (UE 5.8)

Three engine-native libraries. All three ship with the editor; none require project C++.

Authority for the `BlueprintGraphEditor` surface: Epic's official *Unreal Python 5.8
(Experimental)* documentation for `unreal.BlueprintGraphEditor`, cross-checked against the engine
header `Engine/Source/Editor/BlueprintEditorLibrary/Private/BlueprintEditorLibrary/BlueprintGraphEditor.h`.

> **Caveat on the header.** `BlueprintGraphEditor.h` lives in a `Private/` directory. That does not
> prevent the Python binding — Epic documents these methods as public Python API — but it does mean
> the authoritative signatures for Python are the docs, not the header. Where the two disagree,
> trust a live `help(unreal.BlueprintGraphEditor)` probe in the editor.

---

## 1. Getting an editor handle

Obtain a handle **first**; every node factory is an instance method on it.

```python
import unreal
BGE = unreal.BlueprintGraphEditor

bp = unreal.EditorAssetLibrary.load_asset("/Game/Path/BP_Thing")

# a) create a NEW function graph and edit it
editor = BGE.create_and_edit_function_graph(bp, "MyFunction")

# b) edit an EXISTING graph, by object
graph  = unreal.BlueprintEditorLibrary.find_graph(bp, "EventGraph")
editor = BGE.get_graph_editor(graph)

# c) edit an EXISTING graph, by name (no separate find needed)
editor = BGE.get_graph_editor_by_name(bp, "EventGraph")
```

```python
editor.get_graph() -> EdGraph
editor.is_valid() -> bool        # C++ IsValid(); verify the handle before use
```

`create_and_edit_function_graph` **creates**. If a graph of that name already exists you get a
suffixed duplicate (`MyFunction_0`) and your edits land in the copy — the original is untouched.
See trap 2 in `20-rules.md`.

---

## 2. Node factories

All return the concrete node type, so you can chain pin work directly.

```python
editor.add_call_function_node(function_path: str) -> K2Node_CallFunction
editor.add_branch_node() -> K2Node_IfThenElse
editor.add_macro_node(macro_path: str) -> K2Node_MacroInstance
editor.add_return_node() -> K2Node_FunctionResult
editor.add_custom_event_node(event_name: str) -> K2Node_CustomEvent
editor.add_dispatcher_event_node(dispatcher_name: Name, declaring_class=None) -> K2Node_Event
editor.add_component_bound_event_node(component: ActorComponent, event_name: Name) -> K2Node_ComponentBoundEvent
editor.add_get_member_variable_node(member_name: Name, class_path: str = "") -> K2Node_VariableGet
editor.add_set_member_variable_node(member_name: Name, class_path: str = "") -> K2Node_VariableSet
editor.add_get_local_variable_node(local_name: Name) -> K2Node_VariableGet
editor.add_set_local_variable_node(local_name: Name) -> K2Node_VariableSet
editor.create_node_from_name(node_with_category: str, location: Vector2D,
                             context_pins: Array[BlueprintGraphPin],
                             declaring_class=None) -> EdGraphNode
```

Notes that matter:

- **`add_call_function_node` takes a function path**, format
  `/Script/<Module>.<Class>:<Function>`. Verified working examples are in
  `11-parameter-formats.md` §5.
- **`create_node_from_name` takes a palette string** shaped `"Category|Name"`, e.g.
  `"Development|PrintString"`, `"Utilities|FlowControl|Branch"`, `"Math|Vector|VectorLength"`.
  The category prefix is **localised**, so never retype it — take the exact string out of
  `list_available_nodes()`. Retyping a non-ASCII prefix has produced a string that matched nothing
  and returned `None` *silently*.
- **`location` is `Vector2D`**, not `IntPoint`. `IntPoint` raises
  `Failed to convert parameter 'location'`.
- `add_dispatcher_event_node` requires the Blueprint to have been compiled at least once (a valid
  skeleton class), otherwise it returns null.
- `add_component_bound_event_node` must be called on an **event graph**; it returns the existing
  node if one already exists.
- `add_call_function_node` / `add_macro_node` / `create_node_from_name` take **no location**: the node
  lands wherever the editor decides. Set the position yourself afterwards (see §6).

## 3. Node listing and inspection

```python
editor.list_all_nodes() -> Array[K2Node]
editor.list_nodes_of_class(class) -> Array[K2Node]
editor.list_nodes_with_errors() -> Array[K2Node]      # EMessageSeverity::Error
editor.list_nodes_with_warnings() -> Array[K2Node]    # EMessageSeverity::Warning
editor.list_nodes_with_notes() -> Array[K2Node]       # EMessageSeverity::Info
editor.list_available_nodes(context_pins) -> Array[str]
editor.list_comment_nodes() -> Array[EdGraphNode_Comment]
editor.list_component_events(component) -> Array[Name]
editor.find_event_node(event_name: Name) -> K2Node_Event
editor.find_graph_entry_pin() -> BlueprintGraphPin
```

`find_event_node` matches the **member name**, not the display name: `ReceiveBeginPlay`, not
`"Begin Play"`.

`list_component_events(component)` is the correct way to discover bindable delegate names before
calling `add_component_bound_event_node` — do not guess them.

Node-level inspection (position, title, size, pins) is exposed on the node objects themselves and via
`unreal.BlueprintEditorLibrary`; see §7 and `11-parameter-formats.md`.

## 4. Pin operations

Pins are handles. On the Python side the useful methods hang off the **pin object**:

```python
pin.find_input_pin(name) -> BlueprintGraphPin        # on a node
pin.find_input_pin(name)
node.find_execute_pin() -> BlueprintGraphPin
node.find_output_pin(name) -> BlueprintGraphPin
node.list_all_pins() -> Array[BlueprintGraphPin]

pin.get_pin_name() -> Name
pin.get_pin_direction() -> EdGraphPinDirection
pin.get_pin_value() -> str
pin.get_owning_node() -> K2Node
pin.list_connected_pins() -> Array[BlueprintGraphPin]

pin.set_pin_value(value: str) -> bool
pin.try_create_connection(other: BlueprintGraphPin) -> bool
pin.can_create_connection(other: BlueprintGraphPin) -> bool
pin.break_pin_links() -> bool
pin.break_single_pin_link(other: BlueprintGraphPin) -> bool
```

The same operations are also reachable as statics on `unreal.BlueprintGraphPinLibrary`, taking the
pin as the first argument:

```python
BPL = unreal.BlueprintGraphPinLibrary
BPL.is_valid(pin)
BPL.try_create_connection(a, b)
BPL.break_pin_links(pin)
BPL.set_pin_value(pin, "3.5")
BPL.get_pin_name(pin)
BPL.list_connected_pins(pin)
BPL.is_same_native_pin(a, b)
```

Discipline:

- **`can_create_connection` before `try_create_connection`.** Do not use the connect call as a probe.
- **Re-resolve pins after a compile.** Compilation rebuilds nodes and invalidates native pin pointers.
- **Check the bool.** `set_pin_value` returns `False` when the literal was rejected; the pin silently
  keeps its old value.
- `break_pin_links` severs *every* link on the pin — easy way to orphan a chain you did not mean to
  touch. Prefer `break_single_pin_link`.

## 5. Variables

Member variables (persist on the Blueprint):

```python
editor.add_member_variable(member_name: Name, pin_type=[], value: str = "") -> bool
editor.remove_member_variable(member_name: Name) -> bool
```

Local variables (scoped to the current function graph):

```python
editor.list_local_variable_names() -> Array[str]
editor.add_local_variable(local_name: Name, pin_type=[], value: str = "") -> bool
editor.remove_local_variable(local_name: Name) -> bool
editor.get_local_variable_type(local_variable_name: str) -> Optional[EdGraphPinType]
editor.get_local_variable_default_value(local_variable_name: str) -> Optional[str]
editor.set_local_variable_default_value(local_variable_name: str, new_default_value: str) -> bool
```

- `pin_type` defaults to **boolean** when omitted. Build real types with
  `unreal.BlueprintEditorLibrary` (see `11-parameter-formats.md` §3).
- Defaults are **strings**, parsed against the type.
- A newly added member variable is **not serialised until save+reload**. Setting its default in the
  same run fails with `Failed to find property 'X'`. Add it in one run, set the default in the next.
- `list_member_variable_names()` is authoritative for names — they can contain spaces.

## 6. Graph inputs, outputs, and positions

```python
editor.add_graph_input_parameter(input_name: Name, pin_type=[], value: str = "") -> BlueprintGraphPin
editor.remove_graph_input_parameter(input_name: Name) -> bool
editor.add_graph_output_parameter(output_name: Name, pin_type=[]) -> K2Node_FunctionResult
editor.remove_graph_output_parameter(output_name: Name) -> bool
```

Positions are integer graph coordinates:

```python
node.set_node_pos(unreal.IntPoint(x, y))
node.get_node_pos() -> IntPoint
node.get_node_title() -> str
node.get_node_size() -> Vector2D        # via BlueprintEditorLibrary
```

Lay nodes out on a readable grid (e.g. +300 x per column, +150 y per row) instead of stacking them
at one point. Overlapping nodes compile fine and are unusable for the human who reviews the graph.

## 7. Comment nodes

```python
editor.add_comment_node(comment_text: str, location: Vector2D, size: Vector2D = (400, 200)) -> EdGraphNode_Comment
editor.add_comment_to_nodes(comment_text: str, nodes: Array[K2Node], padding: int = 50) -> EdGraphNode_Comment
editor.list_comment_nodes() -> Array[EdGraphNode_Comment]
editor.remove_comment_node(comment_node: EdGraphNode_Comment) -> None
```

`add_comment_to_nodes` computes bounds from the nodes plus padding — use it to label a block you just
generated, so the result is reviewable.

## 8. Function flags

```python
editor.set_is_pure_function(b=True)
editor.set_is_const_function(b=True)
editor.set_is_exec_function(b=True)
editor.set_is_thread_safe_function(b=True)
editor.set_is_call_in_editor_function(b=True)
editor.set_is_unsafe_during_actor_construction_function(b=True)
editor.set_is_deprecated_function(b=True)
editor.set_deprecation_message_on_function(message: Text)
editor.set_function_is_public()
editor.set_function_is_protected()
editor.set_function_is_private()
```

A **pure** function has no exec pins. Trying to wire an exec line into a pure node fails silently
because the pin does not exist — that is a correctness issue, not a formatting nit.

## 9. Node surgery

```python
editor.retarget_node_class(node, old_class, new_class) -> bool
editor.remove_nodes(nodes: Array[K2Node]) -> None
editor.add_node_pin(node) -> bool
editor.remove_node_pin(node, pin) -> bool
```

- `retarget_node_class` handles `K2Node_DynamicCast` (TargetType), `K2Node_CallFunction`
  (FunctionReference), `K2Node_Event` (EventReference) and `K2Node_BaseMCDelegate` (delegate
  reference). Returns true if retargeted or already correct; false if the node kind is unsupported.
  This is the supported way to change a cast target or a bound function without rebuilding the node.
- `add_node_pin` / `remove_node_pin` work only on variadic nodes (Sequence, Switch, commutative
  binary operators such as Add/Multiply, Make Array). Returns false otherwise.

## 10. Static library — `unreal.BlueprintEditorLibrary`

83 methods; the ones that matter alongside the graph editor:

```python
BEL = unreal.BlueprintEditorLibrary

# graph discovery
BEL.find_graph(bp, "EventGraph") -> EdGraph
BEL.find_event_graph(bp) -> EdGraph                  # only the graph literally named EventGraph
BEL.list_graphs(bp) -> Array[EdGraph]
BEL.list_graph_names(bp, out_names)
BEL.add_function_graph(bp, "Name") -> EdGraph
BEL.add_function_override(bp, Name) -> EdGraph
BEL.add_event_override(bp, Name, ...) -> K2Node_Event
BEL.remove_graph(bp, graph)
BEL.remove_function_graph(bp, Name)

# asset-level
BEL.create_blueprint_asset_with_parent(package_path, parent_class) -> Blueprint
BEL.get_blueprint_parent_class(bp) -> UClass
BEL.generated_class(bp) -> UClass
BEL.reparent_blueprint(bp, new_parent_class)         # destructive
BEL.compile_blueprint(bp) -> bool                    # True = compilation RAN
BEL.refresh_open_editors_for_blueprint(bp)

# variable introspection
BEL.list_member_variable_names(bp, include_inherited=True) -> Array[str]
BEL.get_member_variable_type(bp, name) -> Optional[EdGraphPinType]
BEL.change_member_variable_type(bp, name, new_type)  # destructive, no return value
BEL.replace_variable_references(bp, old_name, new_name)
BEL.set_blueprint_variable_category(bp, name, text)
BEL.set_blueprint_variable_instance_editable(bp, name, bool)

# type construction (see 11-parameter-formats.md §3)
BEL.get_basic_type_by_name("float") / get_struct_type / get_class_reference_type /
BEL.get_object_reference_type / get_array_type / get_set_type / get_map_type
BEL.pin_type_to_json_schema(pin_type, self_context) -> str

# version forensics
BEL.get_current_engine_version() -> str
BEL.get_saved_by_engine_version(asset) -> str
```

`BEL.find_summary(bp)` is broken — it asserts. Do not call it.

## 11. C++-only methods — do not call from Python

Not `BlueprintCallable`, so no Python binding:

- `UBlueprintEditorLibrary::GatherUnusedVariables`
- `UBlueprintEditorLibrary::Generic_AddMemberVariableWithValue`
- most of `FBlueprintGraphPin`'s raw statics (`GetNativePinSafe`, `FromNativePin`)

Use `list_nodes_with_errors()` and `remove_unused_variables` (which *is* callable) instead.
