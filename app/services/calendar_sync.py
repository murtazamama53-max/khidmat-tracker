"""
Reconciles raw Google Calendar events against what's already been
synced. Pure logic: takes plain data in, returns a plan of what to do,
never touches the DB or the network directly (routes/calendar.py
executes the plan). This is what makes duplicate-protection, edit
detection, and deletion handling independently testable with fake event
data (blueprint section 6/7/8).

Money math is explicitly NOT this module's job -- it only ever compares
start/end datetimes for change-detection purposes. The actual rate
lookup and Decimal earning calculation happens at the route layer via
the existing rate_service/calculation_engine, exactly as it does for
manually-entered sessions.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Optional


@dataclass(frozen=True)
class RawEvent:
    event_id: str
    occurrence_id: Optional[str]  # the recurring series' master id, if this is one occurrence of a series
    calendar_id: str
    title: str
    start: Optional[datetime]
    end: Optional[datetime]
    is_cancelled: bool


@dataclass(frozen=True)
class MappingRule:
    calendar_id: Optional[str]  # None = matches any calendar
    title_pattern: str  # case-insensitive substring match against the event title
    source_id: int


@dataclass(frozen=True)
class ExistingLinkFact:
    calendar_id: str
    event_id: str
    occurrence_id: Optional[str]
    session_id: int
    start: datetime
    end: datetime
    source_deleted: bool


@dataclass(frozen=True)
class ImportPlanItem:
    event: RawEvent
    source_id: int


@dataclass(frozen=True)
class UpdatePlanItem:
    event: RawEvent
    existing: ExistingLinkFact


@dataclass(frozen=True)
class DraftPlanItem:
    event: RawEvent
    reason: str


@dataclass
class SyncPlan:
    to_import: List[ImportPlanItem]
    to_update: List[UpdatePlanItem]
    to_mark_deleted: List[ExistingLinkFact]
    to_draft: List[DraftPlanItem]
    skipped: List[RawEvent]

    @property
    def events_found(self) -> int:
        return len(self.to_import) + len(self.to_update) + len(self.to_mark_deleted) + len(self.to_draft) + len(self.skipped)


class EventParseError(ValueError):
    pass


def parse_raw_google_event(raw: dict) -> RawEvent:
    """
    Normalizes a raw Google Calendar API event dict into a RawEvent.
    Raises EventParseError for events with no specific time (all-day
    events use a 'date' field instead of 'dateTime') -- these can't
    produce a duration and are surfaced as drafts needing review rather
    than silently skipped.
    """
    event_id = raw.get("id")
    if not event_id:
        raise EventParseError("Event has no id.")

    start_raw = raw.get("start", {})
    end_raw = raw.get("end", {})
    if "dateTime" not in start_raw or "dateTime" not in end_raw:
        raise EventParseError("Event has no specific start/end time (likely an all-day event).")

    try:
        start = datetime.fromisoformat(start_raw["dateTime"])
        end = datetime.fromisoformat(end_raw["dateTime"])
    except ValueError as e:
        raise EventParseError(f"Could not parse event times: {e}") from e

    return RawEvent(
        event_id=event_id,
        occurrence_id=raw.get("recurringEventId"),
        calendar_id=raw.get("_calendar_id", ""),
        title=raw.get("summary", "") or "(untitled event)",
        start=start,
        end=end,
        is_cancelled=(raw.get("status") == "cancelled"),
    )


def match_mapping(event: RawEvent, rules: Iterable[MappingRule]) -> Optional[int]:
    """Returns the matched source_id, or None if no active rule matches. First match wins."""
    title_lower = event.title.lower()
    for rule in rules:
        if rule.calendar_id is not None and rule.calendar_id != event.calendar_id:
            continue
        if rule.title_pattern.lower() in title_lower:
            return rule.source_id
    return None


def _find_existing(event: RawEvent, existing_links: Iterable[ExistingLinkFact]) -> Optional[ExistingLinkFact]:
    for link in existing_links:
        if link.calendar_id == event.calendar_id and link.event_id == event.event_id:
            return link
    return None


def reconcile(
    raw_events: Iterable[dict],
    existing_links: Iterable[ExistingLinkFact],
    mapping_rules: Iterable[MappingRule],
) -> SyncPlan:
    """
    The core sync algorithm. Given the events Google returned for a date
    window, what we've already synced, and the owner's mapping rules,
    decide what to import, update, mark as upstream-deleted, or draft.
    """
    existing_links = list(existing_links)
    mapping_rules = list(mapping_rules)

    plan = SyncPlan(to_import=[], to_update=[], to_mark_deleted=[], to_draft=[], skipped=[])

    for raw in raw_events:
        try:
            event = parse_raw_google_event(raw)
        except EventParseError as e:
            # Can't compute a duration for this one -- surface it as a draft rather than dropping it silently.
            fake_event = RawEvent(
                event_id=raw.get("id", "unknown"),
                occurrence_id=raw.get("recurringEventId"),
                calendar_id=raw.get("_calendar_id", ""),
                title=raw.get("summary", "") or "(untitled event)",
                start=None,
                end=None,
                is_cancelled=(raw.get("status") == "cancelled"),
            )
            if not fake_event.is_cancelled:
                plan.to_draft.append(DraftPlanItem(event=fake_event, reason=str(e)))
            continue

        existing = _find_existing(event, existing_links)

        if existing is not None:
            if event.is_cancelled:
                if not existing.source_deleted:
                    plan.to_mark_deleted.append(existing)
                else:
                    plan.skipped.append(event)
            elif existing.start != event.start or existing.end != event.end:
                plan.to_update.append(UpdatePlanItem(event=event, existing=existing))
            else:
                plan.skipped.append(event)
            continue

        if event.is_cancelled:
            plan.skipped.append(event)  # never synced, cancelled -- nothing to do
            continue

        source_id = match_mapping(event, mapping_rules)
        if source_id is not None:
            plan.to_import.append(ImportPlanItem(event=event, source_id=source_id))
        else:
            plan.to_draft.append(DraftPlanItem(event=event, reason=f"'{event.title}' did not match any mapping rule."))

    return plan
