"""Tests for Epley 1RM math, working set tonnage, and consecutive stall detection."""

from datetime import datetime, timedelta
import pytest

from src.analytics.progression import (
    analyze_exercise_session,
    calculate_epley_e1rm,
    compute_exercise_progression,
)
from src.telemetry.models import ExerciseSession, ExerciseSet


def test_epley_e1rm_formula():
    """Verify standard Epley e1RM formula: w * (1 + r / 30)."""
    assert calculate_epley_e1rm(100.0, 1) == 100.0
    assert calculate_epley_e1rm(100.0, 10) == 133.33
    assert calculate_epley_e1rm(80.0, 6) == 96.0
    assert calculate_epley_e1rm(0.0, 10) == 0.0
    assert calculate_epley_e1rm(100.0, 0) == 0.0


def test_working_set_tonnage_and_warmup_exclusion():
    """Warmup sets should be excluded from working tonnage and rep counts."""
    session = ExerciseSession(
        exercise_id="ex1",
        exercise_name="Bench",
        performed_at=datetime.now(),
        sets=[
            ExerciseSet(set_number=1, weight_kg=40.0, reps=10, is_warmup=True),
            ExerciseSet(set_number=2, weight_kg=70.0, reps=5, is_warmup=True),
            ExerciseSet(set_number=3, weight_kg=100.0, reps=8, is_warmup=False),
            ExerciseSet(set_number=4, weight_kg=90.0, reps=10, is_warmup=False),
        ],
    )
    summary = analyze_exercise_session(session)
    assert len(summary.working_sets) == 2
    assert summary.total_working_reps == 18
    # 100 * 8 = 800; 90 * 10 = 900; Total = 1700
    assert summary.total_working_tonnage == 1700.0
    assert summary.top_set_weight_kg == 100.0
    assert summary.top_set_reps == 8


def test_consecutive_stall_detection(sample_bench_sessions):
    """When top set load does not increase and total reps do not increase across 3 sessions,

    consecutive_stalls should be 2, triggering stall_flag=True.
    """
    metrics = compute_exercise_progression(
        exercise_id="ex_bench",
        exercise_name="Barbell Bench Press",
        historical_sessions=sample_bench_sessions,
        target_top_reps=10,
    )
    # Session 1: 95kg x 8, 85kg x 10 (top 95kg, total reps 18)
    # Session 2: 95kg x 8, 85kg x 10 (top 95kg, total reps 18) -> stall #1
    # Session 3: 95kg x 8, 85kg x 9  (top 95kg, total reps 17) -> stall #2
    assert metrics.consecutive_stalls == 2
    assert metrics.stall_flag is True
    assert metrics.reps_delta == -1
    assert metrics.weight_delta_kg == 0.0


def test_progression_advancement():
    """If top set load or reps increase, consecutive stalls should be 0."""
    now = datetime.now()
    s1 = ExerciseSession(
        exercise_id="ex1",
        exercise_name="Squat",
        performed_at=now - timedelta(days=7),
        sets=[ExerciseSet(set_number=1, weight_kg=100.0, reps=5, is_warmup=False)],
    )
    s2 = ExerciseSession(
        exercise_id="ex1",
        exercise_name="Squat",
        performed_at=now,
        sets=[ExerciseSet(set_number=1, weight_kg=102.5, reps=5, is_warmup=False)],
    )
    metrics = compute_exercise_progression(
        exercise_id="ex1",
        exercise_name="Squat",
        historical_sessions=[s1, s2],
    )
    assert metrics.consecutive_stalls == 0
    assert metrics.stall_flag is False
    assert metrics.weight_delta_kg == 2.5
