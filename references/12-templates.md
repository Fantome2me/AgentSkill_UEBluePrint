# Templates

Copy-paste procedures. Every template targets the **engine-native** surface only, so it works on any
UE 5.8 project. Verification status is stated per template.

| # | Template | Status |
|---|---|---|
| T1 | Pre-flight + backup | verified pattern |
| T2 | Read a graph (safe, read-only) | API verified; run it first on any unfamiliar asset |
| T3 | Create a function graph and build a node chain | API verified by Epic docs; **not yet executed end-to-end here** |
| T4 | Wire pins, with checks | API verified; not yet executed here |
| T5 | Member variables and their defaults | API verified; not yet executed here |
| T6 | Event graph: override an event | API verified; not yet executed here |
| T7 | Compile / save / prove | verified pattern |
| T8 | Report skeleton | verified pattern |

Common preamble for every mutating template:

```python
import os, re, unreal

BGE = unreal.BlueprintGraphEditor
BEL = unreal.BlueprintEditorLibrary
BPL = unreal.BlueprintGraphPinLibrary
```

---

## T1 — Pre-flight and backup

**Status: verified pattern** (each check has a real observed failure behind it).

```python
import hashlib, os, shutil, time

def preflight(asset_file):
    """Returns (ok, messages). Refuses to proceed if the asset is locked."""
    msgs = []
    if not os.path.isfile(asset_file):
        return False, ["asset file not found: " + asset_file]
    try:                                  # R2: a running editor holds the file open
        with open(asset_file, "r+b"):
            pass
        msgs.append("asset is writable")
    except OSError as exc:
        return False, ["asset is LOCKED (%s) - close UnrealEditor first" % exc]
    return True, msgs

def backup(asset_file, backup_root, reason):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest_dir = os.path.join(backup_root, "%s_%s" % (stamp, reason))
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(asset_file))
    shutil.copy2(asset_file, dest)
    with open(asset_file, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    return {"path": dest, "size": os.path.getsize(dest), "sha256": digest}

def sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()
```

Record `sha256` before the edit. T7 compares it afterwards. A backup without a recorded hash cannot
be proven to be a faithful restore.

---

## T2 — Read a graph (do this first on any unfamiliar asset)

**Status: API verified.** Read-only, so it is the safe way to learn an asset's real shape before
touching it.

```python
def dump_graph(bp_path, graph_name):
    bp = unreal.EditorAssetLibrary.load_asset(bp_path)
    if bp is None:
        raise RuntimeError("asset not found: " + bp_path)
    if not isinstance(bp, unreal.Blueprint):
        raise RuntimeError("not a Blueprint: %s" % type(bp).__name__)

    print("parent       :", BEL.get_blueprint_parent_class(bp).get_name())
    print("saved by     :", BEL.get_saved_by_engine_version(bp))
    print("current eng  :", BEL.get_current_engine_version())
    print("status       :", bp.get_editor_property("status"))
    print("graphs       :", [str(g.get_name()) for g in BEL.list_graphs(bp)])
    print("member vars  :", BEL.list_member_variable_names(bp, True))

    graph = BEL.find_graph(bp, graph_name)
    if graph is None:
        raise RuntimeError("no graph named " + graph_name)

    editor = BGE.get_graph_editor(graph)
    if not editor or not editor.is_valid():
        raise RuntimeError("could not obtain a graph editor handle")

    print("\n-- local variables --")
    for n in editor.list_local_variable_names():
        t = editor.get_local_variable_type(n)
        print("   %-28s %s" % (n, BEL.pin_type_to_json_schema(t, None) if t else "?"))

    print("\n-- nodes --")
    for node in editor.list_all_nodes():
        print("   %-34s %s" % (type(node).__name__, node.get_node_title()))
        for pin in node.list_all_pins():
            conns = pin.list_connected_pins()
            arrow = " -> " + ", ".join(
                "%s.%s" % (c.get_owning_node().get_node_title(), c.get_pin_name()) for c in conns
            ) if conns else ""
            print("        %-22s %-11s %r%s" % (
                pin.get_pin_name(), pin.get_pin_direction(), pin.get_pin_value(), arrow))

    print("\n-- diagnostics --")
    print("   errors  :", len(editor.list_nodes_with_errors()))
    print("   warnings:", len(editor.list_nodes_with_warnings()))
    return editor
```

Reading connected pins **from input pins** is the reliable direction when you need to know what feeds
a node. Reachability walks that start from an incomplete root set produce false "orphan" results —
trap 28.

---

## T3 — Create a function graph and build a node chain

