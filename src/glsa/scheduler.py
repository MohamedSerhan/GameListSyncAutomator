"""Windows Task Scheduler integration for automated daily sync."""

import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

TASK_NAME = "GLSA_DailySyncBackloggdToSteam"


def _get_glsa_path() -> str:
    """Get the path to the glsa executable."""
    # The glsa.exe is in the same directory as python.exe in the venv
    venv_scripts = Path(sys.executable).parent
    glsa_exe = venv_scripts / "glsa.exe"
    if glsa_exe.exists():
        return str(glsa_exe)
    # Fallback: run as python module
    return f'"{sys.executable}" -m glsa'


def _build_batch_script() -> Path:
    """Create a batch script that runs both syncs. Returns the script path."""
    glsa = _get_glsa_path()
    script_path = Path.home() / ".glsa" / "sync.bat"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        f'@echo off\r\n'
        f'{glsa} sync --force\r\n'
        f'{glsa} sync-backloggd --force\r\n',
        encoding="utf-8",
    )
    return script_path


def create_scheduled_task(time: str = "09:00") -> tuple[bool, str]:
    """Create a Windows Scheduled Task for daily sync.

    Args:
        time: Time to run daily in HH:MM format (24-hour).

    Returns:
        (success, message) tuple.
    """
    script_path = _build_batch_script()

    # Build task XML for full control over settings
    xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>GLSA daily sync: Backloggd wishlist to Steam and back</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2024-01-01T{time}:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
  </Settings>
  <Actions>
    <Exec>
      <Command>{script_path}</Command>
    </Exec>
  </Actions>
</Task>"""

    # Write XML to temp file
    xml_path = Path.home() / ".glsa" / "task.xml"
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    xml_path.write_text(xml, encoding="utf-16")

    try:
        result = subprocess.run(
            ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"],
            capture_output=True,
            text=True,
        )
        xml_path.unlink(missing_ok=True)

        if result.returncode == 0:
            return True, f"Scheduled task '{TASK_NAME}' created (daily at {time})"
        else:
            return False, f"Failed to create task: {result.stderr.strip()}"
    except FileNotFoundError:
        xml_path.unlink(missing_ok=True)
        return False, "schtasks not found -- this command only works on Windows"


def delete_scheduled_task() -> tuple[bool, str]:
    """Delete the GLSA scheduled task.

    Returns:
        (success, message) tuple.
    """
    try:
        result = subprocess.run(
            ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True, f"Scheduled task '{TASK_NAME}' deleted"
        elif "cannot find" in result.stderr.lower() or "does not exist" in result.stderr.lower():
            return False, f"No scheduled task '{TASK_NAME}' found"
        else:
            return False, f"Failed to delete task: {result.stderr.strip()}"
    except FileNotFoundError:
        return False, "schtasks not found -- this command only works on Windows"


def get_task_status() -> tuple[bool, str]:
    """Check if the GLSA scheduled task exists and its status.

    Returns:
        (exists, info) tuple.
    """
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            # Parse out useful fields
            lines = result.stdout.strip().split("\n")
            info = {}
            for line in lines:
                if ":" in line:
                    key, _, val = line.partition(":")
                    key = key.strip()
                    val = val.strip()
                    if key in ("Status", "Next Run Time", "Last Run Time", "Last Result"):
                        info[key] = val
            return True, info
        else:
            return False, {}
    except FileNotFoundError:
        return False, {}
