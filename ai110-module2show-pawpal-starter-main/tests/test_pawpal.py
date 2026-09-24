"""Core behavior tests for PawPal+.

Two fundamentals that everything else rests on: a task can be marked complete,
and a pet can be given tasks. The broader suite lives in
`test_pawpal_system.py`.
"""

from datetime import date

from pawpal_system import CareTask, Pet, Priority

TODAY = date(2026, 9, 23)


# ---------------------------------------------------------------------------
# Task completion
# ---------------------------------------------------------------------------


def test_mark_complete_changes_status():
    """mark_complete() flips the task from incomplete to complete."""
    task = CareTask("Morning walk", 30, Priority.HIGH)

    assert task.completed is False

    task.mark_complete()

    assert task.completed is True


def test_completed_task_drops_out_of_the_day():
    """Completion has to actually affect scheduling, not just set a flag.

    A status change nothing reads would pass the test above while leaving the
    task on the plan, so this checks the consequence too.
    """
    task = CareTask("Morning walk", 30, Priority.HIGH)
    assert task.is_due_on(TODAY) is True

    task.mark_complete()

    assert task.is_due_on(TODAY) is False


def test_reset_marks_a_task_incomplete_again():
    """The inverse, so a task can be reused the next day."""
    task = CareTask("Morning walk", 30, Priority.HIGH)
    task.mark_complete()

    task.reset()

    assert task.completed is False
    assert task.is_due_on(TODAY) is True


# ---------------------------------------------------------------------------
# Task addition
# ---------------------------------------------------------------------------


def test_add_task_increases_task_count():
    """Adding a task to a pet grows that pet's task list."""
    pet = Pet(name="Biscuit", species="dog")

    assert len(pet.tasks) == 0

    pet.add_task(CareTask("Morning walk", 30, Priority.HIGH))

    assert len(pet.tasks) == 1


def test_add_task_stores_the_task_itself():
    """The count going up is not enough -- the right task has to be there."""
    pet = Pet(name="Biscuit", species="dog")
    walk = CareTask("Morning walk", 30, Priority.HIGH)

    pet.add_task(walk)

    assert pet.tasks[0] is walk


def test_adding_several_tasks_accumulates():
    pet = Pet(name="Biscuit", species="dog")

    pet.add_task(CareTask("Morning walk", 30, Priority.HIGH))
    pet.add_task(CareTask("Breakfast", 10, Priority.HIGH))
    pet.add_task(CareTask("Playtime", 20, Priority.LOW))

    assert len(pet.tasks) == 3


def test_tasks_are_not_shared_between_pets():
    """A mutable default argument would leak tasks into every pet at once."""
    biscuit = Pet(name="Biscuit", species="dog")
    mochi = Pet(name="Mochi", species="cat")

    biscuit.add_task(CareTask("Morning walk", 30, Priority.HIGH))

    assert len(biscuit.tasks) == 1
    assert len(mochi.tasks) == 0
