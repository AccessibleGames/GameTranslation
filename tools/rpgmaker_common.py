#!/usr/bin/env python3
"""Shared RPG Maker data-structure walkers.

MZ/MV data files are per-map JSON objects that carry their event pages in
the "events" (or "commonEvents") key; a whole map may also arrive as a raw
list (e.g. CommonEvents.json is a top-level array).  VX Ace rvdata2 uses a
different layout - events live under "@events" on the map/CommonEvents
object - so it gets its own walker below.

These helpers are pure reads: they never mutate the data, they just decide
whether an object is an event container and yield its event dicts.
"""


def is_event_container(data):
    """True when `data` is an MZ/MV map / CommonEvents object (a dict keyed
    by "events"/"commonEvents", or a list whose elements look like event
    objects - common events have a top-level "list", map events have
    "pages")."""
    if isinstance(data, dict):
        return "events" in data or "commonEvents" in data
    if isinstance(data, list):
        return any(isinstance(x, dict) and ("list" in x or "pages" in x)
                   for x in data)
    return False


def ev_containers(data):
    """Event dicts from an MZ/MV map / CommonEvents object.  A raw list is
    treated as the event array itself (CommonEvents.json); a dict yields the
    events of its "events" and "commonEvents" keys in that order."""
    out = []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    for key in ("events", "commonEvents"):
        arr = data.get(key) or []
        out.extend(x for x in arr if isinstance(x, dict))
    return out


def rvdata2_ev_containers(data):
    """Event dicts from a VX Ace map / CommonEvents rvdata2 object, where
    events live under the "@events" key (a dict keyed by event id, or a
    list).  Returns [] for anything else."""
    if isinstance(data, dict):
        evs = data.get("@events") or {}
        if isinstance(evs, dict):
            return [evs[k] for k in sorted(evs, key=lambda k: (k is None, k))
                    if isinstance(evs[k], dict)]
        if isinstance(evs, list):
            return [e for e in evs if isinstance(e, dict)]
    return []


DIALOGUE_RADIUS = 2


def talk_lines(commands):
    """Pair each command index with its display text, if it has any."""
    lines = []
    for index, command in enumerate(commands):
        code = command.get("code")
        params = command.get("parameters") or []
        text = None
        if code in (401, 405) and params and isinstance(params[0], str):
            text = params[0]
        elif code == 101 and len(params) >= 5 and isinstance(params[4], str):
            text = f"【{params[4]}】"
        elif code == 102 and params and isinstance(params[0], list):
            choices = " / ".join(choice for choice in params[0] if isinstance(choice, str))
            text = f"【选项】{choices}"
        # Commands with no display text still occupy a slot in the window.
        lines.append((index, text))
    return lines


def pos_map(lines):
    positions = {}
    for position, (index, _) in enumerate(lines):
        positions.setdefault(index, position)
    return positions


def window_for(index, lines, radius=DIALOGUE_RADIUS, pos=None):
    """Return nearby display lines, counting hidden command slots in the radius."""
    positions = pos if pos is not None else pos_map(lines)
    position = positions.get(index)
    if position is None:
        return []
    start = max(0, position - radius)
    return [text for _, text in lines[start:position + radius + 1] if text is not None]
