# PawPal+ (Module 2 Project)

You are building **PawPal+**, a Streamlit app that helps a pet owner plan care tasks for their pet.

## Scenario

A busy pet owner needs help staying consistent with pet care. They want an assistant that can:

- Track pet care tasks (walks, feeding, meds, enrichment, grooming, etc.)
- Consider constraints (time available, priority, owner preferences)
- Produce a daily plan and explain why it chose that plan

Your job is to design the system first (UML), then implement the logic in Python, then connect it to the Streamlit UI.

## What you will build

Your final app should:

- Let a user enter basic owner + pet info
- Let a user add/edit tasks (duration + priority at minimum)
- Generate a daily schedule/plan based on constraints and priorities
- Display the plan clearly (and ideally explain the reasoning)
- Include tests for the most important scheduling behaviors

## Getting started

### Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Suggested workflow

1. Read the scenario carefully and identify requirements and edge cases.
2. Draft a UML diagram (classes, attributes, methods, relationships).
3. Convert UML into Python class stubs (no logic yet).
4. Implement scheduling logic in small increments.
5. Add tests to verify key behaviors.
6. Connect your logic to the Streamlit UI in `app.py`.
7. Refine UML so it matches what you actually built.

## 🖥️ Sample Output

Running `python main.py` builds a two-pet household and schedules today. The
owner works 09:00–17:00, so the plan has to fit around that block:

```
==============================================================
                       TODAY'S SCHEDULE
==============================================================
Daily plan for Jordan (Biscuit, Mochi) on 2026-09-24
Ordering: priority-first
Total care time: 125 min of 150 min allowed

Scheduled:
  07:00 - Biscuit: Breakfast (10 min) [high]
      why: high priority; earliest 10 min opening at 07:00; must finish by 08:30
  07:30 - Mochi: Ear medication (5 min) [medium]
      why: medium priority; required care; earliest 5 min opening at 07:30; must finish by 20:00
  07:45 - Biscuit: Morning walk (30 min) [high]
      why: high priority; earliest 30 min opening at 07:45; must finish by 09:00
  08:25 - Mochi: Brush coat (15 min) [low]
      why: low priority; earliest 15 min opening at 08:25
  17:00 - Mochi: Playtime (20 min) [low]
      why: low priority; earliest 20 min opening at 17:00
  17:30 - Biscuit: Evening hike (45 min) [medium]
      why: medium priority; earliest 45 min opening at 17:30

Time conflicts (both asked for the same slot):
  07:30 - Mochi: Ear medication and Biscuit: Morning walk both wanted this time - moved to 07:45

--------------------------------------------------------------
Per pet:
  Biscuit (dog): 3 scheduled, 0 skipped, 85 min
  Mochi (cat): 3 scheduled, 0 skipped, 40 min

Double-booked slots: none
Owner has 150 usable min today; 125 min booked.

--------------------------------------------------------------
WARNING: 1 requested-time conflict(s)
  ! 07:30 - Mochi: Ear medication and Biscuit: Morning walk both wanted this time - moved to 07:45
  Nothing crashed: the loser was moved or skipped, and said so.

--------------------------------------------------------------
Same tasks, two orderings:

  [priority-first] Most important tasks first, so critical care is never crowded out.
    plans: Breakfast -> Ear medication -> Morning walk -> Brush coat -> Playtime -> Evening hike
    6 tasks / 125 min
    ! moved to 07:45

  [quick-wins] Shortest tasks first, to get the most done in a limited day.
    plans: Breakfast -> Ear medication -> Brush coat -> Playtime -> Evening hike
    5 tasks / 95 min
    ! not scheduled: no free slot left that meets its time limits
```

The morning walk and the ear medication both ask for 07:30, and one owner
cannot do both. The plan is still valid — the medication is required, so it
takes the slot and the walk moves to 07:45 — but the owner is told the trade
was made rather than left to notice it.

