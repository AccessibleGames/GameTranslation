#!/usr/bin/env python3
"""Read and validate the line-oriented translation library.

A block starts with an ``@@@id@@@`` header and ends at the next header or EOF.
Each value is literal text, with one framing LF after its final character.
Later blocks with the same ID replace the value without changing key order.
"""
import json
import os
import re
from collections import OrderedDict
from collections.abc import Mapping

from rpgmaker import io_boundary

from . import mvkeys
from .codes import (
    KANA_LETTERS_RE,
    bracket_pair,
    has_text_parameter,
    parameter_of,
    parse_code_sequence,
    parse_codes,
    split_keep_codes,
)

__all__ = ["HEADER_RE", "read_library", "write_library", "append_block",
           "read_jsonl", "read_jsonl_report", "append_jsonl", "apply_rewrites",
           "to_json", "run_gates", "gate_markdown", "code_problems",
           "line_problems", "structure_problems", "readable_text",
           "kana_problem", "validate_blocks",
           "append_batch", "leading_codes", "fix_leading_codes"]

#: ``@@@<id>@@@`` on a line of its own; the id may contain anything but ``@``.
HEADER_RE = re.compile(r"^@@@([^@\n]+?)@@@[ \t]*$")

LIBRARY_NAME = "translations.raw.txt"
GATE_ORDER = ("coverage", "control_codes", "kana", "line_breaks", "pending")


def read_library(path):
    """Parse blocks while preserving trailing LF and interior bare CR."""
    # Iterate on LF only. CR at the end of a line belongs to a CRLF frame.
    values = OrderedDict()
    current = None
    chunks: list[str] = []
    with open(path, encoding="utf-8", errors="replace",
                 newline="\n") as handle:
        for number, line in enumerate(handle, 1):
            bare = line.rstrip("\n").rstrip("\r")
            match = HEADER_RE.match(bare)
            if match:
                if current is not None:
                    values[current] = "\n".join(chunks)
                current = match.group(1)
                chunks = []
                continue
            if current is not None:
                chunks.append(bare)
            elif bare.strip() and not bare.lstrip().startswith("#"):
                # Before the first header only blank lines and ``#`` notes are
                # tolerated; anything else means the file is not a library (a
                # value silently swallowed here would be lost work).
                raise ValueError("%s:%d: text before the first @@@id@@@ header"
                                 % (path, number))
    if current is not None:
        values[current] = "\n".join(chunks)
    return values


def _format_blocks(values: Mapping[str, str]) -> str:
    return "".join(f"@@@{key}@@@\n{text}\n" for key, text in values.items())


def write_library(path, values):
    """Replace the library with the given blocks in mapping order."""
    io_boundary.write_text(path, _format_blocks(values), ensure_parent=False)
    return path


def append_block(path, key_id, text):
    """Append one translated block."""
    if "@@@" in key_id:
        raise ValueError(f"id must not contain '@@@': {key_id!r}")
    io_boundary.append_text(path, _format_blocks({key_id: text}), ensure_parent=False)
    return path


read_jsonl = io_boundary.read_jsonl


def read_jsonl_report(path):
    """Return valid records and (line number, error) pairs for damaged rows."""
    records: list[dict] = []
    errors: list[tuple[int, str]] = []
    if not os.path.isfile(path):
        return records, errors
    with open(path, encoding="utf-8-sig", errors="replace") as handle:
        for number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError as error:
                errors.append((number, str(error)))
            else:
                if isinstance(record, dict):
                    records.append(record)
                else:
                    errors.append((number, "expected a JSON object"))
    return records, errors


def append_jsonl(path, record):
    """Append a JSONL record to an existing work directory."""
    io_boundary.append_jsonl(path, record, ensure_parent=False)
    return path


