"""console_ui helpers — shunt one-liner for commissioning."""

from __future__ import annotations

from console_ui import commission_ina_compact


def test_commission_ina_compact_mark_highest() -> None:
    r = {
        0: {"ok": True, "current": 0.0},
        1: {"ok": True, "current": 0.0},
        2: {"ok": True, "current": 0.098},
        3: {"ok": True, "current": 0.0},
    }
    s = commission_ina_compact(r, num_channels=4, mark_highest_shunt=True)
    assert "max|I| A3" in s
    s2 = commission_ina_compact(r, num_channels=4, mark_highest_shunt=False)
    assert "max|I|" not in s2
