"""Tests for Caliber MCP payload parsing, nested template extraction, and parameter adaptation."""

from datetime import date
from unittest.mock import MagicMock
import pytest

from src.telemetry.caliber import CaliberMCPClient
from src.telemetry.models import WorkoutPlanTemplate, LoggedWorkout


def test_parse_nested_workout_plans():
    """Verify parsing of real Caliber getWorkoutPlans structure: plans -> workouts -> exercises."""
    client = CaliberMCPClient(refresh_token="mock_token")
    mock_payload = {
        "clientId": "fe1487bf-11b2-47f5-90ea-509aba10b141",
        "plans": [
            {
                "title": "Gym Workouts (6/19/25)",
                "description": None,
                "sortOrder": 0,
                "workouts": [
                    {
                        "workoutId": "w_legs_1",
                        "title": "Workout 1: Legs A",
                        "sortOrder": 0,
                        "exercises": [
                            {"exerciseId": "41b29608-378a-4225-8fc2-f9d210ad4681", "exerciseName": "Barbell Back Squat"},
                            {"exerciseId": "ex_rdl", "exerciseName": "Romanian Deadlift"},
                        ],
                    },
                    {
                        "workoutId": "w_push_1",
                        "title": "Workout 2: Push",
                        "sortOrder": 1,
                        "exercises": [
                            {"exerciseId": "ex_bench", "exerciseName": "Barbell Bench Press"},
                            {"exerciseId": "ex_incline", "exerciseName": "Incline Dumbbell Press"},
                        ],
                    },
                ],
            }
        ],
    }

    client._call_tool = MagicMock(return_value=mock_payload)
    templates = client.get_workout_plans()

    assert len(templates) == 2
    assert templates[0].name == "Workout 1: Legs A"
    assert templates[0].template_id == "w_legs_1"
    assert len(templates[0].exercise_ids) == 2
    assert "Barbell Back Squat" in templates[0].exercise_names

    assert templates[1].name == "Workout 2: Push"
    assert templates[1].template_id == "w_push_1"


def test_parse_real_get_workouts_payload():
    """Verify parsing of real Caliber getWorkouts payload with calendarItemId, workoutTitle, and status."""
    client = CaliberMCPClient(refresh_token="mock_token")
    mock_payload = {
        "clientId": "fe1487bf-11b2-47f5-90ea-509aba10b141",
        "from": "2026-11-08",
        "to": "2026-11-23",
        "weightUnit": "KG",
        "workouts": [
            {
                "calendarItemId": "dfb2945f-9308-4f37-8961-3bcac49596cf",
                "workoutId": "tpl_push_id",
                "date": "2026-11-12",
                "workoutTitle": "Push Day",
                "status": "COMPLETED",
                "exercises": [],
            },
            {
                "calendarItemId": "pending_item",
                "date": "2026-11-20",
                "workoutTitle": "Unfinished Session",
                "status": "IN_PROGRESS",
                "exercises": [],
            },
        ],
    }

    client._call_tool = MagicMock(return_value=mock_payload)
    workouts = client.get_workouts(start_date=date(2026, 11, 8), end_date=date(2026, 11, 22))

    # Only completed workouts should be retained
    assert len(workouts) == 1
    assert workouts[0].workout_id == "dfb2945f-9308-4f37-8961-3bcac49596cf"
    assert workouts[0].name == "Push Day"
    assert workouts[0].template_id == "tpl_push_id"
    assert workouts[0].completed_at.date() == date(2026, 11, 12)


def test_build_tool_args_date_adaptation():
    """Verify that _build_tool_args correctly maps date parameters to 'from' and 'to'."""
    client = CaliberMCPClient(refresh_token="mock_token")
    client.tool_schemas = {
        "getNutrition": {
            "type": "object",
            "properties": {
                "from": {"type": "string", "format": "date"},
                "to": {"type": "string", "format": "date"},
            },
            "required": ["from", "to"],
        },
        "getExerciseSessions": {
            "type": "object",
            "properties": {
                "exerciseId": {"type": "string"},
                "from": {"type": "string"},
                "to": {"type": "string"},
            },
            "required": ["exerciseId", "from", "to"],
        },
    }

    # Test getNutrition mapping from startDate/endDate
    args = client._build_tool_args("getNutrition", {"startDate": "2026-11-15", "endDate": "2026-11-22"})
    assert args == {"from": "2026-11-15", "to": "2026-11-22"}

    # Test getExerciseSessions mapping
    args2 = client._build_tool_args(
        "getExerciseSessions",
        {"exerciseId": "41b29608", "from": "2026-10-01", "to": "2026-11-22"},
    )
    assert args2 == {"exerciseId": "41b29608", "from": "2026-10-01", "to": "2026-11-22"}