def safe_replace(text, old, new):
    """Replace ``old`` with ``new`` once, idempotently.

    When ``old`` occurs inside ``new`` (``杂鱼`` -> ``杂鱼剑``) a plain
    :meth:`str.replace` is *not* idempotent: re-applying the same rule to an
    already-updated value yields ``杂鱼剑剑``.  Occurrences already in the new
    form are protected with a sentinel character for the duration of the
    replacement, so a second run is a no-op.  Rules where ``new`` is a
    substring of ``old`` (``嫩穴摹本`` -> ``嫩穴``) stay plain replacements -
    there the already-converted text no longer matches ``old`` at all, so
    protecting it would silently block the conversion.
    """
    if old == new or old not in new:
        return text.replace(old, new)
    sentinel = "\x00"
    while sentinel in text or sentinel in new or sentinel in old:
        sentinel += "\x00"
    return (text.replace(new, sentinel).replace(old, new)
            .replace(sentinel, new))


def apply_rewrites(work_dir, report_path=None):
    """Apply ``rewrites.jsonl`` to the library; report every affected key.

    A rewrite is ``{"old": ..., "new": ..., "reason": ..., "ids": [...]?}``:
    with ``ids`` the substitution is limited to those keys, without it every
    value containing ``old`` is updated (the whole-library case).  The library
    is rewritten in place and the impact report is returned, so the change is
    auditable long after the subagent's reasoning is gone.
    """
    library_path = os.path.join(work_dir, LIBRARY_NAME)
    values = read_library(library_path)
    rewrites = read_jsonl(os.path.join(work_dir, "rewrites.jsonl"))
    applied = []
    changed_keys = set()
    for index, rule in enumerate(rewrites):
        if not isinstance(rule, dict):
            raise ValueError(f"rewrites.jsonl[{index}]: expected an object")
        old, new = rule.get("old"), rule.get("new")
        if not isinstance(old, str) or not isinstance(new, str) or not old:
            raise ValueError("rewrites.jsonl[%d]: needs a non-empty 'old' and a string 'new'"
                             % index)
        scope = rule.get("ids")
        if scope is not None and (
            not isinstance(scope, list) or any(not isinstance(key, str) for key in scope)
        ):
            raise ValueError(f"rewrites.jsonl[{index}]: 'ids' must be a list of strings")
        targets = (key for key in (scope or values) if key in values)
        hits = []
        matched = 0
        for key in targets:
            text = values[key]
            if old not in text:
                continue
            matched += 1
            updated = safe_replace(text, old, new)
            if updated != text:
                values[key] = updated
                hits.append(key)
        changed_keys.update(hits)
        applied.append({
            "old": old, "new": new, "reason": rule.get("reason") or "",
            "scope": "ids" if scope else "all",
            "affected": len(hits),
            "already_up_to_date": matched - len(hits),
            "ids": hits[:50],
        })
    # Publish only after all rules have been applied successfully in memory.
    if changed_keys:
        write_library(library_path, values)
    report = {"rules": len(rewrites), "changed_keys": len(changed_keys),
              "applied": applied}
    out = report_path or os.path.join(work_dir, "rewrite_report.json")
    io_boundary.dump_json(out, report, ensure_parent=False)
    return report


def to_json(work_dir, out_path=None):
    """Turn the raw library into JSON - the one place escaping happens.

    Writes ``translated.json`` (``{source text: translation}``, the shape a
    bake step consumes) and ``translated_ids.json`` (``{id: translation}``,
    full fidelity for the gates).  Conflicting translations of the same source
    string are reported, not hidden.
    """
    keys = mvkeys.load_keys(work_dir)
    values = read_library(os.path.join(work_dir, LIBRARY_NAME))
    by_id = OrderedDict()
    by_text: OrderedDict[str, str] = OrderedDict()
    conflicts = []
    for entry in keys:
        text = values.get(entry["id"])
        if text is None or not text.strip():
            continue
        by_id[entry["id"]] = text
        source = entry["ja"]
        if source in by_text and by_text[source] != text:
            conflicts.append({"ja": source, "kept": by_text[source],
                              "other": text, "id": entry["id"]})
            continue
        by_text[source] = text
    out_dir = os.path.dirname(out_path) if out_path else work_dir
    json_path = out_path or os.path.join(work_dir, "translated.json")
    ids_path = os.path.join(out_dir, "translated_ids.json")
    for path, payload in ((json_path, by_text), (ids_path, by_id)):
        io_boundary.dump_json(path, payload, ensure_parent=False)
    report = {"keys": len(keys), "translated": len(by_id),
              "unique_sources": len(by_text), "conflicts": conflicts[:50],
              "translated_json": json_path, "translated_ids": ids_path}
    io_boundary.dump_json(os.path.join(work_dir, "to_json_report.json"), report,
                          ensure_parent=False)
    return report


