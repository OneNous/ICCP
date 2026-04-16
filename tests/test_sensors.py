import config.settings as cfg
import safety
import sensors


def test_sim_readings_count_and_finite():
    assert sensors.SIM_MODE is True
    st = sensors.SimSensorState()
    r = sensors.read_all_sim(st)
    assert len(r) == cfg.NUM_CHANNELS


def test_safety_overcurrent():
    readings = {
        0: {"ok": True, "current": cfg.MAX_MA + 0.5, "bus_v": 11.0, "error": ""},
    }
    f = safety.evaluate(readings, wet=False)
    assert any("OVERCURRENT" in x for x in f)


def test_safety_undervoltage_overvoltage():
    low = {0: {"ok": True, "current": 0.1, "bus_v": cfg.MIN_BUS_V - 0.5, "error": ""}}
    assert any("UNDERVOLTAGE" in x for x in safety.evaluate(low, wet=False))
    high = {0: {"ok": True, "current": 0.1, "bus_v": cfg.MAX_BUS_V + 0.5, "error": ""}}
    assert any("OVERVOLTAGE" in x for x in safety.evaluate(high, wet=False))


def test_safety_bonding_when_wet():
    readings = {
        0: {"ok": True, "current": 0.01, "bus_v": 11.0, "error": ""},
        1: {"ok": True, "current": 0.01, "bus_v": 11.0, "error": ""},
    }
    f = safety.evaluate(readings, wet=True)
    assert any("BONDING" in x for x in f)


def test_sim_injected_fault_triggers_safety():
    prev = cfg.SIM_INJECT_FAULT_CH
    try:
        cfg.SIM_INJECT_FAULT_CH = 1
        st = sensors.SimSensorState()
        r = sensors.read_all_sim(st)
        f = safety.evaluate(r, wet=False)
        assert any("OVERCURRENT" in x for x in f)
    finally:
        cfg.SIM_INJECT_FAULT_CH = prev
