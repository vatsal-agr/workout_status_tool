"""Caliber MCP Client over HTTP with OAuth 2.0 Token Refresh."""

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
import httpx

from src.config import settings
from src.telemetry.models import (
    BodyStat,
    CaliberTelemetry,
    DailySteps,
    ExerciseSession,
    LoggedWorkout,
    NutritionSummary,
    NutritionTargets,
    WorkoutPlanTemplate,
)

logger = logging.getLogger(__name__)


def _normalize_payload(raw: Any) -> Any:
    """Normalize raw tool output into dict, list, or empty structure."""
    if raw is None:
        return {}
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        trimmed = raw.strip()
        if not trimmed:
            return {}
        try:
            return json.loads(trimmed)
        except json.JSONDecodeError:
            logger.info("MCP tool returned non-JSON text: %s", trimmed[:150])
            return {}
    return {}


def _extract_list(raw: Any, fallback_key: str) -> List[Any]:
    """Extract a list from raw tool output safely, regardless of return format."""
    normalized = _normalize_payload(raw)
    if isinstance(normalized, list):
        return normalized
    if isinstance(normalized, dict):
        val = normalized.get(fallback_key, [])
        if isinstance(val, list):
            return val
    return []


def _extract_dict(raw: Any) -> Optional[Dict[str, Any]]:
    """Extract a dict from raw tool output safely."""
    normalized = _normalize_payload(raw)
    if isinstance(normalized, dict) and normalized:
        return normalized
    return None