def _allowlist(work_dir):
    path = os.path.join(work_dir, "allow_kana.json")
    if not os.path.isfile(path):
        return []
    data = io_boundary.load_json(path)
    items = data.get("items") if isinstance(data, dict) else data
    return [item for item in (items or []) if isinstance(item, dict)
            and item.get("match")]


def _kana_allowed(text, items):
    for item in items:
        match = item["match"]
        if match.startswith("re:"):
            if re.search(match[3:], text):
                return item
        elif match in text:
            return item
    return None


def code_problems(source_text, target_text):
    """Differences between two control-code sequences, as messages.

    Codes are compared one by one; the only tolerated difference is inside a
    *textual* parameter - a name box (``\\nc<チンピラ>``) shows a name to the
    player, so it must be translated, while a numeric argument (``\\px[200]``)
    must survive byte for byte.  The **bracket shape is checked either way**:
    the delimiters decide which token the engine scans for, so ``\\nc<name>``
    rewritten as ``\\nc[name]`` changes the code even though the key is still
    ``NC`` (``tests/test_translation_properties.py`` pins this).
    """
    source = parse_code_sequence(source_text)
    target = parse_code_sequence(target_text)
    if [key for key, _ in source] != [key for key, _ in target]:
        return [f"code sequence: {[tok for _, tok in source]} -> {[tok for _, tok in target]}"]
    problems = []
    # The token-key sequence was just compared, so both lists are the same
    # length; `strict` states that instead of relying on zip's silent
    # truncation, which is this repository's documented failure mode.
    for (_, source_token), (_, target_token) in zip(source, target,
                                                    strict=True):
        if source_token == target_token:
            continue
        if bracket_pair(source_token) != bracket_pair(target_token):
            problems.append(f"code parameter brackets: {source_token} -> {target_token}")
            continue
        if has_text_parameter(source_token) \
                and parameter_of(target_token) is not None:
            continue
        problems.append(f"code parameter: {source_token} -> {target_token}")
    return problems


def leading_codes(text):
    """The control-code prefix of `text` (the codes before any visible text)."""
    out = []
    for is_code, piece in split_keep_codes(text):
        if is_code:
            out.append(piece)
        elif piece:
            break
    return "".join(out)


def fix_leading_codes(source, target):
    """Prepend the source's leading codes when the translation omitted them.

    A continuation line repeats the source's leading codes (``\\px[200]``, a run
    of ``\\{`` size switches), and forgetting one is the most common mechanical
    slip of a long run - it says nothing about the translation, so the tool
    fixes it instead of the translator.

    Two cases are deliberately left alone: a translation that already starts
    with *some* code (only validation may judge whether it is the right one),
    and a prefix whose parameter carries **text** - a name box must be
    translated, and copying the source's Japanese name back would be worse than
    the missing code it was meant to fix.
    """
    prefix = leading_codes(source)
    if not prefix or leading_codes(target):
        return target
    if any(has_text_parameter(token) for token in parse_codes(prefix)):
        return target
    return prefix + target


def readable_text(text):
    """What a player actually reads: text between codes + text parameters."""
    parts = [piece for is_code, piece in split_keep_codes(text) if not is_code]
    parts += [parameter_of(token) for token in parse_codes(text)
              if has_text_parameter(token)]
    return "\n".join(piece for piece in parts if piece)


def line_problems(source_text, target_text):
    """A value whose explicit line breaks differ from its source.

    MZ window text and DB descriptions use real ``\\n`` as *author* line
    breaks (one per window line, deliberate layout), so a value carrying fewer
    breaks is a silently truncated translation.  Neither the kana gate nor the
    control-code gate can see that - the missing line is simply gone, which is
    exactly how 42 skill descriptions lost their second line in a real run.
    """
    source_lines = source_text.count("\n")
    target_lines = target_text.count("\n")
    if source_lines == target_lines:
        return []
    return ["line breaks: source %d -> value %d" % (source_lines, target_lines)]


