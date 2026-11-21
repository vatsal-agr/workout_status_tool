"""Rolling split state machine and ad-hoc workout filtering."""

from datetime import date, datetime
from typing import List, Optional, Tuple
from pydantic import BaseModel, Field

from src.telemetry.models import LoggedWorkout, WorkoutPlanTemplate


class UnplannedFatigueEvent(BaseModel):
    """Ad-hoc or unplanned session that does not advance the rolling split."""
    workout_id: str
    name: str
    completed_at: datetime
    reason: str = "Template ID did not match active rotation"


class RollingSplitState(BaseModel):
    """Computed state of the rolling workout split."""
    today_target_template: WorkoutPlanTemplate
    current_index: int
    total_templates: int
    last_completed_workout: Optional[LoggedWorkout] = None
    days_since_last_workout: Optional[int] = None
    days_since_same_split: Optional[int] = None
    unplanned_fatigue_events: List[UnplannedFatigueEvent] = Field(default_factory=list)
    alternation_notes: Optional[str] = None


def is_incline_chest_exercise(name: str) -> bool:
    """Check if an exercise name corresponds to an incline chest press variation."""
    name_l = name.lower()
    return "incline" in name_l and any(k in name_l for k in ("press", "bench", "dumbbell", "barbell", "chest", "smith"))


def is_flat_chest_exercise(name: str) -> bool:
    """Check if an exercise name corresponds to a flat chest press variation."""
    name_l = name.lower()
    if "incline" in name_l or "decline" in name_l:
        return False
    return any(k in name_l for k in ("flat", "bench press", "chest press"))


def apply_chest_exercise_alternation(
    template: WorkoutPlanTemplate,
    sorted_workouts: List[LoggedWorkout],
) -> Tuple[WorkoutPlanTemplate, Optional[str]]:
    """If today is a Push/Chest split containing both Flat and Incline bench exercises,

    automatically alternate which exercise is prioritized at Exercise #1 based on the
    previous Push workout.
    """
    if not any(k in template.name.lower() for k in ("push", "chest")):
        return template, None

    names = template.exercise_names or []
    ids = template.exercise_ids or []

    incline_idx = -1
    flat_idx = -1
    for idx, (ex_id, ex_name) in enumerate(zip(ids, names)):
        if is_incline_chest_exercise(ex_name):
            incline_idx = idx
        elif is_flat_chest_exercise(ex_name):
            flat_idx = idx

    # If names didn't match, check IDs as fallback
    if incline_idx == -1 or flat_idx == -1:
        for idx, ex_id in enumerate(ids):
            if is_incline_chest_exercise(ex_id):
                incline_idx = idx
            elif is_flat_chest_exercise(ex_id):
                flat_idx = idx

    if incline_idx == -1 or flat_idx == -1:
        return template, None

    # Find the most recent Push workout completed
    last_push_workout = next(
        (w for w in sorted_workouts if any(k in w.name.lower() for k in ("push", "chest")) or w.template_id == template.template_id),
        None,
    )

    last_flat_order = None
    last_incline_order = None
    if last_push_workout and last_push_workout.exercises:
        for order_idx, ex in enumerate(last_push_workout.exercises):
            ex_identifier = ex.exercise_name or ex.exercise_id
            if is_incline_chest_exercise(ex_identifier):
                if last_incline_order is None:
                    last_incline_order = order_idx
            elif is_flat_chest_exercise(ex_identifier):
                if last_flat_order is None:
                    last_flat_order = order_idx

    new_ids = list(ids)
    new_names = list(names)
    alternation_note = None

    # If Flat Bench was done first last time -> Incline Press promoted to #1 today!
    if last_flat_order is not None and (last_incline_order is None or last_flat_order < last_incline_order):
        if incline_idx > flat_idx:
            new_ids[flat_idx], new_ids[incline_idx] = new_ids[incline_idx], new_ids[flat_idx]
            if len(new_names) > max(flat_idx, incline_idx):
                new_names[flat_idx], new_names[incline_idx] = new_names[incline_idx], new_names[flat_idx]
        alternation_note = "INCLINE_PRIORITY: Incline Press promoted to Exercise #1 (alternated from Flat Bench last session). Flat Bench moved to secondary volume."

    # If Incline Press was done first last time -> Flat Bench promoted to #1 today!
    elif last_incline_order is not None and (last_flat_order is None or last_incline_order < last_flat_order):
        if flat_idx > incline_idx:
            new_ids[flat_idx], new_ids[incline_idx] = new_ids[incline_idx], new_ids[flat_idx]
            if len(new_names) > max(flat_idx, incline_idx):
                new_names[flat_idx], new_names[incline_idx] = new_names[incline_idx], new_names[flat_idx]
        alternation_note = "FLAT_BENCH_PRIORITY: Flat Bench promoted to Exercise #1 (alternated from Incline Press last session). Incline moved to secondary volume."

    else:
        # Default initial priority: Flat Bench at Exercise #1
        if flat_idx > incline_idx:
            new_ids[flat_idx], new_ids[incline_idx] = new_ids[incline_idx], new_ids[flat_idx]
            if len(new_names) > max(flat_idx, incline_idx):
                new_names[flat_idx], new_names[incline_idx] = new_names[incline_idx], new_names[flat_idx]
        alternation_note = "FLAT_BENCH_PRIORITY: Initial rotation set with Flat Bench at Exercise #1."

    new_template = template.model_copy(
        update={
            "exercise_ids": new_ids,
            "exercise_names": new_names,
        }
    )
    return new_template, alternation_note


