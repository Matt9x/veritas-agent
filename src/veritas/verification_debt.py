import re


_PYTHON_INTERPRETER_NAME = re.compile(r"python(?:\d+)?(?:\.\d+)?(?:\.exe)?\Z", re.IGNORECASE)


def is_python_interpreter_name(name: str) -> bool:
    """Match a supported interpreter basename, not executable authenticity."""
    basename = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    return _PYTHON_INTERPRETER_NAME.fullmatch(basename) is not None


def normalize_argv(argv: list | tuple) -> tuple[str, ...]:
    """Drop a leading interpreter so the spec's check and the model's command can
    be compared. The spec pins an absolute interpreter path; the model types
    ``python``; both describe the same check. This is syntactic normalization,
    not cryptographic or runtime executable attestation."""
    items = [str(item) for item in argv]
    if items and is_python_interpreter_name(items[0]):
        items = items[1:]
    return tuple(items)
