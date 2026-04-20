"""ADS1115 ALRT: wait_for_edge failure latches polling for process lifetime (no per-OC reset)."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

import config.settings as cfg


def test_wait_for_edge_failure_not_retried_on_second_oc_burst(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import i2c_bench
    import reference as ref

    monkeypatch.setattr(ref, "SIM_MODE", False)
    monkeypatch.setattr(ref, "_ref_smbus", MagicMock())
    monkeypatch.setattr(ref, "_ADS_ALRT_GPIO_SETUP", True)
    monkeypatch.setattr(ref, "_ADS_ALRT_WAIT_EDGE_BROKEN", False)
    monkeypatch.setattr(ref, "_ensure_ads_alrt_gpio", lambda: 24)

    monkeypatch.setattr(cfg, "ADS1115_ALRT_GPIO", 24)
    monkeypatch.setattr(cfg, "ADS1115_ALRT_USE_WAIT_FOR_EDGE", True)
    monkeypatch.setattr(cfg, "REF_ADC_BACKEND", "ads1115")
    monkeypatch.setattr(cfg, "I2C_MUX_ADDRESS", None)
    monkeypatch.setattr(cfg, "I2C_MUX_CHANNEL_ADS1115", None)
    monkeypatch.setattr(cfg, "COMMISSIONING_OC_BURST_SAMPLES", 2)
    monkeypatch.setattr(cfg, "COMMISSIONING_OC_BURST_INTERVAL_S", 0.0)
    monkeypatch.setattr(cfg, "COMMISSIONING_OC_ADS_MEDIAN_SAMPLES", 1)

    monkeypatch.setattr(i2c_bench, "mux_select_on_bus", lambda *a, **k: None)
    monkeypatch.setattr(i2c_bench, "ads1115_start_single_shot", lambda *a, **k: None)
    monkeypatch.setattr(i2c_bench, "_ads1115_dr_conversion_s", lambda dr: 0.001)
    monkeypatch.setattr(i2c_bench, "ads1115_config_os_ready", lambda *a, **k: False)
    monkeypatch.setattr(i2c_bench, "ads1115_wait_os_ready", lambda *a, **k: True)
    monkeypatch.setattr(
        i2c_bench, "ads1115_read_conversion_volts", lambda *a, **k: 0.002
    )

    class _FakeGPIO:
        FALLING = BCM = IN = PUD_UP = OUT = 0

        @staticmethod
        def setwarnings(_x: object) -> None:
            return

        @staticmethod
        def setup(*_a: object, **_k: object) -> None:
            return

        wait_for_edge = MagicMock(side_effect=RuntimeError("Error waiting for edge"))

    rpi = types.ModuleType("RPi")
    rpi.GPIO = _FakeGPIO  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "RPi", rpi)
    monkeypatch.setitem(sys.modules, "RPi.GPIO", _FakeGPIO)

    el = ref.ReferenceElectrode()
    el.collect_oc_decay_samples()
    el.collect_oc_decay_samples()
    assert _FakeGPIO.wait_for_edge.call_count == 1


def test_wait_for_edge_timeout_diag_once(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import i2c_bench
    import reference as ref

    monkeypatch.setattr(ref, "SIM_MODE", False)
    monkeypatch.setattr(ref, "_ref_smbus", MagicMock())
    monkeypatch.setattr(ref, "_ADS_ALRT_GPIO_SETUP", True)
    monkeypatch.setattr(ref, "_ADS_ALRT_WAIT_EDGE_BROKEN", False)
    monkeypatch.setattr(ref, "_ALRT_DIAG_LOGGED_TIMEOUT", False)
    monkeypatch.setattr(ref, "_ALRT_DIAG_LOGGED_RUNTIME", False)
    monkeypatch.setattr(ref, "_ensure_ads_alrt_gpio", lambda: 24)

    monkeypatch.setattr(cfg, "ADS1115_ALRT_GPIO", 24)
    monkeypatch.setattr(cfg, "ADS1115_ALRT_USE_WAIT_FOR_EDGE", True)
    monkeypatch.setattr(cfg, "REF_ADC_BACKEND", "ads1115")
    monkeypatch.setattr(cfg, "I2C_MUX_ADDRESS", None)
    monkeypatch.setattr(cfg, "I2C_MUX_CHANNEL_ADS1115", None)
    monkeypatch.setattr(cfg, "COMMISSIONING_OC_BURST_SAMPLES", 1)
    monkeypatch.setattr(cfg, "COMMISSIONING_OC_BURST_INTERVAL_S", 0.0)
    monkeypatch.setattr(cfg, "COMMISSIONING_OC_ADS_MEDIAN_SAMPLES", 1)

    monkeypatch.setattr(i2c_bench, "mux_select_on_bus", lambda *a, **k: None)
    monkeypatch.setattr(i2c_bench, "ads1115_start_single_shot", lambda *a, **k: None)
    monkeypatch.setattr(i2c_bench, "_ads1115_dr_conversion_s", lambda dr: 0.001)
    monkeypatch.setattr(i2c_bench, "ads1115_config_os_ready", lambda *a, **k: False)
    monkeypatch.setattr(i2c_bench, "ads1115_wait_os_ready", lambda *a, **k: True)
    monkeypatch.setattr(
        i2c_bench, "ads1115_read_conversion_volts", lambda *a, **k: 0.002
    )
    monkeypatch.setattr(i2c_bench, "ads1115_read_config_word", lambda *a, **k: 0)

    class _FakeGPIO:
        FALLING = BCM = IN = PUD_UP = OUT = 0

        @staticmethod
        def setwarnings(_x: object) -> None:
            return

        @staticmethod
        def setup(*_a: object, **_k: object) -> None:
            return

        @staticmethod
        def input(_pin: int) -> int:
            return 1

        wait_for_edge = MagicMock(return_value=None)

    rpi = types.ModuleType("RPi")
    rpi.GPIO = _FakeGPIO  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "RPi", rpi)
    monkeypatch.setitem(sys.modules, "RPi.GPIO", _FakeGPIO)

    ref.ReferenceElectrode().collect_oc_decay_samples()
    out = capsys.readouterr().out
    assert "timed out" in out
    assert "DIAG: ADS1115 ALRT" in out

    ref.ReferenceElectrode().collect_oc_decay_samples()
    out2 = capsys.readouterr().out
    assert "DIAG: ADS1115 ALRT" not in out2