**Status: API verified (Epic's documented example, extended). Not yet executed end-to-end here.**

```python
def build_function(bp_path, func_name):
    bp = unreal.EditorAssetLibrary.load_asset(bp_path)

    # find-or-create, so a re-run does not silently produce func_name_0   (trap 1)
    existing = [str(n) for n in BEL.list_graph_names(bp)]
    if func_name in existing:
        graph  = BEL.find_graph(bp, func_name)
        editor = BGE.get_graph_editor(graph)
    else:
        editor = BGE.create_and_edit_function_graph(bp, func_name)

    if editor.get_graph().get_name() != func_name:
        raise RuntimeError("graph name occupied: %r" % editor.get_graph().get_name())

    entry     = editor.find_graph_entry_pin()
    entry_pos = entry.get_owning_node().get_node_pos()

    print_node = editor.add_call_function_node(
        "/Script/Engine.KismetSystemLibrary.PrintString")
    if print_node is None:
        raise RuntimeError("could not create the print node - check the function path")

    in_string = print_node.find_input_pin("InString")
    if not BPL.is_valid(in_string):
        raise RuntimeError("no InString pin; dump list_all_pins() to see the real names")
    if not in_string.set_pin_value("generated by script"):
        raise RuntimeError("set_pin_value rejected the literal")

    if not BPL.can_create_connection(entry, print_node.find_execute_pin()):
        raise RuntimeError("entry pin cannot connect to the print node")
    if not BPL.try_create_connection(entry, print_node.find_execute_pin()):
        raise RuntimeError("connection failed")

    print_node.set_node_pos(unreal.IntPoint(entry_pos.x + 320, entry_pos.y))
    return editor
```

Notes:

- The node factories take **no location**, so always set the position yourself.
- `create_and_edit_function_graph` creates; it does not reuse. Hence the find-or-create above.
- Restrict yourself to the node factories listed in `10-api.md` §2 plus `create_node_from_name`.
  There is no generic "new node of class X".

---

## T4 — Wire pins with checks

**Status: API verified. Not yet executed here.**

```python
def connect(src_node, src_pin_name, dst_node, dst_pin_name):
    src = src_node.find_output_pin(src_pin_name)
    dst = dst_node.find_input_pin(dst_pin_name)
    if not BPL.is_valid(src) or not BPL.is_valid(dst):
        raise RuntimeError("pin not found (%s -> %s); dump the pins to see real names"
                           % (src_pin_name, dst_pin_name))

    # read the types first, so a mismatch is a clear message rather than a compile surprise
    print("src %-20s %s" % (BPL.get_pin_name(src), BPL.get_pin_type_as_json_schema(src)))
    print("dst %-20s %s" % (BPL.get_pin_name(dst), BPL.get_pin_type_as_json_schema(dst)))

    if not BPL.can_create_connection(src, dst):          # trap 11: never probe with the write call
        raise RuntimeError("pins are not connectable")
    if not BPL.try_create_connection(src, dst):
        raise RuntimeError("try_create_connection failed")

    # verify by reading the connection back
    if not any(BPL.is_same_native_pin(p, dst) for p in BPL.list_connected_pins(src)):
        raise RuntimeError("connection did not stick")
```

To disconnect, prefer the narrow call:

```python
BPL.break_single_pin_link(src, dst)      # one link
BPL.break_pin_links(src)                 # EVERY link on this pin - trap 14
```

After **any** rewire, dump the exec chain and confirm every hop still exists — trap 29.

---

## T5 — Member variables and their defaults

**Status: API verified. Not yet executed here.**

```python
def add_float_member(bp_path, name):
    bp = unreal.EditorAssetLibrary.load_asset(bp_path)
    L  = unreal.BlueprintEditorLibrary

    pin_type = L.get_basic_type_by_name("float")
    # guard against the silent int fallback   (11-parameter-formats.md §3)
    if L.pin_type_to_json_schema(pin_type, None) == \
       L.pin_type_to_json_schema(L.get_basic_type_by_name("int"), None):
        raise RuntimeError("type name not recognised; it fell back to int")

    before = list(L.list_member_variable_names(bp, True))
    if name in before:
        print("already present:", name)
        return

    if not BGE.get_graph_editor_by_name(bp, "EventGraph").add_member_variable(name, pin_type):
        raise RuntimeError("add_member_variable returned False")

    L.compile_blueprint(bp)
    unreal.EditorAssetLibrary.save_asset(bp_path, False)

    after = list(L.list_member_variable_names(bp, True))
    if set(after) - set(before) != {name}:
        raise RuntimeError("variable did not appear: %s -> %s" % (before, after))
    print("added:", name, pin_type)
```

**Setting a default for a variable you just added must happen in a separate run** — a new member
variable is not serialised until save+reload, so an in-run default set fails with
`Failed to find property 'X'` (trap 19). Run the add, then a second script that sets the default.

---

## T6 — Event graph: override an event

**Status: API verified. Not yet executed here.**

```python
def add_begin_play(bp_path):
    bp = unreal.EditorAssetLibrary.load_asset(bp_path)
    editor = BGE.get_graph_editor_by_name(bp, "EventGraph")

    # member name, NOT the display name   (trap 4 / 11-parameter-formats.md §7)
    node = editor.find_event_node(unreal.Name("ReceiveBeginPlay"))
    if node is None:
        node = BEL.add_event_override(bp, unreal.Name("ReceiveBeginPlay"))
    if node is None:
        raise RuntimeError("could not obtain a ReceiveBeginPlay node")
    return node
```

Use `editor.list_component_events(component)` to discover bindable delegate names before
`add_component_bound_event_node`. Do not guess them.

---

## T7 — Compile, save, and prove it

**Status: verified pattern.** This is the closing block for every mutating template.

```python
def compile_save_prove(bp_path, asset_file, before_hash):
    bp = unreal.EditorAssetLibrary.load_asset(bp_path)

    graph = BEL.find_event_graph(bp)
    if graph:
        errs = BGE.get_graph_editor(graph).list_nodes_with_errors()
        if errs:
            raise RuntimeError("%d error node(s) after write" % len(errs))

    if not BEL.compile_blueprint(bp):
        raise RuntimeError("compile_blueprint returned False")
    status = str(bp.get_editor_property("status"))
    if "BS_UP_TO_DATE" not in status:
        raise RuntimeError("compile status is " + status)

    if not unreal.EditorAssetLibrary.save_asset(bp_path, False):
        raise RuntimeError("save_asset returned False")

    BEL.refresh_open_editors_for_blueprint(bp)

    after_hash = sha256(asset_file)
    if after_hash == before_hash:
        raise RuntimeError("DISK HASH UNCHANGED - nothing was actually written (R1)")
    print("disk hash changed: %s -> %s" % (before_hash[:12], after_hash[:12]))
    return after_hash
```

Then, and only then, re-open in a **fresh process** and read the change back (R7). A hash change plus
a fresh-process readback is the whole proof; anything less is a hypothesis.

> `save_asset(path, False)` — the second argument is `only_if_is_dirty`. Passing `False` forces the
> save regardless of the dirty flag, which is what you want when you need to observe a hash change.

---

## T8 — Report skeleton

**Status: verified pattern.** Wrap `main()` so a crash still produces a report — otherwise a failed
run is indistinguishable from a run that never started.

```python
import os, traceback, unreal

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_report.txt")
_LINES = []

def w(msg=""):
    _LINES.append(str(msg))
    unreal.log_warning("BP >>> " + str(msg))     # log() did not reliably reach the log in practice

def emit():
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(_LINES))

def main():
    w("task: ...")
    # ... work ...

try:
    main()
except Exception:
    w("FATAL:")
    for line in traceback.format_exc().splitlines():
        w(line)

emit()          # ALWAYS, even after an exception
```

Then read `_report.txt` — not stdout.

---

## T9 — Discover what a node can do, before using it

**Status: verified pattern.**

```python
editor = BGE.get_graph_editor_by_name(bp, "EventGraph")

# whole palette (about 41k entries; do not print it, filter it)
avail = editor.list_available_nodes([])
print(len(avail), "nodes available")

# find the exact palette string for something you want
cands = [n for n in avail if n.endswith("CreateDynamicMaterialInstance")]
print(cands)          # take one VERBATIM - never retype the localised prefix (trap 6)

# create it, then read back its actual pins before wiring anything
node = editor.create_node_from_name(cands[0], unreal.Vector2D(-600, 300), [])
if node is None:
    raise RuntimeError("create_node_from_name returned None")
for p in node.list_all_pins():
    print("  %-24s %-11s %s" % (p.get_pin_name(), p.get_pin_direction(), p.get_pin_value()))
```

This is the "probe, do not guess" loop from R4, and it is the single highest-value habit in the whole
skill: two cheap read-only calls replace a broken graph and a confusing compile error.

---

## Open items for the next revision

1. Execute T3–T6 against a scratch Blueprint on this machine and promote them from *API verified* to
   *executed*. Each promotion should record the actual output.
2. Confirm the string forms for struct / enum / object-reference pin values (`11-parameter-formats.md` §4).
3. Confirm whether `add_call_function_node` prefers `Module.Class.Function` or `Module.Class:Function`
   on 5.8, or accepts both, and record the answer.
4. Determine whether `unreal.BlueprintTools()`-style project helpers exist in the target project; if
   they do, note that they are project-private and must not leak into shared procedures (R9).
5. Add a Widget Blueprint template using the correct UMG subsystem — explicitly out of scope here.