The two strategies resolve that same clash differently, which is the clearest
thing the demo shows. `priority-first` protects the walk; `quick-wins` fills the
morning with short tasks until the walk's 09:00 deadline can no longer absorb
the shift, and drops it. A conflict is a property of the plan, not of the task
list.

Completing a recurring task books its next instance automatically, which the
demo shows last:

```
Marking today's recurring tasks complete:
  Biscuit: Morning walk done -> next instance due Fri 2026-09-25 (daily)
  Mochi: Brush coat done -> next instance due Thu 2026-10-01 (weekly)
  Tomorrow (Fri 2026-09-25): Breakfast, Ear medication, Morning walk, Playtime, Evening hike
  Next week (Thu 2026-10-01): Breakfast, Ear medication, Morning walk, Brush coat, Playtime, Evening hike
  Today, re-planned: Breakfast, Ear medication, Playtime, Evening hike
  (completed tasks are gone; their successors are not due yet)
```

Finally the demo adds three tasks in deliberately scrambled order and prints
them back sorted and filtered, so both operations are visible without the UI:

```
Adding three tasks to Biscuit, deliberately out of order:
  entered: Bedtime brush (wants 19:30)
  entered: Sunrise sniff (wants 07:15)
  entered: Evening treat (wants 18:30)

  The plan comes back in clock order, not entry order:
    07:00  Biscuit: Breakfast
    07:15  Biscuit: Sunrise sniff
    07:30  Mochi: Ear medication
    07:35  Mochi: Playtime
    17:30  Biscuit: Evening hike
    18:30  Biscuit: Evening treat
    19:30  Biscuit: Bedtime brush

  sort_by_time() on a reversed copy of that list:
    before: 19:30, 18:30, 17:30, 07:35, 07:30, 07:15, 07:00
    after:  07:00, 07:15, 07:30, 07:35, 17:30, 18:30, 19:30
    input untouched: 19:30 still first

--------------------------------------------------------------
Filtering the household with find_tasks():
  everything: 11
  Mochi only (name typed in lower case): 4
  still to do: 9
  already done: 2 -> Biscuit/Morning walk, Mochi/Brush coat
  Biscuit, still to do: 6
  a pet who does not exist: 0 -> (none)
```

## 🧪 Testing PawPal+

```bash
# Run the full test suite:
python -m pytest

# Quieter, one character per test:
python -m pytest -q

# Run with coverage:
python -m pytest --cov
```

Sample test output:

```
$ python -m pytest -q
........................................................................ [ 34%]
........................................................................ [ 69%]
................................................................         [100%]
208 passed in 0.17s
```

Tests live in `tests/`. `test_pawpal.py` covers the two fundamentals — marking
a task complete, and adding a task to a pet.

`test_pawpal_system.py` is the full suite, organised bottom-up: time arithmetic,
enums, `CareTask`, the containers, constraints, strategies, then the scheduler.
The scheduler tests carry the weight, since those are the behaviors that would
actually ruin someone's day if they broke:

- **No double-booking across pets** — the core invariant. One owner cannot walk
  two dogs at once, so every pet shares a single timeline.
- **Busy windows and deadlines are respected** — nothing lands inside the
  workday, and a task with a `latest_finish` either fits before it or is skipped.
- **The daily time budget is never exceeded**, and required care survives a day
  too tight to hold everything.
- **Skips explain themselves** — every dropped task records why.
- **`is_double_booked()` is tested against an injected overlap**, so it cannot
  pass by being vacuously true.
- **Conflict warnings do not cry wolf** — a household whose tasks name no times
  produces no warnings at all, which is asserted directly.
- **A time conflict is not a double booking** — one test builds a plan that
  warns about two tasks wanting 09:00 and asserts `is_double_booked()` is still
  False, so the two checks can never be quietly collapsed into one.

The three behaviors the brief calls for are each covered directly:

