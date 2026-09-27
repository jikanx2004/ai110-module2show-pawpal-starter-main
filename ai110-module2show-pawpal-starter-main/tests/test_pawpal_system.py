"""Tests for the PawPal+ logic layer.

Organised by the layer under test, bottom-up: time math, enums, tasks,
containers, constraints, then the scheduler. The scheduler tests carry the
weight -- they cover the behaviors that would actually ruin someone's day if
they broke (double-booking, ignored deadlines, blown time budgets).

The classes after TestDemoHousehold are the edge cases: invalid input, missed
work, the recurring lifecycle over many days, ties in the sort, the ends of the
day, and who gets named the winner of a contested time. Several of them are
regression tests -- their docstrings say what the old behavior was.

Run with:  pytest
"""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest

from main import build_demo_household
from pawpal_system import (
    MINUTES_PER_DAY,
    CareTask,
    DailyPlan,
    Owner,
    OwnerPreferences,
    Pet,
    Priority,
    PriorityFirstStrategy,
    QuickWinsStrategy,
    Recurrence,
    ScheduledTask,
    Scheduler,
    SchedulingStrategy,
    TaskCategory,
    TimeWindow,
)

WEDNESDAY = date(2026, 9, 23)
THURSDAY = date(2026, 9, 24)
LAST_WEDNESDAY = date(2026, 9, 16)


# ---------------------------------------------------------------------------
# TimeWindow
# ---------------------------------------------------------------------------


class TestTimeWindow:
    def test_duration(self):
        assert TimeWindow(time(8, 0), time(8, 30)).duration_minutes() == 30

    def test_duration_across_hours(self):
        assert TimeWindow(time(8, 45), time(10, 15)).duration_minutes() == 90

    def test_touching_edges_do_not_overlap(self):
        """Back-to-back tasks must both be schedulable."""
        a = TimeWindow(time(8, 0), time(9, 0))
        b = TimeWindow(time(9, 0), time(10, 0))
        assert not a.overlaps(b)
        assert not b.overlaps(a)

    def test_partial_overlap(self):
        a = TimeWindow(time(8, 0), time(9, 0))
        b = TimeWindow(time(8, 30), time(9, 30))
        assert a.overlaps(b)
        assert b.overlaps(a)

    def test_nested_window_overlaps(self):
        outer = TimeWindow(time(8, 0), time(12, 0))
        inner = TimeWindow(time(9, 0), time(10, 0))
        assert outer.overlaps(inner)
        assert inner.overlaps(outer)

    def test_contains(self):
        outer = TimeWindow(time(8, 0), time(12, 0))
        assert outer.contains(TimeWindow(time(9, 0), time(10, 0)))
        assert not outer.contains(TimeWindow(time(11, 0), time(13, 0)))

    def test_contains_is_not_symmetric(self):
        outer = TimeWindow(time(8, 0), time(12, 0))
        inner = TimeWindow(time(9, 0), time(10, 0))
        assert not inner.contains(outer)

    def test_shift_forward_and_back(self):
        w = TimeWindow(time(8, 0), time(8, 30))
        assert w.shift(60) == TimeWindow(time(9, 0), time(9, 30))
        assert w.shift(-60) == TimeWindow(time(7, 0), time(7, 30))

    def test_shift_past_midnight_raises(self):
        with pytest.raises(ValueError):
            TimeWindow(time(23, 0), time(23, 30)).shift(120)

    def test_zero_length_window_rejected(self):
        with pytest.raises(ValueError):
            TimeWindow(time(9, 0), time(9, 0))

    def test_backwards_window_rejected(self):
        with pytest.raises(ValueError):
            TimeWindow(time(10, 0), time(9, 0))

    def test_frozen(self):
        w = TimeWindow(time(8, 0), time(9, 0))
        with pytest.raises(Exception):
            w.start = time(7, 0)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestPriority:
    def test_scores_ascend(self):
        assert Priority.LOW.score() < Priority.MEDIUM.score() < Priority.HIGH.score()

    def test_constructs_from_ui_string(self):
        """app.py feeds raw selectbox strings straight in."""
        assert Priority("high") is Priority.HIGH


class TestRecurrence:
    def test_daily_always_due(self):
        assert Recurrence.DAILY.occurs_on(WEDNESDAY)
        assert Recurrence.DAILY.occurs_on(WEDNESDAY, anchor=LAST_WEDNESDAY)

    def test_weekly_matches_same_weekday(self):
        assert Recurrence.WEEKLY.occurs_on(WEDNESDAY, anchor=LAST_WEDNESDAY)

    def test_weekly_rejects_other_weekday(self):
        assert not Recurrence.WEEKLY.occurs_on(THURSDAY, anchor=LAST_WEDNESDAY)

    def test_once_matches_exact_date(self):
        assert Recurrence.ONCE.occurs_on(WEDNESDAY, anchor=WEDNESDAY)
        assert not Recurrence.ONCE.occurs_on(THURSDAY, anchor=WEDNESDAY)

    @pytest.mark.parametrize("rule", [Recurrence.ONCE, Recurrence.WEEKLY])
    def test_missing_anchor_is_not_due(self, rule):
        """A half-filled task drops out of the plan rather than crashing it."""
        assert not rule.occurs_on(WEDNESDAY)

    def test_nothing_is_due_before_its_anchor(self):
        """The anchor is a start date, which is what keeps rollover honest."""
        assert not Recurrence.DAILY.occurs_on(WEDNESDAY, anchor=THURSDAY)
        assert not Recurrence.WEEKLY.occurs_on(WEDNESDAY, anchor=WEDNESDAY + timedelta(days=7))

    def test_next_after_daily_is_tomorrow(self):
        assert Recurrence.DAILY.next_after(WEDNESDAY) == THURSDAY

    def test_next_after_weekly_is_same_weekday(self):
        nxt = Recurrence.WEEKLY.next_after(WEDNESDAY)
        assert nxt == date(2026, 9, 30) and nxt.weekday() == WEDNESDAY.weekday()

    def test_next_after_once_is_none(self):
        assert Recurrence.ONCE.next_after(WEDNESDAY) is None

    @pytest.mark.parametrize(
        "rule, day, expected",
        [
            (Recurrence.DAILY, date(2026, 12, 31), date(2027, 1, 1)),
            (Recurrence.DAILY, date(2028, 2, 28), date(2028, 2, 29)),
            (Recurrence.WEEKLY, date(2026, 12, 28), date(2027, 1, 4)),
        ],
    )
    def test_next_after_rolls_over_month_year_and_leap_day(self, rule, day, expected):
        """timedelta handles the calendar edges so this module does not have to."""
        assert rule.next_after(day) == expected


# ---------------------------------------------------------------------------
# CareTask
# ---------------------------------------------------------------------------


