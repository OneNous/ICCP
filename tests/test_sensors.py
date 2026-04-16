import config.settings as cfg
import sensors
from control import Controller


def test_sim_readings_count_and_finite():
    assert sensors.SIM_MODE is True
    st = sensors.SimSensorState()
    r = sensors.read_all_sim(st)
    assert len(r) == cfg.NUM_CHANNELS


def test_controller_latches_sim_injected_overcurrent():
    prev = cfg.SIM_INJECT_FAULT_CH
    try:
        cfg.SIM_INJECT_FAULT_CH = 0
        st = sensors.SimSensorState()
        ctrl = Controller()
        r = sensors.read_all_sim(st)
        faults, latched = ctrl.update(r)
        assert any("OVERCURRENT" in f for f in faults)
        assert latched is True
    finally:
        cfg.SIM_INJECT_FAULT_CH = prev


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
