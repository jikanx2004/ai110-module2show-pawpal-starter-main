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

- What constraints does your scheduler consider (for example: time, priority, preferences)?
- How did you decide which constraints mattered most?

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

- How did you use AI tools during this project (for example: design brainstorming, debugging, refactoring)?
- What kinds of prompts or questions were most helpful?

**b. Judgment and verification**

- Describe one moment where you did not accept an AI suggestion as-is.
- How did you evaluate or verify what the AI suggested?

---

## 4. Testing and Verification

**a. What you tested**

- What behaviors did you test?
- Why were these tests important?

**b. Confidence**

- How confident are you that your scheduler works correctly?
- What edge cases would you test next if you had more time?

---

## 5. Reflection

**a. What went well**

- What part of this project are you most satisfied with?

**b. What you would improve**

- If you had another iteration, what would you improve or redesign?

**c. Key takeaway**

- What is one important thing you learned about designing systems or working with AI on this project?
