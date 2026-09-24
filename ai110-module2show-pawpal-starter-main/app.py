"""PawPal+ Streamlit UI.

A thin view over `pawpal_system.py`. Every decision -- what gets scheduled,
when, and why -- belongs to the logic layer; this file only collects input,
calls `Scheduler.build_plan`, and renders the result. Keeping it that way is
what lets `main.py` and the pytest suite exercise the same behavior without
Streamlit in the loop.

Run with:  streamlit run app.py

The household lives in `st.session_state` because Streamlit re-executes this
whole script on every interaction; anything built at module level would be
rebuilt from scratch and the owner's tasks would vanish on each click.
"""

from __future__ import annotations

from datetime import date, time

import streamlit as st

from main import build_demo_household
from pawpal_system import (
    CareTask,
    Owner,
    Pet,
    Priority,
    PriorityFirstStrategy,
    QuickWinsStrategy,
    Recurrence,
    Scheduler,
    TaskCategory,
    TimeWindow,
)

st.set_page_config(page_title="PawPal+", page_icon="🐾", layout="centered")

STRATEGIES = {s.name: s for s in (PriorityFirstStrategy(), QuickWinsStrategy())}


def household() -> Owner:
    """The one Owner this session is editing, seeded with the demo household.

    Starting from `build_demo_household` rather than an empty owner means the
    app has something to schedule on first load, so the plan and the recurrence
    rollover are visible before the user has typed anything.
    """
    if "owner" not in st.session_state:
        st.session_state.owner = build_demo_household(date.today())
    return st.session_state.owner


def sidebar_preferences(owner: Owner) -> tuple[date, str]:
    """Render the owner's constraints and write them back onto `owner`.

    The widgets edit `owner.preferences` in place rather than building a fresh
    OwnerPreferences, so the busy-window list added here survives a rerun.
    Returns the day being planned and the chosen strategy name.
    """
    prefs = owner.preferences
    with st.sidebar:
        st.header("Owner")
        owner.name = st.text_input("Name", value=owner.name)
        plan_day = st.date_input("Planning for", value=date.today())

        st.header("Day")
        prefs.day_start = st.time_input("Awake from", value=prefs.day_start)
        prefs.day_end = st.time_input("Awake until", value=prefs.day_end)
        prefs.max_daily_minutes = st.number_input(
            "Max care minutes per day",
            min_value=0,
            max_value=1440,
            value=prefs.max_daily_minutes,
            step=15,
            help="The scheduler will not book past this, even if the day is free.",
        )
        prefs.break_minutes = st.number_input(
            "Break between tasks (min)",
            min_value=0,
            max_value=120,
            value=prefs.break_minutes,
            step=5,
        )

        st.header("Busy windows")
        st.caption("Work, commute, anything the owner cannot plan around.")
        for window in prefs.busy_windows:
            st.write(f"- {window.start:%H:%M} to {window.end:%H:%M}")
        busy_start = st.time_input("Busy from", value=time(9, 0), key="busy_start")
        busy_end = st.time_input("Busy until", value=time(17, 0), key="busy_end")
        if st.button("Add busy window", width="stretch"):
            try:
                prefs.busy_windows.append(TimeWindow(busy_start, busy_end))
            except ValueError as err:
                st.error(str(err))
        if prefs.busy_windows and st.button(
            "Clear busy windows", width="stretch"
        ):
            prefs.busy_windows.clear()

        st.header("Strategy")
        strategy_name = st.radio(
            "Ordering",
            list(STRATEGIES),
            format_func=lambda n: f"{n} - {STRATEGIES[n].describe()}",
        )

        st.divider()
        if st.button("Reset to demo household", width="stretch"):
            st.session_state.owner = build_demo_household(date.today())
            st.rerun()

    return plan_day, strategy_name


def render_plan(owner: Owner, plan_day: date, strategy_name: str) -> None:
    """Build and display the plan: timeline, skipped tasks, and the explanation."""
    plan = Scheduler(owner.preferences, STRATEGIES[strategy_name]).build_plan(
        owner, plan_day
    )

    cols = st.columns(4)
    cols[0].metric("Scheduled", len(plan.scheduled))
    cols[1].metric("Booked", f"{plan.total_minutes()} min")
    cols[2].metric("Usable today", f"{owner.preferences.available_minutes()} min")
    cols[3].metric("Conflicts", len(plan.conflicts))

    if plan.scheduled:
        st.dataframe(plan.to_rows(), width="stretch", hide_index=True)
    else:
        st.info("Nothing fits today. Loosen the day, the budget, or the busy windows.")

    if plan.is_double_booked():
        st.error(
            "Two tasks were placed on top of each other. That is a scheduler "
            "bug, not an input problem -- it is not the same thing as the time "
            "conflicts below."
        )

    if plan.conflicts:
        st.subheader("Time conflicts")
        st.caption(
            "Two tasks asked for the same time. Nothing failed -- the plan gave "
            "the slot to one of them. You decide whether it chose right."
        )
        for conflict in plan.conflicts:
            if conflict.unresolvable:
                st.error(conflict.label())
            else:
                st.warning(conflict.label())

    if plan.skipped:
        st.subheader("Could not fit")
        for item in plan.skipped:
            st.warning(item.label())

    with st.expander("Why this plan"):
        st.text(plan.explain())

    if plan.scheduled:
        st.subheader("Mark done")
        st.caption(
            "Completing a daily or weekly task books its next instance "
            "automatically -- nothing to re-enter."
        )
        for item in plan.scheduled:
            cols = st.columns([4, 1])
            cols[0].write(item.label())
            done = cols[1].button(
                "Done",
                key=f"done-{item.pet.name}-{item.task.title}",
                width="stretch",
            )
            if done:
                successor = item.pet.complete_task(item.task.title, on=plan_day)
                if successor is None:
                    st.session_state.flash = f"{item.task.title}: done."
                else:
                    st.session_state.flash = (
                        f"{item.task.title}: done. Next one due "
                        f"{successor.anchor_date:%a %d %b}."
                    )
                st.rerun()


