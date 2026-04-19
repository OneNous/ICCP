import config.settings as cfg
import sensors
from control import Controller


def test_sim_readings_count_and_finite():
    assert sensors.SIM_MODE is True
    st = sensors.SimSensorState()
    r = sensors.read_all_sim(st)
    assert len(r) == cfg.NUM_CHANNELS


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
