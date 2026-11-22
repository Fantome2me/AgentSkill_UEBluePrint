# Adjacent systems

Things a Blueprint task often needs that are **not** Blueprint graph APIs. Each has its own surface;
using the Blueprint one here is a common mistake.

| Need | Correct surface |
|---|---|
| Widget tree (UMG) | `unreal.WidgetBlueprintLibrary`, UMG editor subsystem — **not** `BlueprintGraphEditor` |
| Material graphs | `unreal.MaterialEditingLibrary`, or the material toolset via `ToolsetRegistry` |
| Niagara systems | `ToolsetRegistry.execute_tool("NiagaraToolsets.NiagaraToolset_System", ...)` |
| Levels, actors, map settings | `unreal.EditorActorSubsystem`, `unreal.EditorLoadingAndSavingUtils` |
| Asset load/save/duplicate/rename | `unreal.EditorAssetLibrary`, `unreal.EditorLoadingAndSavingUtils` |
| Config | plain text `<Project>/Config/DefaultEngine.ini` |

---

## 1. Reaching hidden editor APIs: `ToolsetRegistry`

Plain Python bindings hide most of the editor. The toolset registry exposes them.

```python
import json, unreal
TR = unreal.ToolsetRegistry

TR.is_available()                                    # True on a normal editor build
schemas = TR.get_all_toolset_json_schemas()           # ~1 MB; write to a file and grep it
schema  = TR.get_toolset_json_schema(unreal.NiagaraToolset_System)

res = TR.execute_tool(
    "NiagaraToolsets.NiagaraToolset_System",          # toolset name, from the schema list
    "SetStackInputData",                              # tool name (last dotted segment)
    json.dumps(payload))
err = res.get_editor_property("error")                # "" on success
val = res.get_editor_property("value")                # a JSON *string*; json.loads it
```

**Object references inside payloads use a full object path, not a package path:**

```python
{"system": {"refPath": "/Game/Dir/MySystem"}}        # wrong -> "not a valid object path"
{"system": {"refPath": "/Game/Dir/MySystem.MySystem"}}   # right
```

Verified Niagara tools (`NiagaraToolset_System`): `GetSystemSummary`, `GetEmitterTopology`,
`GetEmitterInputValues`, `GetRendererData`, `SetRendererData`, `GetEmitterData`, `SetEmitterData`,
`AddSetParametersModule`, `RemoveModule`, `GetStackInputData`, `SetStackInputData`.

A `SetVariables` module does **not** replace a previous one — each call appends. To change a value,
delete the old module first, or accept last-wins. Prefer delete-then-add so the stack does not
accumulate dead modules.

Also available: `editor_toolset.toolsets.material.MaterialTools` (`add_expression`,
`connect_expressions`, `connect_to_output`, `recompile`, `get_property_input`).

**Localisation warning.** Toolset names and node categories can be localised. Take strings out of the
schema/registry rather than retyping them — a retyped localised string matches nothing and fails
silently.

## 2. Maps, WorldSettings, actors

`WorldSettings` is reachable by full object path, without loading the map first:

```python
ws = unreal.find_object(None, "/Game/Dir/MyMap.MyMap:PersistentLevel.WorldSettings_1")  # _1, _2 ...
ws = ws or unreal.find_object(None, "/Game/Dir/MyMap.MyMap:PersistentLevel.WorldSettings")
ws.set_editor_property("default_game_mode",
                       unreal.load_object(None, "/Game/Dir/BP_MyGameMode.BP_MyGameMode_C"))
```

Actor placement:

```python
eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
eas.get_all_level_actors()
actor = eas.spawn_actor_from_class(cls, unreal.Vector(x, y, z), unreal.Rotator(0, 0, 0))
actor.set_actor_label("MyLabel")
actor.get_actor_bounds(False)          # -> (origin, extent); use it to sit things on a floor
```

`spawn_actor_from_class` wants `Vector` + `Rotator`, not a `Transform`.

**Load exactly one map per run.** Loading a second map to "reference" it causes UE to revert the
non-first-loaded level on exit — `save_map` writes a file and the edit is still gone (trap 31).

To copy behaviour between maps, copy the **GameMode reference**, not the nodes. Character movement,
weapon pickup and projectile spawning live in the GameMode's `DefaultPawnClass`.

## 3. Config

```ini
[/Script/EngineSettings.GameMapsSettings]
EditorStartupMap=/Game/Dir/MyMap.MyMap
GameDefaultMap=/Game/Dir/MyMap.MyMap
GlobalDefaultGameMode=/Game/Dir/BP_MyGameMode.BP_MyGameMode_C
```

- Paths use the `Package.Asset` form.
- The editor reads these at startup and **may rewrite the file on exit** — edit with the editor closed
  or the change is lost.
- `EditorStartupMap` = what the editor opens. `GameDefaultMap` = what a packaged build and `-game`
  start in. **PIE is unaffected by both** — PIE plays the currently open map.

## 4. Asset operations

```python
EAL = unreal.EditorAssetLibrary
EAL.load_asset("/Game/A/B")                       # -> None on failure, never raises
EAL.save_asset("/Game/A/B", False)                # False = save even if not dirty
EAL.save_loaded_asset(obj, False)
EAL.does_asset_exist("/Game/A/B")
unreal.EditorLoadingAndSavingUtils.save_map(world, "/Game/Path/Map")
```

`LevelEditorSubsystem.save_current_level()` always returns `False` — do not use it (trap 32).

## 5. Cross-project asset movement

Two different operations, often confused:

| Goal | Method |
|---|---|
| Byte-exact file transport, references may break | copy the `.uasset` files on disk |
| References must survive | **editor migration** — `AssetTools.migrate_assets` with `bSavingDependencies=True` |

File copying cannot rewrite internal package paths. A `.uasset` records its own package path in its
header, so renaming the file on disk makes the name disagree with the header and the asset will not
load (`does not match the name on disk`). Renaming is only safe inside the editor.

For file-level copying, `robocopy` is more reliable than `Copy-Item`, which fails with
`Cannot copy container to existing leaf item` when the destination tree does not exist yet. Verify with
count + total bytes + per-file SHA256, and confirm every file's basename still equals its internal
package name.

## 6. C++ bridge — last resort

Only for what Python genuinely cannot reach (one observed case: connecting a node to
`MP_PixelDepthOffset`, missing from the Python `MaterialProperty` enum). Build a **project plugin**,
not engine code:

```json
{ "FileVersion": 3, "Version": 1, "FriendlyName": "...", "Category": "Other",
  "CanContainContent": false, "Installed": false,
  "Modules": [{ "Name": "<Name>", "Type": "Editor", "LoadingPhase": "Default" }] }
```

```csharp
PrivateDependencyModuleNames.AddRange(new string[] {
    "Core", "CoreUObject", "Engine", "MaterialEditor", "RenderCore",
    "EditorScriptingUtilities",   // required for EditorAssetLibrary.h
});
```

```powershell
& "<UE>\Engine\Build\BatchFiles\Build.bat" <Project>Editor Win64 Development `
    -Project="<abs>.uproject" -WaitMutex -FromMsBuild
```

- `Unable to build while Live Coding is active` → a leftover `UnrealEditor` / `LiveCodingConsole`
  process. Kill it, wait, rebuild.
- Expose behaviour as an `FAutoConsoleCommandWithWorldAndArgs` and call it with
  `unreal.SystemLibrary.execute_console_command(None, "cmd args")`.
- **Keep C++ handlers additive.** One that performed graph mutation with `DeleteMaterialExpression`
  crashed when invoked this way.