def _shape_of(payload):
    """Recursive signature: containers exact, string leaves type-only."""
    if isinstance(payload, dict):
        return ("dict", tuple(sorted(
            (key, _shape_of(value)) for key, value in payload.items())))
    if isinstance(payload, list):
        return ("list", tuple(_shape_of(item) for item in payload))
    if isinstance(payload, str):
        return ("str", _json_shape(payload))
    return ("scalar", payload)


def _json_shape(text):
    """Structural signature of `text` when it is JSON, else ``None``.

    Plugin command arguments are JSON held in *strings*, often nested twice:
    ``['{"label": "戦う", "switchId": "0"}']`` is a JSON list whose items are
    JSON objects serialised as strings.  A translation may rewrite the strings
    inside, but every list, object, key and number has to survive: a lost
    bracket makes the plugin's ``JSON.parse`` throw and the event stops right
    there (the player gets a choice window that never appears).

    String leaves are compared by *type only* - that is the part being
    translated - but a string leaf that is itself ``{...}``/``[...]`` is decoded
    and compared structurally too, so the nesting is checked as well.  Text
    that merely starts with a bracket without being JSON yields ``None`` (no
    check), which keeps ordinary dialogue out of this gate.
    """
    stripped = text.strip()
    if not stripped.startswith(("{", "[")):
        return None
    try:
        payload = json.loads(stripped)
    except ValueError:
        return None
    return _shape_of(payload)


def structure_problems(source_text, target_text):
    """A JSON-valued parameter whose structure the translation broke.

    Only JSON *containers* are judged: when the source is not JSON there is
    nothing to protect, and a value that stopped parsing is reported once
    instead of as a long shape diff.
    """
    source_shape = _json_shape(source_text)
    if source_shape is None:
        return []
    target_shape = _json_shape(target_text)
    if target_shape is None:
        return ["JSON structure: value no longer parses as JSON"]
    if target_shape != source_shape:
        return ["JSON structure: shape differs from the source"]
    return []


def kana_problem(text, items):
    """Kana left in the readable part of `text`, unless the allowlist covers it.

    Kana *letters* only (``KANA_LETTERS_RE``): translated Chinese keeps the
    kana-block punctuation (``゛`` voice marks in moans, ``・``, dashes), so
    counting those as residue rejected hundreds of finished translations of one
    real harvest and pushed them through ``allow_kana.json`` for nothing.  A
    value that is still Japanese contains a kana letter and is still flagged.
    """
    visible = readable_text(text)
    if not KANA_LETTERS_RE.search(visible):
        return None, None
    item = _kana_allowed(visible, items)
    if item:
        return None, item
    return visible, None


def validate_blocks(work_dir, values):
    """Check a batch of translations against the key list before writing it.

    Returns ``[(id, problem), ...]`` - empty means the batch is clean.  Used by
    ``append_batch`` so a bad batch is rejected *before* it reaches the library
    (the library is append-only; a half-written batch is hard to unwind).
    """
    keys = {entry["id"]: entry for entry in mvkeys.load_keys(work_dir)}
    return _validate_blocks(values, keys, _allowlist(work_dir))


def _validate_blocks(values, keys, items):
    problems = []
    for key_id, text in values.items():
        entry = keys.get(key_id)
        if entry is None:
            problems.append((key_id, "unknown id (not in keys.jsonl)"))
            continue
        if not text.strip():
            problems.append((key_id, "empty translation"))
            continue
        problems.extend((key_id, message) for message in code_problems(entry["ja"], text))
        problems.extend((key_id, message) for message in structure_problems(entry["ja"], text))
        problems.extend((key_id, message) for message in line_problems(entry["ja"], text))
        residue, _allowed = kana_problem(text, items)
        if residue:
            problems.append((key_id, f"kana residue: {residue[:40]}"))
    return problems


