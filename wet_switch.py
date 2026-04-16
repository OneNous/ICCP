"""Wet switch with mandatory debounce."""

from __future__ import annotations

import time

import config.settings as cfg

try:
    import RPi.GPIO as GPIO  # noqa: N814
except ImportError:
    GPIO = None  # type: ignore[misc, assignment]


class WetSwitch:
    def __init__(self, use_hw: bool, sim_assume_wet: bool) -> None:
        self._use_hw = use_hw and GPIO is not None
        self._sim_assume_wet = sim_assume_wet
        self._stable_wet = False
        self._candidate = False
        self._candidate_since = time.monotonic()

    def setup(self) -> None:
        if not self._use_hw:
            return
        GPIO.setup(cfg.WET_SWITCH_GPIO, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    def shutdown(self) -> None:
        return

    def read(self) -> bool:
        if not self._use_hw:
            return bool(self._sim_assume_wet)

        raw = GPIO.input(cfg.WET_SWITCH_GPIO) == GPIO.LOW
        now = time.monotonic()
        if raw != self._candidate:
            self._candidate = raw
            self._candidate_since = now
        if (now - self._candidate_since) * 1000.0 >= cfg.WET_DEBOUNCE_MS:
            self._stable_wet = self._candidate
        return self._stable_wet
