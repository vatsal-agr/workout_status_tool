"""Garmin Connect Personal API Client with Graceful Watch-Sync Fallback."""

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
import httpx

from src.config import settings
from src.telemetry.models import (
    GarminCardio,
    GarminDailyMetric,
    GarminHRV,
    GarminSleep,
    GarminTelemetry,
)

logger = logging.getLogger(__name__)


class GarminPersonalAPIClient:
    """Client for Garmin Connect Personal API using static bearer token authentication."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.api_key = api_key or settings.GARMIN_PERSONAL_API_KEY
        self.base_url = (base_url or settings.GARMIN_API_BASE_URL).rstrip("/")

    def _get_headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise ValueError(
                "Garmin API Key missing. Please set GARMIN_PERSONAL_API_KEY in environment or secrets."
            )
        auth_val = self.api_key.strip()
        if not (auth_val.startswith("Bearer ") or auth_val.startswith("OAuth ")):
            auth_val = f"Bearer {auth_val}"
        return {
            "Authorization": auth_val,
            "Accept": "application/json",
        }

    def _get_window_params(
        self, target_date: date, window_days_back: int = 1, window_days_forward: int = 1
    ) -> Dict[str, int]:
        """Garmin Health API strictly requires both uploadStartTimeInSeconds and uploadEndTimeInSeconds."""
        start_dt = datetime.combine(target_date - timedelta(days=window_days_back), datetime.min.time())
        end_dt = datetime.combine(target_date + timedelta(days=window_days_forward), datetime.max.time())
        return {
            "uploadStartTimeInSeconds": int(start_dt.timestamp()),
            "uploadEndTimeInSeconds": int(end_dt.timestamp()),
        }

    def _request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        with httpx.Client(timeout=15.0) as client:
            response = client.get(url, headers=self._get_headers(), params=params or {})
            if response.status_code == 404:
                return None
            if response.status_code >= 400:
                logger.warning("Garmin GET %s failed [%d]: %s", url, response.status_code, response.text[:250])
            response.raise_for_status()
            return response.json()

    def get_hrv_summary(self, target_date: date) -> GarminHRV:
        """Fetch overnight HRV summary for target date.

        Handles unsynced watch condition gracefully.
        """
        try:
            params = self._get_window_params(target_date, window_days_back=1, window_days_forward=1)
            data = self._request("wellness-api/rest/hrv", params)
            if not data or not isinstance(data, list) or len(data) == 0:
                return GarminHRV(status="PENDING_WATCH_SYNC")

            target_str = target_date.isoformat()
            item = next((d for d in data if d.get("calendarDate") == target_str), data[-1])
            last_night_avg = item.get("lastNightAvg") or item.get("hrvSummary", {}).get("weeklyAvg")
            status = item.get("status", "BALANCED") if last_night_avg else "PENDING_WATCH_SYNC"

            return GarminHRV(
                last_night_avg_ms=float(last_night_avg) if last_night_avg else None,
                baseline_7d_ms=float(item.get("baseline", {}).get("balancedLow", 0)) if item.get("baseline") else None,
                status=status,
            )
        except Exception as e:
            logger.warning("Could not fetch Garmin HRV summary for %s: %s", target_date, e)
            return GarminHRV(status="PENDING_WATCH_SYNC")

    def get_sleep_summary(self, target_date: date) -> GarminSleep:
        """Fetch sleep metrics for overnight leading into target date."""
        try:
            params = self._get_window_params(target_date, window_days_back=1, window_days_forward=1)
            data = self._request("wellness-api/rest/sleeps", params)
            if not data or not isinstance(data, list) or len(data) == 0:
                return GarminSleep()

            target_str = target_date.isoformat()
            item = next((d for d in data if d.get("calendarDate") == target_str), data[-1])
            sleep_duration_seconds = item.get("durationInSeconds", 0)
            deep_seconds = item.get("deepSleepDurationInSeconds", 0)
            rem_seconds = item.get("remSleepInSeconds", 0)
            light_seconds = item.get("lightSleepDurationInSeconds", 0)
            awake_seconds = item.get("awakeDurationInSeconds", 0)

            return GarminSleep(
                total_sleep_hours=round(sleep_duration_seconds / 3600.0, 2) if sleep_duration_seconds else None,
                deep_sleep_hours=round(deep_seconds / 3600.0, 2) if deep_seconds else None,
                rem_sleep_hours=round(rem_seconds / 3600.0, 2) if rem_seconds else None,
                light_sleep_hours=round(light_seconds / 3600.0, 2) if light_seconds else None,
                awake_hours=round(awake_seconds / 3600.0, 2) if awake_seconds else None,
            )
        except Exception as e:
            logger.warning("Could not fetch Garmin sleep summary for %s: %s", target_date, e)
            return GarminSleep()

    def get_cardio_metrics(self, target_date: date) -> GarminCardio:
        """Fetch resting heart rate, morning body battery, and 24h activities."""
        rhr: Optional[int] = None
        body_battery: Optional[int] = None
        activities: List[Dict[str, Any]] = []

        try:
            params = self._get_window_params(target_date, window_days_back=1, window_days_forward=1)
            daily_data = self._request("wellness-api/rest/dailies", params)
            if daily_data and isinstance(daily_data, list) and len(daily_data) > 0:
                target_str = target_date.isoformat()
                day_item = next((d for d in daily_data if d.get("calendarDate") == target_str), daily_data[-1])
                rhr = day_item.get("restingHeartRateInBeatsPerMinute")
                body_battery = day_item.get("bodyBatteryHighestValue")
        except Exception as e:
            logger.warning("Could not fetch Garmin dailies for %s: %s", target_date, e)

        try:
            act_params = self._get_window_params(target_date - timedelta(days=1), window_days_back=1, window_days_forward=1)
            act_data = self._request("activity-api/rest/activities", act_params)
            if act_data and isinstance(act_data, list):
                activities = act_data
        except Exception as e:
            logger.warning("Could not fetch Garmin activities: %s", e)

        return GarminCardio(
            resting_heart_rate=rhr,
            body_battery_peak=body_battery,
            activities_24h=activities,
        )

    def fetch_7d_history(self, reference_date: date) -> List[GarminDailyMetric]:
        """Fetch trailing 7 days of daily metrics to calculate baseline means and standard deviations."""
        history: List[GarminDailyMetric] = []
        for i in range(1, 8):
            day = reference_date - timedelta(days=i)
            # In a full production implementation, bulk range endpoints are preferred.
            # Here we query daily summaries for the 7d baseline calculation.
            hrv_metric = self.get_hrv_summary(day)
            sleep_metric = self.get_sleep_summary(day)
            cardio_metric = self.get_cardio_metrics(day)

            history.append(
                GarminDailyMetric(
                    log_date=day,
                    hrv_avg_ms=hrv_metric.last_night_avg_ms,
                    resting_heart_rate=cardio_metric.resting_heart_rate,
                    sleep_total_hours=sleep_metric.total_sleep_hours,
                )
            )
        return history

    def fetch_all_telemetry(self, target_date: Optional[date] = None) -> GarminTelemetry:
        """Fetch complete overnight biometrics and trailing 7d history."""
        t_date = target_date or date.today()
        hrv = self.get_hrv_summary(t_date)
        sleep = self.get_sleep_summary(t_date)
        cardio = self.get_cardio_metrics(t_date)
        history = self.fetch_7d_history(t_date)

        is_synced = hrv.last_night_avg_ms is not None and sleep.total_sleep_hours is not None

        return GarminTelemetry(
            hrv=hrv,
            sleep=sleep,
            cardio=cardio,
            history_7d=history,
            is_synced=is_synced,
        )
