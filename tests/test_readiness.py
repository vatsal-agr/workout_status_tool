"""Tests for the 7-day rolling biometric readiness and autonomic recovery classifier."""

from datetime import date, timedelta
import pytest

from src.analytics.readiness import classify_readiness
from src.telemetry.models import (
    GarminCardio,
    GarminDailyMetric,
    GarminHRV,
    GarminSleep,
    GarminTelemetry,
)


def test_green_readiness_tier(sample_garmin_green):
    """When HRV is high, sleep is >= 6.5h, and RHR is normal, tier is GREEN."""
    result = classify_readiness(sample_garmin_green)
    assert result.tier == "GREEN"
    assert result.today_hrv_ms == 68.0
    assert result.today_sleep_hours == 7.8
    assert "optimal" in result.reason.lower()


def test_amber_readiness_on_short_sleep(sample_garmin_green):
    """When sleep duration dips below 5.5h, tier degrades to AMBER."""
    sample_garmin_green.sleep.total_sleep_hours = 5.0
    result = classify_readiness(sample_garmin_green)
    assert result.tier == "AMBER"
    assert any("short sleep" in flag.lower() for flag in result.raw_flags)


def test_amber_readiness_on_elevated_rhr(sample_garmin_green):
    """When RHR is >= 4 bpm over 7d mean, tier degrades to AMBER."""
    # Baseline mean in sample_garmin_green is 52 bpm
    sample_garmin_green.cardio.resting_heart_rate = 57  # +5 bpm
    result = classify_readiness(sample_garmin_green)
    assert result.tier == "AMBER"
    assert any("elevated rhr" in flag.lower() for flag in result.raw_flags)


def test_red_readiness_on_severe_hrv_crash(sample_garmin_green):
    """When today's HRV drops below 1.5 sigma from 7d baseline, tier degrades to RED."""
    # Baseline mean = 65, std = 5 (default). Red threshold = 65 - 1.5*5 = 57.5 ms
    sample_garmin_green.hrv.last_night_avg_ms = 45.0
    result = classify_readiness(sample_garmin_green)
    assert result.tier == "RED"
    assert any("severe hrv drop" in flag.lower() for flag in result.raw_flags)


def test_pending_watch_sync_fallback(sample_garmin_unsynced):
    """When watch sync hasn't occurred, classifier returns PENDING_WATCH_SYNC."""
    result = classify_readiness(sample_garmin_unsynced)
    assert result.tier == "PENDING_WATCH_SYNC"
    assert "pending" in result.reason.lower()
