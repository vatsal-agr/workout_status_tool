"""Caliber MCP Client over HTTP/SSE with OAuth 2.0 Token Refresh."""

import json
import logging
import queue
import threading
import urllib.parse
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
    """Client for Caliber MCP Server supporting both SSE and Streamable HTTP transports with OAuth 2.0 PKCE."""

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

        # SSE and Session State
        self.message_endpoint: Optional[str] = None
        self._sse_client: Optional[httpx.Client] = None
        self._sse_thread: Optional[threading.Thread] = None
        self._sse_stop_event = threading.Event()
        self._msg_counter = 0
        self._msg_lock = threading.Lock()
        self._responses: Dict[int, Any] = {}
        self._condition = threading.Condition()
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
        with self._msg_lock:
            self._msg_counter += 1
            return self._msg_counter

    def _sse_listener_worker(self, endpoint_queue: queue.Queue):
        """Background worker listening on the SSE event stream."""
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "text/event-stream",
        }
        try:
            with httpx.Client(timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)) as sse_client:
                self._sse_client = sse_client
                with sse_client.stream("GET", self.mcp_url, headers=headers) as response:
                    if response.status_code != 200:
                        logger.error("SSE stream connection rejected [%d]: %s", response.status_code, response.text)
                        endpoint_queue.put(None)
                        return

                    current_event: Optional[str] = None
                    for line in response.iter_lines():
                        if self._sse_stop_event.is_set():
                            break
                        if not line:
                            current_event = None
                            continue
                        if line.startswith("event:"):
                            current_event = line[6:].strip()
                        elif line.startswith("data:"):
                            data_str = line[5:].strip()
                            if current_event == "endpoint":
                                full_endpoint = urllib.parse.urljoin(self.mcp_url, data_str)
                                endpoint_queue.put(full_endpoint)
                            elif current_event == "message" or data_str.startswith("{"):
                                try:
                                    parsed = json.loads(data_str)
                                    msg_id = parsed.get("id")
                                    if msg_id is not None:
                                        with self._condition:
                                            self._responses[msg_id] = parsed
                                            self._condition.notify_all()
                                except json.JSONDecodeError:
                                    pass
        except Exception as e:
            if not self._sse_stop_event.is_set():
                logger.warning("SSE connection closed: %s", e)
            endpoint_queue.put(None)

    def _ensure_session(self):
        """Establish SSE session or initialize connection with the MCP server."""
        if self._initialized and self.message_endpoint:
            return

        if not self.access_token:
            self.refresh_access_token()

        # Step 1: Attempt to establish SSE stream
        endpoint_queue = queue.Queue()
        self._sse_stop_event.clear()
        self._sse_thread = threading.Thread(
            target=self._sse_listener_worker,
            args=(endpoint_queue,),
            daemon=True,
        )
        self._sse_thread.start()

        try:
            self.message_endpoint = endpoint_queue.get(timeout=8.0)
        except queue.Empty:
            self.message_endpoint = None

        if self.message_endpoint:
            logger.info("Established MCP SSE transport. Message endpoint: %s", self.message_endpoint)
        else:
            logger.info("SSE endpoint not provided; falling back to direct HTTP POST on %s", self.mcp_url)
            self.message_endpoint = self.mcp_url

        # Step 2: Send MCP 'initialize' handshake
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
        self._send_rpc(init_payload, expect_id=init_id)

        # Step 3: Send 'notifications/initialized'
        notif_payload = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        self._send_rpc(notif_payload, expect_id=None)
        self._initialized = True
        logger.info("MCP session initialization complete.")

    def _send_rpc(self, payload: Dict[str, Any], expect_id: Optional[int] = None) -> Any:
        """Send a JSON-RPC request to the message endpoint and await response (via HTTP or SSE)."""
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        target_url = self.message_endpoint or self.mcp_url

        with httpx.Client(timeout=25.0) as client:
            response = client.post(target_url, json=payload, headers=headers)
            if response.status_code >= 400:
                logger.error(
                    "MCP POST to %s failed [%d]: %s",
                    target_url,
                    response.status_code,
                    response.text,
                )
                response.raise_for_status()

            # Case A: Server returned JSON-RPC response directly in POST response body
            if response.status_code == 200 and response.headers.get("content-type", "").startswith("application/json"):
                try:
                    result = response.json()
                    if expect_id is not None and result.get("id") == expect_id:
                        return result
                    elif expect_id is None:
                        return result
                except json.JSONDecodeError:
                    pass

        # Case B: Server sends response asynchronously over the SSE stream
        if expect_id is not None:
            with self._condition:
                start_time = datetime.now()
                while expect_id not in self._responses:
                    if (datetime.now() - start_time).total_seconds() > 20.0:
                        raise TimeoutError(f"Timed out waiting for MCP response id={expect_id}")
                    self._condition.wait(timeout=1.0)
                return self._responses.pop(expect_id)

        return None

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

        raw_response = self._send_rpc(body, expect_id=req_id)
        if not raw_response:
            return {}

        if "error" in raw_response:
            raise RuntimeError(f"MCP Tool '{tool_name}' failed: {raw_response['error']}")

        tool_result = raw_response.get("result", {})
        # MCP format: {"content": [{"type": "text", "text": "..."}]} or direct data
        if "content" in tool_result and isinstance(tool_result["content"], list):
            first_item = tool_result["content"][0]
            if first_item.get("type") == "text":
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

    def close(self):
        """Clean up SSE background thread and clients."""
        self._sse_stop_event.set()
        if self._sse_client:
            try:
                self._sse_client.close()
            except Exception:
                pass
