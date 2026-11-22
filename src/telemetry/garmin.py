"""Garmin Connect API Client supporting both Personal API and Health Enterprise API with Graceful Watch-Sync Fallback."""

import json
import logging
import shutil
import subprocess
import urllib.parse
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

PERSONAL_TOKEN_URL = "https://personal.connectapi.garmin.com/personalapitoken-service/personal-api/personal-api-key/token"
PERSONAL_BASE_URL = "https://personal.connectapi.garmin.com"


class GarminPersonalAPIClient:
    """Client for Garmin Connect Personal API (GCDC keys) and Health Enterprise API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.api_key = (api_key or settings.GARMIN_PERSONAL_API_KEY).strip()
        self.base_url = (base_url or settings.GARMIN_API_BASE_URL).rstrip("/")
        self._is_personal_api = self.api_key.startswith("GCDC_")
        self._cached_bearer_token: Optional[str] = None

    def _get_bearer_token(self) -> str:
        """Obtain bearer token (exchanging Personal API key if GCDC_)."""
        if not self.api_key:
            raise ValueError(
                "Garmin API Key missing. Please set GARMIN_PERSONAL_API_KEY in environment or secrets."
            )

        if not self._is_personal_api:
            auth_val = self.api_key
            if not (auth_val.startswith("Bearer ") or auth_val.startswith("OAuth ")):
                auth_val = f"Bearer {auth_val}"
            return auth_val

        if self._cached_bearer_token:
            return f"Bearer {self._cached_bearer_token}"

        logger.info("Exchanging Garmin Personal API Key for Bearer token...")
        headers = {"Authorization": f"ApiKey {self.api_key}"}
        token_str = self._raw_post(PERSONAL_TOKEN_URL, headers=headers)
        if not token_str:
            raise RuntimeError("Failed to obtain Bearer token from Garmin Personal API")

        # Handle raw token or json object
        trimmed = token_str.strip()
        if trimmed.startswith("{"):
            try:
                data = json.loads(trimmed)
                token_val = data.get("accessToken") or data.get("token") or trimmed
            except Exception:
                token_val = trimmed
        else:
            token_val = trimmed

        self._cached_bearer_token = token_val
        logger.info("Successfully obtained Garmin Personal API Bearer token.")
        return f"Bearer {self._cached_bearer_token}"

    def _raw_post(self, url: str, headers: Dict[str, str]) -> Optional[str]:
        """Perform POST with httpx, falling back to curl if TLS/SSL protocol errors occur."""
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(url, headers=headers)
                if resp.status_code == 200:
                    return resp.text
        except (httpx.ConnectError, Exception) as e:
            logger.debug("httpx POST to %s failed (%s), falling back to curl", url, e)

        if shutil.which("curl"):
            cmd = ["curl", "-s", "-X", "POST", url]
            for k, v in headers.items():
                cmd.extend(["-H", f"{k}: {v}"])
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        return None

    def _http_get(self, url: str, headers: Dict[str, str], params: Optional[Dict[str, Any]] = None) -> Any:
        """Perform GET request with httpx, falling back to curl for TLS compatibility."""
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.get(url, headers=headers, params=params or {})
                if resp.status_code == 404:
                    return None
                if resp.status_code >= 400:
                    logger.warning("Garmin GET %s failed [%d]: %s", url, resp.status_code, resp.text[:250])
                resp.raise_for_status()
                return resp.json()
        except (httpx.ConnectError, Exception) as e:
            logger.debug("httpx GET to %s failed (%s), trying curl fallback", url, e)

        if shutil.which("curl"):
            full_url = url
            if params:
                query_str = urllib.parse.urlencode(params)
                full_url = f"{url}?{query_str}" if "?" not in url else f"{url}&{query_str}"
            cmd = ["curl", "-s", "-X", "GET", full_url]
            for k, v in headers.items():
                cmd.extend(["-H", f"{k}: {v}"])
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                try:
                    return json.loads(res.stdout)
                except Exception as parse_err:
                    logger.warning("Could not parse curl JSON response from %s: %s", full_url, parse_err)
                    return None
        return None

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

    def get_hrv_summary(self, target_date: date) -> GarminHRV:
        """Fetch overnight HRV summary for target date."""
        try:
            token = self._get_bearer_token()
            headers = {"Authorization": token, "Accept": "application/json"}

            if self._is_personal_api:
                url = f"{PERSONAL_BASE_URL}/hrv-service/hrv/{target_date.isoformat()}"
                data = self._http_get(url, headers=headers)
                if not data or not isinstance(data, dict):
                    return GarminHRV(status="PENDING_WATCH_SYNC")

                summary = data.get("hrvSummary", {})
                last_night_avg = summary.get("lastNightAvg")
                baseline_val = summary.get("weeklyAvg")
                if not baseline_val and summary.get("baseline"):
                    baseline_val = summary["baseline"].get("balancedLow")
                status = summary.get("status", "BALANCED") if last_night_avg else "PENDING_WATCH_SYNC"

                return GarminHRV(
                    last_night_avg_ms=float(last_night_avg) if last_night_avg else None,
                    baseline_7d_ms=float(baseline_val) if baseline_val else None,
                    status=status,
                )
            else:
                params = self._get_window_params(target_date, window_days_back=1, window_days_forward=1)
                url = f"{self.base_url}/wellness-api/rest/hrv"
                data = self._http_get(url, headers=headers, params=params)
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
            token = self._get_bearer_token()
            headers = {"Authorization": token, "Accept": "application/json"}

            if self._is_personal_api:
                url = f"{PERSONAL_BASE_URL}/sleep-service/sleep/dailySleepSummariesByDate"
                params = {
                    "startDate": (target_date - timedelta(days=1)).isoformat(),
                    "endDate": target_date.isoformat(),
                }
                data = self._http_get(url, headers=headers, params=params)
                if not data or not isinstance(data, list) or len(data) == 0:
                    return GarminSleep()

                target_str = target_date.isoformat()
                item = next((d for d in data if d.get("calendarDate") == target_str), data[-1])
                sleep_duration_seconds = item.get("sleepTimeSeconds") or item.get("durationInSeconds", 0)
                deep_seconds = item.get("deepSleepSeconds") or item.get("deepSleepDurationInSeconds", 0)
                rem_seconds = item.get("remSleepSeconds") or item.get("remSleepInSeconds", 0)
                light_seconds = item.get("lightSleepSeconds") or item.get("lightSleepDurationInSeconds", 0)
                awake_seconds = item.get("awakeSleepSeconds") or item.get("awakeDurationInSeconds", 0)

                return GarminSleep(
                    total_sleep_hours=round(sleep_duration_seconds / 3600.0, 2) if sleep_duration_seconds else None,
                    deep_sleep_hours=round(deep_seconds / 3600.0, 2) if deep_seconds else None,
                    rem_sleep_hours=round(rem_seconds / 3600.0, 2) if rem_seconds else None,
                    light_sleep_hours=round(light_seconds / 3600.0, 2) if light_seconds else None,
                    awake_hours=round(awake_seconds / 3600.0, 2) if awake_seconds else None,
                )
            else:
                params = self._get_window_params(target_date, window_days_back=1, window_days_forward=1)
                url = f"{self.base_url}/wellness-api/rest/sleeps"
                data = self._http_get(url, headers=headers, params=params)
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
        """Fetch resting heart rate, morning body battery, steps, and recent activities."""
        rhr: Optional[int] = None
        body_battery: Optional[int] = None
        steps: Optional[int] = None
        activities: List[Dict[str, Any]] = []

        try:
            token = self._get_bearer_token()
            headers = {"Authorization": token, "Accept": "application/json"}

            if self._is_personal_api:
                url = f"{PERSONAL_BASE_URL}/usersummary-service/usersummary/daily"
                data = self._http_get(url, headers=headers, params={"calendarDate": target_date.isoformat()})
                if data and isinstance(data, dict):
                    rhr = data.get("restingHeartRate") or data.get("lastSevenDaysAvgRestingHeartRate")
                    body_battery = data.get("bodyBatteryHighestValue")
                    steps = data.get("totalSteps")
            else:
                params = self._get_window_params(target_date, window_days_back=1, window_days_forward=1)
                daily_data = self._http_get(f"{self.base_url}/wellness-api/rest/dailies", headers=headers, params=params)
                if daily_data and isinstance(daily_data, list) and len(daily_data) > 0:
                    target_str = target_date.isoformat()
                    day_item = next((d for d in daily_data if d.get("calendarDate") == target_str), daily_data[-1])
                    rhr = day_item.get("restingHeartRateInBeatsPerMinute")
                    body_battery = day_item.get("bodyBatteryHighestValue")
                    steps = day_item.get("steps")

                act_params = self._get_window_params(target_date - timedelta(days=1), window_days_back=1, window_days_forward=1)
                act_data = self._http_get(f"{self.base_url}/activity-api/rest/activities", headers=headers, params=act_params)
                if act_data and isinstance(act_data, list):
                    activities = act_data
        except Exception as e:
            logger.warning("Could not fetch Garmin cardio metrics for %s: %s", target_date, e)

        return GarminCardio(
            resting_heart_rate=rhr,
            body_battery_peak=body_battery,
            steps=steps,
            activities_24h=activities,
        )

    def fetch_7d_history(self, reference_date: date) -> List[GarminDailyMetric]:
        """Fetch trailing 7 days of daily metrics to calculate baseline means and standard deviations."""
        history: List[GarminDailyMetric] = []
        for i in range(1, 8):
            day = reference_date - timedelta(days=i)
            hrv_metric = self.get_hrv_summary(day)
            sleep_metric = self.get_sleep_summary(day)
            cardio_metric = self.get_cardio_metrics(day)

            history.append(
                GarminDailyMetric(
                    log_date=day,
                    hrv_avg_ms=hrv_metric.last_night_avg_ms,
                    resting_heart_rate=cardio_metric.resting_heart_rate,
                    sleep_total_hours=sleep_metric.total_sleep_hours,
                    steps=cardio_metric.steps,
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
