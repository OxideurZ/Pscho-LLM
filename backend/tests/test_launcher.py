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
        security_enabled=False,
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
    monkeypatch.setattr("scripts.launcher.reachable", lambda _url, **_kwargs: False)

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
    monkeypatch.setattr("scripts.launcher.reachable", lambda _url, **_kwargs: True)

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
    monkeypatch.setattr("scripts.launcher.reachable", lambda _url: True)

    try:
        launcher.assert_ports_available()
    except RuntimeError as error:
        assert "aucun processus n’a été arrêté" in str(error)
    else:
        raise AssertionError("a foreign port collision must be reported")


def test_partial_owned_stack_is_fully_cleaned_before_restart(tmp_path, monkeypatch):
    launcher = Launcher(settings_for_launcher(tmp_path))
    stopped = []
    monkeypatch.setattr(
        "scripts.launcher.discover_processes",
        lambda *tokens: [123] if "uvicorn" in tokens else [456, 789],
    )
    monkeypatch.setattr("scripts.launcher.terminate_owned", lambda pid: stopped.append(pid) or True)

    launcher.cleanup_owned_processes()

    assert set(stopped) == {123, 456, 789}


def test_start_recovers_a_healthy_discovered_stack(tmp_path, monkeypatch):
    launcher = Launcher(settings_for_launcher(tmp_path))
    opened = []
    recovered = Mock(return_value=Mock())
    monkeypatch.setattr(launcher, "healthy_instance", lambda: False)
    monkeypatch.setattr(launcher, "recover_healthy_instance", recovered)
    monkeypatch.setattr("scripts.launcher.reachable", lambda _url: True)
    monkeypatch.setattr("scripts.launcher.webbrowser.open", opened.append)

    launcher.start(open_browser=True)

    assert opened == ["http://127.0.0.1:18000/"]
    recovered.assert_called_once_with()


def test_recover_healthy_instance_records_discovered_root_processes(tmp_path, monkeypatch):
    launcher = Launcher(settings_for_launcher(tmp_path))
    monkeypatch.setattr(
        "scripts.launcher.discover_process",
        lambda *tokens: 123 if "uvicorn" in tokens else 456,
    )

    recovered = launcher.recover_healthy_instance()

    assert recovered is not None
    assert recovered.backend_pid == 123
    assert recovered.llama_pid == 456
    assert launcher.read_instance() == recovered


def test_secure_browser_url_uses_fragment_not_query_string(tmp_path, monkeypatch):
    settings = settings_for_launcher(tmp_path).model_copy(update={"security_enabled": True})
    launcher = Launcher(settings)
    monkeypatch.setattr("scripts.launcher.WindowsDpapiSecretStore.get", lambda *_args: b"secret")

    url = launcher.browser_url()

    assert "?" not in url
    assert url.startswith("http://127.0.0.1:18000/#bootstrap=")


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
