"""PawPal+ terminal demo.

A testing ground for the logic layer in `pawpal_system.py`, independent of
Streamlit. Run it with:

    python main.py

It builds a two-pet household, schedules today, and prints the plan.
"""

from __future__ import annotations

from datetime import date, time, timedelta

from pawpal_system import (
    CareTask,
    Owner,
    OwnerPreferences,
    Pet,
    Priority,
    PriorityFirstStrategy,
    QuickWinsStrategy,
    Recurrence,
    Scheduler,
    TaskCategory,
    TimeWindow,
    DailyPlan,
)


def build_demo_household(today: date) -> Owner:
    """A busy owner with two pets and a day that cannot hold everything.

    Returned as a factory rather than built inline so the pytest suite can
    reuse the same fixture instead of rebuilding it.
    """
    preferences = OwnerPreferences(
        day_start=time(7, 0),
        day_end=time(21, 0),
        max_daily_minutes=150,
        busy_windows=[TimeWindow(time(9, 0), time(17, 0))],  # at work
        break_minutes=10,
    )

    biscuit = Pet(
        name="Biscuit",
        species="dog",
        breed="Golden Retriever",
        age_years=4.0,
        energy_level=5,
        notes=["Pulls on the leash", "Loves the river trail"],
    )
    biscuit.add_task(
        CareTask(
            "Morning walk",
            30,
            Priority.HIGH,
            TaskCategory.EXERCISE,
            # Both this and Mochi's ear medication ask for 07:30 on purpose:
            # one owner cannot do both, so the demo has a real conflict to
            # report rather than a contrived one.
            earliest_start=time(7, 30),
            latest_finish=time(9, 0),
        )
    )
    biscuit.add_task(
        CareTask(
            "Breakfast",
            10,
            Priority.HIGH,
            TaskCategory.FEEDING,
            latest_finish=time(8, 30),
        )
    )
    biscuit.add_task(
        CareTask(
            "Evening hike",
            45,
            Priority.MEDIUM,
            TaskCategory.EXERCISE,
            earliest_start=time(17, 30),
        )
    )

    mochi = Pet(
        name="Mochi",
        species="cat",
        breed="Ragdoll",
        age_years=9.0,
        energy_level=2,
        notes=["Ear infection - finish the course"],
    )
    mochi.add_task(
        CareTask(
            "Ear medication",
            5,
            Priority.MEDIUM,
            TaskCategory.MEDICATION,
            earliest_start=time(7, 30),  # collides with Biscuit's walk
            latest_finish=time(20, 0),
            is_required=True,
        )
    )
    mochi.add_task(CareTask("Playtime", 20, Priority.LOW, TaskCategory.ENRICHMENT))
    # Anchored to today on purpose: without an anchor_date a weekly task never
    # comes due, so the demo would quietly skip the recurrence path entirely.
    mochi.add_task(
        CareTask(
            "Brush coat",
            15,
            Priority.LOW,
            TaskCategory.GROOMING,
            recurrence=Recurrence.WEEKLY,
            anchor_date=today,
        )
    )

    return Owner(name="Jordan", pets=[biscuit, mochi], preferences=preferences)


def print_schedule(plan: DailyPlan) -> None:
    """Print the plan as 'Today's Schedule', plus a per-pet breakdown."""
    print("=" * 62)
    print("TODAY'S SCHEDULE".center(62))
    print("=" * 62)
    print(plan.explain())

    print("\n" + "-" * 62)
    print("Per pet:")
    for pet in plan.pets():
        view = plan.for_pet(pet)
        print(
            f"  {pet.name} ({pet.species}): "
            f"{len(view.scheduled)} scheduled, "
            f"{len(view.skipped)} skipped, "
            f"{view.total_minutes()} min"
        )

    print(
        "\nDouble-booked slots: "
        f"{'FOUND - scheduler bug' if plan.is_double_booked() else 'none'}"
    )
    print(
        f"Owner has {plan.owner.preferences.available_minutes()} usable min today; "
        f"{plan.total_minutes()} min booked."
    )
    print_conflicts(plan)


def print_conflicts(plan: DailyPlan) -> None:
    """Print the requested-time clashes as warnings, not errors.

    Kept separate from the timeline because it answers a different question.
    The schedule says what the scheduler did; this says what the owner asked
    for and could not have. The plan above is complete and valid either way --
    this only explains one of the choices it made silently.
    """
    print("\n" + "-" * 62)
    if not plan.conflicts:
        print("Requested-time conflicts: none")
        return
    print(f"WARNING: {len(plan.conflicts)} requested-time conflict(s)")
    for conflict in plan.conflicts:
        print(f"  ! {conflict.label()}")
    print("  Nothing crashed: the loser was moved or skipped, and said so.")


def compare_strategies(owner: Owner, today: date) -> None:
    """Show the same tasks ordered two ways, so the tradeoff is visible."""
    print("\n" + "-" * 62)
    print("Same tasks, two orderings:")
    for strategy in (PriorityFirstStrategy(), QuickWinsStrategy()):
        plan = Scheduler(owner.preferences, strategy).build_plan(owner, today)
        order = " -> ".join(s.task.title for s in plan.scheduled)
        print(f"\n  [{strategy.name}] {strategy.describe()}")
        print(f"    plans: {order or '(nothing fit)'}")
        print(f"    {len(plan.scheduled)} tasks / {plan.total_minutes()} min")
        for conflict in plan.conflicts:
            print(f"    ! {conflict.outcome}")


def demo_rollover(owner: Owner, today: date) -> None:
    """Complete two recurring tasks and show later days pick them back up.

    The point is that the owner never re-enters a repeating task: finishing it
    is what books the next one.
    """
    print()
    print("-" * 62)
    print("Marking today's recurring tasks complete:")
    tomorrow = today + timedelta(days=1)
    next_week = today + timedelta(days=7)

    for pet_name, title in (("Biscuit", "Morning walk"), ("Mochi", "Brush coat")):
        pet = owner.get_pet(pet_name)
        successor = pet.complete_task(title, on=today)
        if successor is None:
            print(f"  {pet_name}: {title} done (one-off, nothing to reschedule)")
            continue
        print(
            f"  {pet_name}: {title} done -> next instance due "
            f"{successor.anchor_date:%a %Y-%m-%d} ({successor.recurrence.value})"
        )

    for label, day in (("Tomorrow", tomorrow), ("Next week", next_week)):
        plan = Scheduler(owner.preferences).build_plan(owner, day)
        titles = ", ".join(s.task.title for s in plan.scheduled)
        print(f"  {label} ({day:%a %Y-%m-%d}): {titles or '(nothing fit)'}")

    today_again = Scheduler(owner.preferences).build_plan(owner, today)
    left = ", ".join(s.task.title for s in today_again.scheduled)
    print(f"  Today, re-planned: {left or '(nothing left)'}")
    print("  (completed tasks are gone; their successors are not due yet)")


def main() -> None:
    today = date.today()
    owner = build_demo_household(today)

    plan = Scheduler(owner.preferences).build_plan(owner, today)
    print_schedule(plan)
    compare_strategies(owner, today)
    demo_rollover(owner, today)


if __name__ == "__main__":
    main()