def calculate_rolling_split(
    templates: List[WorkoutPlanTemplate],
    recent_workouts: List[LoggedWorkout],
    reference_date: Optional[date] = None,
) -> RollingSplitState:
    """Determine the next workout in the rolling cycle without calendar day constraints.

    Rules:
    1. Sort templates by order_index: W = [W_0, W_1, ..., W_{n-1}].
    2. Scan recent workouts in reverse chronological order.
    3. Find the most recent session S_last whose template_id matches an index k in W.
    4. Ad-hoc workouts not matching any template_id do not advance k, but are flagged
       as unplanned fatigue events.
    5. Next workout is W_{(k + 1) % n}.
    6. Compute days_since_last_workout and days_since_same_split.
    """
    ref_date = reference_date or date.today()
    if not templates:
        ordered_templates = [
            WorkoutPlanTemplate(
                template_id="default_routine",
                name="Default Strength Rotation",
                order_index=0,
                exercise_ids=[],
                exercise_names=["Compound Lift A", "Compound Lift B"],
            )
        ]
    else:
        ordered_templates = sorted(templates, key=lambda t: t.order_index)
    n = len(ordered_templates)
    template_id_map = {t.template_id: idx for idx, t in enumerate(ordered_templates)}

    # Sort recent workouts descending by completed_at
    sorted_workouts = sorted(recent_workouts, key=lambda w: w.completed_at, reverse=True)

    unplanned_events: List[UnplannedFatigueEvent] = []
    last_matched_idx: Optional[int] = None
    last_matched_workout: Optional[LoggedWorkout] = None
    last_same_split_workout: Optional[LoggedWorkout] = None

    # Track the absolute most recent workout completed (even if ad-hoc)
    absolute_last_workout: Optional[LoggedWorkout] = sorted_workouts[0] if sorted_workouts else None

    # Scan for last matching template in reverse chronological order
    for workout in sorted_workouts:
        if workout.template_id and workout.template_id in template_id_map:
            if last_matched_idx is None:
                last_matched_idx = template_id_map[workout.template_id]
                last_matched_workout = workout
        else:
            # Workout is ad-hoc / unscheduled
            unplanned_events.append(
                UnplannedFatigueEvent(
                    workout_id=workout.workout_id,
                    name=workout.name,
                    completed_at=workout.completed_at,
                    reason=f"Ad-hoc workout (templateId '{workout.template_id}' not in active plan)",
                )
            )

    # If no template was ever matched, start with the first template
    if last_matched_idx is None:
        next_idx = 0
    else:
        next_idx = (last_matched_idx + 1) % n

    next_template = ordered_templates[next_idx]
    next_template, alt_notes = apply_chest_exercise_alternation(next_template, sorted_workouts)

    # Find the most recent completion of the exact target template (same split)
    for workout in sorted_workouts:
        if workout.template_id == next_template.template_id:
            last_same_split_workout = workout
            break

    # Calculate day deltas
    days_since_last: Optional[int] = None
    if absolute_last_workout:
        days_since_last = max(0, (ref_date - absolute_last_workout.completed_at.date()).days)

    days_since_same: Optional[int] = None
    if last_same_split_workout:
        days_since_same = max(0, (ref_date - last_same_split_workout.completed_at.date()).days)

    return RollingSplitState(
        today_target_template=next_template,
        current_index=next_idx,
        total_templates=n,
        last_completed_workout=last_matched_workout or absolute_last_workout,
        days_since_last_workout=days_since_last,
        days_since_same_split=days_since_same,
        unplanned_fatigue_events=unplanned_events,
        alternation_notes=alt_notes,
    )
