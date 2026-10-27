import subprocess
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.workspaces import FOLDER_PICKER_LOCK


def browse():
    return TestClient(app).post('/agent/workspaces/browse', json={})


def test_browse_returns_validated_folder_without_connecting():
    with patch('backend.app.workspaces.sys.platform', 'darwin'), patch('backend.app.workspaces.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '/Users/example/project/\n', '')) as run, patch('backend.app.workspaces.validate_workspace_root', return_value='/Users/example/project') as validate:
        result = browse()
    assert result.status_code == 200
    assert result.json() == {'path': '/Users/example/project'}
    validate.assert_called_once_with('/Users/example/project/')
    assert run.call_args.args[0][0] == '/usr/bin/osascript'
    assert 'shell' not in run.call_args.kwargs


def test_cancel_preserves_no_selection():
    with patch('backend.app.workspaces.sys.platform', 'darwin'), patch('backend.app.workspaces.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '\n', '')):
        assert browse().json() == {'path': None}


def test_unsupported_platform_has_path_fallback():
    with patch('backend.app.workspaces.sys.platform', 'linux'), patch('backend.app.workspaces.subprocess.run') as run:
        assert browse().status_code == 501
    run.assert_not_called()


def test_picker_timeout_releases_lock():
    with patch('backend.app.workspaces.sys.platform', 'darwin'), patch('backend.app.workspaces.subprocess.run', side_effect=subprocess.TimeoutExpired('osascript', 180)):
        assert browse().status_code == 504
    assert FOLDER_PICKER_LOCK.acquire(blocking=False)
    FOLDER_PICKER_LOCK.release()


def test_picker_error_does_not_expose_stderr():
    with patch('backend.app.workspaces.sys.platform', 'darwin'), patch('backend.app.workspaces.subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'private-details')):
        result = browse()
    assert result.status_code == 503
    assert 'private-details' not in result.text


def test_only_one_picker_can_open():
    with patch('backend.app.workspaces.sys.platform', 'darwin'):
        FOLDER_PICKER_LOCK.acquire()
        try:
            assert browse().status_code == 409
        finally:
            FOLDER_PICKER_LOCK.release()
