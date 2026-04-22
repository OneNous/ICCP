"""Native baseline re-capture scheduling — docs/iccp-requirements.md §2.3 / §8.1.

Covers the three pieces of the "daily" re-capture path:

* `commissioning.native_recapture_due()` reads the `native_recapture_due_unix`
  sentinel persisted by `ReferenceElectrode.save_native`.
* `ReferenceElectrode.save_native` stamps both the absolute measurement time
  and the future `native_recapture_due_unix` equal to `now + NATIVE_RECAPTURE_S`.
* `ReferenceElectrode.next_native_recapture_s` exposes the remaining countdown
  that the runtime reports via telemetry (`next_native_recapture_s`).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import commissioning
import config.settings as cfg
import reference as ref_mod
from reference import ReferenceElectrode


def _wire_comm_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    comm_path = tmp_path / "commissioning.json"
    monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path, raising=False)
    monkeypatch.setattr(commissioning, "_COMM_FILE", comm_path, raising=False)
    monkeypatch.setattr(ref_mod, "_COMM_FILE", comm_path, raising=False)
    return comm_path


def test_save_native_writes_recapture_due_unix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    comm_path = _wire_comm_file(tmp_path, monkeypatch)
    monkeypatch.setattr(cfg, "NATIVE_RECAPTURE_S", 3600.0, raising=False)

    ref = ReferenceElectrode()
    t0 = time.time()
    ref.save_native(-850.0, native_temp_f=72.0)
    data = json.loads(comm_path.read_text(encoding="utf-8"))
    assert data["native_mv"] == -850.0
    assert "native_measured_unix" in data
    assert abs(float(data["native_measured_unix"]) - t0) < 5.0
    assert "native_recapture_due_unix" in data
    assert (
        abs(
            float(data["native_recapture_due_unix"])
            - float(data["native_measured_unix"])
            - 3600.0
        )
        < 0.05
    )


def test_native_recapture_due_fires_only_when_ts_past(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    comm_path = _wire_comm_file(tmp_path, monkeypatch)

    # Due in the future → not due yet.
    comm_path.write_text(
        json.dumps(
            {
                "native_mv": -820.0,
                "native_measured_unix": time.time() - 10.0,
                "native_recapture_due_unix": time.time() + 3600.0,
            }
        ),
        encoding="utf-8",
    )
    assert commissioning.native_recapture_due() is False

    # Due in the past → fire.
    comm_path.write_text(
        json.dumps(
            {
                "native_mv": -820.0,
                "native_measured_unix": time.time() - 10_000.0,
                "native_recapture_due_unix": time.time() - 1.0,
            }
        ),
        encoding="utf-8",
    )
    assert commissioning.native_recapture_due() is True

    # Legacy file without the sentinel → do not fire (avoid spamming re-captures).
    comm_path.write_text(
        json.dumps({"native_mv": -820.0}),
        encoding="utf-8",
    )
    assert commissioning.native_recapture_due() is False


def test_native_recapture_due_tolerates_corrupt_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    comm_path = _wire_comm_file(tmp_path, monkeypatch)
    comm_path.write_text("not-json", encoding="utf-8")
    assert commissioning.native_recapture_due() is False


def test_next_native_recapture_s_counts_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_comm_file(tmp_path, monkeypatch)
    monkeypatch.setattr(cfg, "NATIVE_RECAPTURE_S", 120.0, raising=False)
    ref = ReferenceElectrode()
    ref.save_native(-800.0)
    remaining = ref.next_native_recapture_s()
    assert remaining is not None
    assert 110.0 <= remaining <= 120.0
