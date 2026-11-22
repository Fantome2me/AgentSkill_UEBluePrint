#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Run a Python script inside the Unreal editor and return its report file.

WHY PYTHON subprocess AND NOT POWERSHELL
    PowerShell's `&` operator returns as soon as a GUI process is launched - it does not wait. With
    `&` you read the report before the editor has written it, and conclude "nothing happened".
    subprocess.run blocks until the editor exits.

EDITOR / PROJECT RESOLUTION (in order)
    1. --exe / --uproject command-line arguments
    2. UE_EDITOR_EXE / UE_UPROJECT environment variables
    3. the single .uproject found by walking up from the current directory, plus the engine that
       the registry or a UE_ROOT / UE_PATH environment variable points at

USAGE
    python run_one.py <script.py> [--uproject <abs.uproject>] [--exe <UnrealEditor-Cmd.exe>]
                                  [--report <file>] [--timeout 900] [--keep-editors]
                                  [--mode pythonscript|exec]

MODES
    pythonscript (default)  -run=pythonscript -script=<abs.py>   -- commandlet, no GUI, robust
    exec                    -ExecutePythonScript=<abs.py>        -- full editor, needed when the
                                                                   task requires an editor world

The target script must write its own report file and must let the editor quit.
"""

import argparse
import glob
import os
import subprocess
import sys
import time

ENV_EXE = "UE_EDITOR_EXE"
ENV_PROJECT = "UE_UPROJECT"
DEFAULT_TIMEOUT = 900


def find_uproject(start):
    """Walk up from `start` looking for exactly one .uproject."""
    cur = os.path.abspath(start)
    while True:
        hits = glob.glob(os.path.join(cur, "*.uproject"))
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            print("!! several .uproject files in %s; pass --uproject explicitly" % cur)
            return None
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def find_editor(explicit):
    """Locate UnrealEditor-Cmd.exe from an explicit path, env vars, or common install roots."""
    if explicit:
        return explicit if os.path.isfile(explicit) else None

    for var in ("UE_EDITOR_EXE", "UE_ROOT", "UE_PATH"):
        val = os.environ.get(var)
        if not val:
            continue
        cand = val
        if not cand.lower().endswith(".exe"):
            cand = os.path.join(cand, "Engine", "Binaries", "Win64", "UnrealEditor-Cmd.exe")
        if os.path.isfile(cand):
            return cand

    patterns = [
        r"D:\*UE_5*\Engine\Binaries\Win64\UnrealEditor-Cmd.exe",
        r"C:\Program Files\Epic Games\UE_5*\Engine\Binaries\Win64\UnrealEditor-Cmd.exe",
        r"D:\Program Files\Epic Games\UE_5*\Engine\Binaries\Win64\UnrealEditor-Cmd.exe",
        r"E:\*UE_5*\Engine\Binaries\Win64\UnrealEditor-Cmd.exe",
    ]
    found = []
    for pat in patterns:
        found.extend(glob.glob(pat))
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        print("!! several engines found; pass --exe explicitly:")
        for f in found:
            print("     " + f)
        return None
    return None


def kill_editors():
    """A leftover editor holds the file locks and the Live Coding lock."""
    for name in ("UnrealEditor", "UnrealEditor-Cmd", "LiveCodingConsole"):
        subprocess.run(["taskkill", "/F", "/IM", name + ".exe"],
                       capture_output=True, text=True)
    time.sleep(2)


def default_report(script_path):
    base = os.path.splitext(os.path.basename(script_path))[0]
    return os.path.join(os.path.dirname(os.path.abspath(script_path)), base + ".report.txt")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--report", default=None)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--keep-editors", action="store_true",
                    help="do not kill running editors first (default: kill)")
    ap.add_argument("--exe", default=None)
    ap.add_argument("--uproject", default=None)
    ap.add_argument("--mode", choices=("pythonscript", "exec"), default="pythonscript")
    ap.add_argument("--extra", nargs="*", default=[],
                    help="extra editor arguments, e.g. --extra -NullRHI")
    args = ap.parse_args()

    script = os.path.abspath(args.script)
    if not os.path.isfile(script):
        print("no such script: " + script)
        return 2

    exe = find_editor(args.exe or os.environ.get(ENV_EXE))
    if not exe:
        print("!! could not locate UnrealEditor-Cmd.exe; pass --exe or set " + ENV_EXE)
        return 2

    uproject = args.uproject or os.environ.get(ENV_PROJECT) or find_uproject(os.getcwd())
    if not uproject or not os.path.isfile(uproject):
        print("!! could not locate a .uproject; pass --uproject or set " + ENV_PROJECT)
        return 2

    report = args.report or default_report(script)
    if os.path.isfile(report):
        os.remove(report)          # so a stale report cannot be mistaken for a fresh result

    if not args.keep_editors:
        kill_editors()

    # -unattended is mandatory: without it, a project containing a C++ module starts Live Coding and
    # blocks forever on "LogLiveCoding: Waiting for server".
    if args.mode == "pythonscript":
        cmd = [exe, uproject, "-run=pythonscript", "-script=" + script]
    else:
        cmd = [exe, uproject, "-ExecutePythonScript=" + script]
    cmd += ["-unattended", "-nopause", "-nosplash", "-NoLogTimes", "-NoSound", "-NullRHI"]
    cmd += list(args.extra)

    print("=" * 78)
    print("exe      = %s" % exe)
    print("uproject = %s" % uproject)
    print("script   = %s" % script)
    print("report   = %s" % report)
    print("=" * 78)

    t0 = time.time()
    timed_out = False
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout)
        code = proc.returncode
        out = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        code = -1
        raw = exc.stdout or ""
        out = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
        print("TIMEOUT after %ds" % args.timeout)

    print("exit    = %s" % code)
    print("elapsed = %.0fs" % (time.time() - t0))

    # exit code 0 means "the commandlet ran", NOT "the script worked"   (trap 49)
    low = out.lower()
    if "traceback" in low or "logpython: error" in low:
        print("!! the script raised - see the traceback below even if exit was 0")

    print("--- stdout tail ---")
    print("\n".join(out.splitlines()[-40:]))

    print("")
    if os.path.isfile(report):
        print("--- report: %s ---" % report)
        with open(report, "r", encoding="utf-8", errors="replace") as fh:
            print(fh.read())
    else:
        print("!! report not written: %s" % report)
        print("   the script probably raised before emitting, or returned early without saving")

    return 1 if (timed_out or code != 0) else 0


if __name__ == "__main__":
    sys.exit(main())
