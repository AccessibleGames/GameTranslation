#!/usr/bin/env python3
"""UTF-8 file formats and atomic replacement for toolkit outputs.

Write temporary files beside their destination so publication uses a single
same-filesystem rename. Half-written outputs remain invisible to readers.
"""

import contextlib
import json
import os
import shutil
import tempfile

__all__ = [
    "BOM",
    "append_jsonl",
    "append_text",
    "backup_file",
    "dump_json",
    "load_json",
    "load_text",
    "open_atomic",
    "read_jsonl",
    "write_text",
]

#: Stripped on read, never written.  Kept in one place because three modules
#: used to spell it as a literal "\ufeff" with three different compare orders.
BOM = "\ufeff"


def _prepare_target(path: str, ensure_parent: bool) -> str:
    text = os.fspath(path)
    parent = os.path.dirname(text)
    if parent and ensure_parent:
        os.makedirs(parent, exist_ok=True)
    return text


@contextlib.contextmanager
def open_atomic(path, *, encoding: str | None = None, ensure_parent: bool = True,
                durable: bool = False):
    """Yield a binary stream, or an LF text stream when encoding is given."""
    target = _prepare_target(path, ensure_parent)
    directory = os.path.dirname(target) or "."
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=".gt-tmp-")
    try:
        try:
            mode = "wb" if encoding is None else "w"
            newline = None if encoding is None else "\n"
            with os.fdopen(fd, mode, encoding=encoding, newline=newline,
                           closefd=False) as stream:
                yield stream
                if durable:
                    stream.flush()
                    os.fsync(fd)
        finally:
            os.close(fd)
        # Preserve an existing target's mode where the filesystem allows it.
        with contextlib.suppress(OSError):
            os.chmod(temporary, os.stat(target).st_mode)
        os.replace(temporary, target)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(temporary)


def load_text(path: str, encoding: str = "utf-8-sig") -> str:
    """Read a UTF-8 text file, tolerating a BOM (`utf-8-sig` strips it).

    `utf-8-sig` is a no-op on a file without one, so this is the safe default
    for the hand-edited files in a work directory.
    """
    with open(path, encoding=encoding, newline="") as handle:
        return handle.read()


def load_json(path: str):
    """Load JSON, tolerating a UTF-8 BOM that Windows editors add.

    Raises `OSError` if unreadable and `json.JSONDecodeError` if malformed;
    neither is swallowed here, because "file is missing" and "file is
    damaged" mean different things to a caller and only the caller can decide
    which is recoverable.
    """
    with open(path, encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_text(path, text, *, encoding: str = "utf-8", ensure_parent: bool = True,
               durable: bool = False) -> str:
    """Write `text` atomically as LF-terminated UTF-8.  Returns the path."""
    with open_atomic(path, encoding=encoding, ensure_parent=ensure_parent,
                     durable=durable) as handle:
        handle.write(text)
    return os.fspath(path)


def append_text(path, text, *, encoding: str = "utf-8",
                ensure_parent: bool = True) -> str:
    """Append `text` to a file, creating it if needed.  Returns the path.

    Deliberately **not** atomic: an append is the one write whose atomicity
    the filesystem already gives us for free below the buffer size, and the
    translation library is appended to once per translated key - copying the
    whole file per key would turn a linear flow into a quadratic one.  Callers
    that need an all-or-nothing rewrite want :func:`write_text` instead.
    """
    target = _prepare_target(path, ensure_parent)
    with open(target, "a", encoding=encoding, newline="\n") as handle:
        handle.write(text)
    return target


def dump_json(path, payload, *, indent: int | None = 1, sort_keys: bool = False,
              trailing_newline: bool | None = None, compact: bool = False,
              ensure_parent: bool = True, durable: bool = False) -> str:
    """Write JSON atomically, in one of the repository's two byte-exact styles.

    * pretty (default): `indent=1`, one trailing newline.
    * `compact=True`: `separators=(",", ":")`, no trailing newline - the style
      of the files that are *shipped inside the game* (`js/plugins.js`,
      `data/System.json`), whose exact bytes several tests pin.

    `ensure_ascii=False` in both: escaping non-ASCII would turn every Chinese
    string into `\\uXXXX` in a file a human is expected to read and diff.
    """
    if compact:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                          sort_keys=sort_keys)
        text = body + "\n" if trailing_newline else body
    else:
        text = json.dumps(payload, ensure_ascii=False, indent=indent,
                          sort_keys=sort_keys)
        if trailing_newline is None or trailing_newline:
            text += "\n"
    return write_text(path, text, ensure_parent=ensure_parent, durable=durable)


def read_jsonl(path) -> list:
    """Read JSONL into a list of objects; a missing file is an empty list.

    A malformed line **raises** `ValueError` naming `path:line`, rather than
    being skipped: a caller that silently gets fewer records than the file has
    cannot tell "nothing to do" from "the file is damaged", which is how a
    broken state file turns into a false pass.  `translation/rawlib.py`
    keeps a tolerant variant for the one file a human appends to by hand.
    """
    if not os.path.isfile(path):
        return []
    records = []
    with open(path, encoding="utf-8-sig") as handle:
        for number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError as error:
                raise ValueError(
                    f"{path}:{number}: bad JSON line ({error})") from error
    return records


def append_jsonl(path, record, *, ensure_parent: bool = True) -> str:
    """Append one object as a single LF-terminated line.  Returns the path."""
    return append_text(path, json.dumps(record, ensure_ascii=False) + "\n",
                       ensure_parent=ensure_parent)


def backup_file(source, backup_root, rel=None) -> str | None:
    """Copy `source` into `backup_root/rel` once; return the relative path.

    The first copy wins.  The backup exists to make a patch reversible, so the
    value that must survive is the *original* - overwriting it on a second
    pass would replace the pre-patch content with post-patch content and
    silently destroy the only way back.
    """
    relative = rel if rel is not None else os.path.basename(os.fspath(source))
    source = os.fspath(source)
    if not os.path.isfile(source):
        return None
    target = os.path.join(os.fspath(backup_root), relative)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    if not os.path.isfile(target):
        shutil.copyfile(source, target)
    return relative
