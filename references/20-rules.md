# Rules and trap catalogue

Hard rules first, then traps. Each trap carries a **source** marker, because they are not equally
trustworthy and merging them without labels would launder project-specific folklore into general advice.

| Marker | Meaning |
|---|---|
| **[实测]** | Executed and observed on UE 5.8 in a real session. Highest confidence. |
| **[文档]** | Stated in Epic's official Unreal Python 5.8 documentation. |
| **[源码]** | Read out of the engine source or headers on this machine. |
| **[DSL]** | Observed against a **project-private** helper library (`unreal.BlueprintTools` / `write_graph_dsl`). These traps are real *for that library*; they do not apply to the portable `BlueprintGraphEditor` route. Kept because they show what class of problem to expect. |

---

# Part 1 — Hard rules

## R1 — A successful API return is not a successful edit [实测]

`save_asset` / `save_loaded_asset` / `save_map` returning `True` frequently means nothing was written.
Every write must be proven by **all three**:

1. **Disk hash changed** — `Get-FileHash <asset>.uasset -Algorithm SHA256`, before vs after.
2. **File timestamp changed** — a changed hash with unchanged mtime is impossible; both should move.
3. **Fresh-process readback** — launch a *new* process that loads from disk and reports the value.
   Reading back inside the session you wrote proves only that memory changed.

This single rule prevents most false "done" reports.

## R2 — The editor must be closed for any write [实测]

A running `UnrealEditor` holds the `.uasset` open. Saves then fail with
`LogFileManager: Error: Error moving file ... (Error Code 32)` after 10 retries, and
`LogSavePackage: Error: Error saving ...`.

Worse: a running editor keeps its own in-memory copy, so editing the file underneath it means the
editor's next save **overwrites your work**. This happened twice in the source session.

```powershell
Get-Process UnrealEditor, LiveCodingConsole -ErrorAction SilentlyContinue
# and test the lock directly:
try { $s=[IO.File]::Open($p,'Open','ReadWrite','None'); $s.Close(); 'writable' } catch { 'LOCKED' }
```

Corollary: **two editors cannot share one project.** Two `UnrealEditor.exe` on the same `.uproject`
corrupt the Asset Registry.

## R3 — Back up before anything destructive [实测]

- Copy the `.uasset` / `.umap` / `.ini` into a timestamped folder, recording **size + SHA256** so a
  restore can be asserted byte-for-byte.
- UE also writes `<Project>/Saved/<AssetName><GUID>.tmp` on every overwrite. That `.tmp` is a **valid
  package** and is your recovery path — copy it over the `.uasset`.
- Restoring a whole file is safer than re-editing. Re-editing is what severed an exec chain once.

## R4 — Never invent a node id, function path, or pin name [实测][文档]

Probe first, abort if missing:

```python
avail = editor.list_available_nodes([])
if not any(n.endswith("PrintString") for n in avail):
    raise RuntimeError("node not available; aborting before any write")
```

Pin names are frequently not what you would guess (`InWidthOverride`, not `InValue`; `NewScale`, not
`Scale`; `InText`, not `Text`). Event nodes resolve by member name (`ReceiveBeginPlay`, not
`"Begin Play"`).

## R5 — Check error nodes after every write, then compile, then assert status [实测]

```python
errs = editor.list_nodes_with_errors()
if errs:
    raise RuntimeError("%d error node(s)" % len(errs))

unreal.BlueprintEditorLibrary.compile_blueprint(bp)
status = str(bp.get_editor_property("status"))
if "BS_UP_TO_DATE" not in status:
    raise RuntimeError("compile status is " + status)
```

A graph can be "written" and still be broken. `BS_UP_TO_DATE_WITH_WARNINGS` means it compiles but has
a flagged node — worth surfacing to the user, not silently accepting.

## R6 — Save in the same run as the edit [实测]

An early `return`, or letting the editor close before `save_asset`, loses the change. Save after each
step; only return after saving.

## R7 — Verify in a fresh process [实测]

Same-process checks lie: a graph can look correct in the frame you wrote it and be wrong after
save/reload. Reopen and re-measure. For anything that survives on disk, that is the only result that
counts.

## R8 — Say what you could not verify [实测]

Not checkable from a headless script:

- **Rendering.** Nothing about how it *looks*. A commandlet world has no rendering, and Niagara
  components never activate (`is_active` stays `False` after `activate(True)`).
- **Real keystrokes** — no Slate focus in `-ExecutePythonScript` mode.
- **A built UMG widget tree** — the Slate widget is never constructed in `-unattended` mode.

Report these as *unverified, needs a human look* — never as "done". Offer a measurable A/B switch
(a scalar parameter wired as a gain, default 1, set 0 to disable) instead of asserting a visual result.

## R9 — Write reusable procedures against the engine-native layer only [源码]

Use `unreal.BlueprintGraphEditor` / `BlueprintEditorLibrary` / `BlueprintGraphPinLibrary`. Do not build
on project-private helpers, and do not assume a helper class exists because a previous document used
it — `unreal.BlueprintTools` has no `UBlueprintTools` UCLASS anywhere in the engine (verified by
searching the full source tree). Anything built on it is dead on arrival in another project.

