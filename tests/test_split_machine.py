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