class TestCareTask:
    def test_required_outranks_higher_priority(self):
        required = CareTask("Meds", 5, Priority.LOW, is_required=True)
        urgent = CareTask("Walk", 5, Priority.HIGH)
        assert required.weight() > urgent.weight()

    def test_priority_outranks_brevity(self):
        """No pile of short low-priority tasks should outrank an important one."""
        short = CareTask("Quick", 1, Priority.LOW)
        important = CareTask("Big", 240, Priority.HIGH)
        assert important.weight() > short.weight()

    def test_shorter_wins_ties(self):
        short = CareTask("Short", 10, Priority.MEDIUM)
        long = CareTask("Long", 60, Priority.MEDIUM)
        assert short.weight() > long.weight()

    def test_fits_in_needs_enough_room(self):
        task = CareTask("Walk", 30)
        assert task.fits_in(TimeWindow(time(8, 0), time(8, 30)))
        assert not task.fits_in(TimeWindow(time(8, 0), time(8, 20)))

    def test_fits_in_respects_earliest_start(self):
        task = CareTask("Walk", 30, earliest_start=time(9, 0))
        assert not task.fits_in(TimeWindow(time(8, 0), time(8, 30)))
        assert task.fits_in(TimeWindow(time(9, 0), time(9, 30)))

    def test_fits_in_respects_latest_finish(self):
        task = CareTask("Meds", 30, latest_finish=time(9, 0))
        assert task.fits_in(TimeWindow(time(8, 30), time(9, 0)))
        assert not task.fits_in(TimeWindow(time(8, 45), time(9, 15)))

    def test_completed_task_is_not_due(self):
        task = CareTask("Walk", 30)
        assert task.is_due_on(WEDNESDAY)
        task.mark_complete()
        assert not task.is_due_on(WEDNESDAY)

    def test_reset_makes_task_due_again(self):
        task = CareTask("Walk", 30)
        task.mark_complete()
        task.reset()
        assert task.is_due_on(WEDNESDAY)

    def test_completing_a_daily_task_yields_tomorrows_instance(self):
        task = CareTask("Walk", 30, Priority.HIGH, TaskCategory.EXERCISE)
        successor = task.mark_complete(on=WEDNESDAY)
        assert successor is not None
        assert successor.anchor_date == THURSDAY
        assert successor.completed is False
        assert not successor.is_due_on(WEDNESDAY) and successor.is_due_on(THURSDAY)

    def test_successor_inherits_every_other_field(self):
        task = CareTask(
            "Meds",
            5,
            Priority.HIGH,
            TaskCategory.MEDICATION,
            latest_finish=time(20, 0),
            is_required=True,
        )
        successor = task.mark_complete(on=WEDNESDAY)
        assert (successor.title, successor.duration_minutes) == ("Meds", 5)
        assert successor.priority is Priority.HIGH
        assert successor.category is TaskCategory.MEDICATION
        assert successor.latest_finish == time(20, 0) and successor.is_required

    def test_completing_a_weekly_task_yields_next_week(self):
        task = CareTask("Brush", 15, recurrence=Recurrence.WEEKLY, anchor_date=WEDNESDAY)
        successor = task.mark_complete(on=WEDNESDAY)
        assert successor.anchor_date == date(2026, 9, 30)

    def test_weekly_rollover_follows_the_day_it_was_done(self):
        """Brushing a day late moves the slot rather than keeping a stale anchor."""
        task = CareTask("Brush", 15, recurrence=Recurrence.WEEKLY, anchor_date=WEDNESDAY)
        successor = task.mark_complete(on=THURSDAY)
        assert successor.anchor_date.weekday() == THURSDAY.weekday()

    def test_completing_a_one_off_yields_nothing(self):
        task = CareTask("Vet visit", 45, recurrence=Recurrence.ONCE, anchor_date=WEDNESDAY)
        assert task.mark_complete(on=WEDNESDAY) is None

    def test_completing_twice_does_not_spawn_a_duplicate(self):
        task = CareTask("Walk", 30)
        assert task.mark_complete(on=WEDNESDAY) is not None
        assert task.mark_complete(on=WEDNESDAY) is None

    def test_original_stays_complete_after_rollover(self):
        task = CareTask("Walk", 30)
        task.mark_complete(on=WEDNESDAY)
        assert task.completed and task.completed_on == WEDNESDAY
        assert not task.is_due_on(THURSDAY)

    def test_reset_clears_the_completion_date(self):
        task = CareTask("Walk", 30)
        task.mark_complete(on=WEDNESDAY)
        task.reset()
        assert task.completed_on is None and task.next_instance() is None

    def test_no_earliest_start_means_no_wanted_window(self):
        """A task that named no time has not asked for one."""
        assert CareTask("Walk", 30).wanted_window() is None

    def test_wanted_window_runs_for_the_task_duration(self):
        window = CareTask("Walk", 30, earliest_start=time(7, 30)).wanted_window()
        assert window == TimeWindow(time(7, 30), time(8, 0))

    def test_wanted_window_past_midnight_is_none(self):
        """An unschedulable request drops out of detection instead of crashing."""
        task = CareTask("Walk", 30, earliest_start=time(23, 50))
        assert task.wanted_window() is None

    def test_is_pinned_when_the_window_is_exactly_the_duration(self):
        task = CareTask(
            "Meds", 15, earliest_start=time(8, 0), latest_finish=time(8, 15)
        )
        assert task.is_pinned() is True

    def test_not_pinned_when_the_window_has_slack(self):
        task = CareTask(
            "Meds", 15, earliest_start=time(8, 0), latest_finish=time(9, 0)
        )
        assert task.is_pinned() is False

    @pytest.mark.parametrize(
        "earliest, latest",
        [(None, None), (time(8, 0), None), (None, time(8, 15))],
    )
    def test_not_pinned_without_both_bounds(self, earliest, latest):
        task = CareTask("Meds", 15, earliest_start=earliest, latest_finish=latest)
        assert task.is_pinned() is False

    def test_weekly_task_needs_anchor_to_be_due(self):
        without = CareTask("Brush", 15, recurrence=Recurrence.WEEKLY)
        withanchor = CareTask(
            "Brush", 15, recurrence=Recurrence.WEEKLY, anchor_date=LAST_WEDNESDAY
        )
        assert not without.is_due_on(WEDNESDAY)
        assert withanchor.is_due_on(WEDNESDAY)


# ---------------------------------------------------------------------------
# Pet and Owner
# ---------------------------------------------------------------------------


class TestPet:
    def test_add_task(self):
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Walk", 30))
        assert len(pet.tasks) == 1

    def test_remove_task_reports_success(self):
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Walk", 30))
        assert pet.remove_task("Walk") is True
        assert pet.tasks == []

    def test_remove_missing_task_is_false(self):
        assert Pet("Mochi", "cat").remove_task("Nope") is False

    def test_tasks_due_on_filters_completed(self):
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Walk", 30))
        done = CareTask("Feed", 10)
        done.mark_complete()
        pet.add_task(done)
        assert [t.title for t in pet.tasks_due_on(WEDNESDAY)] == ["Walk"]

    def test_complete_task_files_the_successor(self):
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Feed", 10))
        successor = pet.complete_task("Feed", on=WEDNESDAY)
        assert successor in pet.tasks and len(pet.tasks) == 2
        assert [t.title for t in pet.tasks_due_on(THURSDAY)] == ["Feed"]

    def test_completed_task_leaves_the_day_it_was_done(self):
        """Today shows the task once -- done -- not once done and once pending."""
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Feed", 10))
        pet.complete_task("Feed", on=WEDNESDAY)
        assert pet.tasks_due_on(WEDNESDAY) == []

    def test_complete_task_skips_already_completed_duplicates(self):
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Feed", 10))
        pet.complete_task("Feed", on=WEDNESDAY)
        again = pet.complete_task("Feed", on=THURSDAY)
        assert again.anchor_date == THURSDAY + timedelta(days=1)
        assert len(pet.tasks) == 3

    def test_complete_missing_task_is_none(self):
        assert Pet("Mochi", "cat").complete_task("Nope") is None

    def test_complete_one_off_adds_nothing(self):
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Vet", 45, recurrence=Recurrence.ONCE, anchor_date=WEDNESDAY))
        assert pet.complete_task("Vet", on=WEDNESDAY) is None
        assert len(pet.tasks) == 1

    def test_pets_do_not_share_default_lists(self):
        """A mutable default would leak tasks between every pet."""
        a, b = Pet("A", "dog"), Pet("B", "dog")
        a.add_task(CareTask("Walk", 30))
        assert b.tasks == []


class TestOwner:
    def test_get_pet_ignores_case(self):
        owner = Owner("Jordan", [Pet("Mochi", "cat")])
        assert owner.get_pet("mochi") is not None

    def test_get_pet_returns_none_when_missing(self):
        assert Owner("Jordan").get_pet("Ghost") is None

    def test_all_tasks_flattens_across_pets(self):
        a, b = Pet("A", "dog"), Pet("B", "cat")
        a.add_task(CareTask("Walk", 30))
        b.add_task(CareTask("Feed", 10))
        assert len(Owner("Jordan", [a, b]).all_tasks()) == 2


# ---------------------------------------------------------------------------
# OwnerPreferences
# ---------------------------------------------------------------------------


class TestOwnerPreferences:
    def test_available_minutes_subtracts_busy_time(self):
        prefs = OwnerPreferences(
            day_start=time(8, 0),
            day_end=time(12, 0),
            max_daily_minutes=1000,
            busy_windows=[TimeWindow(time(9, 0), time(10, 0))],
        )
        assert prefs.available_minutes() == 180  # 240 - 60

    def test_available_minutes_capped_by_preference(self):
        prefs = OwnerPreferences(
            day_start=time(8, 0), day_end=time(20, 0), max_daily_minutes=90
        )
        assert prefs.available_minutes() == 90

    def test_overlapping_busy_windows_counted_once(self):
        """Double-counting would understate the free time."""
        prefs = OwnerPreferences(
            day_start=time(8, 0),
            day_end=time(12, 0),
            max_daily_minutes=1000,
            busy_windows=[
                TimeWindow(time(9, 0), time(10, 0)),
                TimeWindow(time(9, 30), time(11, 0)),
            ],
        )
        assert prefs.available_minutes() == 120  # 240 - 120, not 240 - 150

    def test_busy_time_outside_day_is_ignored(self):
        prefs = OwnerPreferences(
            day_start=time(8, 0),
            day_end=time(12, 0),
            max_daily_minutes=1000,
            busy_windows=[TimeWindow(time(13, 0), time(14, 0))],
        )
        assert prefs.available_minutes() == 240

    def test_is_free_rejects_time_outside_day(self):
        prefs = OwnerPreferences(day_start=time(8, 0), day_end=time(20, 0))
        assert not prefs.is_free(TimeWindow(time(7, 0), time(7, 30)))
        assert not prefs.is_free(TimeWindow(time(19, 45), time(20, 30)))

    def test_is_free_rejects_busy_overlap(self):
        prefs = OwnerPreferences(busy_windows=[TimeWindow(time(9, 0), time(17, 0))])
        assert not prefs.is_free(TimeWindow(time(10, 0), time(10, 30)))
        assert prefs.is_free(TimeWindow(time(17, 0), time(17, 30)))


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


