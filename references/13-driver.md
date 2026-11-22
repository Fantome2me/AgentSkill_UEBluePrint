# Driving the editor

Blueprint work happens in a script that the editor runs. Getting the launch right is not optional
polish — most "the script did nothing" reports are launch problems.

## 1. The command

```powershell
& "<UE>\Engine\Binaries\Win64\UnrealEditor-Cmd.exe" "<abs>.uproject" `
    -run=pythonscript -script="<abs>.py" `
    -unattended -nopause -nosplash -NoLogTimes -NoSound -NullRHI
```

`scripts/run_one.py` builds exactly this, resolving the editor and project automatically:

```powershell
python <skill>/scripts/run_one.py my_edit.py --uproject "<abs>.uproject"
```

## 2. Every flag, and why

| Flag | Why it is there |
|---|---|
| `-unattended` | **Mandatory.** Without it, once the project contains a C++ module the editor starts **Live Coding** and blocks forever on `LogLiveCoding: Waiting for server`. |
| `-nopause` | Otherwise a failure waits for a keypress that never comes. |
| `-nosplash` | Keeps startup output clean. |
| `-NoLogTimes` | Makes the log easier to diff between runs. |
| `-NoSound` | Audio init is irrelevant and can fail on a headless/odd device. |
| `-NullRHI` | Skips GPU init entirely. Fine for asset and graph work, and it avoids the memory blow-up of a real D3D12 init. **Rendering is impossible this way** — see `20-rules.md` R8. |

Two counter-intuitive ones:

- **Do not add `-stdout`.** It floods the console and buries the signal.
- **Do not trust exit code 0.** It means "the commandlet ran", not "my script worked". The real signal
  is `LogPython: Error: Traceback` and `Failure - N error(s)` in the log.

## 3. Two launch modes

| Mode | Argument | Use when |
|---|---|---|
| Commandlet (**default**) | `-run=pythonscript -script=<abs.py>` | asset and graph work. No GUI, predictable, self-quitting. |
| Full editor | `-ExecutePythonScript=<abs.py>` | you genuinely need an editor world. Slower, needs the editor free. |

`-ExecCmds` does **not** fire in a `-run=pythonscript` commandlet. If you need to trigger a console
command, do it from inside the script:

```python
unreal.SystemLibrary.execute_console_command(None, "my.command arg")
```

## 4. The report file is the result, not stdout

The script writes its findings to a file; you read the file. Console output gets truncated, reordered,
and interleaved with engine noise.

```python
try:
    main()
except Exception:
    import traceback
    w("FATAL:")
    for line in traceback.format_exc().splitlines():
        w(line)
emit()          # write the report NO MATTER WHAT
```

A script that raises before `emit()` leaves output that is indistinguishable from "the script never
ran". This single pattern removes that whole failure mode. See `12-templates.md` T8.

## 5. Pre-flight checklist

1. `Get-Process UnrealEditor, UnrealEditor-Cmd, LiveCodingConsole` → must be empty.
2. Every asset you will touch is backed up, with size + SHA256 recorded.
3. The target file opens for write (`[IO.File]::Open($p,'Open','ReadWrite','None')`).
4. The `.uproject` path is absolute; the script path is absolute.
5. Node ids, function paths and pin names come from a **probe run**, never from memory.
6. Enough free commit memory to start an editor — a UE 5.8 editor with a real RHI can need several GB,
   and running two editors on one project also corrupts the Asset Registry.

## 6. Diagnosing "the script reported success but nothing changed"

Work down this list; each item has a specific signature.

| Check | Signature of the problem |
|---|---|
| Did the disk hash change? | hash identical → nothing was written. See R1. |
| Was the editor closed? | `Error moving file ... (Error Code 32)`, `LogSavePackage: Error: Error saving` after 10 retries. |
| Did a **stale report** get read? | PowerShell `&` returned before the editor finished. Use `subprocess.run`. |
| Did the script raise before emitting? | no report file at all. |
| Was exit code 0 treated as success? | grep the log for `Traceback`. |
| Was the write a silent no-op? | `create_node_from_name` returned `None`; `set_pin_value` returned `False`; `try_create_connection` returned `False`. |
| Did the graph name collide? | a `_0` suffixed graph exists and got the edit instead. |
| Was the editor's in-memory copy saved over yours? | file changed, then changed back. Check mtime too. |

## 7. Long runs

A cold commandlet start on a large project takes tens of seconds to minutes (derived-data-cache
validation alone can take minutes). Set `--timeout` generously and read the report rather than assuming
a hang. If the timeout does fire, `run_one.py` says `TIMEOUT` and still prints whatever stdout it
captured.

## 8. Memory, and why an editor sometimes dies during startup

An observed failure mode on this class of machine: the editor exits with an access violation while
loading modules, the minidump is 0 bytes, and the log simply stops. When that happens, check the
system commit charge before blaming the project:

- a UE 5.8 editor with D3D12, ray tracing, Substrate and Virtual Shadow Maps can want many GB
- a second editor on the same machine roughly doubles that
- the symptom is `Failed to load '...dll' (GetLastError=1455)` — `1455` is `ERROR_COMMITMENT_LIMIT`

`GetLastError=1455` in the log is the definitive tell. Free memory or let the pagefile grow, then
retry; nothing about the project is wrong.
