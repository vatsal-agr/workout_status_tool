"""Biometric readiness classification and rolling baseline math."""

import math
from typing import List, Optional
from pydantic import BaseModel, Field

from src.telemetry.models import GarminDailyMetric, GarminTelemetry


class BiometricReadinessResult(BaseModel):
    """Output of the 7-day autonomic recovery and readiness classifier."""
    tier: str = "GREEN"  # GREEN, AMBER, RED, PENDING_WATCH_SYNC
    reason: str = ""
    today_hrv_ms: Optional[float] = None
    today_sleep_hours: Optional[float] = None
    today_rhr_bpm: Optional[int] = None
    hrv_7d_mean: Optional[float] = None
    hrv_7d_std: Optional[float] = None
    rhr_7d_mean: Optional[float] = None
    consecutive_amber_days: int = 0
    raw_flags: List[str] = Field(default_factory=list)


def calculate_mean_and_std(values: List[float]) -> (Optional[float], Optional[float]):
    """Calculate arithmetic mean and sample standard deviation."""
    valid_vals = [v for v in values if v is not None and not math.isnan(v)]
    if not valid_vals:
        return None, None
    n = len(valid_vals)
    mean = sum(valid_vals) / n
    if n < 2:
        return round(mean, 2), 0.0
    variance = sum((x - mean) ** 2 for x in valid_vals) / (n - 1)
    std = math.sqrt(variance)
    return round(mean, 2), round(std, 2)


def classify_readiness(garmin_data: GarminTelemetry) -> BiometricReadinessResult:
    """Classify readiness tier using 7-day rolling baselines and overnight telemetry.

    Tiers:
    - PENDING_WATCH_SYNC: Garmin data missing due to unsynced watch.
    - GREEN: HRV_today >= mu_HRV - 0.5*sigma_HRV AND Sleep_total >= 6.5 h.
    - AMBER: HRV_today < mu_HRV - 0.5*sigma_HRV OR Sleep_total < 5.5 h OR RHR_today >= mu_RHR + 4 bpm.
    - RED: >= 2 consecutive days in AMBER OR HRV_today < mu_HRV - 1.5*sigma_HRV.
    """
    today_hrv = garmin_data.hrv.last_night_avg_ms
    today_sleep = garmin_data.sleep.total_sleep_hours
    today_rhr = garmin_data.cardio.resting_heart_rate

    # If telemetry is unsynced or missing, return PENDING_WATCH_SYNC
    if today_hrv is None or today_sleep is None:
        return BiometricReadinessResult(
            tier="PENDING_WATCH_SYNC",
            reason="Watch sync pending or overnight biometric telemetry unavailable. Proceed with standard baseline targets.",
            today_hrv_ms=today_hrv,
            today_sleep_hours=today_sleep,
            today_rhr_bpm=today_rhr,
            raw_flags=["PENDING_WATCH_SYNC"],
        )

    # Calculate 7-day baselines from history
    hist = garmin_data.history_7d or []
    hrv_values = [d.hrv_avg_ms for d in hist if d.hrv_avg_ms is not None]
    rhr_values = [float(d.resting_heart_rate) for d in hist if d.resting_heart_rate is not None]

    hrv_mean, hrv_std = calculate_mean_and_std(hrv_values)
    rhr_mean, _ = calculate_mean_and_std(rhr_values)

    # Defaults if not enough historical days
    if hrv_mean is None:
        hrv_mean = today_hrv
        hrv_std = 5.0
    if hrv_std is None or hrv_std == 0.0:
        hrv_std = 5.0
    if rhr_mean is None and today_rhr is not None:
        rhr_mean = float(today_rhr)

    amber_reasons: List[str] = []
    red_reasons: List[str] = []

    # HRV checks against z-score
    amber_threshold_hrv = hrv_mean - (0.5 * hrv_std)
    red_threshold_hrv = hrv_mean - (1.5 * hrv_std)

    if today_hrv < red_threshold_hrv:
        red_reasons.append(
            f"Severe HRV drop: {today_hrv:.1f}ms < 1.5σ baseline floor ({red_threshold_hrv:.1f}ms)"
        )
    elif today_hrv < amber_threshold_hrv:
        amber_reasons.append(
            f"HRV dip: {today_hrv:.1f}ms < 0.5σ baseline floor ({amber_threshold_hrv:.1f}ms)"
        )

    # Sleep check
    if today_sleep < 5.5:
        amber_reasons.append(f"Short sleep: {today_sleep:.1f}h (<5.5h threshold)")

    # Resting Heart Rate check
    if today_rhr is not None and rhr_mean is not None:
        if today_rhr >= (rhr_mean + 4.0):
            amber_reasons.append(
                f"Elevated RHR: {today_rhr}bpm >= 7d mean ({rhr_mean:.1f}bpm + 4bpm)"
            )

    # Check consecutive amber days from history
    consecutive_amber = 0
    for day in hist[:3]:  # Check trailing recent days
        day_hrv = day.hrv_avg_ms
        day_sleep = day.sleep_total_hours
        if day_hrv and day_hrv < amber_threshold_hrv or (day_sleep and day_sleep < 5.5):
            consecutive_amber += 1
        else:
            break

    if len(amber_reasons) > 0 and consecutive_amber >= 1:
        red_reasons.append(
            f"Compounded autonomic fatigue: {consecutive_amber + 1} consecutive days with degraded markers"
        )

    # Determine final tier
    if red_reasons:
        tier = "RED"
        reason = " | ".join(red_reasons)
    elif amber_reasons or today_sleep < 6.5:
        tier = "AMBER"
        reason = " | ".join(amber_reasons) if amber_reasons else f"Sub-optimal sleep: {today_sleep:.1f}h (<6.5h)"
    else:
        tier = "GREEN"
        reason = f"Autonomic recovery optimal. HRV ({today_hrv:.1f}ms) within baseline range, sleep ({today_sleep:.1f}h) >= 6.5h."

    return BiometricReadinessResult(
        tier=tier,
        reason=reason,
        today_hrv_ms=today_hrv,
        today_sleep_hours=today_sleep,
        today_rhr_bpm=today_rhr,
        hrv_7d_mean=hrv_mean,
        hrv_7d_std=hrv_std,
        rhr_7d_mean=rhr_mean,
        consecutive_amber_days=consecutive_amber,
        raw_flags=red_reasons + amber_reasons,
    )
