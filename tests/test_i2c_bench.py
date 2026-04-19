"""i2c_bench constants align with pi-ina219 + TI ADS1115 timing."""

from __future__ import annotations

import math

from i2c_bench import (
    INA219_DEFAULT_CONFIG_WORD,
    _ads1115_config_word,
    _ads1115_dr_conversion_s,
)


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
    w8 = _ads1115_config_word(2, 4.096, dr=0)
    assert ((w8 >> 5) & 7) == 0
