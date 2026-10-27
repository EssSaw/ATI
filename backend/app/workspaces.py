"""Local workspace management and safe command approval service for ATI."""

import hashlib
import os
import re
import secrets
import selectors
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .project_chat import (
    BINARY_EXTENSIONS,
    EXCLUDED_DIR_NAMES,
    LOCKFILE_NAMES,
    SECRET_EXTENSIONS,
    SECRET_FILENAMES,
    is_excluded_file_path,
    is_safe_relative_posix_path,
)

# Limits and TTL settings
WORKSPACE_TTL_SECONDS = 7200  # 2 hours
MAX_WORKSPACES = 50
COMMAND_APPROVAL_TTL_SECONDS = 300  # 5 minutes
MAX_FILES = 1000
MAX_FILE_BYTES = 30000  # 30k characters / bytes
MAX_TOTAL_BYTES = 200000  # Proposal write budget
MAX_WORKSPACE_BYTES = 10_000_000  # Live readable files, separate from model context
MAX_CHANGES_BATCH = 20
COMMAND_TIMEOUT_SECONDS = 30.0
COMMAND_MAX_OUTPUT_BYTES = 65536  # 64 KB

# Allowed text extensions (broad coverage for modern web & backend development)
ALLOWED_EXTENSIONS: Set[str] = {
    "py", "pyi", "js", "jsx", "ts", "tsx", "mjs", "cjs", "json", "html", "css",
    "scss", "sass", "less", "md", "mdx", "txt", "rst", "toml", "yaml", "yml",
    "xml", "csv", "sql", "sh", "bash", "zsh", "c", "h", "cpp", "hpp", "cs",
    "fs", "fsx", "csproj", "fsproj", "sln", "props", "targets", "java", "go",
    "rs", "rb", "erb", "rake", "gemspec", "php", "swift", "kt", "kts", "gradle",
    "dart", "ex", "exs", "erl", "hrl", "vue", "svelte", "astro", "graphql", "gql",
    "proto", "ini", "cfg", "conf", "gitignore", "dockerfile", "mod", "sum",
}

ALLOWED_SPECIAL_FILENAMES: Set[str] = {
    "dockerfile", "makefile", "gemfile", "rakefile", "procfile", ".gitignore",
    "go.mod", "go.sum", "license", "authors", "contributors",
}

# Permitted executables for command approval
ALLOWED_EXECUTABLES: Set[str] = {
    "npm", "pnpm", "yarn", "bun",
    "python", "python3", "pytest", "uv", "go", "cargo", "dotnet",
    "gradle", "gradlew", "./gradlew", "mvn", "ruby", "bundle",
    "composer", "php", "flutter", "dart", "make",
}

DISALLOWED_INTERPRETERS: Set[str] = {
    "sh", "bash", "zsh", "csh", "tcsh", "fish", "ksh",
    "powershell", "powershell.exe", "cmd", "cmd.exe",
}

DISALLOWED_OPERATIONS: Set[str] = {
    "install", "i", "add", "remove", "uninstall", "deploy", "publish", "delete", "prune", "clean",
}


# Pydantic Request & Response Models
class OpenWorkspaceRequest(BaseModel):
    path: str


class SnapshotWorkspaceRequest(BaseModel):
    workspace_id: str


class WorkspaceChange(BaseModel):
    path: str
    operation: Literal["create", "replace", "delete", "rename"]
    content: Optional[str] = None
    target: Optional[str] = None


class ApplyWorkspaceRequest(BaseModel):
    workspace_id: str
    revision: str
    changes: List[WorkspaceChange]


class PrepareCommandRequest(BaseModel):
    workspace_id: str
    argv: List[str]


class RunCommandRequest(BaseModel):
    workspace_id: str
    approval_id: str
    approved: bool


class FileItem(BaseModel):
    path: str
    content: str


class SkippedItem(BaseModel):
    path: str
    reason: str


class WorkspaceSnapshotResponse(BaseModel):
    workspace_id: str
    name: str
    path: str
    files: List[FileItem]
    skipped: List[SkippedItem]
    revision: str


class PrepareCommandResponse(BaseModel):
    approval_id: str
    argv: List[str]
    cwd: str
    warning: str


