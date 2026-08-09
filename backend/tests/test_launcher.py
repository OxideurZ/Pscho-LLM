import json
from unittest.mock import Mock

from backend.app.config.settings import Settings
from scripts.launcher import Instance, Launcher


def settings_for_launcher(tmp_path):
    return Settings(
        data_directory=tmp_path,
        database_path=tmp_path / "data" / "app.sqlite",
        psych_local_port=18000,
        llama_server_url="http://127.0.0.1:18080",
    )


def test_stale_instance_is_not_trusted_from_pid_alone(tmp_path, monkeypatch):
    launcher = Launcher(settings_for_launcher(tmp_path))
    launcher.instance_path.write_text(
        json.dumps(
            Instance(
                instance_id="instance-a",
                started_at="2026-08-09T00:00:00Z",
                launcher_pid=1,
                backend_pid=123,
                llama_pid=456,
                frontend_pid=None,
                backend_port=18000,
                llama_port=18080,
            ).__dict__
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.launcher.pid_alive", lambda _pid: True)
    monkeypatch.setattr("scripts.launcher.process_identity_matches", lambda *_args: True)
    monkeypatch.setattr("scripts.launcher.reachable", lambda _url: False)

    assert launcher.healthy_instance() is False
    launcher.clear_stale()
    assert not launcher.instance_path.exists()


def test_healthy_instance_requires_matching_ports_pids_and_health(tmp_path, monkeypatch):
    launcher = Launcher(settings_for_launcher(tmp_path))
    launcher.instance_path.write_text(
        json.dumps(
            Instance(
                instance_id="instance-a",
                started_at="2026-08-09T00:00:00Z",
                launcher_pid=1,
                backend_pid=123,
                llama_pid=456,
                frontend_pid=None,
                backend_port=18000,
                llama_port=18080,
            ).__dict__
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.launcher.pid_alive", lambda _pid: True)
    monkeypatch.setattr("scripts.launcher.process_identity_matches", lambda *_args: True)
    monkeypatch.setattr("scripts.launcher.reachable", lambda _url: True)

    assert launcher.healthy_instance() is True


def test_reused_pid_with_a_third_party_command_is_not_owned(tmp_path, monkeypatch):
    launcher = Launcher(settings_for_launcher(tmp_path))
    launcher.instance_path.write_text(
        json.dumps(
            Instance(
                instance_id="instance-a",
                started_at="2026-08-09T00:00:00Z",
                launcher_pid=1,
                backend_pid=123,
                llama_pid=456,
                frontend_pid=None,
                backend_port=18000,
                llama_port=18080,
            ).__dict__
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.launcher.pid_alive", lambda _pid: True)
    monkeypatch.setattr("scripts.launcher.process_identity_matches", lambda *_args: False)
    monkeypatch.setattr("scripts.launcher.reachable", lambda _url: True)

    assert launcher.healthy_instance() is False


def test_port_collision_never_attempts_to_kill_a_third_party(tmp_path, monkeypatch):
    launcher = Launcher(settings_for_launcher(tmp_path))
    monkeypatch.setattr("scripts.launcher.port_is_free", lambda *_args: False)
    monkeypatch.setattr("scripts.launcher.reachable", lambda _url: False)

    try:
        launcher.assert_ports_available()
    except RuntimeError as error:
        assert "aucun processus n’a été arrêté" in str(error)
    else:
        raise AssertionError("a foreign port collision must be reported")


def test_start_reuses_a_healthy_discovered_stack_without_pid_file(tmp_path, monkeypatch):
    launcher = Launcher(settings_for_launcher(tmp_path))
    opened = []
    monkeypatch.setattr(launcher, "healthy_instance", lambda: False)
    monkeypatch.setattr("scripts.launcher.reachable", lambda _url: True)
    monkeypatch.setattr("scripts.launcher.webbrowser.open", opened.append)

    launcher.start(open_browser=True)

    assert opened == ["http://127.0.0.1:18000/"]
    assert not launcher.instance_path.exists()


def test_windows_termination_forces_the_verified_process_tree(monkeypatch):
    completed = Mock()
    monkeypatch.setattr("scripts.launcher.sys.platform", "win32")
    monkeypatch.setattr("scripts.launcher.subprocess.run", completed)

    from scripts.launcher import terminate_owned

    monkeypatch.setattr("scripts.launcher.pid_alive", lambda _pid: True)
    terminate_owned(321)

    assert completed.call_args.args[0] == ["taskkill", "/PID", "321", "/T", "/F"]


def test_pid_alive_uses_psutil_not_windows_signal_semantics(monkeypatch):
    process = Mock()
    process.is_running.return_value = True
    process.status.return_value = "running"
    monkeypatch.setattr("scripts.launcher.psutil.Process", lambda _pid: process)

    from scripts.launcher import pid_alive

    assert pid_alive(321) is True
