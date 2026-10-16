"""Soundboard-only tempo adjustment; pitch and source files stay unchanged."""


def speed_percent(value):
    try:
        import math
        value = float(value)
        return max(-200, min(200, value)) if math.isfinite(value) else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def speed_factor(value):
    value = speed_percent(value)
    return 1 + value / 100 if value >= 0 else 1 / (1 - value / 100)


def tempo_filter(value):
    factor = speed_factor(value)
    parts = []
    while factor < .5:
        parts.append("atempo=0.5")
        factor /= .5
    while factor > 2:
        parts.append("atempo=2")
        factor /= 2
    parts.append(f"atempo={factor:.8g}")
    return ",".join(parts)


SPEED_PRESETS = (-200, -100, -100 / 3, 0, 50, 100, 200)
SPEED_LABELS = ("⅓×", "½×", "¾×", "100%", "1.5×", "2×", "3×")


def preset_index(value):
    factor = speed_factor(value)
    return min(range(len(SPEED_PRESETS)), key=lambda n: abs(speed_factor(SPEED_PRESETS[n]) - factor))
