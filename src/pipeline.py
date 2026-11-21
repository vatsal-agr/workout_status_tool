"""Master Orchestrator: Headless Telemetry Pipeline & Coaching Engine."""

import logging
import sys
from datetime import date
from typing import Optional

from src.analytics.progression import compute_exercise_progression
from src.analytics.readiness import classify_readiness
from src.analytics.split_machine import calculate_rolling_split
from src.auth.rotation import GitHubSecretRotator
from src.config import settings
from src.sheets.client import GoogleSheetsService
from src.sheets.seeder import SheetsSeeder
from src.sheets.writer import SheetsDataWriter
from src.telemetry.caliber import CaliberMCPClient
from src.telemetry.garmin import GarminPersonalAPIClient

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pipeline")


def run_pipeline(reference_date: Optional[date] = None) -> bool:
    """Execute the complete end-to-end telemetry ETL, analytics, and sync workflow."""
    ref_date = reference_date or date.today()
    logger.info("=== Starting Daily Coaching Telemetry Pipeline (%s) ===", ref_date)

    # 1. Caliber Ingestion & Token Refresh
    logger.info("Step 1/6: Fetching Caliber MCP Telemetry...")
    caliber_client = CaliberMCPClient()
    try:
        caliber_client.refresh_access_token()
        caliber_data = caliber_client.fetch_all_telemetry(reference_date=ref_date)
        logger.info(
            "Caliber Telemetry fetched: %d active templates, %d workouts (14d)",
            len(caliber_data.templates),
            len(caliber_data.completed_workouts_14d),
        )
    except Exception as e:
        logger.error("Failed to fetch Caliber telemetry: %s", e)
        raise
    finally:
        caliber_client.close()

    # 2. Garmin Ingestion
    logger.info("Step 2/6: Fetching Garmin Biometrics...")
    garmin_client = GarminPersonalAPIClient()
    try:
        garmin_data = garmin_client.fetch_all_telemetry(target_date=ref_date)
        logger.info(
            "Garmin Telemetry fetched: HRV=%s ms, Sleep=%s h, Synced=%s",
            garmin_data.hrv.last_night_avg_ms,
            garmin_data.sleep.total_sleep_hours,
            garmin_data.is_synced,
        )
    except Exception as e:
        logger.error("Failed to fetch Garmin biometrics: %s", e)
        raise

    # 3. Deterministic Analytics: Rolling Split State Machine
    logger.info("Step 3/6: Computing Rolling Split & Ad-Hoc Sessions...")
    split_state = calculate_rolling_split(
        templates=caliber_data.templates,
        recent_workouts=caliber_data.completed_workouts_14d,
        reference_date=ref_date,
    )
    logger.info(
        "Target Split: '%s' (Cycle position %d/%d). Days since last: %s, days since same: %s",
        split_state.today_target_template.name,
        split_state.current_index + 1,
        split_state.total_templates,
        split_state.days_since_last_workout,
        split_state.days_since_same_split,
    )

    # 4. Deterministic Analytics: Exercise Progression & Stall Detection
    logger.info("Step 4/6: Computing Exercise Progression & Stall Matrix...")
    progressions = []
    for ex_id in split_state.today_target_template.exercise_ids:
        sessions = caliber_data.recent_exercise_sessions.get(ex_id, [])
        # Find exercise name
        ex_name = ex_id
        if sessions:
            ex_name = sessions[0].exercise_name
        elif split_state.today_target_template.exercise_names:
            idx = split_state.today_target_template.exercise_ids.index(ex_id)
            if idx < len(split_state.today_target_template.exercise_names):
                ex_name = split_state.today_target_template.exercise_names[idx]

        metrics = compute_exercise_progression(
            exercise_id=ex_id,
            exercise_name=ex_name,
            historical_sessions=sessions,
        )
        progressions.append(metrics)
        logger.info(
            " - %s: stalls=%d, stall_flag=%s, last_tonnage=%.1f kg",
            metrics.exercise_name,
            metrics.consecutive_stalls,
            metrics.stall_flag,
            metrics.last_session.total_working_tonnage if metrics.last_session else 0.0,
        )

    # 5. Deterministic Analytics: Biometric Readiness Tier
    logger.info("Step 5/6: Classifying Biometric Readiness Tier...")
    readiness = classify_readiness(garmin_data)
    logger.info("Readiness Tier: [%s] -> %s", readiness.tier, readiness.reason)

    # 6. Google Sheets Synchronization
    logger.info("Step 6/6: Updating Google Sheet Tabs...")
    try:
        sheets_service = GoogleSheetsService()
        sheets_service.authenticate()

        # Seed rule control tabs if missing
        seeder = SheetsSeeder(service=sheets_service)
        seeder.seed_control_tabs_if_missing()

        # Write Briefing_Context and Telemetry_Archive
        writer = SheetsDataWriter(sheets_service=sheets_service)
        writer.write_briefing_context(
            split_state=split_state,
            progressions=progressions,
            readiness=readiness,
            caliber_data=caliber_data,
            garmin_data=garmin_data,
        )
        writer.write_telemetry_archive(
            caliber_data=caliber_data,
            garmin_data=garmin_data,
        )
        logger.info("Google Sheets synchronization completed successfully.")
    except Exception as e:
        logger.error("Failed to write to Google Sheets: %s", e)
        raise

    # 7. Automated OAuth Refresh Token Rotation (PyNaCl + GitHub API)
    if caliber_client.new_refresh_token and settings.GH_PAT and settings.GITHUB_REPOSITORY:
        logger.info("Post-Sync: Rotating CALIBER_REFRESH_TOKEN in GitHub Actions Secrets...")
        rotator = GitHubSecretRotator()
        rotated = rotator.update_secret(
            secret_name="CALIBER_REFRESH_TOKEN",
            secret_value=caliber_client.new_refresh_token,
        )
        if rotated:
            logger.info("OAuth token successfully rotated in GitHub Actions Secrets.")
        else:
            logger.warning("Secret rotation did not complete successfully.")

    logger.info("=== Daily Pipeline Finished Successfully ===")
    return True


if __name__ == "__main__":
    try:
        run_pipeline()
    except Exception as exc:
        logger.critical("Pipeline run aborted due to error: %s", exc, exc_info=True)
        sys.exit(1)
