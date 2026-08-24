"""
Shorthand parser.

Turns free-text like "Sbhs(7) & sghs(5-6:20)" into structured DRAFT
component objects. This module NEVER computes money and NEVER guesses an
unknown source -- ambiguous input is returned with needs_confirmation=True
and no calculation is attempted until the structured draft is confirmed
by the calculation engine + rate service (blueprint section 6).
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional

# Separators allowed between multiple components in one capture event.
_SEPARATOR_RE = re.compile(r"\s*[&,;]\s*")

# e.g. "Sbhs(7)"  ->  name=Sbhs, value="7"
_TOKEN_RE = re.compile(
    r"""
    ^\s*
    (?P<name>[A-Za-z][A-Za-z0-9_ ]*?)   # source name, e.g. Sbhs, SGHS, Ahmed
    \s*\(\s*
    (?P<value>[^)]+)
    \)\s*$
    """,
    re.VERBOSE,
)

# Matches a clock time like "5", "5:10", "17:00", "4:50 PM"
_TIME_RE = re.compile(
    r"""
    (?P<hour>\d{1,2})
    (?::(?P<minute>\d{2}))?
    \s*(?P<meridiem>AM|PM|am|pm)?
    """,
    re.VERBOSE,
)

# A time RANGE like "5-6:20" or "4:50 PM-6:30 PM" or "17:00 - 18:20"
_RANGE_RE = re.compile(
    r"""
    ^\s*
    (?P<start>\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm)?)
    \s*-\s*
    (?P<end>\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm)?)
    \s*$
    """,
    re.VERBOSE,
)


class ParseError(ValueError):
    pass


@dataclass
class TimeOfDay:
    hour: int  # 24-hour
    minute: int

    def __str__(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"


@dataclass
class ParsedComponent:
    raw_text: str
    source_name: str  # as typed, e.g. "Sbhs", "sghs" -- resolved against DB later
    mode: str  # "FIXED_HOURS" or "EXACT_TIME"
    quantity_hours: Optional[float] = None
    start: Optional[TimeOfDay] = None
    end: Optional[TimeOfDay] = None
    needs_confirmation: bool = False
    confirmation_reason: str = ""


@dataclass
class ParseResult:
    raw_input: str
    components: List[ParsedComponent] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0 and len(self.components) > 0

    @property
    def needs_review(self) -> bool:
        return any(c.needs_confirmation for c in self.components)


def _parse_single_time(text: str) -> TimeOfDay:
    text = text.strip()
    m = _TIME_RE.match(text)
    if not m:
        raise ParseError(f"Could not parse time: '{text}'")
    hour = int(m.group("hour"))
    minute = int(m.group("minute") or 0)
    meridiem = (m.group("meridiem") or "").upper()

    if meridiem == "PM" and hour != 12:
        hour += 12
    elif meridiem == "AM" and hour == 12:
        hour = 0
    # No meridiem given: assume common working hours (afternoon) ONLY when
    # hour is 1-7, mirroring the blueprint's own examples ("5-6:20" means
    # 5 PM). This is a display convenience -- ambiguous cases are flagged
    # for confirmation by the caller via needs_confirmation.
    elif not meridiem and 1 <= hour <= 7:
        hour += 12

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ParseError(f"Time out of range: '{text}'")

    return TimeOfDay(hour=hour, minute=minute)


def _parse_time_range(text: str) -> tuple[TimeOfDay, TimeOfDay]:
    m = _RANGE_RE.match(text.strip())
    if not m:
        raise ParseError(f"Could not parse time range: '{text}'")
    start = _parse_single_time(m.group("start"))
    end = _parse_single_time(m.group("end"))
    return start, end


# Sources known to be FIXED_HOURS (quantity in brackets = hours worked).
# Everything else defaults to EXACT_TIME when the bracket contains a range,
# or is flagged for confirmation when ambiguous. In the real app this table
# is driven by the income_sources DB table (mode column); this default map
# covers the two sources named explicitly in the blueprint.
_KNOWN_FIXED_HOURS_SOURCES = {"sbhs"}
_KNOWN_EXACT_TIME_SOURCES = {"sghs"}


def parse_component(raw_text: str, known_sources: Optional[dict] = None) -> ParsedComponent:
    """
    Parse one component like "Sbhs(7)" or "sghs(5-6:20)".

    known_sources: optional {lowercase_name: "FIXED_HOURS"|"EXACT_TIME"} map
    sourced from the database's income_sources table, so real deployments
    aren't limited to the two built-in example sources.
    """
    m = _TOKEN_RE.match(raw_text.strip())
    if not m:
        raise ParseError(f"Could not parse component: '{raw_text}'")

    name = m.group("name").strip()
    value = m.group("value").strip()
    name_key = name.lower()

    source_modes = {}
    for s in _KNOWN_FIXED_HOURS_SOURCES:
        source_modes[s] = "FIXED_HOURS"
    for s in _KNOWN_EXACT_TIME_SOURCES:
        source_modes[s] = "EXACT_TIME"
    if known_sources:
        source_modes.update(known_sources)

    known_mode = source_modes.get(name_key)

    # Decide mode primarily from the shape of the value, source hint second.
    looks_like_range = "-" in value
    looks_like_number = re.fullmatch(r"\d+(\.\d+)?", value) is not None

    if known_mode is None:
        # Unknown source: parse what we can, but flag for confirmation.
        if looks_like_range:
            start, end = _parse_time_range(value)
            return ParsedComponent(
                raw_text=raw_text,
                source_name=name,
                mode="EXACT_TIME",
                start=start,
                end=end,
                needs_confirmation=True,
                confirmation_reason=f"'{name}' is not a known source. Please confirm or map it.",
            )
        elif looks_like_number:
            return ParsedComponent(
                raw_text=raw_text,
                source_name=name,
                mode="FIXED_HOURS",
                quantity_hours=float(value),
                needs_confirmation=True,
                confirmation_reason=f"'{name}' is not a known source. Please confirm or map it.",
            )
        else:
            raise ParseError(f"Could not interpret value '{value}' for source '{name}'.")

    if known_mode == "FIXED_HOURS":
        if not looks_like_number:
            raise ParseError(f"Source '{name}' expects a number of hours, got '{value}'.")
        return ParsedComponent(
            raw_text=raw_text,
            source_name=name,
            mode="FIXED_HOURS",
            quantity_hours=float(value),
        )
    else:  # EXACT_TIME
        if not looks_like_range:
            raise ParseError(f"Source '{name}' expects a time range, got '{value}'.")
        start, end = _parse_time_range(value)
        return ParsedComponent(
            raw_text=raw_text,
            source_name=name,
            mode="EXACT_TIME",
            start=start,
            end=end,
        )


def parse_input(raw_text: str, known_sources: Optional[dict] = None) -> ParseResult:
    """
    Parse a full quick-add string that may contain multiple components
    separated by &, comma, or semicolon, e.g.:
      "Sbhs(7) & sghs(5-6:20)"
      "Sbhs(7), sghs(5-6:20)"
    """
    result = ParseResult(raw_input=raw_text)
    if not raw_text or not raw_text.strip():
        result.errors.append("Input is empty.")
        return result

    pieces = [p for p in _SEPARATOR_RE.split(raw_text.strip()) if p.strip()]
    for piece in pieces:
        try:
            result.components.append(parse_component(piece, known_sources=known_sources))
        except ParseError as e:
            result.errors.append(str(e))

    return result
