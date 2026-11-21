"""Google Sheets data writer for Briefing_Context and Telemetry_Archive tabs."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import gspread

from src.analytics.progression import ExerciseProgressionMetrics
from src.analytics.readiness import BiometricReadinessResult
from src.analytics.split_machine import RollingSplitState
from src.sheets.client import GoogleSheetsService
from src.telemetry.models import CaliberTelemetry, GarminTelemetry

logger = logging.getLogger(__name__)


class SheetsDataWriter:
    """Formats and writes structured telemetry and context to Google Sheets."""

    def __init__(self, sheets_service: Optional[GoogleSheetsService] = None):
        self.service = sheets_service or GoogleSheetsService()

    def write_briefing_context(
        self,
        split_state: RollingSplitState,
        progressions: List[ExerciseProgressionMetrics],
        readiness: BiometricReadinessResult,
        caliber_data: CaliberTelemetry,
        garmin_data: GarminTelemetry,
    ) -> None:
        """Overwrite the Briefing_Context tab with compact, plain-text key-value blocks and flat tables."""
        worksheet = self.service.get_or_create_worksheet("Briefing_Context", rows=120, cols=8)

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        # Extract wearable & movement telemetry
        recent_steps = caliber_data.step_logs_7d[-1].step_count if caliber_data.step_logs_7d else 0
        cardio_count = len(garmin_data.cardio.activities_24h) if garmin_data.cardio else 0
        deep_sleep = garmin_data.sleep.deep_sleep_hours if garmin_data.sleep else None
        rem_sleep = garmin_data.sleep.rem_sleep_hours if garmin_data.sleep else None
        body_battery = garmin_data.cardio.body_battery_peak if garmin_data.cardio else None

        rows: List[List[Any]] = [
            # Header Section
            ["### 1. SYSTEM METADATA & STATUS", "", "", "", "", "", ""],
            ["last_synced_utc", now_utc, "", "", "", "", ""],
            ["target_briefing_time", "11:00 AM IST", "", "", "", "", ""],
            ["biometric_readiness_tier", readiness.tier, "", "", "", "", ""],
            ["readiness_rationale", readiness.reason, "", "", "", "", ""],
            ["", "", "", "", "", "", ""],

            # 24/7 Wearable Autonomic Telemetry
            ["### 2. 24/7 AUTONOMIC RECOVERY & PHYSIOLOGICAL DASHBOARD (GARMIN)", "", "", "", "", "", ""],
            ["today_hrv_avg_ms", readiness.today_hrv_ms if readiness.today_hrv_ms is not None else "PENDING_SYNC", "", "", "", "", ""],
            ["hrv_7d_baseline_mean", readiness.hrv_7d_mean or "N/A", "", "", "", "", ""],
            ["hrv_7d_baseline_std", readiness.hrv_7d_std or "N/A", "", "", "", "", ""],
            ["sleep_duration_hours", readiness.today_sleep_hours if readiness.today_sleep_hours is not None else "PENDING_SYNC", "", "", "", "", ""],
            ["deep_sleep_tissue_repair_hours", deep_sleep if deep_sleep is not None else "N/A", "", "", "", "", ""],
            ["rem_sleep_neural_repair_hours", rem_sleep if rem_sleep is not None else "N/A", "", "", "", "", ""],
            ["resting_heart_rate_bpm", readiness.today_rhr_bpm if readiness.today_rhr_bpm is not None else "N/A", "", "", "", "", ""],
            ["rhr_7d_baseline_mean", readiness.rhr_7d_mean or "N/A", "", "", "", "", ""],
            ["body_battery_peak", body_battery if body_battery is not None else "N/A", "", "", "", "", ""],
            ["garmin_sync_status", "SYNCED" if garmin_data.is_synced else "PENDING_WATCH_SYNC", "", "", "", "", ""],
            ["", "", "", "", "", "", ""],

            # Wearable Movement & Situational Strain
            ["### 3. WEARABLE STRAIN & 24/7 SITUATIONAL CONTEXT (GARMIN & CALIBER)", "", "", "", "", "", ""],
            ["yesterday_step_count", recent_steps, "", "", "", "", ""],
            ["yesterday_cardio_sessions_logged", cardio_count, "", "", "", "", ""],
            ["systemic_wearable_strain_call", (
                "High NEAT/step volume - monitor lower back/legs fatigue" if recent_steps > 12000
                else "Moderate activity - good recovery environment" if recent_steps >= 6000
                else "Low physical activity - low fatigue expenditure"
            ), "", "", "", "", ""],
            ["", "", "", "", "", "", ""],

            # Rolling Split Target
            ["### 4. TODAY'S TARGET SPLIT", "", "", "", "", "", ""],
            ["target_split_name", split_state.today_target_template.name, "", "", "", "", ""],
            ["split_cycle_position", f"{split_state.current_index + 1} of {split_state.total_templates}", "", "", "", "", ""],
            ["days_since_last_workout", split_state.days_since_last_workout if split_state.days_since_last_workout is not None else "N/A", "", "", "", "", ""],
            ["days_since_same_split", split_state.days_since_same_split if split_state.days_since_same_split is not None else "N/A", "", "", "", "", ""],
            ["chest_exercise_alternation", split_state.alternation_notes or "N/A", "", "", "", "", ""],
            ["unplanned_fatigue_count", len(split_state.unplanned_fatigue_events), "", "", "", "", ""],
            ["unplanned_events_summary", "; ".join([e.name for e in split_state.unplanned_fatigue_events]) or "None", "", "", "", "", ""],
            ["", "", "", "", "", "", ""],

            # Exercise Target Table
            ["### 5. EXERCISE PROGRESSION & STALL MATRIX", "", "", "", "", "", ""],
            [
                "Exercise Name",
                "Last Performance (Sets x Reps @ kg)",
                "Working Tonnage (kg)",
                "Top e1RM (kg)",
                "Consecutive Stalls",
                "Stall Flag",
                "Ceiling Hit",
            ],
        ]

        for p in progressions:
            last_perf = "None logged"
            tonnage = "0"
            top_e1rm = "0"
            if p.last_session and p.last_session.working_sets:
                sets_str = ", ".join([f"{s.weight_kg}kg x {s.reps}" for s in p.last_session.working_sets])
                last_perf = sets_str
                tonnage = str(p.last_session.total_working_tonnage)
                top_e1rm = str(p.last_session.top_e1rm)

            rows.append([
                p.exercise_name,
                last_perf,
                tonnage,
                top_e1rm,
                p.consecutive_stalls,
                "YES - STALLED" if p.stall_flag else "NO",
                "YES" if p.all_sets_hit_top_rep else "NO",
            ])

        # Clear existing sheet and write updated grid
        worksheet.clear()
        worksheet.update(rows)
        logger.info("Successfully updated 'Briefing_Context' tab in Google Sheets.")

    def write_telemetry_archive(
        self,
        caliber_data: CaliberTelemetry,
        garmin_data: GarminTelemetry,
    ) -> None:
        """Overwrite Telemetry_Archive with 14-day rolling historical metrics."""
        worksheet = self.service.get_or_create_worksheet("Telemetry_Archive", rows=60, cols=10)

        header = [
            "Date",
            "Workout Name",
            "Template ID",
            "Calories",
            "Protein (g)",
            "Steps",
            "HRV Avg (ms)",
            "Sleep (hours)",
            "RHR (bpm)",
        ]
        rows: List[List[Any]] = [header]

        # Index Caliber nutrition and steps by date
        nutr_map = {n.log_date.isoformat(): n for n in caliber_data.nutrition_logs_7d}
        steps_map = {s.log_date.isoformat(): s.step_count for s in caliber_data.step_logs_7d}
        garmin_map = {d.log_date.isoformat(): d for d in garmin_data.history_7d}

        # List completed workouts in 14-day window
        for w in caliber_data.completed_workouts_14d:
            w_date_str = w.completed_at.date().isoformat()
            nutr = nutr_map.get(w_date_str)
            steps = steps_map.get(w_date_str, "")
            g_day = garmin_map.get(w_date_str)

            rows.append([
                w_date_str,
                w.name,
                w.template_id or "Ad-hoc",
                nutr.calories if nutr else "",
                nutr.protein_g if nutr else "",
                steps,
                g_day.hrv_avg_ms if g_day else "",
                g_day.sleep_total_hours if g_day else "",
                g_day.resting_heart_rate if g_day else "",
            ])

        worksheet.clear()
        worksheet.update(rows)
        logger.info("Successfully updated 'Telemetry_Archive' tab in Google Sheets.")