class TestStrategies:
    @pytest.fixture
    def tasks(self):
        return [
            CareTask("Long important", 90, Priority.HIGH),
            CareTask("Short trivial", 5, Priority.LOW),
            CareTask("Medium", 30, Priority.MEDIUM),
        ]

    def test_priority_first_leads_with_importance(self, tasks):
        assert PriorityFirstStrategy().order(tasks)[0].title == "Long important"

    def test_quick_wins_leads_with_brevity(self, tasks):
        assert QuickWinsStrategy().order(tasks)[0].title == "Short trivial"

    def test_strategies_disagree(self, tasks):
        a = [t.title for t in PriorityFirstStrategy().order(tasks)]
        b = [t.title for t in QuickWinsStrategy().order(tasks)]
        assert a != b

    def test_ordering_does_not_mutate_input(self, tasks):
        before = [t.title for t in tasks]
        PriorityFirstStrategy().order(tasks)
        assert [t.title for t in tasks] == before

    def test_ordering_keeps_every_task(self, tasks):
        assert len(QuickWinsStrategy().order(tasks)) == len(tasks)


# ---------------------------------------------------------------------------
# Scheduler -- the behaviors that matter most
# ---------------------------------------------------------------------------


@pytest.fixture
def prefs():
    return OwnerPreferences(
        day_start=time(8, 0),
        day_end=time(20, 0),
        max_daily_minutes=240,
        break_minutes=10,
    )


class TestScheduler:
    def test_places_a_single_task_at_day_start(self, prefs):
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Walk", 30))
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        assert plan.scheduled[0].slot.start == time(8, 0)

    def test_leaves_a_break_between_tasks(self, prefs):
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("A", 30))
        pet.add_task(CareTask("B", 30))
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        first, second = plan.scheduled[0].slot, plan.scheduled[1].slot
        assert second.start >= time(8, 40)  # 08:30 end + 10 min break
        assert not first.overlaps(second)

    def test_never_double_books_across_pets(self, prefs):
        """The core invariant: one owner cannot be in two places at once."""
        a, b = Pet("A", "dog"), Pet("B", "cat")
        for pet in (a, b):
            for i in range(3):
                pet.add_task(CareTask(f"{pet.name}-{i}", 20))
        plan = Scheduler(prefs).build_plan(Owner("J", [a, b], prefs), WEDNESDAY)
        assert len(plan.scheduled) == 6
        assert plan.is_double_booked() is False

    def test_avoids_busy_windows(self):
        prefs = OwnerPreferences(
            day_start=time(8, 0),
            day_end=time(20, 0),
            max_daily_minutes=240,
            busy_windows=[TimeWindow(time(9, 0), time(17, 0))],
        )
        pet = Pet("Mochi", "cat")
        for i in range(4):
            pet.add_task(CareTask(f"T{i}", 30))
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        work = TimeWindow(time(9, 0), time(17, 0))
        assert all(not s.slot.overlaps(work) for s in plan.scheduled)

    def test_respects_latest_finish(self, prefs):
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Meds", 30, latest_finish=time(9, 0)))
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        assert plan.scheduled[0].slot.end <= time(9, 0)

    def test_respects_earliest_start(self, prefs):
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Hike", 30, earliest_start=time(17, 0)))
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        assert plan.scheduled[0].slot.start >= time(17, 0)

    def test_skips_over_budget_tasks_with_a_reason(self):
        prefs = OwnerPreferences(max_daily_minutes=30)
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Fits", 20, Priority.HIGH))
        pet.add_task(CareTask("Too much", 60, Priority.LOW))
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        assert [s.task.title for s in plan.scheduled] == ["Fits"]
        assert plan.skipped[0].task.title == "Too much"
        assert "daily limit" in plan.skipped[0].reason

    def test_never_exceeds_the_budget(self):
        prefs = OwnerPreferences(max_daily_minutes=60)
        pet = Pet("Mochi", "cat")
        for i in range(10):
            pet.add_task(CareTask(f"T{i}", 20))
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        assert plan.total_minutes() <= 60

    def test_skips_impossible_task_with_a_reason(self, prefs):
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Impossible", 30, earliest_start=time(18, 0),
                              latest_finish=time(9, 0)))
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        assert not plan.scheduled
        assert "no free slot" in plan.skipped[0].reason

    def test_required_task_survives_a_tight_day(self):
        prefs = OwnerPreferences(max_daily_minutes=20)
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Meds", 5, Priority.LOW, is_required=True))
        pet.add_task(CareTask("Play", 20, Priority.HIGH))
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        assert [s.task.title for s in plan.scheduled] == ["Meds"]

    def test_completed_tasks_are_not_scheduled(self, prefs):
        pet = Pet("Mochi", "cat")
        done = CareTask("Walk", 30)
        done.mark_complete()
        pet.add_task(done)
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        assert not plan.scheduled
        assert not plan.skipped  # not due at all, so not "skipped" either

    def test_output_is_sorted_by_time(self, prefs):
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Late", 30, Priority.HIGH, earliest_start=time(18, 0)))
        pet.add_task(CareTask("Early", 30, Priority.LOW))
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        starts = [s.slot.start for s in plan.scheduled]
        assert starts == sorted(starts)

    def test_every_placement_records_a_reason(self, prefs):
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Walk", 30, Priority.HIGH))
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        assert "high priority" in plan.scheduled[0].reason

    def test_owner_with_no_pets(self, prefs):
        plan = Scheduler(prefs).build_plan(Owner("Empty", [], prefs), WEDNESDAY)
        assert plan.scheduled == [] and plan.skipped == []

    def test_pet_with_no_tasks(self, prefs):
        owner = Owner("J", [Pet("Mochi", "cat")], prefs)
        assert Scheduler(prefs).build_plan(owner, WEDNESDAY).total_minutes() == 0

    def test_fully_blocked_day_schedules_nothing(self):
        prefs = OwnerPreferences(
            day_start=time(8, 0),
            day_end=time(20, 0),
            busy_windows=[TimeWindow(time(8, 0), time(20, 0))],
        )
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Walk", 30))
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        assert not plan.scheduled
        assert len(plan.skipped) == 1

    def test_defaults_to_priority_first(self, prefs):
        assert Scheduler(prefs).strategy.name == "priority-first"


# ---------------------------------------------------------------------------
# DailyPlan
# ---------------------------------------------------------------------------


