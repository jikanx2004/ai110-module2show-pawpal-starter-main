"""Tests for the PawPal+ logic layer.

Organised by the layer under test, bottom-up: time math, enums, tasks,
containers, constraints, then the scheduler. The scheduler tests carry the
weight -- they cover the behaviors that would actually ruin someone's day if
they broke (double-booking, ignored deadlines, blown time budgets).

Run with:  pytest
"""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest

from main import build_demo_household
from pawpal_system import (
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