class RunCommandResponse(BaseModel):
    argv: List[str]
    cwd: str
    exit_code: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool


# Internal In-Memory Workspace Record
class WorkspaceRecord:
    def __init__(
        self,
        workspace_id: str,
        path: str,
        name: str,
        files: List[Dict[str, str]],
        skipped: List[Dict[str, str]],
        revision: str,
    ):
        self.workspace_id = workspace_id
        self.path = path
        self.name = name
        self.files = files
        self.skipped = skipped
        self.revision = revision
        self.created_at = time.time()
        self.last_accessed_at = time.time()
        self.lock = threading.Lock()

    def touch(self) -> None:
        self.last_accessed_at = time.time()

    def snapshot_dict(self) -> Dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "name": self.name,
            "path": self.path,
            "files": self.files,
            "skipped": self.skipped,
            "revision": self.revision,
        }


# Command Approval Record
class CommandApprovalRecord:
    def __init__(self, approval_id: str, workspace_id: str, argv: List[str], cwd: str):
        self.approval_id = approval_id
        self.workspace_id = workspace_id
        self.argv = list(argv)
        self.cwd = cwd
        self.created_at = time.time()
        self.used = False


# Registries & Locks
WORKSPACES: Dict[str, WorkspaceRecord] = {}
WORKSPACES_LOCK = threading.Lock()

COMMAND_APPROVALS: Dict[str, CommandApprovalRecord] = {}
COMMAND_APPROVALS_LOCK = threading.Lock()


def _cleanup_workspaces() -> None:
    """Evict expired or overflowing workspace records."""
    now = time.time()
    expired = [wid for wid, ws in WORKSPACES.items() if now - ws.last_accessed_at > WORKSPACE_TTL_SECONDS]
    for wid in expired:
        WORKSPACES.pop(wid, None)
    if len(WORKSPACES) >= MAX_WORKSPACES:
        sorted_by_lru = sorted(WORKSPACES.items(), key=lambda item: item[1].last_accessed_at)
        to_evict = len(WORKSPACES) - MAX_WORKSPACES + 1
        for wid, _ in sorted_by_lru[:to_evict]:
            WORKSPACES.pop(wid, None)


def _cleanup_approvals() -> None:
    """Evict expired command approval tokens."""
    now = time.time()
    expired = [aid for aid, rec in COMMAND_APPROVALS.items() if now - rec.created_at > COMMAND_APPROVAL_TTL_SECONDS]
    for aid in expired:
        COMMAND_APPROVALS.pop(aid, None)


# Validation Helpers
def validate_workspace_root(path_str: str) -> Path:
    """Validate root workspace path against strict capability and security requirements."""
    if not isinstance(path_str, str) or not path_str.strip():
        raise HTTPException(status_code=400, detail="Workspace path must be a non-empty string.")

    if "\x00" in path_str:
        raise HTTPException(status_code=400, detail="Invalid characters in path.")

    if not os.path.isabs(path_str):
        raise HTTPException(status_code=400, detail="Workspace path must be an absolute path.")

    # Check for traversal sequences in path components
    p = Path(path_str)
    if any(part in ("..", ".") for part in p.parts[1:]):
        raise HTTPException(status_code=400, detail="Directory traversal sequences are not permitted in workspace path.")

    if not p.exists() or not p.is_dir():
        raise HTTPException(status_code=400, detail="Workspace path does not exist or is not a directory.")

    resolved = p.resolve()
    # Reject filesystem root
    if str(resolved) in ("/", "C:\\", "C:/") or resolved.parent == resolved:
        raise HTTPException(status_code=400, detail="Opening filesystem root directory is not permitted.")

    # Reject user home directory
    home_dir = Path.home().resolve()
    if resolved == home_dir or str(resolved) == str(home_dir):
        raise HTTPException(status_code=400, detail="Opening user home directory is not permitted.")

    # Reject standard system roots
    system_dirs = {"/bin", "/sbin", "/usr", "/usr/bin", "/etc", "/var", "/private", "/proc", "/sys", "/dev"}
    if str(resolved) in system_dirs:
        raise HTTPException(status_code=400, detail="Opening system directory is not permitted.")

    # Check for symlinks in workspace path itself and any of its ancestor directories
    curr = p
    while True:
        if curr.is_symlink() or os.path.islink(str(curr)):
            raise HTTPException(status_code=400, detail="Symlinks in workspace path or its ancestors are not permitted.")
        if curr.parent == curr:
            break
        curr = curr.parent

    return resolved


