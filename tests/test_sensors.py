from unittest.mock import MagicMock

import pytest

import config.settings as cfg
import sensors
from control import Controller


def test_sim_readings_count_and_finite():
    assert sensors.SIM_MODE is True
    st = sensors.SimSensorState()
    r = sensors.read_all_sim(st)
    assert len(r) == cfg.NUM_CHANNELS
    assert sensors.ina219_sensors_ready() is True


def test_sim_per_channel_offsets_split_bus_and_ma(monkeypatch):
    """With noise disabled, biases still separate channels (not four identical columns)."""
    monkeypatch.setattr(sensors.random, "gauss", lambda *a, **k: 0.0)
    monkeypatch.setattr(
        sensors.SimSensorState,
        "channel_is_wet",
        lambda self, ch, sim_s: False,
    )
    st = sensors.SimSensorState()
    r = sensors.read_all_sim(st)
    cur = [r[i]["current"] for i in range(cfg.NUM_CHANNELS)]
    bus = [r[i]["bus_v"] for i in range(cfg.NUM_CHANNELS)]
    assert min(cur) != max(cur)
    assert min(bus) != max(bus)


def test_controller_latches_sim_injected_overcurrent(monkeypatch):
    prev_ch = cfg.SIM_INJECT_FAULT_CH
    prev_ma = cfg.SIM_INJECT_OVERCURRENT_MA
    try:
        monkeypatch.setattr(cfg, "SIM_INJECT_FAULT_CH", 0)
        monkeypatch.setattr(cfg, "SIM_INJECT_OVERCURRENT_MA", cfg.MAX_MA + 1.0)
        st = sensors.SimSensorState()
        ctrl = Controller()
        r = sensors.read_all_sim(st)
        faults, latched = ctrl.update(r)
        assert any("OVERCURRENT" in f for f in faults)
        assert latched is True
    finally:
        cfg.SIM_INJECT_FAULT_CH = prev_ch
        cfg.SIM_INJECT_OVERCURRENT_MA = prev_ma


def test_controller_latches_undervoltage_reading():
    ctrl = Controller()
    r = {
        0: {"ok": True, "current": 0.1, "bus_v": cfg.MIN_BUS_V - 0.5},
    }
    for i in range(1, cfg.NUM_CHANNELS):
        r[i] = {"ok": True, "current": 0.1, "bus_v": 11.0}
    faults, latched = ctrl.update(r)
    assert any("UNDERVOLTAGE" in f for f in faults)
    assert latched is True


def test_controller_latches_overvoltage_reading():
    ctrl = Controller()
    r = {
        0: {"ok": True, "current": 0.1, "bus_v": cfg.MAX_BUS_V + 0.5},
    }
    for i in range(1, cfg.NUM_CHANNELS):
        r[i] = {"ok": True, "current": 0.1, "bus_v": 11.0}
    faults, latched = ctrl.update(r)
    assert any("OVERVOLTAGE" in f for f in faults)
    assert latched is True


def test_ina219_init_mux_select_failure_no_nameerror(monkeypatch, capsys):
    """Regression: mux_select failure must not mask root cause with NameError on port_desc."""
    import sys
    from types import ModuleType

    mux = getattr(cfg, "I2C_MUX_CHANNELS_INA219", None)
    if mux is None or not mux:
        pytest.skip("test needs per-channel TCA mux (I2C_MUX_CHANNELS_INA219)")

    fake_ina = ModuleType("ina219")

    class _FakeINA219:
        RANGE_16V = 16
        GAIN_AUTO = 1
        ADC_128SAMP = 15

        def __init__(self, *_a, **_k):
            pass

        def configure(self, **_k):
            pass

    fake_ina.INA219 = _FakeINA219
    monkeypatch.setitem(sys.modules, "ina219", fake_ina)

    fake_smbus = ModuleType("smbus2")
    fake_smbus.SMBus = lambda _busnum: MagicMock()
    monkeypatch.setitem(sys.modules, "smbus2", fake_smbus)

    def _boom(*_a, **_k):
        raise OSError(5, "simulated mux EIO")

    monkeypatch.setattr("i2c_bench.mux_select_on_bus", _boom)

    with pytest.raises(OSError) as excinfo:
        sensors._init_ina219_sensor_list_for_import()

    assert excinfo.value.errno == 5
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "NameError" not in combined
    assert "TCA9548A ch" in combined
    assert "simulated mux EIO" in combined
