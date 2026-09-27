"""PawPal+ logic layer.

Implements the design in `diagrams/uml.mmd`. Layout:
    1. Enums        -- Priority, TaskCategory, Recurrence
    2. Value types  -- TimeWindow
    3. Domain       -- CareTask, Pet, OwnerPreferences, Owner
    4. Plan output  -- ScheduledTask, SkippedTask, DailyPlan
    5. Engine       -- SchedulingStrategy (+ concrete strategies), Scheduler

A household gets ONE shared plan: the owner cannot walk two dogs at once, so
every pet's tasks compete for the same timeline and the same daily budget.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import date, time, timedelta
from enum import Enum

__all__ = [
    "Priority",
    "TaskCategory",
    "Recurrence",
    "TimeWindow",
    "CareTask",
    "Pet",
    "OwnerPreferences",
    "Owner",
    "ScheduledTask",
    "SkippedTask",
    "ConflictWarning",
    "DailyPlan",
    "SchedulingStrategy",
    "PriorityFirstStrategy",
    "QuickWinsStrategy",
    "Scheduler",
]

MINUTES_PER_DAY = 24 * 60


def _to_minutes(t: time) -> int:
    """Convert a clock time to minutes since midnight.

    `datetime.time` supports comparison but not arithmetic, so all time math in
    this module goes through here and back out via `_from_minutes`.
    """
    return t.hour * 60 + t.minute


def _from_minutes(total: int) -> time:
    """Inverse of `_to_minutes`. Raises ValueError outside a single day."""
    if not 0 <= total < MINUTES_PER_DAY:
        raise ValueError(f"{total} minutes falls outside a single day")
    return time(total // 60, total % 60)


# ---------------------------------------------------------------------------
# 1. Enums
# ---------------------------------------------------------------------------


class Priority(str, Enum):
    """How badly a task needs to happen.

    Values match the strings in the `app.py` priority selectbox, so UI input
    converts with `Priority(value)` and needs no translation table.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    def score(self) -> int:
        """Numeric weight for sorting. Higher means more urgent."""
        return _PRIORITY_SCORES[self]


_PRIORITY_SCORES = {Priority.LOW: 1, Priority.MEDIUM: 2, Priority.HIGH: 3}

# Sorting bands for `CareTask.weight`. Both are wider than the longest duration
# a task may declare (a full day, enforced in `CareTask.__post_init__`), which
# is what stops a long important task from sinking below a short trivial one.
_PRIORITY_BAND = 10_000
_REQUIRED_BAND = 100_000


class TaskCategory(str, Enum):
    """What kind of care a task is. Used for grouping and explanations."""

    FEEDING = "feeding"
    EXERCISE = "exercise"
    MEDICATION = "medication"
    GROOMING = "grooming"
    ENRICHMENT = "enrichment"
    VET = "vet"


class Recurrence(str, Enum):
    """How often a task repeats."""

    ONCE = "once"
    DAILY = "daily"
    WEEKLY = "weekly"

    def occurs_on(self, day: date, anchor: date | None = None) -> bool:
        """Whether a task with this recurrence is due on `day`.

        `anchor` is the task's start date: nothing is due before it. ONCE and
        WEEKLY are also unanswerable without it, so a missing anchor means
        "not due" rather than an error -- a half-filled task should drop out
        of the plan, not crash it.

        The start-date rule is what keeps rollover honest. A completed daily
        task spawns a successor anchored to tomorrow, and that successor must
        stay out of today's plan or the owner would see the task twice.
        """
        if anchor is not None and day < anchor:
            return False
        if self is Recurrence.DAILY:
            return True
        if anchor is None:
            return False
        if self is Recurrence.ONCE:
            return day == anchor
        return day.weekday() == anchor.weekday()

    def next_after(self, day: date) -> date | None:
        """The next date this recurrence comes due after `day`.

        ONCE returns None -- it has no next occurrence. The arithmetic goes
        through `timedelta` rather than touching the day number directly, so
        month ends, year ends and leap days are the standard library's problem
        and not this module's: 2026-12-31 + 1 day is 2027-01-01, for free.
        """
        if self is Recurrence.DAILY:
            return day + timedelta(days=1)
        if self is Recurrence.WEEKLY:
            return day + timedelta(days=7)
        return None


# ---------------------------------------------------------------------------
# 2. Value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimeWindow:
    """A span of clock time on a single day.

    Frozen because windows are compared and reused constantly; `shift` returns
    a new instance rather than mutating. This is the only class that knows how
    to reason about overlap, so conflict detection lives in exactly one place.
    """

    start: time
    end: time

    def __post_init__(self) -> None:
        """Reject a window that ends before it starts, or has no length."""
        if self.end <= self.start:
            raise ValueError(
                f"window must end after it starts: {self.start}-{self.end}"
            )

    def duration_minutes(self) -> int:
        """Length of this window in whole minutes."""
        return _to_minutes(self.end) - _to_minutes(self.start)

    def overlaps(self, other: TimeWindow) -> bool:
        """True if the two windows share any time.

        Touching edges do not count, so a task ending at 09:00 and one starting
        at 09:00 are both schedulable.
        """
        return self.start < other.end and other.start < self.end

    def contains(self, other: TimeWindow) -> bool:
        """True if `other` fits entirely inside this window."""
        return self.start <= other.start and other.end <= self.end

    def shift(self, minutes: int) -> TimeWindow:
        """Return a copy moved forward (or back, if negative) by `minutes`."""
        return TimeWindow(
            _from_minutes(_to_minutes(self.start) + minutes),
            _from_minutes(_to_minutes(self.end) + minutes),
        )


