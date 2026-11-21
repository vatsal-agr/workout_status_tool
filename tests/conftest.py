"""Test fixtures and mock telemetry payloads."""

from datetime import date, datetime, timedelta
import pytest

from src.telemetry.models import (
    BodyStat,
    CaliberTelemetry,
    DailySteps,
    ExerciseSession,
    ExerciseSet,
    GarminCardio,
    GarminDailyMetric,
    GarminHRV,
    GarminSleep,
    GarminTelemetry,
    LoggedWorkout,
    NutritionSummary,
    NutritionTargets,
    WorkoutPlanTemplate,
)


@pytest.fixture
def sample_templates():
    """Sample 3-day rolling workout templates: Push, Pull, Legs."""
    return [
        WorkoutPlanTemplate(
            template_id="tpl_push",
            name="Push Day (Chest/Shoulders/Triceps)",
            order_index=0,
            exercise_ids=["ex_bench", "ex_ohp"],
            exercise_names=["Barbell Bench Press", "Barbell Overhead Press"],
        ),
        WorkoutPlanTemplate(
            template_id="tpl_pull",
            name="Pull Day (Back/Biceps)",
            order_index=1,
            exercise_ids=["ex_pulldown"],
            exercise_names=["Lat Pulldown"],
        ),
        WorkoutPlanTemplate(
            template_id="tpl_legs",
            name="Legs Day (Quads/Hamstrings)",
            order_index=2,
            exercise_ids=["ex_squat", "ex_rdl"],
            exercise_names=["Barbell Back Squat", "Romanian Deadlift"],
        ),
    ]


@pytest.fixture
def sample_workouts_with_adhoc():
    """Sample workouts where Push was completed 3 days ago, and an ad-hoc session yesterday."""
    now = datetime(2026, 11, 21, 10, 0, 0)
    return [
        LoggedWorkout(
            workout_id="w_adhoc_1",
            template_id=None,  # Ad-hoc session
            name="Unplanned Arm Pump & Mobility",
            completed_at=now - timedelta(days=1),
        ),
        LoggedWorkout(
            workout_id="w_push_1",
            template_id="tpl_push",
            name="Push Day",
            completed_at=now - timedelta(days=3),
        ),
        LoggedWorkout(
            workout_id="w_legs_old",
            template_id="tpl_legs",
            name="Legs Day",
            completed_at=now - timedelta(days=5),
        ),
    ]


@pytest.fixture
def sample_bench_sessions():
    """Historical sessions for Bench Press simulating a stall condition."""
    now = datetime(2026, 11, 21, 10, 0, 0)
    return [
        ExerciseSession(
            exercise_id="ex_bench",
            exercise_name="Barbell Bench Press",
            performed_at=now - timedelta(days=14),
            sets=[
                ExerciseSet(set_number=1, weight_kg=95.0, reps=8, is_warmup=False),
                ExerciseSet(set_number=2, weight_kg=85.0, reps=10, is_warmup=False),
            ],
        ),
        ExerciseSession(
            exercise_id="ex_bench",
            exercise_name="Barbell Bench Press",
            performed_at=now - timedelta(days=7),
            sets=[
                ExerciseSet(set_number=1, weight_kg=95.0, reps=8, is_warmup=False),
                ExerciseSet(set_number=2, weight_kg=85.0, reps=10, is_warmup=False),
            ],
        ),
        ExerciseSession(
            exercise_id="ex_bench",
            exercise_name="Barbell Bench Press",
            performed_at=now - timedelta(days=3),
            sets=[
                ExerciseSet(set_number=1, weight_kg=95.0, reps=8, is_warmup=False),
                ExerciseSet(set_number=2, weight_kg=85.0, reps=9, is_warmup=False),
            ],
        ),
    ]


@pytest.fixture
def sample_garmin_green():
    """Garmin telemetry with high HRV, optimal sleep, and normal RHR (GREEN tier)."""
    today = date(2026, 11, 21)
    history = [
        GarminDailyMetric(
            log_date=today - timedelta(days=i),
            hrv_avg_ms=65.0,
            resting_heart_rate=52,
            sleep_total_hours=7.5,
        )
        for i in range(1, 8)
    ]
    return GarminTelemetry(
        hrv=GarminHRV(last_night_avg_ms=68.0, baseline_7d_ms=65.0, status="BALANCED"),
        sleep=GarminSleep(total_sleep_hours=7.8, deep_sleep_hours=1.8, rem_sleep_hours=2.0),
        cardio=GarminCardio(resting_heart_rate=50, body_battery_peak=95),
        history_7d=history,
        is_synced=True,
    )


@pytest.fixture
def sample_garmin_unsynced():
    """Garmin telemetry when watch hasn't synced (PENDING_WATCH_SYNC)."""
    return GarminTelemetry(
        hrv=GarminHRV(status="PENDING_WATCH_SYNC"),
        sleep=GarminSleep(),
        cardio=GarminCardio(),
        history_7d=[],
        is_synced=False,
    )