class TestConflictDetection:
    """Two tasks asking for the same time -- an input clash, not a scheduler bug."""

    @staticmethod
    def _plan(prefs, entries, strategy=None):
        """Build a plan from (pet_name, task) pairs, one Pet per distinct name."""
        pets: dict[str, Pet] = {}
        for pet_name, task in entries:
            pets.setdefault(pet_name, Pet(pet_name, "dog")).add_task(task)
        owner = Owner("Jordan", list(pets.values()), prefs)
        return Scheduler(prefs, strategy).build_plan(owner, WEDNESDAY)

    def test_ordinary_tasks_produce_no_warnings(self, prefs):
        """The anti-noise rule: a task with no requested time asks for nothing.

        Without this the feature degenerates into flagging every task in the
        household against every other one.
        """
        plan = self._plan(
            prefs,
            [
                ("A", CareTask("Walk", 30)),
                ("A", CareTask("Feed", 10)),
                ("B", CareTask("Brush", 15)),
                ("B", CareTask("Play", 20)),
            ],
        )
        assert plan.conflicts == []

    def test_two_tasks_wanting_the_same_time_warn(self, prefs):
        plan = self._plan(
            prefs,
            [
                ("A", CareTask("Walk", 30, earliest_start=time(9, 0))),
                ("B", CareTask("Feed", 10, earliest_start=time(9, 0))),
            ],
        )
        assert len(plan.conflicts) == 1
        assert plan.conflicts[0].wanted.start == time(9, 0)

    def test_one_pet_can_conflict_with_itself(self, prefs):
        """Usually a data-entry mistake, but still the owner's to resolve."""
        plan = self._plan(
            prefs,
            [
                ("A", CareTask("Walk", 30, earliest_start=time(9, 0))),
                ("A", CareTask("Feed", 10, earliest_start=time(9, 0))),
            ],
        )
        assert len(plan.conflicts) == 1

    def test_back_to_back_requests_do_not_conflict(self, prefs):
        """Mirrors test_touching_edges_do_not_overlap, one layer up."""
        plan = self._plan(
            prefs,
            [
                ("A", CareTask("Walk", 30, earliest_start=time(9, 0))),
                ("B", CareTask("Feed", 15, earliest_start=time(9, 30))),
            ],
        )
        assert plan.conflicts == []

    def test_winner_is_the_task_that_kept_the_time(self, prefs):
        plan = self._plan(
            prefs,
            [
                ("A", CareTask("Walk", 30, earliest_start=time(9, 0))),
                ("B", CareTask("Feed", 10, earliest_start=time(9, 0))),
            ],
        )
        warning = plan.conflicts[0]
        held = plan._placement_of(warning.first_task)
        assert held is not None and held.slot == warning.first_task.wanted_window()

    def test_displaced_task_reports_where_it_moved(self, prefs):
        plan = self._plan(
            prefs,
            [
                ("A", CareTask("Walk", 30, earliest_start=time(9, 0))),
                ("B", CareTask("Feed", 10, earliest_start=time(9, 0))),
            ],
        )
        assert "moved to" in plan.conflicts[0].outcome

    def test_skipped_task_reports_why_it_lost_the_time(self, prefs):
        """The loser had nowhere else to go, and the warning says so."""
        plan = self._plan(
            prefs,
            [
                (
                    "A",
                    CareTask(
                        "Walk",
                        30,
                        earliest_start=time(9, 0),
                        latest_finish=time(9, 30),
                    ),
                ),
                ("B", CareTask("Feed", 10, Priority.HIGH, earliest_start=time(9, 0))),
            ],
        )
        assert "not scheduled" in plan.conflicts[0].outcome

    def test_two_pinned_tasks_are_unresolvable(self, prefs):
        """Neither can move, so no scheduler could ever satisfy both."""
        plan = self._plan(
            prefs,
            [
                (
                    "A",
                    CareTask(
                        "Walk", 30, earliest_start=time(9, 0), latest_finish=time(9, 30)
                    ),
                ),
                (
                    "B",
                    CareTask(
                        "Meds", 15, earliest_start=time(9, 0), latest_finish=time(9, 15)
                    ),
                ),
            ],
        )
        assert plan.conflicts[0].unresolvable is True
        assert "IMPOSSIBLE" in plan.conflicts[0].label()

    def test_one_pinned_one_flexible_is_resolvable(self, prefs):
        plan = self._plan(
            prefs,
            [
                (
                    "A",
                    CareTask(
                        "Meds", 15, earliest_start=time(9, 0), latest_finish=time(9, 15)
                    ),
                ),
                ("B", CareTask("Walk", 30, earliest_start=time(9, 0))),
            ],
        )
        assert plan.conflicts[0].unresolvable is False

    def test_three_tasks_at_one_time_warn_pairwise(self, prefs):
        """Documents the N-choose-2 behavior rather than pretending it is absent."""
        plan = self._plan(
            prefs,
            [
                ("A", CareTask("Walk", 30, earliest_start=time(9, 0))),
                ("B", CareTask("Feed", 20, earliest_start=time(9, 0))),
                ("C", CareTask("Brush", 10, earliest_start=time(9, 0))),
            ],
        )
        assert len(plan.conflicts) == 3

    def test_detection_never_raises(self, prefs):
        """'Returns a warning rather than crashing', encoded as a test."""
        plan = self._plan(
            prefs,
            [
                ("A", CareTask("Late", 30, earliest_start=time(23, 50))),
                ("B", CareTask("Also late", 20, earliest_start=time(23, 55))),
                ("B", CareTask("Normal", 10, earliest_start=time(9, 0))),
            ],
        )
        assert isinstance(plan.conflicts, list)

    def test_a_time_conflict_is_not_a_double_booking(self, prefs):
        """The whole design argument, made executable.

        A plan can warn about two tasks wanting 09:00 while still being a
        perfectly valid, non-overlapping timeline. The two checks answer
        different questions and must not be collapsed into one.
        """
        plan = self._plan(
            prefs,
            [
                ("A", CareTask("Walk", 30, earliest_start=time(9, 0))),
                ("B", CareTask("Feed", 10, earliest_start=time(9, 0))),
            ],
        )
        assert plan.conflicts and plan.is_double_booked() is False


class TestDailyPlan:
    @pytest.fixture
    def plan(self, prefs):
        a, b = Pet("A", "dog"), Pet("B", "cat")
        a.add_task(CareTask("Walk", 30, Priority.HIGH))
        b.add_task(CareTask("Feed", 10, Priority.MEDIUM))
        return Scheduler(prefs).build_plan(Owner("Jordan", [a, b], prefs), WEDNESDAY)

    def test_total_minutes(self, plan):
        assert plan.total_minutes() == 40

    def test_no_conflict_in_real_plan(self, plan):
        assert plan.is_double_booked() is False

    def test_double_booking_check_detects_an_overlap(self, plan):
        """Inject an overlap to prove the check is not vacuously true."""
        first = plan.scheduled[0]
        plan.scheduled.append(ScheduledTask(first.task, first.pet, first.slot))
        assert plan.is_double_booked() is True

    def test_pets_lists_each_animal_once(self, plan):
        assert len(plan.pets()) == 2

    def test_for_pet_filters(self, plan):
        pet = plan.pets()[0]
        view = plan.for_pet(pet)
        assert view.scheduled and all(s.pet is pet for s in view.scheduled)
        assert len(view.scheduled) < len(plan.scheduled)

    def test_explain_mentions_owner_and_pets(self, plan):
        text = plan.explain()
        assert "Jordan" in text and "Walk" in text and "priority-first" in text

    def test_to_rows_shape(self, plan):
        expected = {"time", "pet", "task", "minutes", "priority", "why"}
        rows = plan.to_rows()
        assert len(rows) == 2
        assert all(set(r) == expected for r in rows)

    def test_empty_plan_explains_itself(self):
        text = DailyPlan(day=WEDNESDAY, owner=Owner("Jordan")).explain()
        assert "nothing fit" in text

    @pytest.fixture
    def contested(self, prefs):
        a, b = Pet("A", "dog"), Pet("B", "cat")
        a.add_task(CareTask("Walk", 30, earliest_start=time(9, 0)))
        b.add_task(CareTask("Feed", 10, earliest_start=time(9, 0)))
        return Scheduler(prefs).build_plan(Owner("Jordan", [a, b], prefs), WEDNESDAY)

    def test_explain_lists_time_conflicts(self, contested):
        assert "Time conflicts" in contested.explain()

    def test_for_pet_keeps_a_cross_pet_conflict(self, contested):
        """The clash is why this pet's task moved, so it belongs in its view."""
        pet = contested.pets()[0]
        assert contested.for_pet(pet).conflicts == contested.conflicts

    def test_for_pet_drops_an_unrelated_conflict(self, contested):
        stranger = Pet("Stranger", "cat")
        assert contested.for_pet(stranger).conflicts == []


# ---------------------------------------------------------------------------
# The demo fixture used by main.py
# ---------------------------------------------------------------------------


class TestDemoHousehold:
    @pytest.fixture
    def plan(self):
        owner = build_demo_household(WEDNESDAY)
        return Scheduler(owner.preferences).build_plan(owner, WEDNESDAY)

    def test_demo_schedules_both_pets_without_conflict(self, plan):
        assert len(plan.pets()) == 2
        assert plan.is_double_booked() is False

    def test_demo_avoids_the_workday(self, plan):
        work = TimeWindow(time(9, 0), time(17, 0))
        assert all(not s.slot.overlaps(work) for s in plan.scheduled)

    def test_demo_includes_the_weekly_task(self, plan):
        """Proves anchor_date works end to end, not just in isolation."""
        assert any(s.task.title == "Brush coat" for s in plan.scheduled)

    def test_demo_stays_within_budget(self, plan):
        assert plan.total_minutes() <= plan.owner.preferences.max_daily_minutes

    def test_demo_reports_exactly_one_time_conflict(self, plan):
        assert len(plan.conflicts) == 1

    def test_demo_conflict_names_both_tasks(self, plan):
        label = plan.conflicts[0].label()
        assert "Morning walk" in label and "Ear medication" in label

    def test_demo_conflict_is_resolved_by_moving_under_priority_first(self, plan):
        assert plan.conflicts[0].outcome == "moved to 07:45"

    def test_demo_conflict_becomes_a_skip_under_quick_wins(self):
        """The same clash resolves differently depending on the ordering.

        Worth asserting because it shows a conflict is a property of the plan,
        not of the task list: quick-wins fills the morning with short tasks and
        the walk's 09:00 deadline can no longer absorb the shift.
        """
        owner = build_demo_household(WEDNESDAY)
        plan = Scheduler(owner.preferences, QuickWinsStrategy()).build_plan(
            owner, WEDNESDAY
        )
        assert "not scheduled" in plan.conflicts[0].outcome