def is_allowed_text_file(rel_path: str) -> Tuple[bool, str]:
    """Check if relative path qualifies as an allowed text file.

    Returns (is_allowed, skip_reason).
    """
    if not is_safe_relative_posix_path(rel_path):
        return False, "unsafe path"

    pure = PurePosixPath(rel_path)
    parts = [p.lower() for p in pure.parts]
    name = pure.name.lower()
    suffix = pure.suffix.lower().lstrip(".")

    # Excluded directories
    if any(p in EXCLUDED_DIR_NAMES for p in parts[:-1]):
        return False, "generated or private directory"
    if len(parts) == 1 and parts[0] in EXCLUDED_DIR_NAMES:
        return False, "generated or private directory"

    # Secret / binary / lockfile checks from project_chat validator
    if is_excluded_file_path(rel_path):
        if any(part.startswith(".env") or "credential" in part or "secret" in part or "token" in part for part in parts) or suffix in SECRET_EXTENSIONS or name in SECRET_FILENAMES:
            return False, "potential secret"
        if suffix in BINARY_EXTENSIONS:
            return False, "unsupported or binary file"
        if name.endswith(".lock") or name in LOCKFILE_NAMES or name == "npm-shrinkwrap.json":
            return False, "lockfile"
        return False, "potential secret or excluded file"

    # Allowed extensions or special filenames
    if suffix in ALLOWED_EXTENSIONS or name in ALLOWED_SPECIAL_FILENAMES:
        return True, ""

    return False, "unsupported or binary file"


