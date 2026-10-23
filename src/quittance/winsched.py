"""Tâche planifiée Windows : lance `--run-due` chaque jour à l'heure choisie et à l'ouverture de session.

StartWhenAvailable rattrape une exécution manquée (PC éteint à l'heure prévue).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

TASK_NAME = "Quittance Express - envoi automatique"
CREATE_NO_WINDOW = 0x08000000


def available() -> bool:
    return sys.platform == "win32"


def command() -> tuple[str, str]:
    """(programme, arguments) à lancer par la tâche."""
    if getattr(sys, "frozen", False):
        return sys.executable, "--run-due"
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return str(pyw if pyw.exists() else exe), "-m quittance --run-due"


def current_user() -> str:
    dom, user = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    if user:
        return f"{dom}\\{user}" if dom else user
    r = subprocess.run(["whoami"], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    return r.stdout.strip()


def _xml(hour: int, minute: int) -> str:
    prog, args = command()
    user = escape(current_user())
    start = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Envoie les quittances de loyer dues (baux avec envoi automatique activé).</Description>
    <Author>Quittance Express</Author>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
      <Delay>PT3M</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT15M</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(prog)}</Command>
      <Arguments>{escape(args)}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)


def install(hour: int = 9, minute: int = 0) -> None:
    if not available():
        raise RuntimeError("La planification automatique n’est disponible que sous Windows.")
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as f:
        f.write(_xml(hour, minute))
        tmp = f.name
    try:
        r = _run(["/Create", "/TN", TASK_NAME, "/XML", tmp, "/F"])
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip() or "Création de la tâche impossible.")
    finally:
        Path(tmp).unlink(missing_ok=True)


def uninstall() -> None:
    if available():
        _run(["/Delete", "/TN", TASK_NAME, "/F"])


def status() -> dict:
    if not available():
        return {"available": False, "installed": False}
    ps = ("$t = Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue; if (-not $t) { 'null' } else { "
          "$i = $t | Get-ScheduledTaskInfo; [pscustomobject]@{ state = [string]$t.State; "
          "next = $(if ($i.NextRunTime) { $i.NextRunTime.ToString('dd/MM/yyyy HH:mm') } else { '' }); "
          "last = $(if ($i.LastRunTime -and $i.LastRunTime.Year -gt 2000) { $i.LastRunTime.ToString('dd/MM/yyyy HH:mm') } else { '' }); "
          "result = $i.LastTaskResult } | ConvertTo-Json -Compress }") % TASK_NAME.replace("'", "''")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
    try:
        info = json.loads(r.stdout.strip() or "null")
    except ValueError:
        info = None
    if not info:
        return {"available": True, "installed": False}
    return {"available": True, "installed": True, "state": info.get("state", ""), "next_run": info.get("next", ""),
            "last_run": info.get("last", ""), "last_result": info.get("result")}
