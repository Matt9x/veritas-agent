from pathlib import Path


def normalize_argv(argv: list | tuple) -> tuple[str, ...]:
    """Drop a leading interpreter so the spec's check and the model's command can
    be compared. The spec pins an absolute interpreter path; the model types
    ``python``; both describe the same check."""
    items = [str(item) for item in argv]
    if items and "python" in Path(items[0]).name.lower():
        items = items[1:]
    return tuple(items)
