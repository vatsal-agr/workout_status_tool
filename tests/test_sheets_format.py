"""Tests for Google Sheets Briefing_Context formatting and row compactness."""

from datetime import date, datetime
from unittest.mock import MagicMock
import pytest

from src.analytics.progression import ExerciseProgressionMetrics
from src.analytics.readiness import BiometricReadinessResult
from src.analytics.split_machine import RollingSplitState
from src.sheets.writer import SheetsDataWriter
from src.telemetry.models import CaliberTelemetry, GarminTelemetry, WorkoutPlanTemplate


def test_briefing_context_structure_and_compactness():
    """Verify that Briefing_Context generates clean tabular rows under 150 rows."""
    mock_service = MagicMock()
    mock_worksheet = MagicMock()
    mock_service.get_or_create_worksheet.return_value = mock_worksheet

    writer = SheetsDataWriter(sheets_service=mock_service)

    split_state = RollingSplitState(
        today_target_template=WorkoutPlanTemplate(
            template_id="t1",
            name="Push Day",
            exercise_ids=["ex1"],
            exercise_names=["Bench Press"],
        ),
        current_index=0,
        total_templates=3,
        days_since_last_workout=2,
        days_since_same_split=5,
    )

    progressions = [
        ExerciseProgressionMetrics(
            exercise_id="ex1",
            exercise_name="Barbell Bench Press",
            consecutive_stalls=0,
            stall_flag=False,
            all_sets_hit_top_rep=True,
        )
    ]

    readiness = BiometricReadinessResult(
        tier="GREEN",
        reason="Recovery optimal",
        today_hrv_ms=65.0,
        today_sleep_hours=7.5,
        today_rhr_bpm=52,
    )

    caliber_data = CaliberTelemetry()
    garmin_data = GarminTelemetry(is_synced=True)

    writer.write_briefing_context(
        split_state=split_state,
        progressions=progressions,
        readiness=readiness,
        caliber_data=caliber_data,
        garmin_data=garmin_data,
    )

    assert mock_worksheet.clear.called
    assert mock_worksheet.update.called

    written_rows = mock_worksheet.update.call_args[0][0]
    assert len(written_rows) < 150  # Must be compact for Gemini Scheduled Action
    # Verify key sections exist
    flat_text = " ".join([str(cell) for row in written_rows for cell in row])
    assert "SYSTEM METADATA & STATUS" in flat_text
    assert "BIOMETRIC RECOVERY (GARMIN)" in flat_text
    assert "NUTRITION & DAILY ACTIVITY (CALIBER)" in flat_text
    assert "TODAY'S TARGET SPLIT" in flat_text
    assert "EXERCISE PROGRESSION & STALL MATRIX" in flat_text
    assert "Barbell Bench Press" in flat_text