def _merge_windows(windows: list[TimeWindow]) -> list[TimeWindow]:
    """Collapse overlapping windows so their minutes are not counted twice."""
    merged: list[TimeWindow] = []
    for w in sorted(windows, key=lambda x: x.start):
        if merged and w.start <= merged[-1].end:
            last = merged.pop()
            merged.append(TimeWindow(last.start, max(last.end, w.end)))
        else:
            merged.append(w)
    return merged


# ---------------------------------------------------------------------------
# 3. Domain
# ---------------------------------------------------------------------------


@dataclass
class CareTask:
    """One thing that needs doing for a pet.

    `earliest_start` / `latest_finish` are optional hard bounds -- a morning
    walk before 10:00, meds before bedtime. `is_required` marks tasks the
    scheduler should never silently drop.
    """

    title: str
    duration_minutes: int
    priority: Priority = Priority.MEDIUM
    category: TaskCategory = TaskCategory.ENRICHMENT
    recurrence: Recurrence = Recurrence.DAILY
    earliest_start: time | None = None
    latest_finish: time | None = None
    is_required: bool = False
    completed: bool = False
    anchor_date: date | None = None
    completed_on: date | None = None

    def __post_init__(self) -> None:
        """Reject a task no scheduler could place.

        `TimeWindow` already refuses a zero-length span, but it refuses it deep
        inside `_next_slot`, raising about two clock times the caller never
        wrote. Validating here reports the mistake where it was made, the way
        `TimeWindow` validates its own bounds.

        Only the duration is checked. Contradictory time bounds -- a task due
        after 18:00 that must finish by 09:00 -- are *not* rejected: the owner
        can type exactly that into the UI, and this module answers bad input
        the owner can fix with an explanation in the plan rather than an
        exception, the same way a requested-time clash returns a
        ConflictWarning. See `has_impossible_bounds`.
        """
        if self.duration_minutes <= 0:
            raise ValueError(
                f"{self.title!r}: duration must be positive, "
                f"got {self.duration_minutes}"
            )
        if self.duration_minutes > MINUTES_PER_DAY:
            raise ValueError(
                f"{self.title!r}: duration {self.duration_minutes} min is "
                "longer than a day"
            )

    def mark_complete(self, on: date | None = None) -> CareTask | None:
        """Mark this task done on `on` (default today); return its successor.

        Completion is permanent -- `is_due_on` filters a completed task out for
        good -- so a recurring task that was only flagged would never come back.
        Instead completing one hands back a fresh instance for its next
        occurrence, which the caller files alongside it. `Pet.complete_task`
        does that filing automatically; call it rather than this when the task
        belongs to a pet.

        Returns None for a task that does not recur, and for one already
        complete, so completing twice cannot spawn two successors.
        """
        if self.completed:
            return None
        self.completed = True
        self.completed_on = on or date.today()
        return self.next_instance()

    def next_instance(self) -> CareTask | None:
        """A fresh, incomplete copy of this task anchored to its next occurrence.

        None unless this task has been completed and actually recurs. The next
        date is measured from the day the work happened, not from the original
        anchor, so a weekly brushing done on Thursday moves to Thursdays rather
        than drifting back onto a schedule the owner has stopped keeping.

        `dataclasses.replace` copies every other field, so a successor inherits
        duration, priority and time bounds without this method having to list
        them -- and without going stale when a field is added.
        """
        if self.completed_on is None:
            return None
        next_date = self.recurrence.next_after(self.completed_on)
        if next_date is None:
            return None
        return replace(
            self, completed=False, completed_on=None, anchor_date=next_date
        )

    def reset(self) -> None:
        """Mark this task incomplete again, e.g. at the start of a new day.

        Low-level: this clears the two flags and nothing else. It does not know
        about a successor that `Pet.complete_task` filed, so calling it on a
        task that belongs to a pet leaves two live instances of that task and
        the scheduler books both. Use `Pet.reset_task` for a filed task.
        """
        self.completed = False
        self.completed_on = None

    def weight(self) -> float:
        """Sorting score: required beats priority, priority beats brevity.

        Duration only ever breaks a tie inside a band, so the bands have to be
        wider than any duration that can reach them. They are wider than a
        whole day, and `__post_init__` refuses a task longer than a day -- the
        two together are what keep the bands from bleeding: no pile of short
        low-priority tasks can outrank one required task, and no long urgent
        task sinks beneath a brief trivial one.
        """
        score = self.priority.score() * _PRIORITY_BAND
        if self.is_required:
            score += _REQUIRED_BAND
        return float(score - self.duration_minutes)

    def fits_in(self, window: TimeWindow) -> bool:
        """True if this task's duration and time bounds are satisfied by `window`."""
        if window.duration_minutes() < self.duration_minutes:
            return False
        if self.earliest_start is not None and window.start < self.earliest_start:
            return False
        if self.latest_finish is not None and window.end > self.latest_finish:
            return False
        return True

    def wanted_window(self) -> TimeWindow | None:
        """The exact slot this task asked for, or None if it asked for nothing.

        Setting `earliest_start` is how a task names a time it wants; the slot
        it is really after runs from there for its own duration. A task that
        leaves `earliest_start` unset is happy anywhere and has expressed no
        preference at all -- reading the owner's `day_start` as a request would
        make every ordinary task look like a 07:00 claimant and drown the
        conflict warnings in noise.

        Returns None rather than raising for a request that runs past midnight.
        Such a task is unschedulable anyway and `_next_slot` already ignores it;
        conflict detection should drop it too, not crash on it.
        """
        if self.earliest_start is None:
            return None
        start = _to_minutes(self.earliest_start)
        end = start + self.duration_minutes
        if end >= MINUTES_PER_DAY:
            return None
        return TimeWindow(_from_minutes(start), _from_minutes(end))

    def is_pinned(self) -> bool:
        """True when this task's own bounds leave it exactly one possible slot.

        Zero slack between `earliest_start` and `latest_finish` means the task
        is not asking for a time, it is stating one. That distinction decides
        how bad a clash is: two overlapping flexible tasks are a trade the
        scheduler can make, but two overlapping pinned tasks cannot both happen
        no matter what any scheduler does.
        """
        if self.earliest_start is None or self.latest_finish is None:
            return False
        span = _to_minutes(self.latest_finish) - _to_minutes(self.earliest_start)
        return span == self.duration_minutes

    def is_overdue_on(self, day: date) -> bool:
        """True when this task came due before `day` and was never completed.

        A ONCE task is due on exactly one date, so without this a missed vet
        visit drops out of every later plan: the owner is never reminded of the
        one kind of task that cannot come back on its own. WEEKLY is included
        for the same reason -- it would otherwise go unmentioned for six days.

        Looks strictly backwards, which is what keeps rollover intact. A
        successor is always anchored forward of the day the work was done, so a
        completed daily task's replacement can never read as overdue today.

        DAILY is excluded deliberately: it is already due every day, and missed
        days should not pile into a backlog the owner cannot work through.
        """
        if self.completed or self.anchor_date is None:
            return False
        if self.recurrence is Recurrence.DAILY:
            return False
        return day > self.anchor_date

    def days_overdue(self, day: date) -> int:
        """How many days late this task is on `day`, or 0 if it is not late."""
        if not self.is_overdue_on(day):
            return 0
        return (day - self.anchor_date).days

    def has_impossible_bounds(self) -> bool:
        """True when this task's own limits cannot hold its duration.

        Answers a different question from `fits_in`, which judges one candidate
        window: this judges the task against itself, so the plan can say *why*
        nothing fitted. A 30-minute task due after 18:00 and finished by 09:00
        is not competing badly for a crowded day, it is asking for something no
        day contains -- and the owner needs to be told which of the two it is.
        """
        if self.earliest_start is None or self.latest_finish is None:
            return False
        span = _to_minutes(self.latest_finish) - _to_minutes(self.earliest_start)
        return span < self.duration_minutes

    def is_due_on(self, day: date) -> bool:
        """True if this task should be considered when planning `day`.

        Two ways to qualify: the recurrence lands on `day`, or the task came due
        earlier and was never done. See `is_overdue_on`.
        """
        if self.completed:
            return False
        if self.recurrence.occurs_on(day, self.anchor_date):
            return True
        return self.is_overdue_on(day)


