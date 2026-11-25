#!/usr/bin/env python3
"""Parse embedded JavaScript without executing game scripts."""

import logging

log = logging.getLogger("rpgmaker.jssyntax")

_parser = None


def _get_parser():
    """Build the parser once per process (grammar load is not free)."""
    global _parser
    if _parser is None:
        try:
            import tree_sitter_javascript
            from tree_sitter import Language, Parser
        except ImportError as exc:                 # pragma: no cover
            raise ImportError(
                "the 'tree-sitter' and 'tree-sitter-javascript' packages are "
                "required for JS syntax checking - install the toolkit "
                "dependencies (pip install -e .)") from exc
        _parser = Parser(Language(tree_sitter_javascript.language()))
    return _parser


def _describe(source, node):
    """One-line description of a broken node, with position and its text.

    The snippet is the parser's own view of the broken region (whitespace
    collapsed, truncated), which is what a human needs to find the damage -
    tree-sitter reports the start of the error, not the token a JS engine
    would name.
    """
    row, col = node.start_point
    if node.is_missing:
        return "line %d:%d: missing %r" % (row + 1, col + 1, node.type)
    # Node offsets index UTF-8 bytes, including when the script contains kana.
    text = source[node.start_byte:node.end_byte].decode("utf-8", "replace")
    snippet = " ".join(text.split())[:60]
    if snippet:
        return "line %d:%d: unexpected %s" % (row + 1, col + 1,
                                              repr(snippet))
    return "line %d:%d: parse error (%s)" % (row + 1, col + 1, node.type)


def parse_errors(source):
    """List the syntax errors of `source` (empty list when it parses).

    Each entry is ``{"line": int, "column": int, "error": str}`` with
    1-based line and UTF-8 byte column, in source order.
    """
    parser = _get_parser()
    encoded = source.encode("utf-8", "replace")
    tree = parser.parse(encoded)
    found = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            found.append(node)
        elif node.has_error:
            stack.extend(node.children)
    found.sort(key=lambda n: n.start_point)
    errors = []
    seen = set()
    for node in found:
        row, col = node.start_point
        if (row, col) in seen:
            continue
        seen.add((row, col))
        errors.append({"line": row + 1, "column": col + 1,
                       "error": _describe(encoded, node)})
    if errors:
        log.debug("%d syntax error(s), first: %s", len(errors),
                  errors[0]["error"])
    return errors


def is_valid(source):
    """True when `source` parses as JavaScript."""
    return not parse_errors(source)
