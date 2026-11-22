"""Tests for the rolling split state machine and ad-hoc workout filtering."""

from datetime import date, datetime, timedelta
import pytest

from src.analytics.split_machine import calculate_rolling_split
from src.telemetry.models import LoggedWorkout, WorkoutPlanTemplate


def test_initial_split_start(sample_templates):
    """When no workouts exist, split should start at index 0 (W0)."""
    state = calculate_rolling_split(
        templates=sample_templates,
        recent_workouts=[],
        reference_date=date(2026, 11, 21),
    )
    assert state.current_index == 0
    assert state.today_target_template.template_id == "tpl_push"
    assert state.last_completed_workout is None
    assert state.days_since_last_workout is None
    assert len(state.unplanned_fatigue_events) == 0


def test_split_advances_cyclically(sample_templates):
    """If Push (index 0) was completed, next is Pull (index 1). If Legs (index 2), next is Push (0)."""
    push_done = [
        LoggedWorkout(
            workout_id="w1",
            template_id="tpl_push",
            name="Push Day",
            completed_at=datetime(2026, 11, 20, 10, 0),
        )
    ]
    state = calculate_rolling_split(
        templates=sample_templates,
        recent_workouts=push_done,
        reference_date=date(2026, 11, 21),
    )
    assert state.current_index == 1
    assert state.today_target_template.template_id == "tpl_pull"
    assert state.days_since_last_workout == 1

    legs_done = [
        LoggedWorkout(
            workout_id="w2",
            template_id="tpl_legs",
            name="Legs Day",
            completed_at=datetime(2026, 11, 20, 10, 0),
        )
    ]
    state2 = calculate_rolling_split(
        templates=sample_templates,
        recent_workouts=legs_done,
        reference_date=date(2026, 11, 21),
    )
    assert state2.current_index == 0
    assert state2.today_target_template.template_id == "tpl_push"


def test_adhoc_session_filtering(sample_templates, sample_workouts_with_adhoc):
    """Ad-hoc workouts should NOT advance split index k, but are recorded under unplanned events."""
    state = calculate_rolling_split(
        templates=sample_templates,
        recent_workouts=sample_workouts_with_adhoc,
        reference_date=date(2026, 11, 21),
    )
    # Push was last planned session (index 0), so next should be Pull (index 1)
    assert state.current_index == 1
    assert state.today_target_template.template_id == "tpl_pull"
    # Absolute last workout was ad-hoc 1 day ago
    assert state.days_since_last_workout == 1
    # Unplanned event was captured
    assert len(state.unplanned_fatigue_events) == 1
    assert state.unplanned_fatigue_events[0].name == "Unplanned Arm Pump & Mobility"


def test_chest_exercise_alternation():
    """Verify that Push day alternates between Flat Bench and Incline Bench."""
    from src.telemetry.models import ExerciseSession, ExerciseSet

    push_template = WorkoutPlanTemplate(
        template_id="tpl_push",
        name="Push Day",
        order_index=0,
        exercise_ids=["ex_flat_bench", "ex_incline_db"],
        exercise_names=["Barbell Bench Press", "Incline Dumbbell Press"],
    )

    # Case 1: Last Push session did Flat Bench first -> today promotes Incline to #1
    last_push_flat_first = [
        LoggedWorkout(
            workout_id="w_push_1",
            template_id="tpl_push",
            name="Push Day",
            completed_at=datetime(2026, 11, 18, 10, 0),
            exercises=[
                ExerciseSession(exercise_id="ex_flat_bench", exercise_name="Barbell Bench Press"),
                ExerciseSession(exercise_id="ex_incline_db", exercise_name="Incline Dumbbell Press"),
            ],
        )
    ]

    state = calculate_rolling_split(
        templates=[push_template],
        recent_workouts=last_push_flat_first,
        reference_date=date(2026, 11, 21),
    )

    assert state.today_target_template.exercise_names[0] == "Incline Dumbbell Press"
    assert state.today_target_template.exercise_names[1] == "Barbell Bench Press"
    assert state.alternation_notes is not None
    assert "INCLINE_PRIORITY" in state.alternation_notes

    # Case 2: Last Push session did Incline Bench first -> today promotes Flat Bench to #1
    last_push_incline_first = [
        LoggedWorkout(
            workout_id="w_push_2",
            template_id="tpl_push",
            name="Push Day",
            completed_at=datetime(2026, 11, 18, 10, 0),
            exercises=[
                ExerciseSession(exercise_id="ex_incline_db", exercise_name="Incline Dumbbell Press"),
                ExerciseSession(exercise_id="ex_flat_bench", exercise_name="Barbell Bench Press"),
            ],
        )
    ]

    state2 = calculate_rolling_split(
        templates=[push_template],
        recent_workouts=last_push_incline_first,
        reference_date=date(2026, 11, 21),
    )

    assert state2.today_target_template.exercise_names[0] == "Barbell Bench Press"
    assert state2.today_target_template.exercise_names[1] == "Incline Dumbbell Press"
    assert state2.alternation_notes is not None
    assert "FLAT_BENCH_PRIORITY" in state2.alternation_notes


