# PawPal+ Project Reflection

## 1. System Design

**a. Initial design**

My initial UML splits PawPal+ into three layers: the domain (`Owner`,
`OwnerPreferences`, `Pet`, `CareTask`), the plan it produces (`ScheduledTask`,
`DailyPlan`), and the engine that connects them (`Scheduler` plus a swappable
`SchedulingStrategy`). `OwnerPreferences` owns every constraint so the scheduler
asks it what is allowed instead of hardcoding limits, and `TimeWindow` is the
only class that compares times, which keeps overlap logic in one place.

I put tasks on the `Pet` rather than the `Owner` so a two-pet household plans
each animal separately. I also kept ordering out of `Scheduler`: which ordering
is best is a real tradeoff, so `SchedulingStrategy` decides what to try first
while `Scheduler` decides where it fits. Each `ScheduledTask` stores the reason
it landed in its slot, since the plan has to explain itself.

**b. Design changes**

Yes. Turning the UML into code exposed a gap in `Recurrence.occurs_on()`: the
diagram had it taking only a day, but that question is unanswerable on its own.
You cannot tell whether a weekly task is due today without knowing which weekday
it started on, so I added an `anchor` parameter holding the task's original date.
The UML hid the problem because a method name can look complete while its
signature is missing the information it needs.

I also made `Owner.get_pet()` and `Scheduler._next_slot()` return `None` instead
of raising. Both have ordinary not-found cases — a name that does not match, a
day with no room left — and those are results the caller should handle, not
errors.

---

## 2. Scheduling Logic and Tradeoffs

**a. Constraints and priorities**

The scheduler plans inside five constraints. Two belong to the owner and live
on `OwnerPreferences`: the waking day (`day_start`/`day_end`), and `busy_windows`
for time already spoken for, like a 09:00-17:00 workday. One is a budget —
`max_daily_minutes` — which exists because a free day is not the same as a
willing one; an owner may have fourteen hours available and still only want to
spend two of them on pet care. The remaining two belong to the task:
`earliest_start`/`latest_finish` as hard bounds, and `duration_minutes` as the
room it needs. `break_minutes` sits between them as a courtesy gap, applied only
where the scheduler is choosing the time itself — a task that names its own
start gets the time it asked for, back to back with whatever came before.

Ranking them was mostly forced rather than chosen. Anything that makes a plan
*physically impossible* has to win: two tasks cannot share a minute, so the
no-double-booking rule is not a preference at all but the invariant everything
else operates under, and it is the one thing `DailyPlan.is_double_booked()`
asserts can never be violated. Below that sit the owner's hard limits, since
booking pet care into the middle of a work meeting produces a plan that will
simply not be followed. Only then does task-level priority come in, and it
decides *order of consideration*, not entitlement — `SchedulingStrategy` picks
what to try first, and the day's remaining space decides what actually fits.

The one ranking I had to make a judgement about was `is_required` versus
`priority`, and I gave required care its own band above priority entirely. A
low-priority medication is still medication. `CareTask.weight()` spaces those
bands wider than the longest duration a task may declare, so brevity can only
ever break a tie *inside* a band and never lift a task out of one.

**b. Tradeoffs**

My conflict detection reads `earliest_start` as "the time this task wants",
which is an overload: that field is really a floor, not a wish. When Biscuit's
morning walk and Mochi's ear medication both set `earliest_start=07:30`, they
genuinely are competing for 07:30 and the warning is true. But a task that sets
07:00 only to mean "not before I leave the house" will be reported as wanting
07:00 against anything else that starts then, and that warning is not true. I
took the overload anyway, because the honest alternative — a separate
`preferred_start` field — is not one change but six: the field, a Streamlit
control, a decision about whether `_next_slot` should try to honour it, a
`fits_in` question, the UML, and a check that `dataclasses.replace` carries it
across recurrence rollover. For a household of two pets and six tasks that is a
redesign of the scheduling contract to remove a false positive I have not
actually hit. I documented it instead of hiding it, and `CareTask.is_pinned()`
narrows the blast radius by marking which clashes are genuinely unresolvable.

The related choice is that the scheduler *reports* a conflict rather than
resolving it. `DailyPlan.detect_conflicts()` names the two tasks and says what
became of the loser; it never decides that 07:30 mattered more to the
medication than to the walk. Nothing in my data model expresses that ranking,
and inventing one would mean the plan quietly overruling the owner on a
judgement only they can make. Warning is the weaker behaviour and the right one
here — the plan it describes is already valid, so there is nothing to fail.

