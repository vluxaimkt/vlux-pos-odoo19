r"""Let Odoo browser tests (HttpCase / tours) drive Microsoft Edge on Windows.

Odoo's test runner asks the browser for an ephemeral DevTools port
(``--remote-debugging-port=0``) and reads the real port back from the
``DevToolsActivePort`` file Chrome writes in the profile directory. Edge
exposes DevTools but does not write that file, so this wrapper picks a free
port, launches Edge, waits until ``/json/version`` answers and writes the file
itself. All Edge processes are attached to a job object so they die with the
wrapper when Odoo terminates it.

Use it through the .cmd next to it::

    set VLUX_PYTHON=C:\Odoo\venv\Scripts\python.exe
    set ODOO_BROWSER_BIN=C:\Odoo\custom_addons\tools\dev\edge_devtools_wrapper.cmd

Windows local development only; CI uses Chromium on Linux.
"""
from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
STILL_ACTIVE = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


def find_edge() -> str:
    override = os.environ.get("VLUX_EDGE_BIN")
    for candidate in ((override,) if override else ()) + EDGE_CANDIDATES:
        if candidate and os.path.exists(candidate):
            return candidate
    raise SystemExit("msedge.exe not found; set VLUX_EDGE_BIN")


def switch_value(args: list[str], name: str) -> str:
    for arg in args:
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return ""


def free_port() -> str:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return str(probe.getsockname()[1])


def kill_on_close_job():
    """Job object so that every Edge process dies together with this wrapper."""
    kernel32 = ctypes.windll.kernel32
    job = kernel32.CreateJobObjectW(None, None)

    class BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimit),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    info = ExtendedLimit()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    kernel32.SetInformationJobObject(
        job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
    )
    return job, kernel32


def parent_alive(kernel32, pid: int) -> bool:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle == 0:
        return False
    exit_code = ctypes.c_ulong()
    kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
    kernel32.CloseHandle(handle)
    return exit_code.value == STILL_ACTIVE


def main() -> int:
    args = sys.argv[1:]
    port = switch_value(args, "--remote-debugging-port")
    profile = switch_value(args, "--user-data-dir")
    if not port or not profile:
        raise SystemExit("expected --remote-debugging-port and --user-data-dir")
    if port == "0":
        port = free_port()
        args = [a for a in args if not a.startswith("--remote-debugging-port=")]
        args.append(f"--remote-debugging-port={port}")

    job, kernel32 = kill_on_close_job()
    process = subprocess.Popen([find_edge(), "--do-not-de-elevate", *args], stderr=subprocess.DEVNULL)
    kernel32.AssignProcessToJobObject(job, int(process._handle))

    def devtools_ready() -> str:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as response:
                return json.load(response)["webSocketDebuggerUrl"]
        except Exception:  # noqa: BLE001 - browser not ready / gone
            return ""

    browser_ws = ""
    deadline = time.time() + 60
    while time.time() < deadline and not browser_ws:
        browser_ws = devtools_ready()
        if not browser_ws:
            time.sleep(0.25)
    if not browser_ws:
        kernel32.TerminateJobObject(job, 1)
        raise SystemExit("Edge did not expose DevTools in time")
    path = browser_ws.split(f":{port}", 1)[1]
    with open(os.path.join(profile, "DevToolsActivePort"), "w", encoding="utf-8") as stream:
        stream.write(port + "\n" + path + "\n")

    # The Edge launcher process may hand off to a browser process and exit, so
    # liveness is tracked through DevTools rather than through the launcher pid.
    # Odoo holds the pid of the cmd.exe running this script (os.getppid() is not
    # reliable for .cmd launches), so that pid is located by its command line.
    # When Odoo stops the browser it terminates that pid and removes the
    # profile directory; either signal ends this wrapper.
    launcher_pid = find_launcher_pid(profile)
    port_file = os.path.join(profile, "DevToolsActivePort")
    misses = 0
    while True:
        time.sleep(0.5)
        if launcher_pid and not parent_alive(kernel32, launcher_pid):
            break
        if not os.path.exists(port_file):
            break
        misses = 0 if devtools_ready() else misses + 1
        if misses >= 6:
            break
    kernel32.TerminateJobObject(job, 0)
    kill_profile_processes(profile)
    return 0


def find_launcher_pid(profile: str) -> int:
    needle = profile.replace("'", "''")
    script = (
        "(Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{needle}*' }} | "
        "Select-Object -First 1 -ExpandProperty ProcessId)"
    )
    try:
        output = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout.strip()
        return int(output) if output.isdigit() else 0
    except (subprocess.SubprocessError, ValueError):
        return 0


def kill_profile_processes(profile: str) -> None:
    """Edge may re-spawn its browser process outside the job; the profile path
    is unique per test run, so anything still referencing it is ours."""
    needle = profile.replace("'", "''")
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{needle}*' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
