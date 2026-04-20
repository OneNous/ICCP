"""Self-commissioning writes commissioning.json."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

import config.settings as cfg
import commissioning
import sensors
from reference import ReferenceElectrode


def test_commissioning_run_writes_json(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path
    comm_path = root / "commissioning.json"
    monkeypatch.setattr(cfg, "PROJECT_ROOT", root)
    monkeypatch.setattr(commissioning, "_COMM_FILE", comm_path)
    import reference as ref_mod

    monkeypatch.setattr(ref_mod, "_COMM_FILE", comm_path)

    monkeypatch.setattr(commissioning, "COMMISSIONING_SETTLE_S", 0)
    monkeypatch.setattr(cfg, "COMMISSIONING_NATIVE_SAMPLE_COUNT", 1)
    monkeypatch.setattr(cfg, "COMMISSIONING_NATIVE_SAMPLE_INTERVAL_S", 0.0)
    monkeypatch.setattr(commissioning, "RAMP_SETTLE_S", 0.0)
    monkeypatch.setattr(commissioning, "CONFIRM_TICKS", 1)
    monkeypatch.setattr(commissioning, "TARGET_RAMP_STEP_MA", 0.05)

    monkeypatch.setattr(commissioning, "_pump_control", lambda *a, **k: None)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    ref = ReferenceElectrode()
    monkeypatch.setattr(
        ReferenceElectrode,
        "read",
        lambda self, duties=None, statuses=None: 210.0,
    )
    monkeypatch.setattr(
        commissioning,
        "_instant_off_ref_mv_and_restore",
        lambda *a, **k: (100.0, 110.0),
    )

    ctrl = SimpleNamespace(
        _pwm=SimpleNamespace(all_off=lambda: None),
        update=lambda readings: None,
        duties=lambda: {i: 40.0 for i in range(cfg.NUM_CHANNELS)},
        channel_statuses=lambda: {i: "PROTECTING" for i in range(cfg.NUM_CHANNELS)},
    )

    commissioning.run(ref, ctrl, sim_state=sensors.SimSensorState(), verbose=False)

    assert comm_path.is_file()
    data = json.loads(comm_path.read_text())
    assert "native_mv" in data
    assert "commissioned_target_ma" in data
    assert "commissioned_at" in data
    assert isinstance(data["native_mv"], (int, float))
    assert data.get("final_shift_mv") == 110.0