| Required behavior | Where |
|---|---|
| **Sorting correctness** — tasks returned in chronological order | `TestSortByTime` (a reversed list comes back ordered, the input is not mutated, minutes inside an hour sort correctly) and `TestScheduler::test_output_is_sorted_by_time` |
| **Recurrence logic** — completing a daily task creates tomorrow's | `TestCareTask::test_completing_a_daily_task_yields_tomorrows_instance`, `TestPet::test_complete_task_files_the_successor`, and `TestRecurringTaskLifecycle::test_ten_days_of_rollover_stay_exactly_one_task_a_day` |
| **Conflict detection** — duplicate times are flagged | `TestConflictDetection` (11 tests) and `TestConflictAttribution` (6 tests) |

A later pass added edge-case coverage, most of it written as regression tests
against defects the first suite had missed. Their docstrings say what the old
behavior was:

- **Invalid input** (`TestTaskValidation`) — a zero or negative duration used to
  crash `build_plan` from deep inside `TimeWindow`; it is now refused where it
  is written. Contradictory time bounds are deliberately *not* refused, because
  the UI can produce them and the plan should explain them instead.
- **Missed work** (`TestOverdueTasks`) — a one-off task that came due and was
  never done used to vanish from every later plan. It now surfaces as overdue,
  and the tests pin down that this did *not* break rollover: a completed daily
  task's successor is still kept out of today.
- **The recurring lifecycle** (`TestRecurringTaskLifecycle`) — removing and
  resetting a rolled-over task, completing one retroactively or ahead of time,
  and ten consecutive days of rollover with exactly one task due per day.
- **Ordering** (`TestOrderingEdgeCases`) — the sort is total (a long important
  task cannot sink below a short trivial one), stable, and deterministic across
  runs.
- **Identity, not titles** (`TestIdentityBasedEditing`) — after one rollover two
  rows share a title, so deleting or undoing the row a user clicked has to act
  on that object, not on the name.
- **Filtering** (`TestFindTasks`) — including the case that surprises people: a
  rolled-over task appears in both the `completed=True` and `completed=False`
  results, because the record and its successor are two different objects.

**Confidence level: ⭐⭐⭐⭐☆ (4 / 5)**

Four rather than five, on the evidence rather than on feel. In its favour: 208
tests pass in well under a second, the scheduler's core invariant
(`is_double_booked()` is never True) is asserted across every edge case the
suite builds, and the edge-case pass found and fixed five real defects — which
is itself the reason for the missing star, since a suite that had felt thorough
at 131 tests was still hiding those. What is not covered: no property-based or
randomised testing, so the guarantees hold only for the cases someone thought
to write; no persistence layer exists, so nothing is tested across restarts;
and the Streamlit UI is verified by driving it headlessly rather than by any
committed test, since the app's own behavior is not part of the suite.

## 📐 Smarter Scheduling

