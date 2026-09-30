"""Removing a project: from bullpen only (its chat history stays, and adding the
folder again brings it back), or with its folder moved to the Trash, where it
can be restored. Nothing here deletes files for good."""
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from .store import StoreError, write_json

MAX_COUNT = 200_000  # files counted before the size shows as "at least"


def forget(store, pid):
    """Take the project out of the list; its data folder stays."""
    with store.changed:
        projects = store.projects()
        if not any(p["id"] == pid for p in projects):
            raise StoreError(404, "no such project: %s" % pid)
        write_json(store.root / "projects.json", {"projects": [p for p in projects if p["id"] != pid]})


def _refuse(store, path):
    """Why path must not go to the Trash, or None."""
    home, path = Path.home().resolve(), Path(path).resolve()
    if path in (home, Path(path.anchor)) or path in home.parents:
        return "that is your home folder or above it"
    here = Path(__file__).resolve().parent.parent
    for keep in (here, store.root.resolve()):
        if keep == path or path in keep.parents:
            return "bullpen itself lives there"
    for p in store.projects():
        other = Path(p["path"]).resolve()
        if other != path and path in other.parents:
            return "it holds another project (%s)" % p["name"]
    return None


def info(store, pid):
    """What moving the folder to the Trash would take: for the warning."""
    path = Path(store.project(pid)["path"])
    files = size = 0
    for root, dirs, names in os.walk(path):
        for n in names:
            files += 1
            try:
                size += os.lstat(os.path.join(root, n)).st_size
            except OSError:
                pass
        if files >= MAX_COUNT:
            break
    changes = None
    if (path / ".git").exists() and shutil.which("git"):
        r = subprocess.run(["git", "-C", str(path), "status", "--porcelain"], capture_output=True,
                           text=True, timeout=20)
        changes = len(r.stdout.splitlines()) if r.returncode == 0 else None
    return {"path": str(path), "exists": path.is_dir(), "files": files, "more": files >= MAX_COUNT,
            "size": size, "git_changes": changes, "refused": _refuse(store, path)}


def to_trash(path):
    """Move a folder to the desktop's Trash (freedesktop on Linux, the Recycle
    Bin on Windows, ~/.Trash on macOS)."""
    path = Path(path).resolve()
    if os.name == "nt":
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]"
                            "::DeleteDirectory($args[0], 'OnlyErrorDialogs', 'SendToRecycleBin')", str(path)],
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise StoreError(500, "the Recycle Bin refused it: %s" % r.stderr.strip())
        return
    if os.uname().sysname == "Darwin":
        trash, info_dir = Path.home() / ".Trash", None
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "Trash"
        trash, info_dir = base / "files", base / "info"
        info_dir.mkdir(parents=True, exist_ok=True)
    trash.mkdir(parents=True, exist_ok=True)
    name, n = path.name, 1
    while (trash / name).exists() or (info_dir and (info_dir / (name + ".trashinfo")).exists()):
        n += 1
        name = "%s.%d" % (path.name, n)
    if info_dir:  # written first, as the spec asks, so the Trash can always restore it
        (info_dir / (name + ".trashinfo")).write_text(
            "[Trash Info]\nPath=%s\nDeletionDate=%s\n" % (quote(str(path)), datetime.now().strftime("%Y-%m-%dT%H:%M:%S")))
    try:
        os.rename(path, trash / name)  # same disk only: never a copy-and-delete of a big tree
    except OSError as e:
        if info_dir:
            (info_dir / (name + ".trashinfo")).unlink(missing_ok=True)
        raise StoreError(409, "could not move it to the Trash (%s); nothing was deleted" % e.strerror) from None


def trash(store, pid, confirm):
    """Move the project's folder to the Trash and take it out of bullpen.
    confirm must be the project's name, typed by the user."""
    project = store.project(pid)
    if confirm != project["name"]:
        raise StoreError(400, "type the project's name to confirm")
    why = _refuse(store, project["path"])
    if why:
        raise StoreError(403, "not moved to the Trash: " + why)
    if Path(project["path"]).is_dir():
        to_trash(project["path"])
    forget(store, pid)
    return project