@dataclass
class Pet:
    """An animal in the owner's care, and the tasks it needs.

    Tasks hang off the pet rather than the owner so each animal's needs stay
    identifiable once they are pooled into a household plan.
    """

    name: str
    species: str
    breed: str = ""
    age_years: float = 0.0
    energy_level: int = 3
    notes: list[str] = field(default_factory=list)
    tasks: list[CareTask] = field(default_factory=list)

    def add_task(self, task: CareTask) -> None:
        """Attach a task to this pet."""
        self.tasks.append(task)

    def remove_task(self, title: str) -> bool:
        """Remove one task with this title, preferring the live instance.

        A rollover leaves the completed record and its successor side by side
        under one title, the completed one first. Removing the first match
        would delete the history and leave the task still due tomorrow, which
        is the opposite of what removing it means. A completed record is only
        removed when nothing open matches.

        Returns False if no task matched at all.
        """
        for task in self.tasks:
            if task.title == title and not task.completed:
                return self.remove_instance(task)
        for task in self.tasks:
            if task.title == title:
                return self.remove_instance(task)
        return False

    def remove_instance(self, task: CareTask) -> bool:
        """Remove this exact task object. False if it is not this pet's.

        Identity rather than title, because a title is not an identifier here:
        after a rollover one title covers both the completed record and its
        successor. A caller holding the row a user clicked -- the UI's task
        list -- has to be able to say which of the two it means, and asking by
        title would delete the other one.

        `CareTask` is a mutable dataclass, so two tasks with identical fields
        compare equal; matching with `is` is the same choice
        `DailyPlan._placement_of` makes, for the same reason.
        """
        for i, existing in enumerate(self.tasks):
            if existing is task:
                del self.tasks[i]
                return True
        return False

    def complete_task(self, title: str, on: date | None = None) -> CareTask | None:
        """Complete the first matching open task and roll it over if it recurs.

        Returns the successor -- already added to this pet -- or None if no open
        task matched or the task was a one-off. The completed task stays in the
        list so the day keeps a record of what was actually done; the successor
        sits beside it, due on its own date.
        """
        for task in self.tasks:
            if task.title == title and not task.completed:
                successor = task.mark_complete(on)
                if successor is not None:
                    self.tasks.append(successor)
                return successor
        return None

    def reset_task(self, title: str) -> bool:
        """Undo a completion: clear the record and drop the successor it filed.

        `CareTask.reset` is not enough for a task that belongs to a pet.
        Completing one files a successor beside it, so clearing the flag alone
        would leave two live instances of the same task and the scheduler would
        book both. This undoes the whole completion instead.

        Works on the most recent completed instance, so resetting twice walks
        back through the history rather than undoing one day twice. Returns
        False when no completed instance matched.
        """
        for task in reversed(self.tasks):
            if task.title == title and task.completed:
                return self.undo_completion(task)
        return False

    def undo_completion(self, task: CareTask) -> bool:
        """Undo this exact completion, withdrawing the successor it filed.

        The identity-based form of `reset_task`, for the same reason
        `remove_instance` exists: the UI knows which row was clicked, and a
        rolled-over task has two rows under one title.

        Returns False unless `task` is a completed task of this pet.
        """
        if not task.completed or not any(other is task for other in self.tasks):
            return False
        spawned = (
            task.recurrence.next_after(task.completed_on)
            if task.completed_on is not None
            else None
        )
        task.reset()
        if spawned is not None:
            for other in self.tasks:
                if (
                    other is not task
                    and other.title == task.title
                    and not other.completed
                    and other.anchor_date == spawned
                ):
                    self.remove_instance(other)
                    break
        return True

    def tasks_due_on(self, day: date) -> list[CareTask]:
        """Tasks whose recurrence makes them relevant to `day`."""
        return [t for t in self.tasks if t.is_due_on(day)]


