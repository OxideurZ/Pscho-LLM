from types import SimpleNamespace

import pytest

from backend.app.config import Settings
from backend.app.runtime import RuntimeNotManagedError, RuntimeOffloadService
from scripts.offload_runtime import offload


def test_offload_service_refuses_an_unmanaged_process(tmp_path) -> None:
    settings = Settings(
        data_directory=tmp_path,
        database_path=tmp_path / "app.sqlite",
        model_expected_sha256="a" * 64,
    )
    with pytest.raises(RuntimeNotManagedError):
        RuntimeOffloadService(settings, tmp_path).schedule()


def test_offload_stops_model_before_backend_and_removes_state(tmp_path, monkeypatch) -> None:
    events: list[object] = []
    state_path = tmp_path / "instance.json"
    state_path.write_text("{}", encoding="utf-8")
    instance = SimpleNamespace(llama_pid=11, backend_pid=22)

    class FakeLauncher:
        instance_path = state_path

        def __init__(self, _settings) -> None:
            pass

        def read_instance(self):
            return instance

        def log(self, message: str) -> None:
            events.append(message)

    monkeypatch.setattr("scripts.offload_runtime.Launcher", FakeLauncher)
    monkeypatch.setattr("scripts.offload_runtime.process_identity_matches", lambda *_args: True)
    monkeypatch.setattr(
        "scripts.offload_runtime.terminate_owned",
        lambda process_id: events.append(process_id) or True,
    )

    assert offload(Settings(data_directory=tmp_path, database_path=tmp_path / "app.sqlite"))
    assert events[0] == 11
    assert events[-1] == 22
    assert not state_path.exists()
