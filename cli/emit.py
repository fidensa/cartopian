"""NDJSON record emitter per DEC-008."""
import json
import sys


def emit_record(record: dict, *, out=None) -> None:
    """Emit one NDJSON record to stdout (DEC-008).

    One compact JSON object per line, UTF-8, trailing newline. Bare scalars
    and top-level arrays are code defects and raise TypeError.
    """
    if not isinstance(record, dict):
        raise TypeError(
            f"emit_record requires a dict; got {type(record).__name__}"
        )
    if out is None:
        out = sys.stdout
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    out.write(line + "\n")