## R10 — Compile after structural change, and re-resolve handles [文档]

Compilation can replace nodes, invalidating native pin pointers. Never reuse a pin or node handle
across a `compile_blueprint` call — re-resolve by name.

## R11 — Prefer additive work in C++ bridges [实测]

If you must add a project C++ plugin (only for what Python cannot reach), keep its handlers
**additive**. A C++ handler that did graph mutation with `DeleteMaterialExpression` crashed when
invoked through `execute_console_command`. Expose behaviour as an
`FAutoConsoleCommandWithWorldAndArgs` and drive it with
`unreal.SystemLibrary.execute_console_command(None, "cmd args")` — note `-ExecCmds` does **not** fire
in a `-run=pythonscript` commandlet.

---

# Part 2 — Trap catalogue

## 2.1 Graph creation and identity

| # | Trap | Symptom | Fix | Source |
|---|---|---|---|---|
| 1 | `create_and_edit_function_graph` on an existing name | you get `Name_0`; edits land in the copy, the original is untouched | find-or-create, then `assert g.get_name() == Name` | [实测] |
| 2 | Adding a parameter to an existing function | entry-node pin mismatch → compile failure | delete and recreate the function — but the name is not freed immediately (`Name_0`) | [DSL] |
| 3 | `remove_function_graph` then immediate recreate | the new graph is `Name_0` | reuse or rename | [DSL] |
| 4 | Caching a graph handle across a compile | stale handle, edits go nowhere | re-resolve after every compile | [文档] |

## 2.2 Node creation

| # | Trap | Symptom | Fix | Source |
|---|---|---|---|---|
| 5 | `create_node_from_name` location as `IntPoint` | `Failed to convert parameter 'location'` | use `unreal.Vector2D(x, y)` | [实测] |
| 6 | Retyping a localised category prefix | returns `None` **without raising** | take the string straight from `list_available_nodes()` | [实测] |
| 7 | Guessing a function path | `add_call_function_node` returns null | probe the palette; try both `.` and `:` spellings | [实测] |
| 8 | Calling `add_dispatcher_event_node` before any compile | returns null — no skeleton class yet | compile the Blueprint once first | [文档] |
| 9 | `add_component_bound_event_node` on a function graph | returns null — event graphs only | target the event graph | [文档] |
| 10 | Assuming a generic "create node by C++ class" exists | nothing to call | use the specific factories, or `create_node_from_name` | [源码] |

## 2.3 Pins and wiring

| # | Trap | Symptom | Fix | Source |
|---|---|---|---|---|
| 11 | `try_create_connection` used as a probe | dirty package from failed attempt | call `can_create_connection` first | [文档] |
| 12 | Wiring exec into a **pure** node | fails silently — pure nodes have no exec pin | route through `Branch`, or hand the value off via a variable | [实测] |
| 13 | `IsValid` treated as an exec node | wiring `BeginPlay.then -> IsValid.execute` fails silently | `IsValid.ReturnValue -> Branch.Condition`, then branch on it | [实测] |
| 14 | `break_pin_links()` on one pin | severs **every** link on that pin, orphaning a chain you did not mean to touch | prefer `break_single_pin_link` | [实测] |
| 15 | Not re-reading pins after a compile | stale handles, silent no-ops | re-resolve by name | [文档] |
| 16 | Guessing pin names | `find_input_pin` returns an invalid handle | dump `list_all_pins()` and read the real names | [实测] |
| 17 | Ignoring `set_pin_value`'s bool | pin keeps its old value, no error | check the return, and the literal format | [文档] |
| 18 | `add_node_pin` on a fixed-arity node | returns `False` | variadic nodes only (Sequence, Switch, Add/Multiply, Make Array) | [文档] |

## 2.4 Variables

| # | Trap | Symptom | Fix | Source |
|---|---|---|---|---|
| 19 | Setting a new member variable's default in the same run | `Failed to find property 'X'` — not serialised until save+reload | add in one run, set the default in the next | [实测] |
| 20 | Omitting `pin_type` | silently becomes **boolean** | always pass a real `EdGraphPinType` | [文档] |
| 21 | Guessing a CDO property name | not found | names may contain spaces — read `list_member_variable_names()` | [实测] |
| 22 | `set_editor_property` chained | `AttributeError: 'NoneType'` — it returns `None` | one-shot dict: `obj.set_editor_property("Weapon Type", {...})` | [实测] |
| 23 | Renaming a variable without updating references | graphs still reference the old name | call `replace_variable_references(bp, old, new)` | [源码] |
| 24 | `change_member_variable_type` treated as verifiable | returns nothing — no success signal | re-read with `get_member_variable_type` | [源码] |

## 2.5 Graph semantics