@dataclass
class OwnerPreferences:
    """The owner's constraints -- the boundaries the scheduler plans inside.

    `busy_windows` are times the owner is unavailable (work, commute). These
    are carved out of the day before any task is placed.
    """

    day_start: time = time(8, 0)
    day_end: time = time(21, 0)
    max_daily_minutes: int = 180
    busy_windows: list[TimeWindow] = field(default_factory=list)
    break_minutes: int = 10

    def available_minutes(self) -> int:
        """Usable minutes in the day after removing busy windows.

        Capped by `max_daily_minutes` -- the owner may have a long day but only
        want to spend a couple of hours of it on pet care.
        """
        if self.day_end <= self.day_start:
            return 0
        day_open, day_close = _to_minutes(self.day_start), _to_minutes(self.day_end)
        free = day_close - day_open
        for busy in _merge_windows(self.busy_windows):
            overlap_start = max(day_open, _to_minutes(busy.start))
            overlap_end = min(day_close, _to_minutes(busy.end))
            if overlap_end > overlap_start:
                free -= overlap_end - overlap_start
        return max(0, min(free, self.max_daily_minutes))

    def is_free(self, window: TimeWindow) -> bool:
        """True if `window` sits in the day and clears every busy window."""
        if window.start < self.day_start or window.end > self.day_end:
            return False
        return not any(window.overlaps(busy) for busy in self.busy_windows)


@dataclass
class Owner:
    """The person doing the care."""

    name: str
    pets: list[Pet] = field(default_factory=list)
    preferences: OwnerPreferences = field(default_factory=OwnerPreferences)

    def add_pet(self, pet: Pet) -> None:
        """Add a pet to this owner's household."""
        self.pets.append(pet)

    def get_pet(self, name: str) -> Pet | None:
        """Look up a pet by name, ignoring case. Returns None if not found."""
        for pet in self.pets:
            if pet.name.casefold() == name.casefold():
                return pet
        return None

    def all_tasks(self) -> list[CareTask]:
        """Every task across every pet, flattened."""
        return [task for pet in self.pets for task in pet.tasks]

    def find_tasks(
        self,
        pet_name: str | None = None,
        completed: bool | None = None,
    ) -> list[tuple[Pet, CareTask]]:
        """Tasks matching the given filters, each paired with its pet.

        Both filters are optional and both narrow: no arguments give the whole
        household, `completed=False` gives everything still outstanding, and
        the two together answer "what has Mochi got left to do". `None` means
        "do not care", which is why `completed` is a tri-state rather than a
        plain flag -- `False` and "unfiltered" are different questions.

        Returns pairs rather than bare tasks for the same reason
        `Scheduler._candidates` does: a CareTask cannot say whose it is, and
        "Brush coat" is different work on a Ragdoll than on a Retriever.

        `pet_name` is matched by `get_pet`, so it ignores case exactly like a
        lookup does, and an unknown name gives nothing rather than everything
        -- a filter that silently stops filtering is worse than an empty list.
        """
        wanted = None
        if pet_name is not None:
            wanted = self.get_pet(pet_name)
            if wanted is None:
                return []
        return [
            (pet, task)
            for pet in self.pets
            for task in pet.tasks
            if (wanted is None or pet is wanted)
            and (completed is None or task.completed is completed)
        ]


# ---------------------------------------------------------------------------
# 4. Plan output
# ---------------------------------------------------------------------------