# ---------------------------------------------------------------------------
# Invalid input -- the shapes that used to crash a plan
# ---------------------------------------------------------------------------


class TestTaskValidation:
    """A task no scheduler could place is refused where it is written."""

    @pytest.mark.parametrize("bad", [0, -1, -30])
    def test_non_positive_duration_is_refused(self, bad):
        """This used to surface as a TimeWindow error in the middle of a plan.

        A zero-minute task reached `_next_slot`, which built a window ending
        where it started, and the whole household's plan died on a message
        about two clock times the caller never wrote.
        """
        with pytest.raises(ValueError, match="duration must be positive"):
            CareTask("Ghost", bad)

    def test_duration_longer_than_a_day_is_refused(self):
        with pytest.raises(ValueError, match="longer than a day"):
            CareTask("Marathon", MINUTES_PER_DAY + 1)

    def test_a_whole_day_is_allowed(self):
        """The boundary stays legal: absurd, but not incoherent."""
        assert CareTask("Vigil", MINUTES_PER_DAY).duration_minutes == MINUTES_PER_DAY

    def test_the_successor_of_a_valid_task_is_valid(self):
        """`replace` re-runs validation, so rollover must not trip over it."""
        task = CareTask("Walk", 30, recurrence=Recurrence.DAILY)
        assert task.mark_complete(on=WEDNESDAY).duration_minutes == 30

    def test_contradictory_bounds_are_reported_not_raised(self, prefs):
        """Bad time bounds are the owner's to fix, so the plan explains them.

        The UI collects `earliest_start` and `latest_finish` as two independent
        time inputs with no cross-check, so 18:00-09:00 is a couple of clicks
        away. Raising here would take the app down on its next rerun, and this
        module answers input the owner can fix with an explanation -- the same
        choice `ConflictWarning` makes.
        """
        pet = Pet("Mochi", "cat")
        pet.add_task(
            CareTask(
                "Impossible", 30, earliest_start=time(18, 0), latest_finish=time(9, 0)
            )
        )
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        assert not plan.scheduled
        assert "cannot hold 30 min" in plan.skipped[0].reason

    def test_impossible_bounds_are_distinguished_from_a_crowded_day(self):
        """Both end with the task off the plan; only one can be fixed by time."""
        impossible = CareTask(
            "Impossible", 30, earliest_start=time(18, 0), latest_finish=time(9, 0)
        )
        tight = CareTask(
            "Tight", 30, earliest_start=time(8, 0), latest_finish=time(8, 30)
        )
        assert impossible.has_impossible_bounds()
        assert not tight.has_impossible_bounds()
        assert not CareTask("Free", 30).has_impossible_bounds()


# ---------------------------------------------------------------------------
# Overdue work -- a task that came due and was never done
# ---------------------------------------------------------------------------


class TestOverdueTasks:
    """A missed task must not disappear from every later plan."""

    def test_a_missed_one_off_stays_due(self):
        """The case with no second chance: ONCE cannot come back on its own."""
        vet = CareTask(
            "Vet visit", 45, recurrence=Recurrence.ONCE, anchor_date=LAST_WEDNESDAY
        )
        assert vet.is_due_on(LAST_WEDNESDAY)
        assert vet.is_due_on(WEDNESDAY)
        assert vet.is_overdue_on(WEDNESDAY)
        assert vet.days_overdue(WEDNESDAY) == 7

    def test_completing_a_missed_one_off_clears_it(self):
        vet = CareTask(
            "Vet visit", 45, recurrence=Recurrence.ONCE, anchor_date=LAST_WEDNESDAY
        )
        vet.mark_complete(on=WEDNESDAY)
        assert not vet.is_due_on(WEDNESDAY)
        assert not vet.is_overdue_on(THURSDAY)

    def test_a_one_off_is_not_overdue_before_its_date(self):
        vet = CareTask("Vet visit", 45, recurrence=Recurrence.ONCE, anchor_date=THURSDAY)
        assert not vet.is_overdue_on(WEDNESDAY)
        assert vet.days_overdue(WEDNESDAY) == 0

    def test_a_missed_weekly_shows_up_the_next_day_too(self):
        brush = CareTask(
            "Brush", 15, recurrence=Recurrence.WEEKLY, anchor_date=LAST_WEDNESDAY
        )
        assert brush.is_overdue_on(LAST_WEDNESDAY + timedelta(days=1))
        assert brush.is_due_on(WEDNESDAY)

    def test_a_daily_task_never_accumulates_a_backlog(self):
        """DAILY is already due every day; missed days must not pile up."""
        walk = CareTask(
            "Walk", 30, recurrence=Recurrence.DAILY, anchor_date=LAST_WEDNESDAY
        )
        assert walk.is_due_on(WEDNESDAY)
        assert not walk.is_overdue_on(WEDNESDAY)
        assert walk.days_overdue(WEDNESDAY) == 0

    def test_an_unanchored_task_is_never_overdue(self):
        """With no anchor there is no date it was supposed to have happened."""
        brush = CareTask("Brush", 15, recurrence=Recurrence.WEEKLY)
        assert not brush.is_overdue_on(WEDNESDAY)

    def test_a_completed_task_is_never_overdue(self):
        vet = CareTask(
            "Vet", 45, recurrence=Recurrence.ONCE, anchor_date=LAST_WEDNESDAY
        )
        vet.mark_complete(on=LAST_WEDNESDAY)
        assert not vet.is_overdue_on(WEDNESDAY)

    def test_rollover_still_keeps_a_successor_out_of_today(self):
        """The invariant overdue could have broken, stated outright.

        Overdue looks strictly backwards and a successor is anchored forwards,
        so finishing today's walk must not make tomorrow's copy appear today.
        """
        pet = Pet("Mochi", "cat")
        pet.add_task(
            CareTask("Walk", 30, recurrence=Recurrence.DAILY, anchor_date=WEDNESDAY)
        )
        successor = pet.complete_task("Walk", on=WEDNESDAY)
        assert not successor.is_overdue_on(WEDNESDAY)
        assert pet.tasks_due_on(WEDNESDAY) == []
        assert [t.title for t in pet.tasks_due_on(THURSDAY)] == ["Walk"]

    def test_a_weekly_successor_is_not_overdue_in_between(self):
        pet = Pet("Mochi", "cat")
        pet.add_task(
            CareTask("Brush", 15, recurrence=Recurrence.WEEKLY, anchor_date=WEDNESDAY)
        )
        successor = pet.complete_task("Brush", on=WEDNESDAY)
        assert not successor.is_overdue_on(THURSDAY)
        assert pet.tasks_due_on(THURSDAY) == []

    def test_the_plan_says_how_late_an_overdue_task_is(self, prefs):
        pet = Pet("Mochi", "cat")
        pet.add_task(
            CareTask(
                "Vet visit", 45, recurrence=Recurrence.ONCE, anchor_date=LAST_WEDNESDAY
            )
        )
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        assert "overdue since 2026-09-16 (7 days late)" in plan.scheduled[0].reason
        assert "overdue since" in plan.explain()

    def test_one_day_late_reads_as_one_day(self, prefs):
        pet = Pet("Mochi", "cat")
        pet.add_task(
            CareTask("Vet", 45, recurrence=Recurrence.ONCE, anchor_date=WEDNESDAY)
        )
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), THURSDAY)
        assert "(1 day late)" in plan.scheduled[0].reason

    def test_a_skipped_overdue_task_says_it_was_already_late(self):
        """The worst thing a plan can contain: late already, and still not done."""
        prefs = OwnerPreferences(
            day_start=time(8, 0), day_end=time(20, 0), max_daily_minutes=10
        )
        pet = Pet("Mochi", "cat")
        pet.add_task(
            CareTask(
                "Vet visit", 45, recurrence=Recurrence.ONCE, anchor_date=LAST_WEDNESDAY
            )
        )
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        assert "overdue since" in plan.skipped[0].reason
        assert "daily limit" in plan.skipped[0].reason