class CaliberMCPClient:
    """Client for Caliber MCP Server with Streamable HTTP JSON-RPC 2.0 and OAuth 2.0 PKCE."""

    def __init__(
        self,
        refresh_token: Optional[str] = None,
        mcp_url: Optional[str] = None,
        token_url: Optional[str] = None,
        client_id: Optional[str] = None,
    ):
        self.refresh_token = refresh_token or settings.CALIBER_REFRESH_TOKEN
        self.mcp_url = mcp_url or settings.CALIBER_MCP_URL
        self.token_url = token_url or settings.CALIBER_TOKEN_URL
        self.client_id = client_id or settings.CALIBER_CLIENT_ID
        self.access_token: Optional[str] = None
        self.new_refresh_token: Optional[str] = None

        self._msg_counter = 0
        self._initialized = False

    def refresh_access_token(self) -> Tuple[str, str]:
        """Exchange the single-use refresh token for a new access token and rotating refresh token."""
        if not self.refresh_token:
            raise ValueError(
                "No Caliber refresh token available. Run bootstrap script or set CALIBER_REFRESH_TOKEN."
            )

        payload = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "refresh_token": self.refresh_token,
        }

        logger.info("Exchanging Caliber OAuth refresh token...")
        with httpx.Client(timeout=15.0) as client:
            response = client.post(self.token_url, data=payload)
            if response.status_code >= 400:
                logger.error("Token refresh failed [%d]: %s", response.status_code, response.text)
            response.raise_for_status()
            data = response.json()

        self.access_token = data["access_token"]
        self.new_refresh_token = data.get("refresh_token", self.refresh_token)
        logger.info("Successfully refreshed Caliber access token.")
        return self.access_token, self.new_refresh_token

    def _next_id(self) -> int:
        self._msg_counter += 1
        return self._msg_counter

    def _ensure_session(self):
        """Perform MCP initialize handshake via Streamable HTTP."""
        if self._initialized:
            return

        if not self.access_token:
            self.refresh_access_token()

        init_id = self._next_id()
        init_payload = {
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "workout-pipeline",
                    "version": "1.0.0",
                },
            },
            "id": init_id,
        }

        logger.info("Sending MCP initialize handshake...")
        self._send_rpc(init_payload)

        # Confirm initialization notification
        notif_payload = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        self._send_rpc(notif_payload)
        self._initialized = True
        logger.info("MCP session initialization complete.")

    def _send_rpc(self, payload: Dict[str, Any]) -> Any:
        """Send a JSON-RPC request to the MCP HTTP endpoint."""
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        with httpx.Client(timeout=25.0) as client:
            response = client.post(self.mcp_url, json=payload, headers=headers)
            if response.status_code >= 400:
                logger.error(
                    "MCP POST failed [%d]: %s",
                    response.status_code,
                    response.text,
                )
                response.raise_for_status()

            if response.status_code == 200 and response.headers.get("content-type", "").startswith("application/json"):
                try:
                    return response.json()
                except json.JSONDecodeError:
                    return {}

            return {}

    def _call_tool(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Call a remote MCP tool using JSON-RPC 2.0 tools/call."""
        self._ensure_session()

        req_id = self._next_id()
        body = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            },
            "id": req_id,
        }

        raw_response = self._send_rpc(body)
        if not raw_response:
            return {}

        if "error" in raw_response:
            raise RuntimeError(f"MCP Tool '{tool_name}' failed: {raw_response['error']}")

        tool_result = raw_response.get("result", {})
        # Extract content from MCP format: {"content": [{"type": "text", "text": "..."}]}
        if "content" in tool_result and isinstance(tool_result["content"], list) and len(tool_result["content"]) > 0:
            first_item = tool_result["content"][0]
            if first_item.get("type") == "text":
                text_content = first_item.get("text", "")
                try:
                    return json.loads(text_content)
                except json.JSONDecodeError:
                    return text_content
        return tool_result.get("data", tool_result)

    def get_workout_plans(self) -> List[WorkoutPlanTemplate]:
        """Fetch active workout plan template sequence."""
        raw = self._call_tool("getWorkoutPlans")
        templates_data = _extract_list(raw, "templates")
        return [WorkoutPlanTemplate.model_validate(t) for t in templates_data]

    def get_workouts(self, start_date: date, end_date: date) -> List[LoggedWorkout]:
        """Fetch completed workouts within the specified date window."""
        raw = self._call_tool(
            "getWorkouts",
            {
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
            },
        )
        workouts_data = _extract_list(raw, "workouts")
        return [LoggedWorkout.model_validate(w) for w in workouts_data]

    def get_exercise_sessions(
        self, exercise_id: str, limit: int = 3
    ) -> List[ExerciseSession]:
        """Fetch historical sessions for a specific exercise ID."""
        raw = self._call_tool(
            "getExerciseSessions",
            {"exerciseId": exercise_id, "limit": limit},
        )
        sessions_data = _extract_list(raw, "sessions")
        return [ExerciseSession.model_validate(s) for s in sessions_data]

    def get_nutrition(self, start_date: date, end_date: date) -> List[NutritionSummary]:
        """Fetch daily nutrition summaries."""
        raw = self._call_tool(
            "getNutrition",
            {
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
            },
        )
        nutrition_data = _extract_list(raw, "logs")
        return [NutritionSummary.model_validate(n) for n in nutrition_data]

    def get_nutrition_targets(self) -> Optional[NutritionTargets]:
        """Fetch daily macro targets."""
        raw = self._call_tool("getNutritionTargets")
        data = _extract_dict(raw)
        if not data:
            return None
        return NutritionTargets.model_validate(data)

    def get_steps(self, start_date: date, end_date: date) -> List[DailySteps]:
        """Fetch daily step counts."""
        raw = self._call_tool(
            "getSteps",
            {
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
            },
        )
        steps_data = _extract_list(raw, "steps")
        return [DailySteps.model_validate(s) for s in steps_data]

    def get_body_stats(self, start_date: date, end_date: date) -> List[BodyStat]:
        """Fetch daily body stats."""
        raw = self._call_tool(
            "getBodyStats",
            {
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
            },
        )
        stats_data = _extract_list(raw, "stats")
        return [BodyStat.model_validate(b) for b in stats_data]

    def fetch_all_telemetry(self, reference_date: Optional[date] = None) -> CaliberTelemetry:
        """Fetch full telemetry payload needed for pipeline processing."""
        ref_date = reference_date or date.today()
        start_14d = ref_date - timedelta(days=14)
        start_7d = ref_date - timedelta(days=7)

        templates = self.get_workout_plans()
        workouts_14d = self.get_workouts(start_date=start_14d, end_date=ref_date)
        nutrition_7d = self.get_nutrition(start_date=start_7d, end_date=ref_date)
        targets = self.get_nutrition_targets()
        steps_7d = self.get_steps(start_date=start_7d, end_date=ref_date)
        body_stats_7d = self.get_body_stats(start_date=start_7d, end_date=ref_date)

        # Pull historical sessions for exercises present in the active templates
        recent_sessions: Dict[str, List[ExerciseSession]] = {}
        distinct_exercise_ids = {
            ex_id for t in templates for ex_id in t.exercise_ids if ex_id
        }
        for ex_id in distinct_exercise_ids:
            try:
                recent_sessions[ex_id] = self.get_exercise_sessions(ex_id, limit=3)
            except Exception as e:
                logger.warning("Could not fetch sessions for exercise %s: %s", ex_id, e)
                recent_sessions[ex_id] = []

        return CaliberTelemetry(
            templates=templates,
            completed_workouts_14d=workouts_14d,
            recent_exercise_sessions=recent_sessions,
            nutrition_logs_7d=nutrition_7d,
            nutrition_targets=targets,
            step_logs_7d=steps_7d,
            body_stats_7d=body_stats_7d,
        )

    def close(self):
        """Cleanup handler (no-op for stateless HTTP)."""
        pass