def test_canonical_5day_split_rotation():
    """Verify default canonical 5-day split: Push -> Pull -> Legs A -> Legs B -> Shoulders & Arms."""
    # 1. No templates provided -> starts with Push (index 0 of 5)
    state0 = calculate_rolling_split(
        templates=[],
        recent_workouts=[],
        reference_date=date(2026, 11, 21),
    )
    assert state0.total_templates == 5
    assert state0.current_index == 0
    assert state0.today_target_template.name == "Push"

    # 2. Push completed -> next is Pull (index 1)
    push_done = [
        LoggedWorkout(
            workout_id="w_push",
            template_id="push",
            name="Push",
            completed_at=datetime(2026, 11, 20, 10, 0),
        )
    ]
    state1 = calculate_rolling_split(templates=[], recent_workouts=push_done, reference_date=date(2026, 11, 21))
    assert state1.current_index == 1
    assert state1.today_target_template.name == "Pull"

    # 3. Pull completed -> next is Legs A (index 2)
    pull_done = [
        LoggedWorkout(
            workout_id="w_pull",
            template_id="pull",
            name="Pull Day",
            completed_at=datetime(2026, 11, 20, 10, 0),
        )
    ]
    state2 = calculate_rolling_split(templates=[], recent_workouts=pull_done, reference_date=date(2026, 11, 21))
    assert state2.current_index == 2
    assert state2.today_target_template.name == "Legs A"

    # 4. Legs A completed -> next is Legs B (index 3)
    lega_done = [
        LoggedWorkout(
            workout_id="w_lega",
            template_id=None,
            name="Legs A - Quad Focus",
            completed_at=datetime(2026, 11, 20, 10, 0),
        )
    ]
    state3 = calculate_rolling_split(templates=[], recent_workouts=lega_done, reference_date=date(2026, 11, 21))
    assert state3.current_index == 3
    assert state3.today_target_template.name == "Legs B"

    # 5. Legs B completed -> next is Shoulders & Arms (index 4)
    legb_done = [
        LoggedWorkout(
            workout_id="w_legb",
            template_id=None,
            name="Legs B - Hamstrings & Glutes",
            completed_at=datetime(2026, 11, 20, 10, 0),
        )
    ]
    state4 = calculate_rolling_split(templates=[], recent_workouts=legb_done, reference_date=date(2026, 11, 21))
    assert state4.current_index == 4
    assert "Shoulders & Arms" in state4.today_target_template.name

    # 6. Shoulders & Arms completed -> wraps back to Push (index 0)
    freq_done = [
        LoggedWorkout(
            workout_id="w_freq",
            template_id=None,
            name="Shoulder & Arm Pump",
            completed_at=datetime(2026, 11, 20, 10, 0),
        )
    ]
    state5 = calculate_rolling_split(templates=[], recent_workouts=freq_done, reference_date=date(2026, 11, 21))
    assert state5.current_index == 0
    assert state5.today_target_template.name == "Push"


def test_corrupted_error_strings_filtered_out():
    """Verify that backend exception strings are never treated as workouts."""
    corrupted_workouts = [
        LoggedWorkout(
            workout_id="w_err",
            template_id=None,
            name="Parameter specified as non-null is null",
            completed_at=datetime(2026, 11, 21, 9, 0),
        ),
        LoggedWorkout(
            workout_id="w_real_push",
            template_id=None,
            name="Push Session",
            completed_at=datetime(2026, 11, 20, 10, 0),
        ),
    ]
    state = calculate_rolling_split(templates=[], recent_workouts=corrupted_workouts, reference_date=date(2026, 11, 21))
    # Should ignore the error string and match w_real_push -> target is Pull
    assert state.current_index == 1
    assert state.today_target_template.name == "Pull"
    assert len(state.unplanned_fatigue_events) == 0
