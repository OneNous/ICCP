"""
Incremental PWM only (v1) — no PID.

Safe for electrochemical loads: worst case is slow convergence, not current spikes
from badly tuned PID.
"""

from __future__ import annotations

from typing import Any

import config.settings as cfg

try:
    import RPi.GPIO as GPIO  # noqa: N814
except ImportError:
    GPIO = None  # type: ignore[misc, assignment]


class PWMController:
    def __init__(self, use_hw: bool) -> None:
        self._use_hw = use_hw and GPIO is not None
        self._duty: dict[int, float] = {i: 0.0 for i in range(cfg.NUM_CHANNELS)}
        self._pwm: dict[int, Any] = {}
        self._gpio_ready = False

    def setup(self) -> None:
        if not self._use_hw:
            return
        for i, pin in enumerate(cfg.PWM_GPIO_PINS):
            GPIO.setup(pin, GPIO.OUT)
            pwm = GPIO.PWM(pin, cfg.PWM_FREQUENCY_HZ)
            pwm.start(0)
            self._pwm[i] = pwm
        self._gpio_ready = True

    def shutdown(self) -> None:
        if not self._gpio_ready:
            return
        for i, pwm in self._pwm.items():
            try:
                pwm.ChangeDutyCycle(0)
                pwm.stop()
            except Exception:
                pass
        self._pwm.clear()
        self._gpio_ready = False

    def _apply_hw(self) -> None:
        if not self._gpio_ready:
            return
        for i, pwm in self._pwm.items():
            pwm.ChangeDutyCycle(int(round(self._duty.get(i, 0.0))))

    def update(
        self,
        readings: dict[int, dict],
        wet: bool,
        fault_latched: bool,
    ) -> None:
        """
        Incremental duty toward TARGET_MA per channel.
        If dry or latched, force duty to 0.
        """
        if fault_latched or not wet:
            for i in self._duty:
                self._duty[i] = 0.0
            self._apply_hw()
            return

        for ch, reading in readings.items():
            if ch >= cfg.NUM_CHANNELS or not reading.get("ok"):
                continue
            cur = float(reading["current"])
            d = self._duty.get(ch, 0.0)
            if cur < cfg.TARGET_MA:
                d = min(d + cfg.PWM_STEP, cfg.PWM_MAX_DUTY)
            elif cur > cfg.TARGET_MA:
                d = max(d - cfg.PWM_STEP, cfg.PWM_MIN_DUTY)
            self._duty[ch] = d

        self._apply_hw()

    def duties(self) -> dict[int, float]:
        return dict(self._duty)
