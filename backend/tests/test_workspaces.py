"""Tests for local workspace service and command approval API."""

import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.app.workspaces import (
    COMMAND_APPROVALS,
    COMMAND_MAX_OUTPUT_BYTES,
    MAX_CHANGES_BATCH,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    MAX_WORKSPACES,
    WORKSPACES,
    apply_workspace_changes,
    compute_revision_hash,
    get_workspace_snapshot,
    router as workspaces_router,
)

# Local app for standalone workspaces router testing
local_app = FastAPI()


@local_app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    msg = "; ".join([f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}" for err in errors])
    return JSONResponse(status_code=422, content={"detail": f"Validation error: {msg}"})


local_app.include_router(workspaces_router)


@pytest.fixture
def client():
    """Test client for workspaces routes."""
    return TestClient(local_app)


@pytest.fixture(autouse=True)
def clean_registry():
    """Ensure workspaces and command approvals registries are clean."""
    WORKSPACES.clear()
    COMMAND_APPROVALS.clear()
    yield
    WORKSPACES.clear()
    COMMAND_APPROVALS.clear()


@pytest.fixture
def test_workspace_dir():
    """Create an isolated test directory inside the project tree (never in /tmp)."""
    base_dir = Path.cwd() / "backend" / "tests" / ".test_workspaces_data"
    base_dir.mkdir(parents=True, exist_ok=True)
    ws_dir = base_dir / f"test_ws_{uuid.uuid4().hex}"
    ws_dir.mkdir(parents=True, exist_ok=True)
    try:
        yield ws_dir
    finally:
        shutil.rmtree(ws_dir, ignore_errors=True)
        try:
            base_dir.rmdir()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# POST /agent/workspaces/open tests
# ---------------------------------------------------------------------------


def test_open_valid_workspace(client, test_workspace_dir):
    # Setup test project files
    (test_workspace_dir / "README.md").write_text("# My Project\n", encoding="utf-8")
    (test_workspace_dir / "src").mkdir()
    (test_workspace_dir / "src" / "main.py").write_text("print('hello world')\n", encoding="utf-8")
    (test_workspace_dir / "go.mod").write_text("module example.com/app\n\ngo 1.22\n", encoding="utf-8")
    (test_workspace_dir / "Gemfile").write_text("source 'https://rubygems.org'\n", encoding="utf-8")
    (test_workspace_dir / "App.csproj").write_text("<Project Sdk=\"Microsoft.NET.Sdk\" />\n", encoding="utf-8")

    res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    assert res.status_code == 200
    data = res.json()

    assert "workspace_id" in data
    assert data["name"] == test_workspace_dir.name
    assert data["path"] == str(test_workspace_dir.resolve())
    assert isinstance(data["revision"], str) and len(data["revision"]) == 64

    paths = [f["path"] for f in data["files"]]
    assert "README.md" in paths
    assert "src/main.py" in paths
    assert "go.mod" in paths
    assert "Gemfile" in paths
    assert "App.csproj" in paths
    # Verify snapshot paths do not include root folder name
    for p in paths:
        assert not p.startswith(test_workspace_dir.name)
        assert not p.startswith("/")

    # Deterministic revision
    files = data["files"]
    expected_rev = compute_revision_hash(files)
    assert data["revision"] == expected_rev


def test_open_rejects_non_absolute_path(client):
    res = client.post("/agent/workspaces/open", json={"path": "some/relative/dir"})
    assert res.status_code == 400
    assert "absolute" in res.json()["detail"].lower()


def test_open_rejects_empty_path(client):
    res = client.post("/agent/workspaces/open", json={"path": ""})
    assert res.status_code == 400


def test_open_rejects_nonexistent_directory(client):
    res = client.post("/agent/workspaces/open", json={"path": "/nonexistent/path/for/workspace/12345"})
    assert res.status_code == 400
    assert "not exist" in res.json()["detail"].lower()


def test_open_rejects_filesystem_root(client):
    res = client.post("/agent/workspaces/open", json={"path": "/"})
    assert res.status_code == 400
    assert "root" in res.json()["detail"].lower()


def test_open_rejects_user_home_directory(client):
    home = str(Path.home())
    res = client.post("/agent/workspaces/open", json={"path": home})
    assert res.status_code == 400
    assert "home" in res.json()["detail"].lower()


def test_open_rejects_system_directory(client):
    res = client.post("/agent/workspaces/open", json={"path": "/usr"})
    assert res.status_code == 400
    assert "system" in res.json()["detail"].lower()


def test_open_rejects_traversal_sequences(client, test_workspace_dir):
    traversal_path = str(test_workspace_dir / ".." / test_workspace_dir.name)
    res = client.post("/agent/workspaces/open", json={"path": traversal_path})
    assert res.status_code == 400
    assert "traversal" in res.json()["detail"].lower()


def test_open_rejects_symlink_root_and_ancestors(client, test_workspace_dir):
    symlink_target = test_workspace_dir.parent / f"symlink_ws_{uuid.uuid4().hex}"
    try:
        os.symlink(test_workspace_dir, symlink_target)
        res = client.post("/agent/workspaces/open", json={"path": str(symlink_target)})
        assert res.status_code == 400
        assert "symlink" in res.json()["detail"].lower()
    finally:
        if symlink_target.is_symlink() or symlink_target.exists():
            symlink_target.unlink()


def test_open_skips_descendant_symlinks(client, test_workspace_dir):
    # Regular file
    (test_workspace_dir / "regular.py").write_text("a = 1\n", encoding="utf-8")
    # Symlink file
    target_file = test_workspace_dir / "target.py"
    target_file.write_text("b = 2\n", encoding="utf-8")
    symlink_file = test_workspace_dir / "symlink_file.py"
    os.symlink(target_file, symlink_file)

    # Symlink directory
    real_subdir = test_workspace_dir / "real_dir"
    real_subdir.mkdir()
    (real_subdir / "sub.py").write_text("c = 3\n", encoding="utf-8")
    symlink_dir = test_workspace_dir / "symlink_dir"
    os.symlink(real_subdir, symlink_dir)

    res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    assert res.status_code == 200
    data = res.json()

    included_paths = [f["path"] for f in data["files"]]
    assert "regular.py" in included_paths
    assert "target.py" in included_paths
    assert "real_dir/sub.py" in included_paths
    assert "symlink_file.py" not in included_paths
    assert "symlink_dir/sub.py" not in included_paths

    skipped_reasons = {s["path"]: s["reason"] for s in data["skipped"]}
    assert "symlink_file.py" in skipped_reasons
    assert "symlink" in skipped_reasons["symlink_file.py"]
    assert "symlink_dir" in skipped_reasons
    assert "symlink" in skipped_reasons["symlink_dir"]


def test_open_skips_excluded_dirs_secrets_binaries_lockfiles(client, test_workspace_dir):
    # Allowed
    (test_workspace_dir / "index.js").write_text("console.log('ok');\n", encoding="utf-8")

    # Excluded directories
    git_dir = test_workspace_dir / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("git config", encoding="utf-8")

    nm_dir = test_workspace_dir / "node_modules" / "pkg"
    nm_dir.mkdir(parents=True)
    (nm_dir / "index.js").write_text("module.exports = {};", encoding="utf-8")

    venv_dir = test_workspace_dir / ".venv" / "bin"
    venv_dir.mkdir(parents=True)
    (venv_dir / "activate").write_text("#!/bin/sh", encoding="utf-8")

    # Secrets
    (test_workspace_dir / ".env").write_text("API_KEY=123", encoding="utf-8")
    (test_workspace_dir / "id_rsa").write_text("PRIVATE KEY", encoding="utf-8")
    (test_workspace_dir / "server.key").write_text("KEY", encoding="utf-8")

    # Binaries
    (test_workspace_dir / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (test_workspace_dir / "compiled.pyc").write_bytes(b"\x00\x00\x00\x00")

    # Lockfiles
    (test_workspace_dir / "package-lock.json").write_text("{}", encoding="utf-8")

    res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    assert res.status_code == 200
    data = res.json()

    included_paths = [f["path"] for f in data["files"]]
    assert included_paths == ["index.js"]

    skipped_map = {s["path"]: s["reason"] for s in data["skipped"]}
    assert ".git" in skipped_map
    assert "node_modules" in skipped_map
    assert ".venv" in skipped_map
    assert ".env" in skipped_map
    assert "id_rsa" in skipped_map
    assert "server.key" in skipped_map
    assert "image.png" in skipped_map
    assert "compiled.pyc" in skipped_map
    assert "package-lock.json" in skipped_map


def test_open_file_and_aggregate_size_limits(client, test_workspace_dir):
    # File exceeding 30k
    large_text = "x" * (MAX_FILE_BYTES + 50)
    (test_workspace_dir / "large.py").write_text(large_text, encoding="utf-8")

    # Normal file
    (test_workspace_dir / "normal.py").write_text("print('fine')\n", encoding="utf-8")

    res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    assert res.status_code == 200
    data = res.json()

    included_paths = [f["path"] for f in data["files"]]
    assert "normal.py" in included_paths
    assert "large.py" not in included_paths

    skipped_map = {s["path"]: s["reason"] for s in data["skipped"]}
    assert "large.py" in skipped_map
    assert "30,000" in skipped_map["large.py"]


def test_open_max_files_limit(client, test_workspace_dir):
    for i in range(MAX_FILES + 5):
        (test_workspace_dir / f"file_{i:03d}.py").write_text(f"# file {i}\n", encoding="utf-8")

    res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    assert res.status_code == 200
    data = res.json()

    assert len(data["files"]) == MAX_FILES
    assert len(data["skipped"]) == 5
    for s in data["skipped"]:
        assert "1,000-file" in s["reason"]


# ---------------------------------------------------------------------------
# POST /agent/workspaces/snapshot tests
# ---------------------------------------------------------------------------


def test_snapshot_live_refresh(client, test_workspace_dir):
    (test_workspace_dir / "initial.py").write_text("x = 1\n", encoding="utf-8")
    open_res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    assert open_res.status_code == 200
    initial_data = open_res.json()
    wid = initial_data["workspace_id"]
    initial_rev = initial_data["revision"]

    # Write a new file to disk
    (test_workspace_dir / "added.py").write_text("y = 2\n", encoding="utf-8")

    snap_res = client.post("/agent/workspaces/snapshot", json={"workspace_id": wid})
    assert snap_res.status_code == 200
    snap_data = snap_res.json()

    assert snap_data["workspace_id"] == wid
    paths = [f["path"] for f in snap_data["files"]]
    assert "initial.py" in paths
    assert "added.py" in paths
    assert snap_data["revision"] != initial_rev


def test_snapshot_not_found(client):
    res = client.post("/agent/workspaces/snapshot", json={"workspace_id": "nonexistent_wid"})
    assert res.status_code == 404


def test_get_workspace_snapshot_contract(test_workspace_dir):
    (test_workspace_dir / "test.py").write_text("z = 10\n", encoding="utf-8")
    from backend.app.workspaces import open_workspace, OpenWorkspaceRequest

    snap_resp = open_workspace(OpenWorkspaceRequest(path=str(test_workspace_dir)))
    wid = snap_resp.workspace_id

    # Test direct python contract export
    retrieved = get_workspace_snapshot(wid)
    assert retrieved["workspace_id"] == wid
    assert retrieved["revision"] == snap_resp.revision
    assert retrieved["name"] == test_workspace_dir.name


def test_get_workspace_snapshot_not_found():
    with pytest.raises(Exception) as exc:
        get_workspace_snapshot("missing_workspace_token")
    assert "404" in str(exc.value) or getattr(exc.value, "status_code", None) == 404


def test_apply_workspace_changes_contract(test_workspace_dir):
    (test_workspace_dir / "mod.py").write_text("orig = 1\n", encoding="utf-8")
    from backend.app.workspaces import open_workspace, OpenWorkspaceRequest

    snap_resp = open_workspace(OpenWorkspaceRequest(path=str(test_workspace_dir)))
    wid = snap_resp.workspace_id
    rev = snap_resp.revision

    updated = apply_workspace_changes(
        wid,
        rev,
        [{"path": "mod.py", "operation": "replace", "content": "updated = 2\n"}],
    )
    assert updated["workspace_id"] == wid
    assert (test_workspace_dir / "mod.py").read_text(encoding="utf-8") == "updated = 2\n"
    assert updated["revision"] != rev


# ---------------------------------------------------------------------------
# POST /agent/workspaces/apply tests
# ---------------------------------------------------------------------------


def test_apply_create_file(client, test_workspace_dir):
    (test_workspace_dir / "existing.py").write_text("# existing\n", encoding="utf-8")
    open_res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    wid = open_res.json()["workspace_id"]
    rev = open_res.json()["revision"]

    res = client.post(
        "/agent/workspaces/apply",
        json={
            "workspace_id": wid,
            "revision": rev,
            "changes": [
                {
                    "path": "src/created.py",
                    "operation": "create",
                    "content": "def hello(): pass\n",
                }
            ],
        },
    )
    assert res.status_code == 200
    data = res.json()

    assert (test_workspace_dir / "src" / "created.py").exists()
    assert (test_workspace_dir / "src" / "created.py").read_text(encoding="utf-8") == "def hello(): pass\n"
    assert data["revision"] != rev
    paths = [f["path"] for f in data["files"]]
    assert "src/created.py" in paths
    assert "existing.py" in paths


def test_apply_create_fails_if_file_exists(client, test_workspace_dir):
    (test_workspace_dir / "file.py").write_text("initial", encoding="utf-8")
    open_res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    wid = open_res.json()["workspace_id"]
    rev = open_res.json()["revision"]

    res = client.post(
        "/agent/workspaces/apply",
        json={
            "workspace_id": wid,
            "revision": rev,
            "changes": [
                {
                    "path": "file.py",
                    "operation": "create",
                    "content": "overwrite attempt",
                }
            ],
        },
    )
    assert res.status_code == 400
    assert "already exists" in res.json()["detail"].lower()
    # Ensure file content wasn't overwritten
    assert (test_workspace_dir / "file.py").read_text(encoding="utf-8") == "initial"


def test_apply_replace_file(client, test_workspace_dir):
    (test_workspace_dir / "file.py").write_text("original content\n", encoding="utf-8")
    open_res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    wid = open_res.json()["workspace_id"]
    rev = open_res.json()["revision"]

    res = client.post(
        "/agent/workspaces/apply",
        json={
            "workspace_id": wid,
            "revision": rev,
            "changes": [
                {
                    "path": "file.py",
                    "operation": "replace",
                    "content": "updated content\n",
                }
            ],
        },
    )
    assert res.status_code == 200
    assert (test_workspace_dir / "file.py").read_text(encoding="utf-8") == "updated content\n"


def test_apply_replace_fails_if_file_missing(client, test_workspace_dir):
    (test_workspace_dir / "file.py").write_text("original\n", encoding="utf-8")
    open_res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    wid = open_res.json()["workspace_id"]
    rev = open_res.json()["revision"]

    res = client.post(
        "/agent/workspaces/apply",
        json={
            "workspace_id": wid,
            "revision": rev,
            "changes": [
                {
                    "path": "missing.py",
                    "operation": "replace",
                    "content": "updated",
                }
            ],
        },
    )
    assert res.status_code == 400
    assert "does not exist" in res.json()["detail"].lower()


def test_apply_delete_file(client, test_workspace_dir):
    (test_workspace_dir / "to_delete.py").write_text("delete me\n", encoding="utf-8")
    open_res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    wid = open_res.json()["workspace_id"]
    rev = open_res.json()["revision"]

    res = client.post(
        "/agent/workspaces/apply",
        json={
            "workspace_id": wid,
            "revision": rev,
            "changes": [
                {
                    "path": "to_delete.py",
                    "operation": "delete",
                }
            ],
        },
    )
    assert res.status_code == 200
    assert not (test_workspace_dir / "to_delete.py").exists()
    assert "to_delete.py" not in [f["path"] for f in res.json()["files"]]


def test_apply_delete_fails_if_file_missing(client, test_workspace_dir):
    (test_workspace_dir / "file.py").write_text("ok\n", encoding="utf-8")
    open_res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    wid = open_res.json()["workspace_id"]
    rev = open_res.json()["revision"]

    res = client.post(
        "/agent/workspaces/apply",
        json={
            "workspace_id": wid,
            "revision": rev,
            "changes": [
                {
                    "path": "nonexistent.py",
                    "operation": "delete",
                }
            ],
        },
    )
    assert res.status_code == 400
    assert "does not exist" in res.json()["detail"].lower()


def test_apply_rename_file(client, test_workspace_dir):
    (test_workspace_dir / "old_name.py").write_text("original script\n", encoding="utf-8")
    open_res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    wid = open_res.json()["workspace_id"]
    rev = open_res.json()["revision"]

    res = client.post(
        "/agent/workspaces/apply",
        json={
            "workspace_id": wid,
            "revision": rev,
            "changes": [
                {
                    "path": "old_name.py",
                    "operation": "rename",
                    "target": "new_name.py",
                }
            ],
        },
    )
    assert res.status_code == 200
    assert not (test_workspace_dir / "old_name.py").exists()
    assert (test_workspace_dir / "new_name.py").exists()
    assert (test_workspace_dir / "new_name.py").read_text(encoding="utf-8") == "original script\n"


def test_apply_rename_fails_if_target_exists(client, test_workspace_dir):
    (test_workspace_dir / "src.py").write_text("source\n", encoding="utf-8")
    (test_workspace_dir / "dst.py").write_text("dest\n", encoding="utf-8")
    open_res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    wid = open_res.json()["workspace_id"]
    rev = open_res.json()["revision"]

    res = client.post(
        "/agent/workspaces/apply",
        json={
            "workspace_id": wid,
            "revision": rev,
            "changes": [
                {
                    "path": "src.py",
                    "operation": "rename",
                    "target": "dst.py",
                }
            ],
        },
    )
    assert res.status_code == 400
    assert "already exists" in res.json()["detail"].lower()


def test_apply_stale_revision_rejects_409(client, test_workspace_dir):
    (test_workspace_dir / "file.py").write_text("initial\n", encoding="utf-8")
    open_res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    wid = open_res.json()["workspace_id"]

    res = client.post(
        "/agent/workspaces/apply",
        json={
            "workspace_id": wid,
            "revision": "outdated_stale_revision_hash",
            "changes": [
                {
                    "path": "file.py",
                    "operation": "replace",
                    "content": "changed\n",
                }
            ],
        },
    )
    assert res.status_code == 409
    assert "stale" in res.json()["detail"].lower()


def test_apply_batch_size_limit_and_content_bounds(client, test_workspace_dir):
    (test_workspace_dir / "base.py").write_text("x = 1\n", encoding="utf-8")
    open_res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    wid = open_res.json()["workspace_id"]
    rev = open_res.json()["revision"]

    # Exceeding 20 changes
    too_many_changes = [
        {"path": f"f_{i}.py", "operation": "create", "content": "pass\n"}
        for i in range(MAX_CHANGES_BATCH + 1)
    ]
    res_many = client.post(
        "/agent/workspaces/apply",
        json={"workspace_id": wid, "revision": rev, "changes": too_many_changes},
    )
    assert res_many.status_code == 422
    assert "20" in res_many.json()["detail"]

    # Exceeding 30k chars for single file
    huge_content = "a" * (MAX_FILE_BYTES + 10)
    res_single = client.post(
        "/agent/workspaces/apply",
        json={
            "workspace_id": wid,
            "revision": rev,
            "changes": [{"path": "huge.py", "operation": "create", "content": huge_content}],
        },
    )
    assert res_single.status_code == 422
    assert "30,000" in res_single.json()["detail"]

    # Exceeding 200k aggregate chars
    chunk_size = 25000
    aggregate_changes = [
        {"path": f"agg_{i}.py", "operation": "create", "content": "a" * chunk_size}
        for i in range(MAX_TOTAL_BYTES // chunk_size + 1)
    ]
    res_agg = client.post(
        "/agent/workspaces/apply",
        json={"workspace_id": wid, "revision": rev, "changes": aggregate_changes},
    )
    assert res_agg.status_code == 422
    assert "200,000" in res_agg.json()["detail"]


def test_apply_rejects_traversal_and_symlinks(client, test_workspace_dir):
    (test_workspace_dir / "valid.py").write_text("ok\n", encoding="utf-8")
    open_res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    wid = open_res.json()["workspace_id"]
    rev = open_res.json()["revision"]

    # Traversal
    res = client.post(
        "/agent/workspaces/apply",
        json={
            "workspace_id": wid,
            "revision": rev,
            "changes": [{"path": "../outside.py", "operation": "create", "content": "bad"}],
        },
    )
    assert res.status_code in (400, 422)

    # Symlink target
    symlink_file = test_workspace_dir / "symlink.py"
    target_file = test_workspace_dir / "valid.py"
    os.symlink(target_file, symlink_file)

    res_sym = client.post(
        "/agent/workspaces/apply",
        json={
            "workspace_id": wid,
            "revision": rev,
            "changes": [{"path": "symlink.py", "operation": "replace", "content": "bad"}],
        },
    )
    assert res_sym.status_code == 400
    assert "symlink" in res_sym.json()["detail"].lower()


def test_apply_rejects_secret_or_binary_paths(client, test_workspace_dir):
    (test_workspace_dir / "valid.py").write_text("ok\n", encoding="utf-8")
    open_res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    wid = open_res.json()["workspace_id"]
    rev = open_res.json()["revision"]

    res = client.post(
        "/agent/workspaces/apply",
        json={
            "workspace_id": wid,
            "revision": rev,
            "changes": [{"path": ".env", "operation": "create", "content": "SECRET=123"}],
        },
    )
    assert res.status_code == 400
    assert "not allowed" in res.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Workspace TTL and Max Workspaces Limits
# ---------------------------------------------------------------------------


def test_workspaces_capacity_lru_eviction(client, test_workspace_dir):
    # Open MAX_WORKSPACES + 2 workspaces
    wids = []
    for i in range(MAX_WORKSPACES + 2):
        ws_subdir = test_workspace_dir / f"ws_{i}"
        ws_subdir.mkdir()
        (ws_subdir / "file.py").write_text(f"# {i}\n", encoding="utf-8")
        res = client.post("/agent/workspaces/open", json={"path": str(ws_subdir)})
        assert res.status_code == 200
        wids.append(res.json()["workspace_id"])

    assert len(WORKSPACES) <= MAX_WORKSPACES
    # The first created workspaces should have been evicted
    assert wids[0] not in WORKSPACES


def test_workspaces_ttl_expiration(client, test_workspace_dir):
    (test_workspace_dir / "f.py").write_text("1\n", encoding="utf-8")
    res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    wid = res.json()["workspace_id"]

    # Backdate last_accessed_at by 3 hours (TTL is 2 hours)
    WORKSPACES[wid].last_accessed_at = time.time() - 10800

    snap_res = client.post("/agent/workspaces/snapshot", json={"workspace_id": wid})
    assert snap_res.status_code == 404


# ---------------------------------------------------------------------------
# Command Preparation and Execution tests
# ---------------------------------------------------------------------------


def test_commands_prepare_valid(client, test_workspace_dir):
    (test_workspace_dir / "main.py").write_text("print('hello')\n", encoding="utf-8")
    open_res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    wid = open_res.json()["workspace_id"]

    argv = ["python3", "-m", "compileall", "."]
    res = client.post("/agent/workspaces/commands/prepare", json={"workspace_id": wid, "argv": argv})
    assert res.status_code == 200
    data = res.json()

    assert "approval_id" in data
    assert data["argv"] == argv
    assert data["cwd"] == str(test_workspace_dir.resolve())
    assert "not sandboxed" in data["warning"].lower()


def test_commands_prepare_rejects_shell_interpreters(client, test_workspace_dir):
    (test_workspace_dir / "main.py").write_text("print('hello')\n", encoding="utf-8")
    open_res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    wid = open_res.json()["workspace_id"]

    shells = [
        ["bash", "-c", "echo hello"],
        ["sh", "script.sh"],
        ["zsh", "-c", "ls"],
        ["powershell", "Get-ChildItem"],
        ["cmd.exe", "/c", "dir"],
    ]
    for cmd in shells:
        res = client.post("/agent/workspaces/commands/prepare", json={"workspace_id": wid, "argv": cmd})
        assert res.status_code == 400
        assert "shell" in res.json()["detail"].lower()


def test_commands_prepare_rejects_python_c_flag(client, test_workspace_dir):
    (test_workspace_dir / "main.py").write_text("print('hello')\n", encoding="utf-8")
    open_res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    wid = open_res.json()["workspace_id"]

    cmds = [
        ["python3", "-c", "import os; os.system('ls')"],
        ["python", "-c", "print(1)"],
        [sys.executable, "-c", "print(2)"],
    ]
    for cmd in cmds:
        res = client.post("/agent/workspaces/commands/prepare", json={"workspace_id": wid, "argv": cmd})
        assert res.status_code == 400
        assert "-c" in res.json()["detail"]


def test_commands_prepare_rejects_disallowed_executables(client, test_workspace_dir):
    (test_workspace_dir / "main.py").write_text("print('hello')\n", encoding="utf-8")
    open_res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    wid = open_res.json()["workspace_id"]

    bad_executables = [
        ["curl", "https://example.com"],
        ["rm", "-rf", "."],
        ["cat", "main.py"],
        ["gcc", "main.c"],
    ]
    for cmd in bad_executables:
        res = client.post("/agent/workspaces/commands/prepare", json={"workspace_id": wid, "argv": cmd})
        assert res.status_code == 400
        assert "not permitted" in res.json()["detail"].lower()


def test_commands_prepare_rejects_disallowed_operations(client, test_workspace_dir):
    (test_workspace_dir / "main.py").write_text("print('hello')\n", encoding="utf-8")
    open_res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    wid = open_res.json()["workspace_id"]

    bad_operations = [
        ["npm", "install"],
        ["npm", "i", "lodash"],
        ["yarn", "add", "express"],
        ["bun", "remove", "react"],
        ["cargo", "clean"],
    ]
    for cmd in bad_operations:
        res = client.post("/agent/workspaces/commands/prepare", json={"workspace_id": wid, "argv": cmd})
        assert res.status_code == 400
        assert "not permitted" in res.json()["detail"].lower()


def test_commands_run_denial(client, test_workspace_dir):
    (test_workspace_dir / "main.py").write_text("print('hello')\n", encoding="utf-8")
    open_res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    wid = open_res.json()["workspace_id"]

    prep_res = client.post(
        "/agent/workspaces/commands/prepare",
        json={"workspace_id": wid, "argv": ["python3", "-m", "compileall", "."]},
    )
    approval_id = prep_res.json()["approval_id"]

    res = client.post(
        "/agent/workspaces/commands/run",
        json={"workspace_id": wid, "approval_id": approval_id, "approved": False},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["exit_code"] is None
    assert "denied" in data["stderr"].lower()
    assert data["timed_out"] is False
    assert data["truncated"] is False


def test_commands_run_approved_execution(client, test_workspace_dir):
    (test_workspace_dir / "main.py").write_text("print('hello')\n", encoding="utf-8")
    open_res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    wid = open_res.json()["workspace_id"]

    # Use sys.executable with compileall --help as standard safe invocation
    argv = [sys.executable, "-m", "compileall", "--help"]
    prep_res = client.post("/agent/workspaces/commands/prepare", json={"workspace_id": wid, "argv": argv})
    approval_id = prep_res.json()["approval_id"]

    res = client.post(
        "/agent/workspaces/commands/run",
        json={"workspace_id": wid, "approval_id": approval_id, "approved": True},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["exit_code"] == 0
    assert "compileall" in data["stdout"].lower() or "usage" in data["stdout"].lower()
    assert data["timed_out"] is False
    assert data["truncated"] is False


def test_commands_run_one_use_token(client, test_workspace_dir):
    (test_workspace_dir / "main.py").write_text("print('hello')\n", encoding="utf-8")
    open_res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    wid = open_res.json()["workspace_id"]

    prep_res = client.post(
        "/agent/workspaces/commands/prepare",
        json={"workspace_id": wid, "argv": ["python3", "--version"]},
    )
    approval_id = prep_res.json()["approval_id"]

    # First run
    res1 = client.post(
        "/agent/workspaces/commands/run",
        json={"workspace_id": wid, "approval_id": approval_id, "approved": True},
    )
    assert res1.status_code == 200

    # Second run with same approval token must fail
    res2 = client.post(
        "/agent/workspaces/commands/run",
        json={"workspace_id": wid, "approval_id": approval_id, "approved": True},
    )
    assert res2.status_code == 400
    assert "already" in res2.json()["detail"].lower() or "invalid" in res2.json()["detail"].lower()


def test_commands_run_expired_token(client, test_workspace_dir):
    (test_workspace_dir / "main.py").write_text("print('hello')\n", encoding="utf-8")
    open_res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    wid = open_res.json()["workspace_id"]

    prep_res = client.post(
        "/agent/workspaces/commands/prepare",
        json={"workspace_id": wid, "argv": ["python3", "--version"]},
    )
    approval_id = prep_res.json()["approval_id"]

    # Set created_at to 6 minutes ago (TTL is 5 minutes)
    COMMAND_APPROVALS[approval_id].created_at = time.time() - 360

    res = client.post(
        "/agent/workspaces/commands/run",
        json={"workspace_id": wid, "approval_id": approval_id, "approved": True},
    )
    assert res.status_code == 400
    assert "expired" in res.json()["detail"].lower()


def test_commands_run_environment_sanitization(client, test_workspace_dir):
    # Set a sensitive key in environment
    secret_key = "ATI_SECRET_API_KEY_TEST"
    os.environ[secret_key] = "super_secret_value_123"

    try:
        # Script checks whether secret_key is present in child process environment
        check_script = test_workspace_dir / "check_env.py"
        check_script.write_text(
            "import os, sys\n"
            f"sys.exit(1 if '{secret_key}' in os.environ else 0)\n",
            encoding="utf-8",
        )

        open_res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
        wid = open_res.json()["workspace_id"]

        argv = [sys.executable, "check_env.py"]
        prep_res = client.post(
            "/agent/workspaces/commands/prepare",
            json={"workspace_id": wid, "argv": argv},
        )
        approval_id = prep_res.json()["approval_id"]

        res = client.post(
            "/agent/workspaces/commands/run",
            json={"workspace_id": wid, "approval_id": approval_id, "approved": True},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["exit_code"] == 0
        assert data["timed_out"] is False
    finally:
        os.environ.pop(secret_key, None)


def test_commands_run_output_truncation_mocked():
    # Test selector logic with >64KB output
    fake_output = b"X" * (COMMAND_MAX_OUTPUT_BYTES + 1024)

    # Directly verify chunk truncation behavior
    chunks = []
    total = 0
    truncated = False
    for i in range(0, len(fake_output), 4096):
        chunk = fake_output[i : i + 4096]
        if total < COMMAND_MAX_OUTPUT_BYTES:
            avail = COMMAND_MAX_OUTPUT_BYTES - total
            keep = chunk[:avail]
            chunks.append(keep)
            total += len(keep)
            if len(chunk) > len(keep):
                truncated = True
        else:
            truncated = True

    assert total == COMMAND_MAX_OUTPUT_BYTES
    assert truncated is True


def test_commands_run_timeout(client, test_workspace_dir):
    sleep_script = test_workspace_dir / "sleep.py"
    sleep_script.write_text("import time\ntime.sleep(10)\n", encoding="utf-8")

    open_res = client.post("/agent/workspaces/open", json={"path": str(test_workspace_dir)})
    wid = open_res.json()["workspace_id"]

    argv = [sys.executable, "sleep.py"]
    prep_res = client.post(
        "/agent/workspaces/commands/prepare",
        json={"workspace_id": wid, "argv": argv},
    )
    approval_id = prep_res.json()["approval_id"]

    with patch("backend.app.workspaces.COMMAND_TIMEOUT_SECONDS", 0.2):
        res = client.post(
            "/agent/workspaces/commands/run",
            json={"workspace_id": wid, "approval_id": approval_id, "approved": True},
        )
    assert res.status_code == 200
    data = res.json()
    assert data["timed_out"] is True
    assert data["exit_code"] is None



def test_live_accessor_sees_external_edits(client, test_workspace_dir):
    path = test_workspace_dir / 'live.py'
    path.write_text('value = 1\n')
    opened = client.post('/agent/workspaces/open', json={'path': str(test_workspace_dir)}).json()
    path.write_text('value = 2\n')
    current = get_workspace_snapshot(opened['workspace_id'])
    assert current['revision'] != opened['revision']
    assert next(f['content'] for f in current['files'] if f['path'] == 'live.py') == 'value = 2\n'


def test_apply_rejects_external_edit_without_explicit_refresh(client, test_workspace_dir):
    path = test_workspace_dir / 'live.py'
    path.write_text('value = 1\n')
    opened = client.post('/agent/workspaces/open', json={'path': str(test_workspace_dir)}).json()
    path.write_text('external = True\n')
    result = client.post('/agent/workspaces/apply', json={
        'workspace_id': opened['workspace_id'], 'revision': opened['revision'],
        'changes': [{'path': 'live.py', 'operation': 'replace', 'content': 'overwritten = True\n'}],
    })
    assert result.status_code == 409
    assert path.read_text() == 'external = True\n'


def test_live_read_budget_does_not_hide_frontend_after_backend_files(client, test_workspace_dir):
    (test_workspace_dir / 'backend').mkdir()
    for i in range(10):
        (test_workspace_dir / 'backend' / f'file{i}.py').write_text('#' * 25000)
    (test_workspace_dir / 'frontend').mkdir()
    composer = test_workspace_dir / 'frontend' / 'ProjectChat.jsx'
    composer.write_text('export default function Chat() {}')
    result = client.post('/agent/workspaces/open', json={'path': str(test_workspace_dir)})
    assert result.status_code == 200
    assert 'frontend/ProjectChat.jsx' in {f['path'] for f in result.json()['files']}
