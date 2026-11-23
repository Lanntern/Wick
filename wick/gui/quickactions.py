"""Finder's right-click menu on a Mac: "Compress with Wick" and "Convert with Wick…" as Quick Actions.

macOS adds a Quick Action for each Automator workflow in ~/Library/Services. Each one Wick writes runs a shell
command with the selected files as its arguments, which starts Wick (or hands them to the Wick that's running,
like Explorer's menu does on Windows). Removing them deletes the two workflows.
"""

from __future__ import annotations

import plistlib
import shlex
import shutil
import subprocess
import uuid
from pathlib import Path

ITEMS = {"Compress with Wick": [], "Convert with Wick…": ["--convert", "choose"]}


def services_folder() -> Path:
    return Path.home() / "Library" / "Services"


def workflow_paths() -> list[Path]:
    return [services_folder() / f"{name}.workflow" for name in ITEMS]


def installed() -> bool:
    return all(p.is_dir() for p in workflow_paths())


def _document(command: str) -> dict:
    """An Automator Quick Action: Run Shell Script with Finder's selected files as arguments."""
    return {
        "AMApplicationBuild": "523", "AMApplicationVersion": "2.10", "AMDocumentVersion": "2",
        "actions": [{"action": {
            "AMAccepts": {"Container": "List", "Optional": True, "Types": ["com.apple.cocoa.string"]},
            "AMActionVersion": "2.0.3", "AMApplication": ["Automator"], "AMParameterProperties": {
                "COMMAND_STRING": {}, "CheckedForUserDefaultShell": {}, "inputMethod": {}, "shell": {},
                "source": {}},
            "AMProvides": {"Container": "List", "Types": ["com.apple.cocoa.string"]},
            "ActionBundlePath": "/System/Library/Automator/Run Shell Script.action",
            "ActionName": "Run Shell Script",
            "ActionParameters": {"COMMAND_STRING": command, "CheckedForUserDefaultShell": True,
                                 "inputMethod": 1, "shell": "/bin/bash", "source": ""},
            "BundleIdentifier": "com.apple.RunShellScript", "CFBundleVersion": "2.0.3",
            "CanShowSelectedItemsWhenRun": False, "CanShowWhenRun": True, "Category": ["AMCategoryUtilities"],
            "Class Name": "RunShellScriptAction", "InputUUID": str(uuid.uuid4()).upper(), "Keywords": ["Shell"],
            "OutputUUID": str(uuid.uuid4()).upper(), "UUID": str(uuid.uuid4()).upper(),
            "UnlocalizedApplications": ["Automator"], "arguments": {}, "isViewVisible": 1, "location": "309:316",
            "nibPath": "/System/Library/Automator/Run Shell Script.action/Contents/Resources/Base.lproj/main.nib"},
            "isViewVisible": 1}],
        "connectors": {},
        "workflowMetaData": {
            "applicationBundleIDsByPath": {}, "applicationPaths": [], "inputTypeIdentifier":
                "com.apple.Automator.fileSystemObject", "outputTypeIdentifier": "com.apple.Automator.nothing",
            "presentationMode": 15, "processesInput": False,
            "serviceApplicationBundleID": "com.apple.finder", "serviceApplicationPath": "/System/Library/CoreServices/"
                                                                                      "Finder.app",
            "serviceInputTypeIdentifier": "com.apple.Automator.fileSystemObject",
            "serviceOutputTypeIdentifier": "com.apple.Automator.nothing", "serviceProcessesInput": False,
            "systemImageName": "NSActionTemplate", "useAutomaticInputType": False,
            "workflowTypeIdentifier": "com.apple.Automator.servicesMenu"},
    }


def _info(name: str) -> dict:
    return {"NSServices": [{"NSMenuItem": {"default": name}, "NSMessage": "runWorkflowAsService",
                            "NSRequiredContext": {"NSApplicationIdentifier": "com.apple.finder"},
                            "NSSendFileTypes": ["public.item"]}]}


def command_for(launch: list[str], extra: list[str]) -> str:
    """The shell line: Wick with its arguments, then Finder's selection ("$@"), in the background."""
    return " ".join(shlex.quote(a) for a in [*launch, *extra]) + ' "$@" >/dev/null 2>&1 &'


def install(launch: list[str]) -> None:
    for (name, extra), path in zip(ITEMS.items(), workflow_paths(), strict=True):
        contents = path / "Contents"
        contents.mkdir(parents=True, exist_ok=True)
        with open(contents / "document.wflow", "wb") as f:
            plistlib.dump(_document(command_for(launch, extra)), f)
        with open(contents / "Info.plist", "wb") as f:
            plistlib.dump(_info(name), f)
    refresh()


def uninstall() -> None:
    for path in workflow_paths():
        shutil.rmtree(path, ignore_errors=True)
    refresh()


def refresh() -> None:
    """Tell macOS the Services list changed (otherwise it notices at the next sign-in)."""
    try:
        subprocess.run(["/System/Library/CoreServices/pbs", "-update"], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        pass


__all__ = ["install", "uninstall", "installed", "workflow_paths", "command_for", "ITEMS"]