@dataclass
class ScheduledTask:
    """A task placed at a specific time, with the reason it landed there.

    The reason is captured at placement time, while the scheduler still knows
    why it chose this slot. Reconstructing that after the fact is much harder,
    and the project requires the plan to explain itself.
    """

    task: CareTask
    pet: Pet
    slot: TimeWindow
    reason: str = ""

    def label(self) -> str:
        """One-line display string."""
        return (
            f"{self.slot.start:%H:%M} - {self.pet.name}: {self.task.title} "
            f"({self.task.duration_minutes} min) [{self.task.priority.value}]"
        )


@dataclass
class SkippedTask:
    """A task that was due but could not be placed, and why.

    A bare CareTask could not say whose it was or what went wrong, which is
    exactly what the owner needs to know.
    """

    task: CareTask
    pet: Pet
    reason: str = ""

    def label(self) -> str:
        """One-line display string."""
        return (
            f"{self.pet.name}: {self.task.title} "
            f"({self.task.duration_minutes} min) - {self.reason}"
        )


@dataclass
class ConflictWarning:
    """Two tasks that asked for the same clock time, and what came of it.

    Data, never an exception. By the time this exists the scheduler has already
    produced a workable plan by moving one of the two, so raising would throw a
    good plan away in order to report a preference. Only the owner can judge
    whether 07:30 really belonged to the walk or to the medication, so the plan
    states the trade and leaves the judgement to them.

    Normally `first` kept the time and `second` lost it. When `both_displaced`
    is set, neither kept it -- two tasks can both want 07:30 on a day that only
    opens at 08:00 -- and naming either one the winner would simply be false.
    The two are then ordered by where they actually landed, earliest first,
    with a task that was never placed last, and `outcome` says so.

    Each task is paired with its pet the way ScheduledTask and SkippedTask are:
    a bare pair of tasks could not say whose they were, which is the first
    thing the owner needs to know.
    """

    wanted: TimeWindow
    first_pet: Pet
    first_task: CareTask
    second_pet: Pet
    second_task: CareTask
    outcome: str = ""
    unresolvable: bool = False
    both_displaced: bool = False

    def involves(self, pet: Pet) -> bool:
        """True if `pet` owns either side.

        Lets `DailyPlan.for_pet` keep a cross-pet clash in one animal's view
        instead of dropping it, which is precisely the case the owner most
        needs to see: this pet lost its slot to the *other* one.
        """
        return self.first_pet is pet or self.second_pet is pet

    def label(self) -> str:
        """One-line display string, same shape as the other plan entries.

        Named `label` to match ScheduledTask and SkippedTask so `explain()` and
        the Streamlit UI can render any of the three without knowing which they
        hold. This string is the warning the scheduler returns instead of
        raising.
        """
        note = "IMPOSSIBLE: " if self.unresolvable else ""
        return (
            f"{note}{self.wanted.start:%H:%M} - {self.first_pet.name}: "
            f"{self.first_task.title} and {self.second_pet.name}: "
            f"{self.second_task.title} both wanted this time - {self.outcome}"
        )


