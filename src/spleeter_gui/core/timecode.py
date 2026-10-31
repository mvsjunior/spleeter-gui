"""Times as the user reads and types them."""


def parse_time(text: str) -> float | None:
    """Reads "90", "12,5", "1:30" or "1:02:03" as seconds. Empty text gives None."""
    text = text.strip().replace(",", ".")
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError(f"Tempo inválido: {text}")
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        raise ValueError(f"Tempo inválido: {text}") from None
    if any(n < 0 or n != n or n == float("inf") for n in numbers):
        raise ValueError(f"Tempo inválido: {text}")
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    return seconds


def format_time(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