I also kept a deliberately un-clever `_next_slot()`. I wrote the comprehension
version, the single `next()` over a generator that collapses my four guard
clauses into one condition, and measured it: byte-identical output on the demo
household and 8.5% *slower* over 2000 plans. Even if it had been faster, each
guard clause names one reason a candidate slot was rejected, and a scheduler
whose whole purpose is explaining itself should not make "why did this slot
lose?" unanswerable to save eight lines.

---

## 3. AI Collaboration

**a. How you used AI**

Across the build I used AI for four distinct jobs, and they were not equally
productive. Design brainstorming was the weakest: asked in the abstract, an
assistant will happily produce a plausible-sounding class list that has no
contact with the problem. Debugging and test design were the strongest, and the
difference between them came down to one thing — whether the question was
anchored to the actual file.

The single most effective technique was refusing to accept a *generic* answer.
Asking "what are the most important edge cases for a pet scheduler with sorting
and recurring tasks" produces a textbook list: empty input, duplicate times,
leap years. Asking the same question with `pawpal_system.py` attached, and
insisting the answer be checked against the code rather than asserted, produced
something different — short scripts run against the real module to see what it
actually did. That is what turned up five defects I did not know I had, and it
is the difference between a list of things that *sound* risky and a list of
things that *are* broken.

The other prompt pattern that paid off was asking for the reasoning behind a
suggestion before accepting the code. "Why this rather than the obvious thing"
repeatedly surfaced that the obvious thing was fine and the clever thing was
not — which is how `_next_slot()` stayed as four readable guard clauses instead
of collapsing into one dense generator expression.

Working in separate chat sessions per phase mattered more than I expected. When
I opened a session purely for testing, the assistant had no memory of the design
conversation and had to read the module cold — so it tested what the code *does*
rather than what I remembered intending. A single long session would have
carried my own explanations forward as assumptions, and those assumptions were
exactly where the bugs were hiding. The cost is that context has to be re-supplied
each time; the benefit is a reader with no stake in the original design.

**b. Judgment and verification**

The clearest case was input validation. The assistant proposed that `CareTask`
reject contradictory time bounds on construction — a task due after 18:00 that
must finish by 09:00 — alongside rejecting a non-positive duration. Both look
like the same kind of defensive check. Running the suite showed they are not:
the second broke `test_skips_impossible_task_with_a_reason`, a test I already
had, which asserts the scheduler *reports* an impossible task rather than
raising on it.

The tempting move was to update the test, since the new code was "more correct".
I kept the test and reversed half the suggestion instead, because the test
encoded something the assistant could not see from the module alone: `app.py`
collects `earliest_start` and `latest_finish` as two independent time inputs
with no cross-check, so 18:00-09:00 is two clicks away for any user. Raising
there would take the whole app down on its next rerun, replacing a plan that
explains itself with a stack trace. The duration check survived — that one
guards a genuine crash the UI cannot produce — and the impossible-bounds case
became a clearer skip reason instead. One suggestion, half kept.

A second, smaller case: the same aliased-task bug (one `CareTask` object
attached to two pets) drew a suggestion to make the task-to-pet lookup tolerate
duplicates so both pets got their slot. That would have preserved broken input
silently, and it would still have been wrong, because a shared instance carries
one `completed` flag — finishing the task for one pet finishes it for both. It
raises now instead.

How I verified suggestions: by running them, not by reading them. Every claimed
defect was reproduced with a script against the real module before anything was
changed, and every fix was checked by running the new tests against the *old*
code to confirm they actually fail without it. Twenty-nine of them did. A test
that passes on the broken version is worse than no test, because it reports
safety that was never established.

---

## 4. Testing and Verification

**a. What you tested**

The suite is organised bottom-up — time arithmetic, enums, `CareTask`, the
containers, constraints, strategies, then the scheduler — but the weight is
deliberately not evenly spread. The scheduler tests carry it, because those are
the behaviors that would actually ruin someone's day.

The invariant everything else rests on is that no two tasks are ever placed on
top of each other. One owner cannot walk two dogs at once, so every pet in the
household competes for one timeline, and `is_double_booked()` returning True
would mean a bug rather than a busy morning. That check is asserted across every
edge case the suite builds, and it is also tested against a deliberately
injected overlap so it cannot pass by being vacuously true — a check that can
never fail is not a check.

Beyond that: busy windows and deadlines are respected; the daily budget is never
exceeded and required care survives a day too tight to hold everything; every
dropped task records why; completing a daily task books tomorrow's instance and
keeps it out of today; and conflict warnings do not cry wolf — a household whose
tasks name no times produces no warnings at all, which is asserted directly.