# ---------------------------------------------------------------------------
# The recurring lifecycle -- removing, resetting, repeating
# ---------------------------------------------------------------------------


class TestRecurringTaskLifecycle:
    @staticmethod
    def _pet_with_daily_walk():
        pet = Pet("Mochi", "cat")
        pet.add_task(
            CareTask("Walk", 30, recurrence=Recurrence.DAILY, anchor_date=WEDNESDAY)
        )
        return pet

    def test_remove_task_takes_the_live_instance_not_the_record(self):
        """Removing a rolled-over task has to actually stop it coming back.

        Completion leaves the record first in the list and the successor after
        it, so removing the first match deleted the history and left the task
        due again tomorrow -- reported as a success.
        """
        pet = self._pet_with_daily_walk()
        pet.complete_task("Walk", on=WEDNESDAY)
        assert pet.remove_task("Walk")
        assert pet.tasks_due_on(THURSDAY) == []
        assert [t.completed for t in pet.tasks] == [True]

    def test_remove_task_falls_back_to_a_completed_record(self):
        """With nothing open left, the record is what removing must mean."""
        pet = self._pet_with_daily_walk()
        pet.tasks[0].mark_complete(on=WEDNESDAY)
        assert pet.remove_task("Walk")
        assert pet.tasks == []

    def test_remove_missing_title_is_still_false(self):
        assert not self._pet_with_daily_walk().remove_task("Nope")

    def test_reset_task_undoes_the_whole_completion(self):
        """Clearing the flag alone left two live copies of the same task."""
        pet = self._pet_with_daily_walk()
        pet.complete_task("Walk", on=WEDNESDAY)
        assert pet.reset_task("Walk")
        assert len(pet.tasks) == 1
        assert [t.title for t in pet.tasks_due_on(WEDNESDAY)] == ["Walk"]
        assert [t.title for t in pet.tasks_due_on(THURSDAY)] == ["Walk"]

    def test_reset_task_does_not_double_book_the_day(self, prefs):
        pet = self._pet_with_daily_walk()
        pet.complete_task("Walk", on=WEDNESDAY)
        pet.reset_task("Walk")
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        assert len(plan.scheduled) == 1
        assert not plan.is_double_booked()

    def test_reset_task_without_a_completion_is_false(self):
        assert not self._pet_with_daily_walk().reset_task("Walk")

    def test_reset_task_handles_a_one_off(self):
        """A ONCE task files no successor, so there is nothing to withdraw."""
        pet = Pet("Mochi", "cat")
        pet.add_task(
            CareTask("Vet", 45, recurrence=Recurrence.ONCE, anchor_date=WEDNESDAY)
        )
        pet.complete_task("Vet", on=WEDNESDAY)
        assert pet.reset_task("Vet")
        assert len(pet.tasks) == 1
        assert [t.title for t in pet.tasks_due_on(WEDNESDAY)] == ["Vet"]

    def test_completing_retroactively_puts_the_task_back_on_today(self):
        """Surprising but right: catching up on yesterday lands on today.

        Asserted deliberately. An owner logging yesterday's walk this morning
        should still see today's walk to do, not skip a day.
        """
        pet = self._pet_with_daily_walk()
        successor = pet.complete_task("Walk", on=WEDNESDAY - timedelta(days=1))
        assert successor.anchor_date == WEDNESDAY
        assert [t.title for t in pet.tasks_due_on(WEDNESDAY)] == ["Walk"]

    def test_completing_ahead_of_time_skips_to_the_day_after(self):
        pet = self._pet_with_daily_walk()
        successor = pet.complete_task("Walk", on=THURSDAY)
        assert successor.anchor_date == THURSDAY + timedelta(days=1)
        assert pet.tasks_due_on(WEDNESDAY) == []

    def test_ten_days_of_rollover_stay_exactly_one_task_a_day(self, prefs):
        """The repeating case end to end: no duplicates, no gaps, no clashes."""
        pet = self._pet_with_daily_walk()
        owner = Owner("J", [pet], prefs)
        for offset in range(10):
            day = WEDNESDAY + timedelta(days=offset)
            assert [t.title for t in pet.tasks_due_on(day)] == ["Walk"], f"day {offset}"
            plan = Scheduler(prefs).build_plan(owner, day)
            assert len(plan.scheduled) == 1
            assert not plan.is_double_booked()
            pet.complete_task("Walk", on=day)
        assert len(pet.tasks) == 11  # ten records, plus the live successor


# ---------------------------------------------------------------------------
# Ordering -- the sort has to be total and stable
# ---------------------------------------------------------------------------


class TestOrderingEdgeCases:
    def test_a_long_important_task_still_outranks_a_short_trivial_one(self):
        """Duration must only break ties, never cross a priority band.

        The bands were 1000 apart while a task may legally run for a whole day,
        so a five-minute LOW task outranked a 1100-minute MEDIUM one.
        """
        long_medium = CareTask("Kennel day", 1100, Priority.MEDIUM)
        short_low = CareTask("Treat", 5, Priority.LOW)
        assert long_medium.weight() > short_low.weight()
        assert PriorityFirstStrategy().order([short_low, long_medium])[0] is long_medium

    def test_required_outranks_everything_even_at_full_length(self):
        required = CareTask("Meds", MINUTES_PER_DAY, Priority.LOW, is_required=True)
        urgent = CareTask("Walk", 1, Priority.HIGH)
        assert required.weight() > urgent.weight()

    @pytest.mark.parametrize(
        "strategy", [PriorityFirstStrategy(), QuickWinsStrategy()]
    )
    def test_ordering_an_empty_list(self, strategy):
        assert strategy.order([]) == []

    @pytest.mark.parametrize(
        "strategy", [PriorityFirstStrategy(), QuickWinsStrategy()]
    )
    def test_indistinguishable_tasks_keep_their_input_order(self, strategy):
        """Both sorts must be stable, or the plan reshuffles between runs."""
        tasks = [CareTask(name, 20, Priority.MEDIUM) for name in ("A", "B", "C")]
        assert [t.title for t in strategy.order(tasks)] == ["A", "B", "C"]

    def test_the_plan_is_deterministic(self, prefs):
        """Same household, same day, same plan -- twice."""

        def household():
            ana, bo = Pet("Ana", "dog"), Pet("Bo", "cat")
            ana.add_task(CareTask("Walk", 20, Priority.MEDIUM))
            ana.add_task(CareTask("Feed", 20, Priority.MEDIUM))
            bo.add_task(CareTask("Brush", 20, Priority.MEDIUM))
            return Owner("J", [ana, bo], prefs)

        first = Scheduler(prefs).build_plan(household(), WEDNESDAY)
        second = Scheduler(prefs).build_plan(household(), WEDNESDAY)
        assert first.to_rows() == second.to_rows()

    def test_ties_resolve_by_pet_order_then_task_order(self, prefs):
        """With nothing to choose between them, the owner's own order wins."""
        ana, bo = Pet("Ana", "dog"), Pet("Bo", "cat")
        ana.add_task(CareTask("A1", 20, Priority.MEDIUM))
        ana.add_task(CareTask("A2", 20, Priority.MEDIUM))
        bo.add_task(CareTask("B1", 20, Priority.MEDIUM))
        plan = Scheduler(prefs).build_plan(Owner("J", [ana, bo], prefs), WEDNESDAY)
        assert [s.task.title for s in plan.scheduled] == ["A1", "A2", "B1"]

    def test_a_strategy_inventing_a_task_does_not_reach_the_plan(self, prefs):
        """The extension point's contract: order the tasks, do not add any.

        A smuggled task has no pet, and guessing one would put work on the
        wrong animal. Failing is the honest outcome.
        """

        class Rogue(SchedulingStrategy):
            name = "rogue"

            def order(self, tasks):
                return list(tasks) + [CareTask("Smuggled", 10)]

            def describe(self):
                return "adds a task nobody asked for"

        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Walk", 30))
        with pytest.raises(KeyError):
            Scheduler(prefs, Rogue()).build_plan(Owner("J", [pet], prefs), WEDNESDAY)


# ---------------------------------------------------------------------------
# Placement -- the edges of the day and the budget
# ---------------------------------------------------------------------------