def append_batch(work_dir, batch_path, note=None, fix_leading=False):
    """Validate a batch file and append it to the library (all or nothing).

    The batch file has the library format (``@@@id@@@`` + raw translation), so
    a batch is just a fragment of the library.  Nothing is written when any
    block fails validation: the caller gets every problem and the library stays
    exactly as it was.  With ``fix_leading`` a missing leading control code is
    restored from the source first (see ``fix_leading_codes``).
    """
    values = read_library(batch_path)
    if not values:
        return {"added": 0, "fixed": 0,
                "problems": [("", "batch file is empty")]}
    keys = {entry["id"]: entry for entry in mvkeys.load_keys(work_dir)}
    fixed = 0
    if fix_leading:
        for key_id, text in list(values.items()):
            entry = keys.get(key_id)
            if entry is None:
                continue
            restored = fix_leading_codes(entry["ja"], text)
            if restored != text:
                values[key_id] = restored
                fixed += 1
    problems = _validate_blocks(values, keys, _allowlist(work_dir))
    if problems:
        return {"added": 0, "fixed": fixed, "problems": problems}
    library = os.path.join(work_dir, LIBRARY_NAME)
    io_boundary.append_text(library, _format_blocks(values), ensure_parent=False)
    if note:
        append_jsonl(os.path.join(work_dir, "progress.jsonl"),
                     {"note": note, "added": len(values),
                      "fixed_leading": fixed,
                      "ids": list(values)[:3]})
    return {"added": len(values), "fixed": fixed, "problems": []}


def _gate_coverage(keys, values):
    missing = [entry["id"] for entry in keys
               if not (values.get(entry["id"]) or "").strip()]
    return {
        "name": "coverage",
        "ok": not missing,
        "total": len(keys),
        "missing": len(missing),
        "detail": missing[:50],
    }


def _gate_codes(keys, values):
    """The code sequence of a translation must match its source."""
    mismatch, translated_parameters = [], 0
    for entry in keys:
        text = values.get(entry["id"])
        if not text:
            continue
        problems = code_problems(entry["ja"], text)
        if not problems:
            problems = structure_problems(entry["ja"], text)
        if not problems:
            # `code_problems` above returns empty only when the key sequences
            # are identical, so the two parses are the same length.
            if any(source_token != target_token for (_, source_token),
                   (_, target_token)
                   in zip(parse_code_sequence(entry["ja"]),
                          parse_code_sequence(text), strict=True)):
                translated_parameters += 1
            continue
        mismatch.append({"id": entry["id"], "where": entry["where"],
                         "reason": problems[0].split(":")[0],
                         "problems": problems,
                         "source": [tok for _, tok in
                                    parse_code_sequence(entry["ja"])],
                         "target": [tok for _, tok in
                                    parse_code_sequence(text)]})
    return {
        "name": "control_codes",
        "ok": not mismatch,
        "checked": sum(1 for entry in keys if values.get(entry["id"])),
        "mismatched": len(mismatch),
        "translated_parameters": translated_parameters,
        "detail": mismatch[:50],
    }


def _gate_kana(keys, values, items):
    """Kana left in the readable parts of a translation.

    Readable parts are the text between codes **plus** any textual code
    parameter (a name box shows its parameter to the player).  Numeric code
    arguments and the code itself are never read as prose.
    """
    allowed, residue = [], []
    for entry in keys:
        text = values.get(entry["id"])
        if not text:
            continue
        visible, item = kana_problem(text, items)
        if item:
            allowed.append({"id": entry["id"], "match": item["match"],
                            "reason": item.get("reason") or ""})
        elif visible:
            residue.append({"id": entry["id"], "where": entry["where"],
                            "text": visible})
    return {
        "name": "kana",
        "ok": not residue,
        "allowed": len(allowed),
        "residue": len(residue),
        "detail": residue[:50],
    }


#: Statuses that count as closed for the pending gate.
CLOSED_STATUS = ("resolved", "decided", "wontfix")