def task_status(task: CareTask, plan_day: date) -> str:
    """One-word state for a task relative to the day being planned.

    Split out because the distinction that matters -- "done" versus "upcoming"
    versus "not due" -- is exactly what makes recurrence rollover visible, and
    burying it in an f-string inside a loop made it unreadable.
    """
    if task.completed:
        return f"done {task.completed_on:%d %b}"
    if task.is_due_on(plan_day):
        return "due"
    if task.anchor_date is not None and task.anchor_date > plan_day:
        return f"upcoming {task.anchor_date:%a %d %b}"
    return "not due"


def render_tasks(owner: Owner, plan_day: date) -> None:
    """List every pet's tasks with their status, so rollover is visible.

    A completed task stays in the list as the day's record and its successor
    appears beside it, due on a later date. Showing both is the clearest way to
    see that finishing a recurring task rescheduled it rather than deleting it.
    """
    for pet in owner.pets:
        with st.expander(f"{pet.name} ({pet.species}) - {len(pet.tasks)} tasks"):
            if not pet.tasks:
                st.caption("No tasks yet.")
            for task in pet.tasks:
                st.write(
                    f"- {task.title} ({task.duration_minutes} min, "
                    f"{task.priority.value}, {task.recurrence.value}) - "
                    f"**{task_status(task, plan_day)}**"
                )


def add_pet_form(owner: Owner) -> None:
    """Form for adding an animal to the household."""
    with st.form("add_pet", clear_on_submit=True):
        cols = st.columns([2, 1, 1])
        name = cols[0].text_input("Pet name", value="")
        species = cols[1].selectbox("Species", ["dog", "cat", "other"])
        age = cols[2].number_input(
            "Age (years)", min_value=0.0, max_value=40.0, step=0.5
        )
        if st.form_submit_button("Add pet") and name.strip():
            owner.add_pet(Pet(name=name.strip(), species=species, age_years=age))
            st.rerun()


def add_task_form(owner: Owner) -> None:
    """Form for attaching a care task to one of the pets.

    Priority, TaskCategory and Recurrence take their selectbox strings
    directly -- the enum values were chosen to match the UI, so there is no
    translation table between this file and the logic layer.
    """
    with st.form("add_task", clear_on_submit=True):
        pet_name = st.selectbox("For which pet", [p.name for p in owner.pets])
        cols = st.columns([2, 1, 1])
        title = cols[0].text_input("Task", value="")
        duration = cols[1].number_input("Minutes", min_value=1, max_value=240, value=20)
        priority = cols[2].selectbox("Priority", [p.value for p in Priority], index=1)

        cols = st.columns(3)
        category = cols[0].selectbox("Category", [c.value for c in TaskCategory])
        recurrence = cols[1].selectbox(
            "Repeats", [r.value for r in Recurrence], index=1
        )
        required = cols[2].checkbox("Required care", help="Never silently dropped.")

        cols = st.columns(2)
        use_earliest = cols[0].checkbox(
            "Not before",
            help="Also read as the time this task wants, for conflict warnings.",
        )
        earliest = cols[0].time_input(
            "Earliest start", value=time(8, 0), label_visibility="collapsed"
        )
        use_latest = cols[1].checkbox("Must finish by")
        latest = cols[1].time_input(
            "Latest finish", value=time(20, 0), label_visibility="collapsed"
        )

        if st.form_submit_button("Add task") and title.strip():
            owner.get_pet(pet_name).add_task(
                CareTask(
                    title=title.strip(),
                    duration_minutes=int(duration),
                    priority=Priority(priority),
                    category=TaskCategory(category),
                    recurrence=Recurrence(recurrence),
                    earliest_start=earliest if use_earliest else None,
                    latest_finish=latest if use_latest else None,
                    is_required=required,
                    # A weekly or one-off task never comes due without an
                    # anchor, and today is the only default the UI can infer.
                    anchor_date=date.today(),
                )
            )
            st.rerun()


owner = household()
plan_day, strategy_name = sidebar_preferences(owner)

st.title("🐾 PawPal+")
st.caption(f"One shared timeline for {owner.name}'s household.")

flash = st.session_state.pop("flash", None)
if flash:
    st.success(flash)

st.header("Schedule")
if owner.pets:
    render_plan(owner, plan_day, strategy_name)
else:
    st.info("Add a pet to get started.")

st.header("Pets and tasks")
render_tasks(owner, plan_day)
add_pet_form(owner)
if owner.pets:
    add_task_form(owner)