class TestSchedulerEdgeCases:
    def test_a_pinned_start_is_honored_over_the_break(self, prefs):
        """`break_minutes` applies only where the scheduler picks the time.

        A task that names 08:30 gets 08:30, back to back with the one before
        it. Stated because `test_leaves_a_break_between_tasks` reads like an
        unconditional rule and is not one.
        """
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Walk", 30))
        pet.add_task(CareTask("Meds", 15, earliest_start=time(8, 30)))
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        assert [(s.task.title, s.slot.start) for s in plan.scheduled] == [
            ("Walk", time(8, 0)),
            ("Meds", time(8, 30)),
        ]
        assert not plan.is_double_booked()

    def test_the_budget_does_not_know_about_busy_time(self):
        """`max_daily_minutes` and `available_minutes()` are different numbers.

        The scheduler spends against the budget alone, so a day that is mostly
        busy loses tasks to missing slots, not to a blown budget. Pinned down
        because the two are easy to confuse -- `available_minutes()` exists and
        the scheduler never consults it.
        """
        prefs = OwnerPreferences(
            day_start=time(8, 0),
            day_end=time(20, 0),
            max_daily_minutes=180,
            busy_windows=[TimeWindow(time(9, 0), time(20, 0))],
        )
        assert prefs.available_minutes() == 60
        pet = Pet("Mochi", "cat")
        for i in range(4):
            pet.add_task(CareTask(f"Walk {i}", 20))
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        assert plan.total_minutes() < prefs.max_daily_minutes
        assert plan.skipped
        assert all("no free slot" in item.reason for item in plan.skipped)

    def test_a_zero_budget_schedules_nothing(self):
        prefs = OwnerPreferences(max_daily_minutes=0)
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Walk", 30))
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        assert not plan.scheduled
        assert "daily limit" in plan.skipped[0].reason

    def test_no_break_packs_tasks_back_to_back(self):
        prefs = OwnerPreferences(
            day_start=time(8, 0), day_end=time(20, 0), break_minutes=0
        )
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Walk", 30))
        pet.add_task(CareTask("Feed", 30))
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        assert [s.slot.start for s in plan.scheduled] == [time(8, 0), time(8, 30)]

    def test_a_break_longer_than_the_day_still_places_the_first_task(self):
        prefs = OwnerPreferences(
            day_start=time(8, 0), day_end=time(9, 0), break_minutes=600
        )
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Walk", 30))
        pet.add_task(CareTask("Feed", 10))
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        assert len(plan.scheduled) == 1
        assert len(plan.skipped) == 1

    def test_earliest_start_before_the_day_opens_waits_for_the_day(self, prefs):
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Walk", 30, earliest_start=time(6, 0)))
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        assert plan.scheduled[0].slot.start == time(8, 0)

    def test_earliest_start_after_the_day_closes_is_skipped(self, prefs):
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Walk", 30, earliest_start=time(22, 0)))
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        assert not plan.scheduled
        assert plan.skipped

    def test_a_busy_window_ending_at_day_start_costs_nothing(self):
        prefs = OwnerPreferences(
            day_start=time(8, 0),
            day_end=time(20, 0),
            busy_windows=[TimeWindow(time(6, 0), time(8, 0))],
        )
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Walk", 30))
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        assert plan.scheduled[0].slot.start == time(8, 0)

    def test_a_task_cannot_run_past_midnight(self):
        """The day is a single date, so nothing may spill over its end."""
        prefs = OwnerPreferences(
            day_start=time(23, 30), day_end=time(23, 59), max_daily_minutes=240
        )
        pet = Pet("Mochi", "cat")
        pet.add_task(CareTask("Walk", 45))
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        assert not plan.scheduled
        assert plan.skipped

    def test_an_aliased_task_is_refused(self, prefs):
        """One CareTask under two pets used to be credited to the last pet.

        Both slots were rendered under Bo and Ana's day came back empty. The
        instance is shared, so completing it for one pet would have completed
        it for the other as well.
        """
        shared = CareTask("Brush", 20)
        ana, bo = Pet("Ana", "dog"), Pet("Bo", "cat")
        ana.add_task(shared)
        bo.add_task(shared)
        with pytest.raises(ValueError, match="shared by Ana and Bo"):
            Scheduler(prefs).build_plan(Owner("J", [ana, bo], prefs), WEDNESDAY)

    def test_two_equal_but_separate_tasks_are_fine(self, prefs):
        """Identical fields are not identity: each pet gets its own brushing."""
        ana, bo = Pet("Ana", "dog"), Pet("Bo", "cat")
        ana.add_task(CareTask("Brush", 20))
        bo.add_task(CareTask("Brush", 20))
        plan = Scheduler(prefs).build_plan(Owner("J", [ana, bo], prefs), WEDNESDAY)
        assert [s.pet.name for s in plan.scheduled] == ["Ana", "Bo"]
        assert not plan.is_double_booked()


# ---------------------------------------------------------------------------
# Conflict attribution -- who is named the winner, and when nobody is
# ---------------------------------------------------------------------------


class TestConflictAttribution:
    @staticmethod
    def _plan(prefs, entries):
        """Build a plan from (pet_name, task) pairs, one Pet per distinct name."""
        pets: dict[str, Pet] = {}
        for pet_name, task in entries:
            pets.setdefault(pet_name, Pet(pet_name, "dog")).add_task(task)
        owner = Owner("Jordan", list(pets.values()), prefs)
        return Scheduler(prefs).build_plan(owner, WEDNESDAY)

    @staticmethod
    def _both_want_half_seven():
        return [
            ("Ana", CareTask("Walk", 30, earliest_start=time(7, 30))),
            ("Bo", CareTask("Feed", 30, earliest_start=time(7, 30))),
        ]

    def test_neither_task_kept_a_time_the_day_never_offered(self, prefs):
        """Both asked for 07:30 on a day that opens at 08:00.

        The report used to name one of them the winner and say the other had
        moved, which pointed the owner at the wrong task: the one it called the
        loser had actually been given the earlier slot.
        """
        plan = self._plan(prefs, self._both_want_half_seven())
        assert len(plan.conflicts) == 1
        clash = plan.conflicts[0]
        assert clash.both_displaced
        assert "neither kept it" in clash.outcome
        assert "neither kept it" in clash.label()

    def test_a_displaced_pair_is_reported_earliest_first(self, prefs):
        plan = self._plan(prefs, self._both_want_half_seven())
        clash = plan.conflicts[0]
        first = plan._placement_of(clash.first_task)
        second = plan._placement_of(clash.second_task)
        assert first.slot.start < second.slot.start

    def test_a_kept_time_is_still_reported_as_a_win(self, prefs):
        """The ordinary case must not regress into the both-displaced shape."""
        plan = self._plan(
            prefs,
            [
                ("Ana", CareTask("Walk", 30, earliest_start=time(9, 0))),
                ("Bo", CareTask("Feed", 10, earliest_start=time(9, 0))),
            ],
        )
        clash = plan.conflicts[0]
        assert not clash.both_displaced
        assert plan._placement_of(clash.first_task).slot == clash.wanted
        assert "moved to" in clash.outcome

    def test_both_tasks_skipped_over_the_same_time(self):
        """Nothing was placed, so nothing can be called the winner."""
        prefs = OwnerPreferences(
            day_start=time(8, 0), day_end=time(20, 0), max_daily_minutes=0
        )
        plan = self._plan(
            prefs,
            [
                ("Ana", CareTask("Walk", 30, earliest_start=time(9, 0))),
                ("Bo", CareTask("Feed", 30, earliest_start=time(9, 0))),
            ],
        )
        assert not plan.scheduled
        clash = plan.conflicts[0]
        assert clash.both_displaced
        assert "not scheduled" in clash.outcome

    def test_a_request_running_past_midnight_is_ignored_not_raised(self):
        """`wanted_window()` gives up on it, so detection has to as well."""
        prefs = OwnerPreferences(
            day_start=time(8, 0), day_end=time(23, 59), max_daily_minutes=240
        )
        plan = self._plan(
            prefs,
            [
                ("Ana", CareTask("Walk", 30, earliest_start=time(23, 50))),
                ("Bo", CareTask("Feed", 30, earliest_start=time(23, 50))),
            ],
        )
        assert plan.conflicts == []
        assert plan.detect_conflicts() == []

    def test_no_shape_of_clash_double_books(self, prefs):
        """The output invariant, across every clash this class builds."""
        shapes = [
            self._both_want_half_seven(),
            [
                ("Ana", CareTask("Walk", 30, earliest_start=time(9, 0))),
                ("Bo", CareTask("Feed", 10, earliest_start=time(9, 0))),
                ("Cy", CareTask("Play", 15, earliest_start=time(9, 0))),
            ],
            [
                (
                    "Ana",
                    CareTask(
                        "Walk",
                        30,
                        earliest_start=time(8, 0),
                        latest_finish=time(8, 30),
                    ),
                ),
                (
                    "Bo",
                    CareTask(
                        "Meds",
                        30,
                        earliest_start=time(8, 0),
                        latest_finish=time(8, 30),
                    ),
                ),
            ],
        ]
        for entries in shapes:
            plan = self._plan(prefs, entries)
            assert not plan.is_double_booked()


