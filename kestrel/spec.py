"""Specification parsing utilities."""


def parse_freq(text: str) -> float:
    """Parse a frequency string with SI suffix (e.g. '1.5G', '100M', '50k')."""
    text = text.strip()
    if not text:
        raise ValueError("empty frequency")
    suffixes = {
        "T": 1e12, "G": 1e9, "M": 1e6, "k": 1e3, "K": 1e3,
        "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12,
    }
    if text[-1] in suffixes:
        return float(text[:-1]) * suffixes[text[-1]]
    return float(text)


def parse_time(text: str) -> float:
    """Parse a time string with SI suffix (e.g. '2ps', '5n')."""
    text = text.strip()
    if not text:
        raise ValueError("empty time")
    suffixes = {"s": 1, "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15}
    for i in range(len(text) - 1, 0, -1):
        if text[i:] in suffixes:
            return float(text[:i]) * suffixes[text[i:]]
        if text[i:].rstrip("s") in suffixes:
            return float(text[:i]) * suffixes[text[i:].rstrip("s")]
    return float(text)
