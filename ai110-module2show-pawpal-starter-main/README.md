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

## 🧪 Testing PawPal+

```bash
# Run the full test suite:
pytest

# Run with coverage:
pytest --cov
```

Sample test output:

```
$ pytest -q
........................................................................ [ 54%]
...........................................................              [100%]
131 passed in 0.21s
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

## 📐 Smarter Scheduling

| Feature | Method(s) | Notes |
|---------|-----------|-------|
| Sorting — ordering | `CareTask.weight()`, `PriorityFirstStrategy.order()`, `QuickWinsStrategy.order()` | Which tasks are *tried* first. Ordering is a swappable strategy, not baked into the scheduler. `weight()` bands are spaced so required care outranks priority, and priority outranks brevity. |
| Sorting — output | `Scheduler.sort_by_time()` | Which order the owner *reads* them in. Placement order is deliberately not clock order, so the finished list is sorted once on the way out with `sorted(items, key=lambda s: s.slot.start)`. Stable, so same-minute tasks keep their placement order. |
| Filtering | `Pet.tasks_due_on()`, `CareTask.is_due_on()`, `Scheduler._candidates()` | Completed and not-yet-due tasks drop out before scheduling starts. `DailyPlan.for_pet()` filters the finished plan down to one animal. |
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

Describe your app in numbered steps so a reader can follow along without watching a video:

1. <!-- Describe this step -->
2. <!-- Describe this step -->
3. <!-- Describe this step -->
4. <!-- Describe this step -->
5. <!-- Add more steps as needed -->

**Screenshot or video** *(optional)*: <!-- Insert a screenshot or link to a demo video here -->