# ---------------------------------------------------------------------------
# Editing one row -- identity, not title
# ---------------------------------------------------------------------------


class TestIdentityBasedEditing:
    """Acting on the row that was clicked, where a title picks out two rows."""

    @staticmethod
    def _rolled_over_pet():
        """A pet whose single task has been completed once, leaving two rows.

        This is the ordinary state of any recurring task after one day's use,
        and the state in which a title stops identifying anything.
        """
        pet = Pet("Mochi", "cat")
        pet.add_task(
            CareTask("Walk", 30, recurrence=Recurrence.DAILY, anchor_date=WEDNESDAY)
        )
        pet.complete_task("Walk", on=WEDNESDAY)
        return pet

    def test_remove_instance_takes_the_row_it_was_given(self):
        """Deleting the completed record must leave tomorrow's walk alone."""
        pet = self._rolled_over_pet()
        record, successor = pet.tasks
        assert pet.remove_instance(record)
        assert [t is successor for t in pet.tasks] == [True]
        assert [t.title for t in pet.tasks_due_on(THURSDAY)] == ["Walk"]

    def test_remove_instance_can_take_the_successor_instead(self):
        """And deleting the live one must stop the task coming back."""
        pet = self._rolled_over_pet()
        record, successor = pet.tasks
        assert pet.remove_instance(successor)
        assert [t is record for t in pet.tasks] == [True]
        assert pet.tasks_due_on(THURSDAY) == []

    def test_remove_instance_ignores_an_equal_but_foreign_task(self):
        """Equal fields are not identity -- CareTask compares value by value."""
        pet = self._rolled_over_pet()
        twin = CareTask(
            "Walk", 30, recurrence=Recurrence.DAILY, anchor_date=WEDNESDAY
        )
        assert not pet.remove_instance(twin)
        assert len(pet.tasks) == 2

    def test_undo_completion_restores_the_record_and_drops_the_successor(self):
        pet = self._rolled_over_pet()
        record, successor = pet.tasks
        assert pet.undo_completion(record)
        assert not record.completed
        assert record.completed_on is None
        assert not any(t is successor for t in pet.tasks)
        assert [t.title for t in pet.tasks_due_on(WEDNESDAY)] == ["Walk"]

    def test_undo_completion_refuses_an_open_task(self):
        """Undo only means anything on a row that says "done"."""
        pet = self._rolled_over_pet()
        assert not pet.undo_completion(pet.tasks[1])
        assert len(pet.tasks) == 2

    def test_undo_completion_refuses_a_foreign_task(self):
        pet = self._rolled_over_pet()
        stranger = CareTask("Walk", 30)
        stranger.mark_complete(on=WEDNESDAY)
        assert not pet.undo_completion(stranger)
        assert len(pet.tasks) == 2

    def test_undo_then_replan_shows_the_task_once(self, prefs):
        """The point of withdrawing the successor: no duplicate on the day."""
        pet = self._rolled_over_pet()
        pet.undo_completion(pet.tasks[0])
        plan = Scheduler(prefs).build_plan(Owner("J", [pet], prefs), WEDNESDAY)
        assert len(plan.scheduled) == 1
        assert not plan.is_double_booked()

    def test_the_title_wrappers_still_behave(self):
        """`remove_task` and `reset_task` delegate but keep their contracts."""
        pet = self._rolled_over_pet()
        record = pet.tasks[0]
        assert pet.remove_task("Walk")  # prefers the live instance
        assert [t is record for t in pet.tasks] == [True]

        pet = self._rolled_over_pet()
        assert pet.reset_task("Walk")  # most recent completion, successor gone
        assert len(pet.tasks) == 1
        assert not pet.tasks[0].completed


# ---------------------------------------------------------------------------
# Sorting a finished plan into a timeline
# ---------------------------------------------------------------------------


class TestSortByTime:
    @staticmethod
    def _slots(*hours):
        """One half-hour ScheduledTask per hour given, in the order given."""
        pet = Pet("Mochi", "cat")
        return [
            ScheduledTask(
                CareTask(f"{hour:02d}00", 30),
                pet,
                TimeWindow(time(hour, 0), time(hour, 30)),
            )
            for hour in hours
        ]

    def test_a_reversed_list_comes_back_in_order(self):
        items = self._slots(19, 12, 8)
        ordered = Scheduler.sort_by_time(items)
        assert [s.slot.start.hour for s in ordered] == [8, 12, 19]

    def test_sorting_does_not_mutate_the_input(self):
        """`sorted` builds a new list, so the caller's order survives."""
        items = self._slots(19, 12, 8)
        Scheduler.sort_by_time(items)
        assert [s.slot.start.hour for s in items] == [19, 12, 8]

    def test_it_needs_no_scheduler_instance(self):
        """A staticmethod: ordering a list wants no preferences or strategy."""
        assert Scheduler.sort_by_time([]) == []

    def test_minutes_inside_an_hour_sort_correctly(self):
        """The key is a real `time`, which is why this needs no thought.

        Comparing "08:05" against "08:45" as text happens to give the same
        answer, but only because clock strings are zero padded to a fixed
        width; the moment anything formats an hour without its leading zero,
        "8:45" sorts before "08:05". Sorting the time object cannot go wrong
        that way, so the module never puts a clock into a string to compare it.
        """
        pet = Pet("Mochi", "cat")
        late = ScheduledTask(
            CareTask("late", 10), pet, TimeWindow(time(8, 45), time(8, 55))
        )
        early = ScheduledTask(
            CareTask("early", 10), pet, TimeWindow(time(8, 5), time(8, 15))
        )
        ordered = Scheduler.sort_by_time([late, early])
        assert [s.task.title for s in ordered] == ["early", "late"]


# ---------------------------------------------------------------------------
# Filtering the household
# ---------------------------------------------------------------------------


class TestFindTasks:
    @staticmethod
    def _household():
        """Two pets, one of which has a completed task and its successor."""
        biscuit, mochi = Pet("Biscuit", "dog"), Pet("Mochi", "cat")
        biscuit.add_task(CareTask("Walk", 30))
        biscuit.add_task(CareTask("Feed", 10))
        mochi.add_task(CareTask("Brush", 15))
        mochi.complete_task("Brush", on=WEDNESDAY)
        return Owner("Jordan", [biscuit, mochi])

    def test_no_filters_returns_the_whole_household(self):
        owner = self._household()
        assert len(owner.find_tasks()) == len(owner.all_tasks())

    def test_each_task_is_paired_with_the_pet_that_owns_it(self):
        """The pairing is the point: a bare CareTask cannot say whose it is."""
        owner = self._household()
        for pet, task in owner.find_tasks():
            assert any(owned is task for owned in pet.tasks)

    def test_filter_by_pet_name_ignores_case(self):
        """Matched through `get_pet`, so a typed filter behaves like a lookup."""
        found = self._household().find_tasks(pet_name="mOcHi")
        assert found
        assert {pet.name for pet, _ in found} == {"Mochi"}

    def test_an_unknown_pet_name_returns_nothing(self):
        """A filter that silently stops filtering is worse than an empty list."""
        assert self._household().find_tasks(pet_name="Rex") == []

    def test_filter_by_completion(self):
        owner = self._household()
        done = owner.find_tasks(completed=True)
        assert [task.title for _, task in done] == ["Brush"]
        assert all(task.completed for _, task in done)

    def test_the_rolled_over_pair_splits_across_the_two_states(self):
        """One title, two rows: the record is done, the successor is not."""
        owner = self._household()
        done = owner.find_tasks(pet_name="Mochi", completed=True)
        todo = owner.find_tasks(pet_name="Mochi", completed=False)
        assert [t.title for _, t in done] == ["Brush"]
        assert [t.title for _, t in todo] == ["Brush"]
        assert done[0][1] is not todo[0][1]

    def test_filters_combine(self):
        owner = self._household()
        found = owner.find_tasks(pet_name="Biscuit", completed=False)
        assert [task.title for _, task in found] == ["Walk", "Feed"]

    def test_completed_false_is_not_the_same_as_unfiltered(self):
        """None means "do not care"; False means "not done yet"."""
        owner = self._household()
        assert len(owner.find_tasks(completed=False)) < len(owner.find_tasks())

    def test_an_empty_household_filters_to_nothing(self):
        assert Owner("Jordan").find_tasks(completed=False) == []