The most valuable tests were the ones written *after* I thought the system
worked. A dedicated edge-case pass found five genuine defects: a zero-minute
task crashed the whole plan from deep inside `TimeWindow`; deleting a
rolled-over task removed the completed record and left the live copy still due
tomorrow, while reporting success; one `CareTask` attached to two pets was
credited entirely to the last one; a conflict where *both* tasks were displaced
named an arbitrary winner and told the owner the wrong task had moved; and
`reset()` on a filed task produced two live copies that both got scheduled.
None of these are exotic. Four of the five need only one rollover to reach,
which is the ordinary state of any recurring task after a single day of use.

**b. Confidence**

Four out of five, and the missing star is earned rather than modest. 208 tests
pass in under a second and every fix is backed by a test verified to fail
against the previous code. But the suite had felt thorough at 131 tests while
still hiding all five defects above, which is the most useful thing it taught
me: passing tests measure the questions you thought to ask, not the correctness
of the system. I trust the scheduler on the cases someone has considered, and I
have no basis for trusting it beyond them.

What I would test next, in order of how much it worries me. First,
property-based testing over randomly generated households — generate pets,
tasks and constraints at random, then assert the invariants hold (nothing
double-booked, nothing over budget, nothing outside the day, every due task
either scheduled or skipped-with-a-reason). Every defect found so far came from
a case a human thought of; randomised input is the only way to find the ones
nobody did. Second, the unbounded growth of completed history — a pet's task
list never shrinks, so a year of daily walks is 365 dead records that every
filter walks through. Third, the interaction between overdue tasks and the daily
budget, which I have tested singly but not together: a household with several
overdue required tasks and a tight budget is exactly where the ordering rules
would show a flaw. Fourth, the Streamlit layer itself, which is currently
verified by driving it headlessly rather than by any committed test.

---

## 5. Reflection

**a. What went well**

The part I am most satisfied with is that the plan explains itself. Every
`ScheduledTask` stores the reason it landed where it did, captured at placement
time while the scheduler still knows; every `SkippedTask` says what it lost to;
and `ConflictWarning` names both tasks and reports what became of the one that
did not get the time. None of that is required to produce a correct schedule —
a scheduler that silently returned the same list would pass most of the same
tests. But the difference between a tool an owner trusts and one they quietly
stop using is whether it can answer "why isn't the walk on here?", and that
answer cannot be reconstructed after the fact.

The decision that made this work was keeping the logic layer free of Streamlit.
`pawpal_system.py` imports nothing from the UI, which is why `main.py` and 208
tests can exercise the identical behavior with no browser in the loop — and why
the edge-case pass was able to probe the real scheduler directly instead of
clicking through a web app to reach it.

**b. What you would improve**

Three things, in order of value.

Persistence is the honest first answer: everything lives in
`st.session_state`, so closing the tab loses the household. That is the one
limitation a real user would hit within a minute.

Second, placement is greedy — tasks are ordered once, then each is dropped into
the earliest slot that fits. It is predictable and easy to explain, which is
worth a lot in a system whose whole purpose is explaining itself, but it is not
deadline-aware: a flexible task can occupy a slot that a later, more urgent task
needed. Earliest-deadline-first ordering would fix it, at the cost of a plan
that is harder to justify to the user.

Third, I would revisit the `earliest_start` overload described in §2b. It is
currently doing two jobs — a hard floor and a stated preference — and the
conflict detector reads it as the second. The fix is a separate
`preferred_start` field, which I deliberately did not build because it is six
changes rather than one and it removes a false positive I have not actually hit.
That reasoning still holds at two pets and six tasks. It would stop holding the
moment a third person used this.

**c. Key takeaway**

Being the lead architect with a capable assistant turns out to be mostly about
owning the *questions*, not the code. The assistant could write any method I
described, and write it well. What it could not do was know which of my existing
tests encoded a real contract and which were incidental — that is why it
suggested validation that would have crashed the app, and why keeping the test
instead of the suggestion was the right call.

The sharper version of the lesson is that plausible and correct are very far
apart, and AI output sits much closer to plausible. Generated tests pass
readily; that is not evidence of anything. The technique that actually worked
was adversarial: run the code, reproduce the defect, then run the new test
against the *old* version to prove it fails without the fix. Twenty-nine did.
The assistant was fully capable of doing that verification — but only once
someone insisted on it, and insisting on it is the job.
