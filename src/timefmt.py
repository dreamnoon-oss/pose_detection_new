"""Shared time formatting: seconds → HH:MM:SS(.d)."""


def format_hms(seconds, *, decimals=0):
    """Format seconds as ``HH:MM:SS`` (with optional decimal on seconds)."""
    if seconds is None:
        return "—"
    seconds = round(max(0.0, float(seconds)), decimals)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    if decimals > 0:
        width = 3 + decimals  # "SS." + decimals
        return f"{h:02d}:{m:02d}:{s:0{width}.{decimals}f}"
    return f"{h:02d}:{m:02d}:{int(s):02d}"
