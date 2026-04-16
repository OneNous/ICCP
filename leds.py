"""Status LED (BCM). No-op in simulator / without GPIO."""

from __future__ import annotations

import config.settings as cfg

try:
    import RPi.GPIO as GPIO  # noqa: N814
except ImportError:
    GPIO = None  # type: ignore[misc, assignment]


class StatusLEDs:
    def __init__(self, use_hw: bool) -> None:
        self._use_hw = (
            use_hw
            and GPIO is not None
            and cfg.LED_STATUS_GPIO is not None
        )
        self._ready = False

    def setup(self) -> None:
        if not self._use_hw:
            return
        GPIO.setup(cfg.LED_STATUS_GPIO, GPIO.OUT)
        self._ready = True

    def shutdown(self) -> None:
        if not self._ready:
            return
        try:
            GPIO.output(cfg.LED_STATUS_GPIO, GPIO.LOW)
        except Exception:
            pass
        self._ready = False

    def set_running_ok(self, ok: bool) -> None:
        if not self._ready:
            return
        GPIO.output(cfg.LED_STATUS_GPIO, GPIO.HIGH if ok else GPIO.LOW)