def compute_revision_hash(files: List[Dict[str, str]]) -> str:
    """Compute deterministic revision hash over all included file paths and contents."""
    hasher = hashlib.sha256()
    for f in sorted(files, key=lambda x: x["path"]):
        hasher.update(f["path"].encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(f["content"].encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


def scan_workspace_files(workspace_root: Path) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """Scan workspace directory for allowed text files within bounds."""
    files: List[Dict[str, str]] = []
    skipped: List[Dict[str, str]] = []
    seen_paths: Set[str] = set()
    total_bytes = 0

    root_str = str(workspace_root)

    for root_dir, dirnames, filenames in os.walk(root_str, topdown=True, followlinks=False):
        dirnames.sort()
        filenames.sort()

        # Check directory symlinks and exclusions
        dirs_to_remove = []
        for d in dirnames:
            full_d = os.path.join(root_dir, d)
            rel_d = os.path.relpath(full_d, root_str).replace("\\", "/")
            if os.path.islink(full_d):
                skipped.append({"path": rel_d, "reason": "symlink not permitted"})
                dirs_to_remove.append(d)
            elif d.lower() in EXCLUDED_DIR_NAMES:
                skipped.append({"path": rel_d, "reason": "generated or private directory"})
                dirs_to_remove.append(d)
        for d in dirs_to_remove:
            dirnames.remove(d)

        # Process individual files
        for f in filenames:
            full_f = os.path.join(root_dir, f)
            rel_f = os.path.relpath(full_f, root_str).replace("\\", "/")

            if rel_f in seen_paths:
                continue
            seen_paths.add(rel_f)

            if os.path.islink(full_f):
                skipped.append({"path": rel_f, "reason": "symlink not permitted"})
                continue

            allowed, skip_reason = is_allowed_text_file(rel_f)
            if not allowed:
                skipped.append({"path": rel_f, "reason": skip_reason})
                continue

            try:
                st = os.lstat(full_f)
                if st.st_size > MAX_FILE_BYTES:
                    skipped.append({"path": rel_f, "reason": "file exceeds 30,000-byte read limit"})
                    continue
            except OSError:
                skipped.append({"path": rel_f, "reason": "could not read file"})
                continue

            if len(files) >= MAX_FILES:
                skipped.append({"path": rel_f, "reason": "1,000-file live workspace limit"})
                continue

            if total_bytes + st.st_size > MAX_WORKSPACE_BYTES:
                skipped.append({"path": rel_f, "reason": "10,000,000-byte live workspace budget limit"})
                continue

            try:
                fd = os.open(full_f, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(fd, "rb") as fp:
                    raw_data = fp.read(MAX_FILE_BYTES + 1)
            except OSError:
                skipped.append({"path": rel_f, "reason": "could not read file or symlink detected"})
                continue

            if b"\x00" in raw_data:
                skipped.append({"path": rel_f, "reason": "binary or unsupported encoding"})
                continue

            try:
                text = raw_data.decode("utf-8")
            except UnicodeDecodeError:
                skipped.append({"path": rel_f, "reason": "binary or unsupported encoding"})
                continue

            if re.search(r"[\x00-\x08\x0e-\x1f\ufffd]", text):
                skipped.append({"path": rel_f, "reason": "binary or unsupported encoding"})
                continue

            if len(text) > MAX_FILE_BYTES:
                skipped.append({"path": rel_f, "reason": "file exceeds 30,000-byte read limit"})
                continue

            if total_bytes + len(text) > MAX_WORKSPACE_BYTES:
                skipped.append({"path": rel_f, "reason": "10,000,000-byte live workspace budget limit"})
                continue

            files.append({"path": rel_f, "content": text})
            total_bytes += len(text)

    files.sort(key=lambda x: x["path"])
    return files, skipped


def is_allowed_executable(exe: str) -> bool:
    """Check if executable string is in allowed set or matches approved local paths."""
    base = os.path.basename(exe)
    if exe in ALLOWED_EXECUTABLES or base in ALLOWED_EXECUTABLES:
        return True
    if exe == sys.executable or base == os.path.basename(sys.executable):
        return True
    if re.match(r"^(\./)?(\.venv|venv)/bin/(python|python3|pytest)$", exe):
        return True
    return False


# Router definition
router = APIRouter(prefix="/agent/workspaces", tags=["workspaces"])


# Integration contract exports
def get_workspace_snapshot(workspace_id: str) -> Dict[str, Any]:
    """Retrieve full workspace snapshot by workspace_id."""
    with WORKSPACES_LOCK:
        _cleanup_workspaces()
        ws = WORKSPACES.get(workspace_id)
        if not ws:
            raise HTTPException(status_code=404, detail="Workspace not found or expired.")
        ws.touch()
        with ws.lock:
            root = validate_workspace_root(ws.path)
            ws.files, ws.skipped = scan_workspace_files(root)
            ws.revision = compute_revision_hash(ws.files)
            return ws.snapshot_dict()


def apply_workspace_changes(
    workspace_id: str,
    revision: str,
    changes: List[Any],
) -> Dict[str, Any]:
    """Thread-safe batch validation and application of disk mutations."""
    with WORKSPACES_LOCK:
        _cleanup_workspaces()
        ws = WORKSPACES.get(workspace_id)
        if not ws:
            raise HTTPException(status_code=404, detail="Workspace not found or expired.")
        ws.touch()

    with ws.lock:
        root_path = Path(ws.path)
        if not root_path.exists() or not root_path.is_dir():
            raise HTTPException(status_code=400, detail="Workspace root directory no longer exists on disk.")

        root_path = validate_workspace_root(ws.path)
        ws.files, ws.skipped = scan_workspace_files(root_path)
        ws.revision = compute_revision_hash(ws.files)
        # Compare with disk, not the previous cached snapshot.
        if revision != ws.revision:
            raise HTTPException(
                status_code=409,
                detail="Workspace revision is stale. Refresh snapshot before applying changes.",
            )

        # Batch bounds validation
        if not changes:
            raise HTTPException(status_code=422, detail="Changes list cannot be empty.")
        if len(changes) > MAX_CHANGES_BATCH:
            raise HTTPException(status_code=422, detail=f"Batch exceeds maximum of {MAX_CHANGES_BATCH} changes.")

        aggregate_chars = 0
        normalized_changes = []
        for c in changes:
            if isinstance(c, dict):
                path = c.get("path", "")
                op = c.get("operation", "")
                content = c.get("content")
                target = c.get("target")
            else:
                path = getattr(c, "path", "")
                op = getattr(c, "operation", "")
                content = getattr(c, "content", None)
                target = getattr(c, "target", None)

            if content is not None:
                if not isinstance(content, str):
                    raise HTTPException(status_code=422, detail="Change content must be a string.")
                if len(content) > MAX_FILE_BYTES:
                    raise HTTPException(status_code=422, detail="File content exceeds 30,000 characters limit.")
                aggregate_chars += len(content)

            normalized_changes.append({"path": path, "operation": op, "content": content, "target": target})

        if aggregate_chars > MAX_TOTAL_BYTES:
            raise HTTPException(status_code=422, detail="Aggregate change content exceeds 200,000 characters limit.")

        # Validate all changes before mutating
        batch_paths: Set[str] = set()
        for ch in normalized_changes:
            path = ch["path"]
            op = ch["operation"]
            target = ch["target"]

            if not is_safe_relative_posix_path(path):
                raise HTTPException(status_code=400, detail=f"Unsafe or invalid relative path: '{path}'.")

            # Traversal check
            target_file = (root_path / path).resolve()
            try:
                if os.path.commonpath([str(root_path), str(target_file)]) != str(root_path):
                    raise HTTPException(status_code=400, detail=f"Path traversal outside workspace root: '{path}'.")
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Path traversal outside workspace root: '{path}'.")

            # Symlink checks in path and existing ancestor components
            raw_target = root_path / path
            curr = raw_target
            while curr != root_path:
                if curr.is_symlink() or os.path.islink(str(curr)):
                    raise HTTPException(status_code=400, detail=f"Symlinks are not permitted in path: '{path}'.")
                if not curr.exists() and not curr.is_symlink():
                    curr = curr.parent
                    continue
                curr = curr.parent

            allowed, reason = is_allowed_text_file(path)
            if not allowed:
                raise HTTPException(status_code=400, detail=f"Path '{path}' is not allowed: {reason}.")

            if op == "rename":
                if not target or not isinstance(target, str):
                    raise HTTPException(status_code=400, detail="Rename operation requires target relative path.")
                if not is_safe_relative_posix_path(target):
                    raise HTTPException(status_code=400, detail=f"Unsafe target rename path: '{target}'.")
                dest_target = (root_path / target).resolve()
                try:
                    if os.path.commonpath([str(root_path), str(dest_target)]) != str(root_path):
                        raise HTTPException(status_code=400, detail=f"Target path traversal outside root: '{target}'.")
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"Target path traversal outside root: '{target}'.")

                curr_dest = root_path / target
                while curr_dest != root_path:
                    if curr_dest.is_symlink() or os.path.islink(str(curr_dest)):
                        raise HTTPException(status_code=400, detail=f"Symlinks are not permitted in target path: '{target}'.")
                    if not curr_dest.exists() and not curr_dest.is_symlink():
                        curr_dest = curr_dest.parent
                        continue
                    curr_dest = curr_dest.parent

                allowed_tgt, tgt_reason = is_allowed_text_file(target)
                if not allowed_tgt:
                    raise HTTPException(status_code=400, detail=f"Target rename path '{target}' is not allowed: {tgt_reason}.")

            # Operation-specific existence constraints
            if op == "create":
                if raw_target.exists():
                    raise HTTPException(status_code=400, detail=f"File '{path}' already exists; cannot create. Use replace instead.")
            elif op == "replace":
                if not raw_target.exists() or not raw_target.is_file():
                    raise HTTPException(status_code=400, detail=f"File '{path}' does not exist; cannot replace.")
            elif op == "delete":
                if not raw_target.exists() or not raw_target.is_file():
                    raise HTTPException(status_code=400, detail=f"File '{path}' does not exist; cannot delete.")
            elif op == "rename":
                if not raw_target.exists() or not raw_target.is_file():
                    raise HTTPException(status_code=400, detail=f"Source file '{path}' does not exist; cannot rename.")
                raw_dest = root_path / target
                if raw_dest.exists():
                    raise HTTPException(status_code=400, detail=f"Target file '{target}' already exists; cannot rename.")
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported change operation: '{op}'.")

            if path in batch_paths:
                raise HTTPException(status_code=400, detail=f"Duplicate operation targeting '{path}' in batch.")
            batch_paths.add(path)
            if op == "rename" and target:
                if target in batch_paths:
                    raise HTTPException(status_code=400, detail=f"Duplicate operation targeting '{target}' in batch.")
                batch_paths.add(target)

        # Apply all mutations safely
        for ch in normalized_changes:
            path = ch["path"]
            op = ch["operation"]
            content = ch["content"] or ""
            target = ch["target"]
            raw_target = root_path / path

            if op == "create":
                raw_target.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(str(raw_target), os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o644)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
            elif op == "replace":
                fd = os.open(str(raw_target), os.O_TRUNC | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
            elif op == "delete":
                os.unlink(str(raw_target))
            elif op == "rename" and target:
                raw_dest = root_path / target
                raw_dest.parent.mkdir(parents=True, exist_ok=True)
                os.rename(str(raw_target), str(raw_dest))

        # Re-scan workspace on disk and update revision
        new_files, new_skipped = scan_workspace_files(root_path)
        ws.files = new_files
        ws.skipped = new_skipped
        ws.revision = compute_revision_hash(new_files)
        ws.touch()
        return ws.snapshot_dict()


# API Route Handlers
FOLDER_PICKER_LOCK = threading.Lock()


@router.post("/browse")
def browse_workspace_folder() -> Dict[str, Optional[str]]:
    """Choose a folder on the backend's Mac without uploading its contents."""
    if sys.platform != "darwin":
        raise HTTPException(status_code=501, detail="Native folder browsing is available on macOS. Paste the local project path instead.")
    if not FOLDER_PICKER_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A folder chooser is already open. Select a folder or cancel it first.")
    try:
        script = 'try\nreturn POSIX path of (choose folder with prompt "Choose a local project folder for Codebook")\non error number -128\nreturn ""\nend try'
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True, text=True, timeout=180, check=False,
        )
        if result.returncode != 0:
            raise HTTPException(status_code=503, detail="Could not open the Mac folder chooser. Paste the local project path instead.")
        selected = result.stdout.rstrip("\r\n")
        return {"path": str(validate_workspace_root(selected)) if selected else None}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Folder selection timed out. Try Browse again or paste the path.")
    except OSError:
        raise HTTPException(status_code=503, detail="Mac folder chooser is unavailable. Paste the local project path instead.")
    finally:
        FOLDER_PICKER_LOCK.release()


@router.post("/open", response_model=WorkspaceSnapshotResponse)
def open_workspace(req: OpenWorkspaceRequest) -> WorkspaceSnapshotResponse:
    validated_root = validate_workspace_root(req.path)
    root_str = str(validated_root)
    name = validated_root.name or os.path.basename(root_str.rstrip("/")) or "project"

    files, skipped = scan_workspace_files(validated_root)
    revision = compute_revision_hash(files)
    workspace_id = secrets.token_hex(16)

    record = WorkspaceRecord(
        workspace_id=workspace_id,
        path=root_str,
        name=name,
        files=files,
        skipped=skipped,
        revision=revision,
    )

    with WORKSPACES_LOCK:
        _cleanup_workspaces()
        WORKSPACES[workspace_id] = record

    return WorkspaceSnapshotResponse(**record.snapshot_dict())


@router.post("/snapshot", response_model=WorkspaceSnapshotResponse)
def snapshot_workspace(req: SnapshotWorkspaceRequest) -> WorkspaceSnapshotResponse:
    with WORKSPACES_LOCK:
        _cleanup_workspaces()
        ws = WORKSPACES.get(req.workspace_id)
        if not ws:
            raise HTTPException(status_code=404, detail="Workspace not found or expired.")
        ws.touch()

    with ws.lock:
        root_path = Path(ws.path)
        if not root_path.exists() or not root_path.is_dir():
            raise HTTPException(status_code=400, detail="Workspace directory on disk no longer exists.")
        root_path = validate_workspace_root(ws.path)
        files, skipped = scan_workspace_files(root_path)
        revision = compute_revision_hash(files)
        ws.files = files
        ws.skipped = skipped
        ws.revision = revision
        ws.touch()
        return WorkspaceSnapshotResponse(**ws.snapshot_dict())


@router.post("/apply", response_model=WorkspaceSnapshotResponse)
def apply_workspace(req: ApplyWorkspaceRequest) -> WorkspaceSnapshotResponse:
    snapshot = apply_workspace_changes(req.workspace_id, req.revision, req.changes)
    return WorkspaceSnapshotResponse(**snapshot)


@router.post("/commands/prepare", response_model=PrepareCommandResponse)
def prepare_command(req: PrepareCommandRequest) -> PrepareCommandResponse:
    with WORKSPACES_LOCK:
        _cleanup_workspaces()
        ws = WORKSPACES.get(req.workspace_id)
        if not ws:
            raise HTTPException(status_code=404, detail="Workspace not found or expired.")
        ws.touch()

    argv = req.argv
    if not argv or not isinstance(argv, list) or not all(isinstance(a, str) and "\x00" not in a for a in argv):
        raise HTTPException(status_code=400, detail="Command must be a non-empty array of strings without null bytes.")

    exe = argv[0].strip()
    if not exe:
        raise HTTPException(status_code=400, detail="Executable command name cannot be empty.")

    # Disallow explicit shell interpreters
    if exe.lower() in DISALLOWED_INTERPRETERS or os.path.basename(exe).lower() in DISALLOWED_INTERPRETERS:
        raise HTTPException(status_code=400, detail="Shell interpreters (sh/bash/zsh/powershell/cmd) are not permitted.")

    for arg in argv[1:]:
        if arg.lower() in DISALLOWED_INTERPRETERS or os.path.basename(arg).lower() in DISALLOWED_INTERPRETERS:
            raise HTTPException(status_code=400, detail="Direct invocation of shell interpreters is not permitted.")

    # Executable whitelist check
    if not is_allowed_executable(exe):
        raise HTTPException(
            status_code=400,
            detail=f"Executable '{exe}' is not permitted. Only developer tools (npm, python, pytest, etc.) are allowed.",
        )

    # Check python -c or eval flags
    base_exe = os.path.basename(exe).lower()
    if "python" in base_exe:
        for arg in argv[1:]:
            if arg == "-c" or arg.startswith("-c"):
                raise HTTPException(status_code=400, detail="Executing inline code via python -c is not permitted.")

    for arg in argv[1:]:
        if arg in ("-e", "--eval", "-r"):
            raise HTTPException(status_code=400, detail="Executing arbitrary inline eval scripts is not permitted.")

    # Restrict operations
    for arg in argv[1:]:
        if arg.lower() in DISALLOWED_OPERATIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Operation '{arg}' is not permitted. Only test, build, lint, check, typecheck, format are supported.",
            )

    warning = (
        "Execution is NOT sandboxed and runs as your OS user. "
        "Any project script has arbitrary OS rights, file access, and network privileges. "
        "Review the exact arguments and directory before approval."
    )

    approval_id = secrets.token_hex(16)
    rec = CommandApprovalRecord(approval_id, ws.workspace_id, argv, ws.path)

    with COMMAND_APPROVALS_LOCK:
        _cleanup_approvals()
        COMMAND_APPROVALS[approval_id] = rec

    return PrepareCommandResponse(approval_id=approval_id, argv=argv, cwd=ws.path, warning=warning)


@router.post("/commands/run", response_model=RunCommandResponse)
def run_command(req: RunCommandRequest) -> RunCommandResponse:
    """Run command with one-use approval token in synchronous threadpool off main async loop."""
    with COMMAND_APPROVALS_LOCK:
        _cleanup_approvals()
        rec = COMMAND_APPROVALS.get(req.approval_id)
        if not rec:
            raise HTTPException(status_code=400, detail="Invalid, expired, or non-existent command approval token.")
        if rec.used:
            raise HTTPException(status_code=400, detail="Command approval token has already been used.")
        if time.time() - rec.created_at > COMMAND_APPROVAL_TTL_SECONDS:
            COMMAND_APPROVALS.pop(req.approval_id, None)
            raise HTTPException(status_code=400, detail="Command approval token has expired (5-minute TTL).")
        if rec.workspace_id != req.workspace_id:
            raise HTTPException(status_code=400, detail="Approval token does not match requested workspace.")
        rec.used = True
        COMMAND_APPROVALS.pop(req.approval_id, None)

    if not req.approved:
        return RunCommandResponse(
            argv=rec.argv,
            cwd=rec.cwd,
            exit_code=None,
            stdout="",
            stderr="Command execution was denied by user.",
            timed_out=False,
            truncated=False,
        )

    validate_workspace_root(rec.cwd)
    get_workspace_snapshot(req.workspace_id)

    # Prepare minimal environment with secrets stripped
    clean_env: Dict[str, str] = {}
    for k in ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TERM"):
        if k in os.environ:
            clean_env[k] = os.environ[k]

    for k in list(clean_env.keys()):
        upper = k.upper()
        if any(s in upper for s in ("KEY", "SECRET", "TOKEN", "AUTH", "PASS", "CRED", "API")):
            clean_env.pop(k, None)

    if "PATH" not in clean_env:
        clean_env["PATH"] = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    if "HOME" not in clean_env:
        clean_env["HOME"] = str(Path.home())

    try:
        proc = subprocess.Popen(
            rec.argv,
            cwd=rec.cwd,
            env=clean_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        return RunCommandResponse(
            argv=rec.argv,
            cwd=rec.cwd,
            exit_code=127,
            stdout="",
            stderr=f"Executable '{rec.argv[0]}' not found.",
            timed_out=False,
            truncated=False,
        )
    except Exception:
        return RunCommandResponse(
            argv=rec.argv,
            cwd=rec.cwd,
            exit_code=1,
            stdout="",
            stderr="Failed to spawn command process.",
            timed_out=False,
            truncated=False,
        )

    stdout_fd = proc.stdout.fileno()  # type: ignore[union-attr]
    stderr_fd = proc.stderr.fileno()  # type: ignore[union-attr]
    os.set_blocking(stdout_fd, False)
    os.set_blocking(stderr_fd, False)

    sel = selectors.DefaultSelector()
    sel.register(stdout_fd, selectors.EVENT_READ, data="stdout")
    sel.register(stderr_fd, selectors.EVENT_READ, data="stderr")

    stdout_chunks: List[bytes] = []
    stderr_chunks: List[bytes] = []
    stdout_len = 0
    stderr_len = 0
    truncated = False
    timed_out = False
    deadline = time.time() + COMMAND_TIMEOUT_SECONDS

    while sel.get_map():
        rem = deadline - time.time()
        if rem <= 0:
            timed_out = True
            break
        events = sel.select(timeout=min(0.2, max(0.01, rem)))
        for key, mask in events:
            stream_name = key.data
            fd = key.fileobj
            try:
                chunk = os.read(fd, 4096)  # type: ignore[arg-type]
            except (BlockingIOError, InterruptedError):
                continue
            except Exception:
                chunk = b""

            if not chunk:
                sel.unregister(fd)
                continue

            if stream_name == "stdout":
                if stdout_len < COMMAND_MAX_OUTPUT_BYTES:
                    available = COMMAND_MAX_OUTPUT_BYTES - stdout_len
                    keep = chunk[:available]
                    stdout_chunks.append(keep)
                    stdout_len += len(keep)
                    if len(chunk) > len(keep):
                        truncated = True
                else:
                    truncated = True
            elif stream_name == "stderr":
                if stderr_len < COMMAND_MAX_OUTPUT_BYTES:
                    available = COMMAND_MAX_OUTPUT_BYTES - stderr_len
                    keep = chunk[:available]
                    stderr_chunks.append(keep)
                    stderr_len += len(keep)
                    if len(chunk) > len(keep):
                        truncated = True
                else:
                    truncated = True

    sel.close()

    if timed_out:
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc.wait(timeout=2.0)
        except Exception:
            pass
        exit_code = None
    else:
        try:
            proc.wait(timeout=5.0)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                proc.kill()
            exit_code = None

    try:
        proc.stdout.close()  # type: ignore[union-attr]
        proc.stderr.close()  # type: ignore[union-attr]
    except Exception:
        pass

    out_text = b"".join(stdout_chunks).decode("utf-8", errors="replace")
    err_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")

    return RunCommandResponse(
        argv=rec.argv,
        cwd=rec.cwd,
        exit_code=exit_code,
        stdout=out_text,
        stderr=err_text,
        timed_out=timed_out,
        truncated=truncated,
    )
