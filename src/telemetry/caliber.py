"""Caliber MCP Client over HTTP/SSE with OAuth 2.0 Token Refresh."""

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


class CaliberMCPClient:
    """Client for Caliber MCP Server with OAuth 2.0 PKCE refresh token lifecycle."""

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

    def refresh_access_token(self) -> Tuple[str, str]:
        """Exchange the single-use refresh token for a new access token and rotating refresh token.

        Returns:
            Tuple[str, str]: (access_token, new_refresh_token)
        """
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
            response.raise_for_status()
            data = response.json()

        self.access_token = data["access_token"]
        # Caliber issues a rotating refresh token; fall back to current if not rotated
        self.new_refresh_token = data.get("refresh_token", self.refresh_token)
        logger.info("Successfully refreshed Caliber access token.")
        return self.access_token, self.new_refresh_token

    def _call_tool(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Call a remote MCP tool using JSON-RPC 2.0 over HTTP POST."""
        if not self.access_token:
            self.refresh_access_token()

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        body = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            },
            "id": 1,
        }

        with httpx.Client(timeout=20.0) as client:
            response = client.post(self.mcp_url, json=body, headers=headers)
            response.raise_for_status()
            result = response.json()

        if "error" in result:
            raise RuntimeError(f"MCP Tool '{tool_name}' failed: {result['error']}")

        tool_result = result.get("result", {})
        # MCP responses can be {"content": [{"type": "text", "text": "..."}]} or direct data
        if "content" in tool_result and isinstance(tool_result["content"], list):
            first_item = tool_result["content"][0]
            if first_item.get("type") == "text":
                import json
                try:
                    return json.loads(first_item.get("text", "{}"))
                except json.JSONDecodeError:
                    return first_item.get("text")
        return tool_result.get("data", tool_result)

    def get_workout_plans(self) -> List[WorkoutPlanTemplate]:
        """Fetch active workout plan template sequence."""
        raw = self._call_tool("getWorkoutPlans")
        templates_data = raw if isinstance(raw, list) else raw.get("templates", [])
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
        workouts_data = raw if isinstance(raw, list) else raw.get("workouts", [])
        return [LoggedWorkout.model_validate(w) for w in workouts_data]

    def get_exercise_sessions(
        self, exercise_id: str, limit: int = 3
    ) -> List[ExerciseSession]:
        """Fetch historical sessions for a specific exercise ID."""
        raw = self._call_tool(
            "getExerciseSessions",
            {"exerciseId": exercise_id, "limit": limit},
        )
        sessions_data = raw if isinstance(raw, list) else raw.get("sessions", [])
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
        nutrition_data = raw if isinstance(raw, list) else raw.get("logs", [])
        return [NutritionSummary.model_validate(n) for n in nutrition_data]

    def get_nutrition_targets(self) -> Optional[NutritionTargets]:
        """Fetch daily macro targets."""
        raw = self._call_tool("getNutritionTargets")
        if not raw:
            return None
        return NutritionTargets.model_validate(raw)

    def get_steps(self, start_date: date, end_date: date) -> List[DailySteps]:
        """Fetch daily step counts."""
        raw = self._call_tool(
            "getSteps",
            {
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
            },
        )
        steps_data = raw if isinstance(raw, list) else raw.get("steps", [])
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
        stats_data = raw if isinstance(raw, list) else raw.get("stats", [])
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