| # | Trap | Symptom | Fix | Source |
|---|---|---|---|---|
| 25 | Relative offset in `UserConstructionScript` | re-applies on every construction run; the offset compounds and lowering the parameter appears to do nothing | use a relative offset only from **`BeginPlay`**, or `SetActorLocation` with an absolute target | [实测] |
| 26 | `GetPlayerCameraManager` during actor construction | warning node; **PIE aborts** with "Aborting Editor Play Session" (it is flagged `UnsafeDuringActorConstruction`) | move the chain to `EventGraph`'s `BeginPlay` | [实测] |
| 27 | Pure nodes evaluate at their **consumption** point | late reads of a scratch variable return stale values | read stable variables instead of scratch temporaries | [DSL] |
| 28 | Reachability-based pruning | deleted a live Timeline chain because the walk's root set omitted the driving event | start from **every** `K2Node_Event` plus nodes with no inbound exec, and assert an expected node count; safer still, do not prune | [实测] |
| 29 | Any rewire | can silently orphan what a branch's `then` used to drive | after rewiring, dump the whole exec chain and confirm every hop still exists | [实测] |
| 30 | `break_pin_links` + reconnect | same as 29 | same as 29 | [实测] |

## 2.6 Persistence, maps and config

| # | Trap | Symptom | Fix | Source |
|---|---|---|---|---|
| 31 | Loading a second level to "reference" it | UE reverts the non-first-loaded level on exit — your edit is gone though `save_map` wrote a file | load exactly **one** map per run, the one you intend to edit | [实测] |
| 32 | `LevelEditorSubsystem.save_current_level()` | always returns `False` | use `unreal.EditorLoadingAndSavingUtils.save_map(world, "/Game/Path/Map")` | [实测] |
| 33 | Editing `DefaultEngine.ini` with the editor open | the editor rewrites the file on exit — change lost | edit config with the editor closed | [实测] |
| 34 | Component-template `relative_scale3d` | reads back correctly in memory, spawned instances still come out at scale 1.0 | spawn an instance and read the **component's world scale** | [实测] |
| 35 | `spawn_actor_from_class` with a `Transform` | conversion failure | pass `unreal.Vector` + `unreal.Rotator` | [实测] |

## 2.7 Textures and rendering

| # | Trap | Symptom | Fix | Source |
|---|---|---|---|---|
| 36 | `UTexture2D::CreateTransient` not GC-rooted | reads correctly in the same frame, blank afterwards | `AddToRoot()` + cache per asset key | [实测] |
| 37 | Editor viewports are not Realtime | stale content, and TAA/TSR history smears into an unclearable artefact | detect change and call `Invalidate` + `RedrawAllViewports` | [实测] |
| 38 | `bAlwaysPersistRenderingState=true` with `bCaptureEveryFrame=false` | **completely black** capture | keep both false, or both consistent | [实测] |
| 39 | `SceneCapture` assumed to equal the viewport | exposure / post-process / show-flags differ no matter how the PP is set | capture the **viewport itself** (read its pixels) rather than re-rendering | [实测] |
| 40 | `Game View` (`G`) in a custom `SEditorViewport` | no `G` — it is an `FLevelViewportCommands` command, not bound by `SEditorViewport::BindCommands()` | bind it explicitly, or toggle from Blueprint around the capture | [实测] |
| 41 | Overriding `SEditorViewport::BindCommands()` with an empty body | **no** shortcut works in that viewport | call the parent, then add the commands you want | [实测] |
| 42 | Translucent material + Pixel Depth Offset | PDO is compiled out entirely unless the material writes velocity; the render never changes even with a gain 0-vs-1 A/B | set `bOutputTranslucentVelocity = True` on the material and re-save | [实测] |
| 43 | Niagara emitter in **World Space** | scaling the owning component cannot change particle size, ever | check `bLocalSpace`; change the particle attribute instead | [实测] |
| 44 | Python `unreal.MaterialProperty` is incomplete | 17 of 36 entries; `MP_PixelDepthOffset` (28) is missing, so `get_material_property_input_node(mat, MP_PixelDepthOffset)` raises `Cannot pythonize '28'` | this one connection needs a C++ bridge | [实测] |

## 2.8 Driving the editor

| # | Trap | Symptom | Fix | Source |
|---|---|---|---|---|
| 45 | PowerShell `&` on the editor | returns immediately; you read a stale or absent report | use Python `subprocess.run`, which blocks | [实测] |
| 46 | A leftover editor process | Live Coding locks the build; two editors fight over one asset | kill `UnrealEditor` / `UnrealEditor-Cmd` / `LiveCodingConsole` before building | [实测] |
| 47 | Omitting `-unattended` | with a C++ module present the editor starts **Live Coding** and blocks forever on `LogLiveCoding: Waiting for server` | always pass `-unattended` | [实测] |
| 48 | Using `-stdout` | floods the console | write findings to a report file and read that | [实测] |
| 49 | Trusting exit code 0 | means "the commandlet ran", not "the script worked" | grep the log for `LogPython: Error: Traceback` and `Failure - N error(s)` | [实测] |
| 50 | A script that raises before writing its report | output looks identical to "the script never ran" | wrap `main()` in `try/except` that still writes the report | [实测] |

---

## Reporting template

State, explicitly:

1. what you changed (asset paths, graphs, node counts)
2. the verification performed (hash before/after, fresh-process readback result, error-node count)
3. what you could **not** verify, and why
4. the backup location, so a revert is one command

Anything not in (2) is a hypothesis, and must be labelled as one.