@dataclass
class DailyPlan:
    """The scheduler's output for one household on one day.

    Covers every pet on a single timeline. `skipped` holds tasks that were due
    but did not fit; they still belong to their pet, the plan only records that
    it could not place them.

    `conflicts` holds tasks that *asked for* the same time. That is a different
    question from `is_double_booked()`, which asks whether two tasks were
    *placed* on top of each other. The first is an ordinary crowded morning;
    the second would be a bug.
    """

    day: date
    owner: Owner
    scheduled: list[ScheduledTask] = field(default_factory=list)
    skipped: list[SkippedTask] = field(default_factory=list)
    conflicts: list[ConflictWarning] = field(default_factory=list)
    strategy_name: str = ""

    def pets(self) -> list[Pet]:
        """The distinct pets in this plan, in first-seen order."""
        out: list[Pet] = []
        for pet in [s.pet for s in self.scheduled] + [k.pet for k in self.skipped]:
            if not any(pet is seen for seen in out):
                out.append(pet)
        return out

    def for_pet(self, pet: Pet) -> DailyPlan:
        """A copy of this plan narrowed to one animal.

        Conflicts are filtered by `involves` rather than by ownership, so a
        clash with another pet stays in this pet's view. Dropping it would hide
        the reason this animal's task moved.
        """
        return DailyPlan(
            day=self.day,
            owner=self.owner,
            scheduled=[s for s in self.scheduled if s.pet is pet],
            skipped=[k for k in self.skipped if k.pet is pet],
            conflicts=[c for c in self.conflicts if c.involves(pet)],
            strategy_name=self.strategy_name,
        )

    def total_minutes(self) -> int:
        """Total scheduled care time for the day."""
        return sum(s.task.duration_minutes for s in self.scheduled)

    def is_double_booked(self) -> bool:
        """True if any two scheduled slots overlap. Should always be False.

        This is the output invariant: the scheduler shares one `taken` list
        across the whole household, so a True here is a bug in the scheduler,
        not a crowded day. For the input-level question -- did two tasks ask
        for the same time? -- see `conflicts` and `detect_conflicts()`.

        Sorting first means only neighbours need comparing, since a sorted list
        can only conflict between adjacent entries.
        """
        slots = sorted((s.slot for s in self.scheduled), key=lambda w: w.start)
        return any(a.overlaps(b) for a, b in zip(slots, slots[1:]))

    def detect_conflicts(self) -> list[ConflictWarning]:
        """Every pair of tasks that asked for the same clock time.

        Distinct from `is_double_booked()`, which asks whether the *output*
        overlapped. This asks whether the *input* was over-subscribed: two
        tasks both named 07:30, the scheduler quietly handed it to one of them,
        and without this the owner would never learn the trade was made.

        Never raises. A clash is information the owner acts on, not an error --
        the plan it describes is already valid.

        Compares every pair rather than sweeping a sorted list: a household's
        day is single-digit tasks, and a sweep only compares neighbours, so a
        long 07:00 task clashing with a short 07:50 one two entries away would
        be missed. Three tasks at one time therefore report three pairs.
        """
        requested = self._requested_slots()
        conflicts: list[ConflictWarning] = []
        for i, (pet_a, task_a, want_a) in enumerate(requested):
            for pet_b, task_b, want_b in requested[i + 1 :]:
                if want_a.overlaps(want_b):
                    conflicts.append(
                        self._warning_for(
                            pet_a, task_a, want_a, pet_b, task_b, want_b
                        )
                    )
        return conflicts

    def _warning_for(
        self,
        pet_a: Pet,
        task_a: CareTask,
        want_a: TimeWindow,
        pet_b: Pet,
        task_b: CareTask,
        want_b: TimeWindow,
    ) -> ConflictWarning:
        """Describe one clash: who kept the time, who lost it, what followed.

        Whichever task actually holds the window it asked for is the winner.

        Neither one holding it is a real outcome, not a fallback to be folded
        into the second task losing: two tasks can both ask for 07:30 on a day
        that opens at 08:00, and then the loser-shaped report is wrong in a way
        the owner would act on -- it would name the task that got the *earlier*
        slot as the one that had to move. That case gets its own shape.
        """
        if self._kept(task_a, want_a):
            return self._won_by(pet_a, task_a, want_a, pet_b, task_b)
        if self._kept(task_b, want_b):
            return self._won_by(pet_b, task_b, want_b, pet_a, task_a)

        ordered = sorted(
            ((pet_a, task_a), (pet_b, task_b)),
            key=lambda pair: self._start_of(pair[1]),
        )
        (first_pet, first_task), (second_pet, second_task) = ordered
        return ConflictWarning(
            wanted=min(want_a, want_b, key=lambda w: w.start),
            first_pet=first_pet,
            first_task=first_task,
            second_pet=second_pet,
            second_task=second_task,
            outcome=(
                f"neither kept it: {first_pet.name}'s {first_task.title} "
                f"{self._outcome_for(first_task)}, "
                f"{second_pet.name}'s {second_task.title} "
                f"{self._outcome_for(second_task)}"
            ),
            unresolvable=first_task.is_pinned() and second_task.is_pinned(),
            both_displaced=True,
        )

    def _won_by(
        self,
        w_pet: Pet,
        winner: CareTask,
        wanted: TimeWindow,
        l_pet: Pet,
        loser: CareTask,
    ) -> ConflictWarning:
        """A clash where `winner` is sitting in the window it asked for."""
        return ConflictWarning(
            wanted=wanted,
            first_pet=w_pet,
            first_task=winner,
            second_pet=l_pet,
            second_task=loser,
            outcome=self._outcome_for(loser),
            unresolvable=winner.is_pinned() and loser.is_pinned(),
        )

    def _kept(self, task: CareTask, wanted: TimeWindow) -> bool:
        """True if `task` was placed in exactly the window it asked for.

        TimeWindow is frozen, so `==` compares by value.
        """
        placed = self._placement_of(task)
        return placed is not None and placed.slot == wanted

    def _start_of(self, task: CareTask) -> int:
        """Minutes into the day where `task` landed; a whole day if it did not.

        The sort key for a clash neither task won: the one that ended up
        earlier is reported first, and a task that was never placed sorts last.
        """
        placed = self._placement_of(task)
        if placed is None:
            return MINUTES_PER_DAY
        return _to_minutes(placed.slot.start)

    def _requested_slots(self) -> list[tuple[Pet, CareTask, TimeWindow]]:
        """Every task in this plan that named a time, with the window it named.

        Draws on `skipped` as well as `scheduled`: a task that lost its slot so
        badly it never got placed is the clearest conflict there is, and
        leaving it out would hide the worst case.
        """
        out: list[tuple[Pet, CareTask, TimeWindow]] = []
        for pet, task in [(s.pet, s.task) for s in self.scheduled] + [
            (k.pet, k.task) for k in self.skipped
        ]:
            window = task.wanted_window()
            if window is not None:
                out.append((pet, task, window))
        return out

    def _placement_of(self, task: CareTask) -> ScheduledTask | None:
        """The scheduled entry for `task`, or None if it was not placed.

        Matched with `is` rather than `==`: CareTask is a mutable dataclass, so
        two tasks with identical fields compare equal. `Scheduler.build_plan`
        keys its pet lookup on `id()` for the same reason.
        """
        for item in self.scheduled:
            if item.task is task:
                return item
        return None

    def _outcome_for(self, task: CareTask) -> str:
        """How the plan resolved a task that lost the time it asked for."""
        placed = self._placement_of(task)
        if placed is not None:
            return f"moved to {placed.slot.start:%H:%M}"
        for item in self.skipped:
            if item.task is task:
                return f"not scheduled: {item.reason}"
        return "dropped from the plan"

    def explain(self) -> str:
        """Human-readable summary of the plan and why tasks were kept or dropped."""
        pet_names = ", ".join(p.name for p in self.pets()) or "no pets"
        lines = [
            f"Daily plan for {self.owner.name} ({pet_names}) on {self.day:%Y-%m-%d}",
            f"Ordering: {self.strategy_name or 'unspecified'}",
            f"Total care time: {self.total_minutes()} min of "
            f"{self.owner.preferences.max_daily_minutes} min allowed",
            "",
        ]
        if self.scheduled:
            lines.append("Scheduled:")
            for item in self.scheduled:
                lines.append(f"  {item.label()}")
                if item.reason:
                    lines.append(f"      why: {item.reason}")
        else:
            lines.append("Scheduled: nothing fit today.")
        if self.skipped:
            lines += ["", "Skipped:"]
            lines += [f"  {item.label()}" for item in self.skipped]
        if self.conflicts:
            lines += ["", "Time conflicts (both asked for the same slot):"]
            lines += [f"  {item.label()}" for item in self.conflicts]
        return "\n".join(lines)

    def to_rows(self) -> list[dict]:
        """Flatten to dicts for `st.table` / `st.dataframe` in the Streamlit UI."""
        return [
            {
                "time": f"{s.slot.start:%H:%M}-{s.slot.end:%H:%M}",
                "pet": s.pet.name,
                "task": s.task.title,
                "minutes": s.task.duration_minutes,
                "priority": s.task.priority.value,
                "why": s.reason,
            }
            for s in self.scheduled
        ]


