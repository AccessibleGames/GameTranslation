#!/usr/bin/env python3
"""Merge two fonts, using the Chinese font for overlapping characters."""

import os
import tempfile
from pathlib import Path
from typing import Annotated

from rpgmaker import cliutil, io_boundary, platform

try:
    from fontTools.ttLib import TTFont
except ImportError as exc:                     # pragma: no cover - hint path
    raise ImportError(
        "fontTools is required to merge the Chinese and Japanese fonts - "
        "install the optional extra (uv sync --extra fonts)") from exc


def scale_to(font_path, out_path, target_upm):
    """Normalize outlines and metrics using the fontTools table visitors."""
    from fontTools.ttLib.scaleUpem import scale_upem

    # TTC inputs use their first face, matching the order shown by the engine.
    face = 0 if Path(font_path).suffix.lower() == ".ttc" else -1
    with TTFont(font_path, fontNumber=face) as font:
        scale_upem(font, target_upm)
        with io_boundary.open_atomic(out_path, ensure_parent=False) as stream:
            font.save(stream)
    return out_path


def merge_fonts(cn_font, jp_font, out_path, upm):
    from fontTools.merge import Merger

    paths = platform.require_native_paths("merge fonts", cn_font=cn_font,
                                          jp_font=jp_font, out_path=out_path)
    output = Path(str(paths["out_path"]))
    output.parent.mkdir(parents=True, exist_ok=True)
    # Keep each merge's intermediate names private to that invocation.
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        inputs = [
            str(scale_to(str(paths[role]), Path(temporary) / f"{role}.ttf", upm))
            for role in ("cn_font", "jp_font")
        ]
        merged = Merger().merge(inputs)
        with io_boundary.open_atomic(output) as stream:
            merged.save(stream)
    return str(output)


def cmd(cn_font: Annotated[str, cliutil.Argument(
            help="Chinese font first (glyph priority)")],
        jp_font: Annotated[str, cliutil.Argument(
            help="Japanese font (lacks GB glyphs)")],
        out_ttf: Annotated[str, cliutil.Argument(help="merged TTF to write")],
        upm: Annotated[int, cliutil.Option(
            "--upm", help="units per em used for both inputs")] = 2048,
        verbose: cliutil.Verbose = False,
        quiet: cliutil.Quiet = False,
        log_file: cliutil.LogFile = None) -> int:
    """Merge a Chinese and a Japanese font into one CJK TTF."""
    cliutil.setup_logging(verbose, quiet, log_file)
    paths = cliutil.own_paths("merge fonts", cn_font=cn_font, jp_font=jp_font, out_path=out_ttf)
    for role in ("cn_font", "jp_font"):
        if not os.path.isfile(str(paths[role])):
            return cliutil.fail(f"input font not found: {paths[role]}")
    output = merge_fonts(cn_font, jp_font, out_ttf, upm)
    print("merged size:", os.path.getsize(output), output)
    return 0


app = cliutil.command_app(cmd)


def main(argv=None) -> int:
    return cliutil.run(app, argv, prog="merge_font.py")


if __name__ == "__main__":
    raise SystemExit(main())