| Feature | Method(s) | Notes |
|---------|-----------|-------|
| Sorting — ordering | `CareTask.weight()`, `PriorityFirstStrategy.order()`, `QuickWinsStrategy.order()` | Which tasks are *tried* first. Ordering is a swappable strategy, not baked into the scheduler. `weight()` bands are spaced so required care outranks priority, and priority outranks brevity. |
| Sorting — output | `Scheduler.sort_by_time()` | Which order the owner *reads* them in. Placement order is deliberately not clock order, so the finished list is sorted once on the way out with `sorted(items, key=lambda s: s.slot.start)`. Stable, so same-minute tasks keep their placement order. |
| Filtering — for the plan | `Pet.tasks_due_on()`, `CareTask.is_due_on()`, `Scheduler._candidates()` | Completed and not-yet-due tasks drop out before scheduling starts. `DailyPlan.for_pet()` filters the finished plan down to one animal. |
| Filtering — for the owner | `Owner.find_tasks(pet_name, completed)` | Ad-hoc queries over the whole household: by pet (case-insensitive, via `get_pet`), by completion, or both. `completed` is tri-state — `None` means "do not care", which is a different question from `False`. Returns `(Pet, CareTask)` pairs, because a task cannot say whose it is. |
| Missed work | `CareTask.is_overdue_on()`, `CareTask.days_overdue()` | A `ONCE` or `WEEKLY` task that came due and was never completed keeps appearing until it is done. Looks strictly backwards, which is what stops it colliding with rollover: a successor is always anchored forwards, so it can never read as overdue today. `DAILY` is excluded — it is already due every day, and missed days should not become a backlog. |
| Bad input | `CareTask.__post_init__()`, `CareTask.has_impossible_bounds()` | Split two ways on purpose. A non-positive or longer-than-a-day duration is refused on construction. Contradictory time bounds (due after 18:00, finish by 09:00) are *not* refused — the UI collects those as two independent inputs, so the plan explains them instead of the app crashing. |
| Editing one row | `Pet.remove_instance()`, `Pet.undo_completion()` | Identity-based twins of the title-based `remove_task` / `reset_task`. After a rollover two rows share a title, so the UI's Delete and Undo buttons act on the task object in that row rather than on its name. |
| Double-booking (prevented) | `TimeWindow.overlaps()`, `Scheduler._next_slot()`, `DailyPlan.is_double_booked()` | All overlap logic lives in `TimeWindow`. One shared `taken` list spans the household, so two pets can never get the same slot. `is_double_booked()` is an invariant check — True would mean a bug. |
| Time conflicts (reported) | `CareTask.wanted_window()`, `CareTask.is_pinned()`, `DailyPlan.detect_conflicts()`, `ConflictWarning.label()` | A different question: did two tasks *ask for* the same time? Returned as data, never raised — the plan it describes is already valid. `is_pinned()` marks a clash no scheduler could resolve, because neither task has any slack. |
| Recurring tasks | `Recurrence.occurs_on()`, `Recurrence.next_after()`, `CareTask.mark_complete()`, `CareTask.next_instance()`, `Pet.complete_task()` | `DAILY` always fires; `WEEKLY` matches the anchor's weekday; `ONCE` matches the exact date. Completing a recurring task spawns its successor via `timedelta` and files it beside the original, so the owner never re-enters a repeating task. |
| Time constraints | `CareTask.fits_in()`, `OwnerPreferences.is_free()` | Hard bounds (`earliest_start` / `latest_finish`) plus the owner's busy windows and day boundaries. |
| Budget + explanation | `OwnerPreferences.available_minutes()`, `DailyPlan.explain()`, `SkippedTask.reason` | One minute budget covers the household. Every placement, skip and conflict records its reason at the moment the decision is made. |

**Known tradeoff:** placement is greedy — tasks are ordered once, then each is
dropped into the earliest slot that fits. This is predictable and easy to
explain, but it is not deadline-aware: a flexible task can occupy a slot that a
later, more urgent task needed. An earliest-deadline-first ordering would fix
it, at the cost of a plan that is harder to justify to the user.

**Second known tradeoff:** conflict detection reads `earliest_start` as the time
a task wants, but that field is really a floor. Two tasks that both mean "any
time after 17:00" will be reported as wanting 17:00. A separate
`preferred_start` field would remove the false positive, at the cost of a second
time field meaning almost the same thing as an existing one. See
`reflection.md` §2b.

## 📸 Demo Walkthrough

### What the interface gives you

`streamlit run app.py` opens a single page in three bands:

- **Sidebar — the owner's constraints.** Waking hours, a daily care budget, the
  break between tasks, busy windows (work, commute), which day to plan, and
  which ordering strategy to use. Changing any of them re-plans immediately, so
  the sidebar doubles as a way to interrogate the scheduler: shrink the budget
  and watch which tasks it protects.
- **Schedule — the plan.** Four metrics across the top (tasks scheduled, minutes
  booked, minutes actually usable, conflicts found), then the plan as a sorted
  table, then conflict warnings, then anything that could not fit and why, then
  an expandable **Why this plan** panel holding `DailyPlan.explain()` verbatim.
  Every scheduled task carries a **Done** button.
- **Pets and tasks — the household.** One expander per pet, listing every task
  with its state (`due`, `OVERDUE since Fri 18 Sep (9 days late)`,
  `done 27 Sep`, `upcoming Mon 28 Sep`). Each row has **Delete**, and completed
  rows also have **Undo**. Below that, forms to add a pet or a task.