# ---------------------------------------------------------------------------
# 5. Engine
# ---------------------------------------------------------------------------


class SchedulingStrategy(ABC):
    """Decides what order tasks are *considered* in.

    Separated from Scheduler because "which ordering is right" is a product
    tradeoff, not a settled fact -- swapping strategies is how the app shows
    that tradeoff instead of just asserting one answer.
    """

    name: str = "base"

    @abstractmethod
    def order(self, tasks: list[CareTask]) -> list[CareTask]:
        """Return `tasks` sorted into the order the scheduler should try them."""

    @abstractmethod
    def describe(self) -> str:
        """Short explanation of this ordering, shown to the user in the plan."""


class PriorityFirstStrategy(SchedulingStrategy):
    """Highest priority first. Protects critical care like medication."""

    name = "priority-first"

    def order(self, tasks: list[CareTask]) -> list[CareTask]:
        """Sort by weight descending, so required and urgent care goes first."""
        return sorted(tasks, key=lambda t: t.weight(), reverse=True)

    def describe(self) -> str:
        """One-line summary of this ordering for the plan's explanation."""
        return "Most important tasks first, so critical care is never crowded out."


class QuickWinsStrategy(SchedulingStrategy):
    """Shortest first. Fits the most individual tasks into a tight day."""

    name = "quick-wins"

    def order(self, tasks: list[CareTask]) -> list[CareTask]:
        """Sort by duration ascending, breaking ties by weight."""
        return sorted(tasks, key=lambda t: (t.duration_minutes, -t.weight()))

    def describe(self) -> str:
        """One-line summary of this ordering for the plan's explanation."""
        return "Shortest tasks first, to get the most done in a limited day."


