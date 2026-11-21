"""Per-exercise progression, tonnage calculation, and stall detection."""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field

from src.telemetry.models import ExerciseSession, ExerciseSet


class SetPerformanceSummary(BaseModel):
    """Summary of an individual set."""
    set_number: int
    weight_kg: float
    reps: int
    e1rm: float


class SessionPerformanceSummary(BaseModel):
    """Summary metrics of a historical session for an exercise."""
    performed_at: Optional[datetime] = None
    working_sets: List[SetPerformanceSummary] = Field(default_factory=list)
    total_working_tonnage: float = 0.0
    total_working_reps: int = 0
    top_set_weight_kg: float = 0.0
    top_set_reps: int = 0
    top_e1rm: float = 0.0


class ExerciseProgressionMetrics(BaseModel):
    """Computed progression analysis and stall count for a given exercise."""
    exercise_id: str
    exercise_name: str
    last_session: Optional[SessionPerformanceSummary] = None
    historical_sessions: List[SessionPerformanceSummary] = Field(default_factory=list)
    consecutive_stalls: int = 0
    weight_delta_kg: float = 0.0
    reps_delta: int = 0
    e1rm_delta: float = 0.0
    all_sets_hit_top_rep: bool = False
    stall_flag: bool = False


def calculate_epley_e1rm(weight_kg: float, reps: int) -> float:
    """Calculate estimated 1RM using the Epley formula: e1RM = w * (1 + r / 30).

    If reps == 1, e1RM is simply the weight lifted.
    If reps <= 0 or weight <= 0, returns 0.0.
    """
    if weight_kg <= 0 or reps <= 0:
        return 0.0
    if reps == 1:
        return round(weight_kg, 2)
    return round(weight_kg * (1.0 + (reps / 30.0)), 2)


def analyze_exercise_session(session: ExerciseSession) -> SessionPerformanceSummary:
    """Compute working tonnage, top set, and e1RM for an exercise session."""
    working_sets: List[SetPerformanceSummary] = []
    total_tonnage = 0.0
    total_reps = 0
    top_weight = 0.0
    top_reps = 0
    top_e1rm = 0.0

    for s in session.sets:
        if s.is_warmup:
            continue
        e1rm = calculate_epley_e1rm(s.weight_kg, s.reps)
        working_sets.append(
            SetPerformanceSummary(
                set_number=s.set_number,
                weight_kg=s.weight_kg,
                reps=s.reps,
                e1rm=e1rm,
            )
        )
        total_tonnage += s.weight_kg * s.reps
        total_reps += s.reps

        # Identify top set by weight, breaking ties with reps
        if (s.weight_kg > top_weight) or (s.weight_kg == top_weight and s.reps > top_reps):
            top_weight = s.weight_kg
            top_reps = s.reps
            top_e1rm = e1rm

    return SessionPerformanceSummary(
        performed_at=session.performed_at,
        working_sets=working_sets,
        total_working_tonnage=round(total_tonnage, 2),
        total_working_reps=total_reps,
        top_set_weight_kg=top_weight,
        top_set_reps=top_reps,
        top_e1rm=top_e1rm,
    )


def compute_exercise_progression(
    exercise_id: str,
    exercise_name: str,
    historical_sessions: List[ExerciseSession],
    target_top_reps: int = 10,
) -> ExerciseProgressionMetrics:
    """Analyze up to 3 historical sessions for progression and consecutive stalls.

    Stall definition:
    A session is considered stalled if neither the top-set load nor the total working
    reps increased compared to the immediately preceding session.
    """
    if not historical_sessions:
        return ExerciseProgressionMetrics(
            exercise_id=exercise_id,
            exercise_name=exercise_name,
        )

    # Sort chronological ascending (oldest to newest)
    sorted_sessions = sorted(
        historical_sessions,
        key=lambda s: s.performed_at or datetime.min,
    )

    summaries = [analyze_exercise_session(s) for s in sorted_sessions]
    last_summary = summaries[-1]

    # Calculate consecutive stalls counting backwards from the most recent session
    consecutive_stalls = 0
    for i in range(len(summaries) - 1, 0, -1):
        current = summaries[i]
        previous = summaries[i - 1]

        # Check if top set load increased OR total working reps increased
        weight_increased = current.top_set_weight_kg > previous.top_set_weight_kg
        reps_increased = current.total_working_reps > previous.total_working_reps

        if not weight_increased and not reps_increased:
            consecutive_stalls += 1
        else:
            break

    # Deltas between the last two sessions
    weight_delta = 0.0
    reps_delta = 0
    e1rm_delta = 0.0
    if len(summaries) >= 2:
        curr = summaries[-1]
        prev = summaries[-2]
        weight_delta = round(curr.top_set_weight_kg - prev.top_set_weight_kg, 2)
        reps_delta = curr.total_working_reps - prev.total_working_reps
        e1rm_delta = round(curr.top_e1rm - prev.top_e1rm, 2)

    # Check if all working sets hit the ceiling
    all_hit_top = False
    if last_summary.working_sets:
        all_hit_top = all(s.reps >= target_top_reps for s in last_summary.working_sets)

    return ExerciseProgressionMetrics(
        exercise_id=exercise_id,
        exercise_name=exercise_name,
        last_session=last_summary,
        historical_sessions=summaries,
        consecutive_stalls=consecutive_stalls,
        weight_delta_kg=weight_delta,
        reps_delta=reps_delta,
        e1rm_delta=e1rm_delta,
        all_sets_hit_top_rep=all_hit_top,
        stall_flag=consecutive_stalls >= 2,
    )