def read_pending(work_dir):
    """Collapse ``pending.jsonl`` to the latest entry per key (last wins).

    Returns ``(latest, entries, errors)``.  ``pending.jsonl`` is append-only,
    so appending ``{"id": ..., "status": "resolved"}`` closes the earlier
    ``open`` entry for that id instead of requiring an edit.  Every consumer
    (the pending gate and ``status``) reads through this helper, so their
    counts cannot drift apart.
    """
    records, errors = read_jsonl_report(os.path.join(work_dir,
                                                     "pending.jsonl"))
    latest = OrderedDict()
    for item in records:
        key = (item.get("id") or item.get("question") or item.get("why")
               or json.dumps(item, ensure_ascii=False, sort_keys=True))
        latest[key] = item
    return latest, len(records), errors


def pending_open(latest):
    """Open questions among the collapsed pending records."""
    return [item for item in latest.values()
            if (item.get("status") or "open") not in CLOSED_STATUS]


def _gate_pending(work_dir):
    """Every open question must have a conclusion.

    See :func:`read_pending` for the last-wins collapse.  Lines that cannot be
    parsed fail the gate (they may hold an unreviewed question).
    """
    latest, entries, errors = read_pending(work_dir)
    open_items = pending_open(latest)
    return {
        "name": "pending",
        "ok": not open_items and not errors,
        "entries": entries,
        "open": len(open_items),
        "unparsable": len(errors),
        "detail": (["line %d: %s" % (number, message)
                    for number, message in errors[:5]]
                   + [item.get("id") or item.get("question")
                      for item in open_items][:50]),
    }


def _gate_lines(keys, values):
    """Every value keeps its source's explicit line breaks (no silent loss)."""
    mismatched: list[dict[str, str]] = []
    for entry in keys:
        text = values.get(entry["id"])
        if not text:
            continue
        mismatched.extend({"id": entry["id"], "where": entry["where"],
                           "reason": message, "source": entry["ja"][:80],
                           "value": text[:80]}
                          for message in line_problems(entry["ja"], text))
    return {
        "name": "line_breaks",
        "ok": not mismatched,
        "checked": sum(1 for entry in keys if values.get(entry["id"])),
        "mismatched": len(mismatched),
        "detail": mismatched[:50],
    }


def run_gates(work_dir, out_path=None):
    """Run the hard gates.  Baking is refused unless all of them pass."""
    keys = mvkeys.load_keys(work_dir)
    values = read_library(os.path.join(work_dir, LIBRARY_NAME))
    items = _allowlist(work_dir)
    gates = [_gate_coverage(keys, values), _gate_codes(keys, values),
             _gate_kana(keys, values, items), _gate_lines(keys, values),
             _gate_pending(work_dir)]
    report = {
        "ok": all(gate["ok"] for gate in gates),
        "keys": len(keys),
        "translated": sum(1 for entry in keys if values.get(entry["id"])),
        "gates": gates,
    }
    path = out_path or os.path.join(work_dir, "gate_report.json")
    io_boundary.dump_json(path, report, ensure_parent=False)
    return report


def gate_markdown(report):
    """Human summary of a gate report (also what gets shown to the owner)."""
    lines = ["# Gate report", "",
             "**%s** - %d/%d keys translated" % (
                 "ALL PASS" if report["ok"] else "FAILED",
                 report["translated"], report["keys"]), "",
             "| gate | result | detail |", "|---|---|---|"]
    for gate in report["gates"]:
        if gate["name"] == "coverage":
            detail = "%d missing" % gate["missing"]
        elif gate["name"] == "control_codes":
            detail = "%d of %d mismatched" % (gate["mismatched"],
                                              gate["checked"])
        elif gate["name"] == "kana":
            detail = "%d residue, %d allowlisted" % (gate["residue"],
                                                     gate["allowed"])
        elif gate["name"] == "line_breaks":
            detail = "%d of %d values differ from the source line count" % (
                gate["mismatched"], gate["checked"])
        else:
            detail = "%d open of %d" % (gate["open"], gate["entries"])
            if gate.get("unparsable"):
                detail += ", %d unparsable" % gate["unparsable"]
        lines.append("| {} | {} | {} |".format(gate["name"], "PASS" if gate["ok"] else "**FAIL**",
                        detail))
    lines.append("")
    return "\n".join(lines)