class Scheduler:
    """Turns a household's tasks into a DailyPlan, inside the owner's constraints.

    Not a dataclass: this is the behavior-bearing class, and its two fields are
    collaborators rather than data.
    """

    def __init__(
        self,
        preferences: OwnerPreferences,
        strategy: SchedulingStrategy | None = None,
    ) -> None:
        """Store the owner's constraints; default to priority-first ordering."""
        self.preferences = preferences
        self.strategy = strategy or PriorityFirstStrategy()

    def build_plan(self, owner: Owner, day: date) -> DailyPlan:
        """Main entry point. Select, order, and place every pet's tasks for `day`.

        One `taken` list and one minute budget are shared across the whole
        household, so two pets can never be booked into the same slot.
        """
        pairs = self._candidates(owner, day)
        # CareTask is a mutable dataclass and therefore unhashable, so the
        # task -> pet mapping is keyed by identity instead.
        pet_by_task = {id(task): pet for pet, task in pairs}
        ordered = self.strategy.order([task for _, task in pairs])

        taken: list[TimeWindow] = []
        scheduled: list[ScheduledTask] = []
        skipped: list[SkippedTask] = []
        used = 0
        budget = self.preferences.max_daily_minutes

        for task in ordered:
            pet = pet_by_task[id(task)]
            if used + task.duration_minutes > budget:
                skipped.append(
                    SkippedTask(
                        task,
                        pet,
                        self._note_overdue(
                            task,
                            day,
                            f"would exceed the {budget} min daily limit "
                            f"({used} min already booked)",
                        ),
                    )
                )
                continue
            slot = self._next_slot(task, taken)
            if slot is None:
                skipped.append(
                    SkippedTask(
                        task,
                        pet,
                        self._note_overdue(task, day, self._no_slot_reason(task)),
                    )
                )
                continue
            taken.append(slot)
            used += task.duration_minutes
            scheduled.append(
                ScheduledTask(task, pet, slot, self._reason_for(task, slot, day))
            )

        plan = DailyPlan(
            day=day,
            owner=owner,
            scheduled=self.sort_by_time(scheduled),
            skipped=skipped,
            strategy_name=self.strategy.name,
        )
        # Captured here, while the plan is being made, for the same reason
        # ScheduledTask stores its reason at placement time: the competition
        # is invisible once the day is laid out, so it is recorded now.
        plan.conflicts = plan.detect_conflicts()
        return plan

    @staticmethod
    def sort_by_time(items: list[ScheduledTask]) -> list[ScheduledTask]:
        """Return `items` in chronological order of their start time.

        Tasks are placed in whatever order the strategy considered them, which
        is deliberately not clock order -- a high-priority evening task can be
        booked before a low-priority morning one. The owner reads the plan as a
        timeline, so the final list is sorted here, once, on the way out.

        `sorted` is stable, so two tasks starting at the same minute keep their
        placement order; the key only has to answer "when does this start".
        """
        return sorted(items, key=lambda s: s.slot.start)

    def _candidates(self, owner: Owner, day: date) -> list[tuple[Pet, CareTask]]:
        """Tasks due on `day` across all pets, each kept paired with its pet.

        Refuses the same CareTask instance appearing under two pets. The plan
        keys its task -> pet lookup by identity, so an aliased task would be
        credited to whichever pet was added last and vanish from the other
        pet's view of the day. The sharing itself is the bug, not just the
        lookup: one instance carries one `completed` flag, so finishing the
        task for one pet would silently finish it for both. Failing loudly
        beats either quiet wrong answer.
        """
        pairs: list[tuple[Pet, CareTask]] = []
        seen: dict[int, Pet] = {}
        for pet in owner.pets:
            for task in pet.tasks_due_on(day):
                shared_with = seen.get(id(task))
                if shared_with is not None:
                    raise ValueError(
                        f"task {task.title!r} is shared by {shared_with.name} "
                        f"and {pet.name}; each pet needs its own CareTask"
                    )
                seen[id(task)] = pet
                pairs.append((pet, task))
        return pairs

    def _next_slot(self, task: CareTask, taken: list[TimeWindow]) -> TimeWindow | None:
        """Earliest window fitting `task` that clears `taken` and the owner's
        busy windows. Returns None if the day has no room left.

        Only boundary times are tried -- the day's start, the end of each busy
        window, and the end of each booked slot plus a break. A task can only
        ever start at one of those, so stepping minute by minute would test
        hundreds of positions to reach the same answer.
        """
        prefs = self.preferences
        earliest = _to_minutes(task.earliest_start or prefs.day_start)

        starts = {_to_minutes(prefs.day_start), earliest}
        starts.update(_to_minutes(b.end) for b in prefs.busy_windows)
        starts.update(_to_minutes(t.end) + prefs.break_minutes for t in taken)

        for start in sorted(s for s in starts if s >= earliest):
            end = start + task.duration_minutes
            if end >= MINUTES_PER_DAY:
                continue
            window = TimeWindow(_from_minutes(start), _from_minutes(end))
            if not prefs.is_free(window):
                continue
            if any(window.overlaps(t) for t in taken):
                continue
            if not task.fits_in(window):
                continue
            return window
        return None

    def _reason_for(self, task: CareTask, slot: TimeWindow, day: date) -> str:
        """Build the explanation string stored on the resulting ScheduledTask."""
        bits = [f"{task.priority.value} priority"]
        if task.is_required:
            bits.append("required care")
        if task.is_overdue_on(day):
            bits.append(self._lateness(task, day))
        bits.append(
            f"earliest {task.duration_minutes} min opening at {slot.start:%H:%M}"
        )
        if task.latest_finish is not None:
            bits.append(f"must finish by {task.latest_finish:%H:%M}")
        return "; ".join(bits)

    @staticmethod
    def _lateness(task: CareTask, day: date) -> str:
        """How late `task` is on `day`, phrased for the plan."""
        late = task.days_overdue(day)
        return (
            f"overdue since {task.anchor_date:%Y-%m-%d} "
            f"({late} day{'s' if late != 1 else ''} late)"
        )

    @staticmethod
    def _no_slot_reason(task: CareTask) -> str:
        """Why nothing fitted: a crowded day, or bounds no day could satisfy.

        Both start with "no free slot" because both end the same way for the
        owner -- the task is not on today's plan -- but only one of them can be
        fixed by freeing up time.
        """
        if not task.has_impossible_bounds():
            return "no free slot left that meets its time limits"
        return (
            f"no free slot left: its own limits "
            f"({task.earliest_start:%H:%M}-{task.latest_finish:%H:%M}) cannot "
            f"hold {task.duration_minutes} min"
        )

    def _note_overdue(self, task: CareTask, day: date, reason: str) -> str:
        """Prefix a skip reason with how late `task` is, if it is late at all.

        A task that was already overdue and then could not be fitted is the
        worst case a plan can contain, and the bare skip reason does not say
        so -- it reads the same as a task first seen today.
        """
        if not task.is_overdue_on(day):
            return reason
        return f"{self._lateness(task, day)}; {reason}"
