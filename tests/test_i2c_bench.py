"""i2c_bench constants align with pi-ina219 + TI ADS1115 timing."""

from __future__ import annotations

import math
import time

from i2c_bench import (
    INA219_DEFAULT_CONFIG_WORD,
    _ads1115_config_word,
    _ads1115_dr_conversion_s,
    ads1115_behind_i2c_mux,
    ads1115_wait_os_ready,
)


class _FakeAds1115Bus:
    """SMBus-like fake: config reads increment counter; OS high after N reads."""

    def __init__(self, os_ready_after_cfg_reads: int) -> None:
        self._cfg_reads = 0
        self._after = max(1, int(os_ready_after_cfg_reads))

    def read_i2c_block_data(self, addr: int, reg: int, nbytes: int) -> list[int]:
        if reg == 0x01:
            self._cfg_reads += 1
            if self._cfg_reads >= self._after:
                status = 0x8000 | 0x0480
            else:
                status = 0x0480
            return [(status >> 8) & 0xFF, status & 0xFF]
        return [0, 0]

    def write_i2c_block_data(self, addr: int, reg: int, data: list[int]) -> None:
        pass


def test_ina219_default_config_matches_pi_ina219_formula() -> None:
    # pi-ina219 INA219._configure: vr<<13 | gain<<11 | bus_adc<<7 | shunt_adc<<3 | 7
    cfg = (0 << 13) | (0 << 11) | (15 << 7) | (15 << 3) | 7
    assert cfg == 0x07FF
    assert INA219_DEFAULT_CONFIG_WORD == 0x07FF
    assert (INA219_DEFAULT_CONFIG_WORD >> 13) & 1 == 0  # BRNG = 16 V range
    assert (INA219_DEFAULT_CONFIG_WORD >> 11) & 3 == 0  # PGA ÷1 → 10 µV shunt LSB


def test_ads1115_dr_conversion_inverse_of_dr() -> None:
    assert math.isclose(_ads1115_dr_conversion_s(5), 1 / 250.0, rel_tol=0, abs_tol=1e-9)
    assert math.isclose(_ads1115_dr_conversion_s(0), 1 / 8.0, rel_tol=0, abs_tol=1e-9)


def test_ads1115_config_word_dr_field() -> None:
    w = _ads1115_config_word(0, 4.096, dr=5)
    assert ((w >> 5) & 7) == 5
    assert (w & 3) == 0  # COMP_QUE != 11 for ALERT/RDY conversion-ready mode
    w8 = _ads1115_config_word(2, 4.096, dr=0)
    assert ((w8 >> 5) & 7) == 0
    assert (w8 & 3) == 0


def test_ads1115_behind_i2c_mux_gate() -> None:
    assert ads1115_behind_i2c_mux(None, None) is False
    assert ads1115_behind_i2c_mux(0x70, None) is False
    assert ads1115_behind_i2c_mux(None, 4) is False
    assert ads1115_behind_i2c_mux(0x70, 4) is True


def test_ads1115_wait_os_ready_true_after_polls() -> None:
    bus = _FakeAds1115Bus(os_ready_after_cfg_reads=4)
    assert ads1115_wait_os_ready(bus, 0x48, deadline_s=1.0, poll_interval_s=0.001) is True
    assert bus._cfg_reads == 4


def test_ads1115_wait_os_ready_false_on_timeout() -> None:
    bus = _FakeAds1115Bus(os_ready_after_cfg_reads=10_000)
    t0 = time.monotonic()
    assert ads1115_wait_os_ready(bus, 0x48, deadline_s=0.02, poll_interval_s=0.005) is False
    assert time.monotonic() - t0 < 0.15
    assert bus._cfg_reads >= 2
