import json

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
    monkeypatch.setattr("scripts.launcher.reachable", lambda _url: True)

    assert launcher.healthy_instance() is True


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