### An example workflow

1. **Open the app.** It starts from the demo household — Biscuit the retriever
   and Mochi the cat — so there is a real plan on screen before you type
   anything.
2. **Add a pet.** *Pets and tasks* → **Add pet** → name, species, age.
3. **Give it a task.** **Add task** → choose the pet, a title, minutes,
   priority, category and how it repeats. Optionally tick *Not before* or
   *Must finish by* to give it hard bounds.
4. **Read the schedule.** The table at the top is in clock order no matter what
   order you entered tasks in.
5. **Provoke a conflict.** Give two pets a task that both start at the same
   time. Neither is dropped: the plan gives the slot to one and reports the
   trade as a warning, naming both tasks and saying where the other one went.
6. **Mark something done.** Click **Done** on a scheduled task. If it recurs,
   the confirmation tells you when the next one is due — you never re-enter a
   repeating task.
7. **Change your mind.** That task now shows as done in *Pets and tasks*, with
   an **Undo** button that clears the completion *and* withdraws the successor
   it booked, so the day does not end up holding two copies.

### What the Scheduler is doing behind that

- **Two different sorts.** `SchedulingStrategy.order()` decides which tasks are
  *tried* first (importance, or brevity); `Scheduler.sort_by_time()` decides the
  order you *read* them in. They are deliberately not the same: a high-priority
  evening task is placed before a low-priority morning one, then the finished
  list is sorted into a timeline on the way out.
- **Conflict warnings, not conflict errors.** Two tasks asking for one time is
  information, not a failure — the plan it describes is already valid. Ordinary
  clashes render as `st.warning`; a clash neither task could win (both pinned to
  the same slot with no slack) renders as `st.error`, because no scheduler can
  fix that one and only the owner can.
- **Recurrence that books itself.** Completing a daily or weekly task files its
  next instance automatically, anchored to the day the work actually happened.
- **Filtering.** Completed and not-yet-due tasks drop out before scheduling
  starts, and `Owner.find_tasks()` answers ad-hoc questions like "what has Mochi
  still got left to do".
- **One shared timeline.** A single `taken` list and one minute budget span the
  whole household, because one owner cannot walk two dogs at once.

### Sample CLI output

The same logic without Streamlit — `python main.py`, final section:

```
--------------------------------------------------------------
Adding three tasks to Biscuit, deliberately out of order:
  entered: Bedtime brush (wants 19:30)
  entered: Sunrise sniff (wants 07:15)
  entered: Evening treat (wants 18:30)

  The plan comes back in clock order, not entry order:
    07:00  Biscuit: Breakfast
    07:15  Biscuit: Sunrise sniff
    07:30  Mochi: Ear medication
    07:35  Mochi: Playtime
    17:30  Biscuit: Evening hike
    18:30  Biscuit: Evening treat
    19:30  Biscuit: Bedtime brush

  sort_by_time() on a reversed copy of that list:
    before: 19:30, 18:30, 17:30, 07:35, 07:30, 07:15, 07:00
    after:  07:00, 07:15, 07:30, 07:35, 17:30, 18:30, 19:30
    input untouched: 19:30 still first

--------------------------------------------------------------
Filtering the household with find_tasks():
  everything: 11 -> Biscuit/Morning walk, Biscuit/Breakfast, ...
  Mochi only (name typed in lower case): 4 -> Mochi/Ear medication, ...
  still to do: 9 -> Biscuit/Breakfast, Biscuit/Evening hike, ...
  already done: 2 -> Biscuit/Morning walk, Mochi/Brush coat
  Biscuit, still to do: 6 -> Biscuit/Breakfast, Biscuit/Evening hike, ...
  a pet who does not exist: 0 -> (none)
```

The full run — today's schedule, the two strategies compared, and the
recurrence rollover — is in [Sample Output](#️-sample-output) above.

**Screenshot or video** *(optional)*: <!-- Insert a screenshot or link to a demo video here -->
