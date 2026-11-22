"""Caliber MCP Client over HTTP with OAuth 2.0 Token Refresh."""

import json
import logging
import re
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
            return trimmed
    return {}


def _extract_list(raw: Any, fallback_key: str) -> List[Any]:
    """Extract a list from raw tool output safely."""
    normalized = _normalize_payload(raw)
    if isinstance(normalized, list):
        return normalized
    if isinstance(normalized, dict):
        val = normalized.get(fallback_key, [])
        if isinstance(val, list):
            return val
        for k, v in normalized.items():
            if isinstance(v, list):
                return v
    return []


def _extract_dict(raw: Any) -> Optional[Dict[str, Any]]:
    """Extract a dict from raw tool output safely."""
    normalized = _normalize_payload(raw)
    if isinstance(normalized, dict) and normalized:
        return normalized
    return None


def _parse_text_workout_fallback(text: str) -> List[LoggedWorkout]:
    """If MCP returns natural language / markdown for workouts, extract structured workout entries."""
    if not text or not isinstance(text, str):
        return []
    # Guard against parsing error messages, Java/Kotlin exceptions, or stack traces as workouts
    lower = text.lower()
    if any(k in lower for k in ("exception", "parameter specified as non-null is null", "traceback", "internal server error")):
        logger.warning("Ignoring error output in text workout parser: %s", text[:200])
        return []

    workouts = []
    lines = text.splitlines()
    for line in lines:
        line_clean = line.strip().lstrip("#*- ")
        if not line_clean:
            continue
        date_match = re.search(r"\b(202\d-\d{2}-\d{2})\b", line_clean)
        if date_match or any(k in line_clean.lower() for k in ("completed", "session", "push", "pull", "legs", "shoulder")):
            workout_date = datetime.strptime(date_match.group(1), "%Y-%m-%d") if date_match else datetime.now()
            name_part = line_clean.split(":")[0] if ":" in line_clean else line_clean
            workouts.append(
                LoggedWorkout(
                    workout_id=f"parsed_{len(workouts) + 1}",
                    template_id=None,
                    name=name_part[:50],
                    completed_at=workout_date,
                    exercises=[],
                )
            )
    return workouts


def _parse_text_workout_plans_fallback(text: str) -> List[WorkoutPlanTemplate]:
    """Extract workout templates if MCP returns markdown or structured text."""
    if not text or not isinstance(text, str):
        return []
    lower = text.lower()
    if any(k in lower for k in ("exception", "parameter specified as non-null is null", "traceback", "error")):
        return []

    plans: List[WorkoutPlanTemplate] = []
    current_plan_name = None
    current_exercises: List[str] = []

    for line in text.splitlines():
        line_clean = line.strip()
        if not line_clean:
            continue
        if line_clean.startswith("#") or line_clean.lower().startswith("plan:") or line_clean.lower().startswith("workout:"):
            if current_plan_name and current_exercises:
                plans.append(
                    WorkoutPlanTemplate(
                        template_id=current_plan_name.lower().replace(" ", "_"),
                        name=current_plan_name,
                        order_index=len(plans),
                        exercise_ids=[f"ex_{len(plans)+1}_{i+1}" for i in range(len(current_exercises))],
                        exercise_names=list(current_exercises),
                    )
                )
            header_clean = line_clean.lstrip("#: ").replace("Plan:", "").replace("Workout:", "").strip()
            current_plan_name = header_clean
            current_exercises = []
        elif current_plan_name and (line_clean.startswith("- ") or line_clean.startswith("* ") or re.match(r"^\d+\.", line_clean)):
            ex_name = re.sub(r"^[-*\d.]+\s*", "", line_clean).strip()
            if ex_name:
                current_exercises.append(ex_name)

    if current_plan_name and current_exercises:
        plans.append(
            WorkoutPlanTemplate(
                template_id=current_plan_name.lower().replace(" ", "_"),
                name=current_plan_name,
                order_index=len(plans),
                exercise_ids=[f"ex_{len(plans)+1}_{i+1}" for i in range(len(current_exercises))],
                exercise_names=list(current_exercises),
            )
        )
    return plans


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
        self.tool_schemas: Dict[str, Dict[str, Any]] = {}

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

        # Discover and log all available tools & schemas
        try:
            tools_payload = {
                "jsonrpc": "2.0",
                "method": "tools/list",
                "id": self._next_id(),
            }
            tools_res = self._send_rpc(tools_payload)
            tools = tools_res.get("result", {}).get("tools", [])
            for t in tools:
                tname = t.get("name")
                if tname:
                    self.tool_schemas[tname] = t.get("inputSchema", {})
                    logger.info("Caliber MCP tool '%s' schema: %s", tname, json.dumps(t.get("inputSchema", {})))
        except Exception as e:
            logger.warning("Could not query tools/list: %s", e)

    def _send_rpc(self, payload: Dict[str, Any]) -> Any:
        """Send a JSON-RPC request to the MCP HTTP endpoint."""
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
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

            if response.status_code in (200, 202):
                content_type = response.headers.get("content-type", "")
                if "application/json" in content_type:
                    try:
                        return response.json()
                    except json.JSONDecodeError:
                        pass

                text = response.text
                if "data:" in text:
                    for line in text.splitlines():
                        if line.startswith("data:"):
                            try:
                                return json.loads(line[5:].strip())
                            except json.JSONDecodeError:
                                pass

                try:
                    return json.loads(text)
                except Exception:
                    return {"result": text}

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
                logger.info("Tool '%s' raw output preview: %s", tool_name, text_content[:250])
                if (
                    tool_result.get("isError")
                    or "Parameter specified as non-null is null" in text_content
                    or text_content.strip().startswith("java.lang.")
                ):
                    logger.error("Caliber MCP tool '%s' returned backend error: %s", tool_name, text_content)
                    raise RuntimeError(f"Caliber tool '{tool_name}' failed with server error: {text_content}")

                try:
                    return json.loads(text_content)
                except json.JSONDecodeError:
                    return text_content

        res_data = tool_result.get("data", tool_result)
        logger.info("Tool '%s' returned: %s", tool_name, str(res_data)[:250])
        return res_data

    def _build_tool_args(self, tool_name: str, preferred_args: Dict[str, Any]) -> Dict[str, Any]:
        """Align arguments to tool's inputSchema properties and required fields."""
        schema = self.tool_schemas.get(tool_name, {})
        props = schema.get("properties", {})
        required = schema.get("required", [])

        if not props:
            return preferred_args

        out_args: Dict[str, Any] = {}
        for k, v in preferred_args.items():
            if k in props:
                out_args[k] = v
            elif k in ("from", "startDate"):
                for alt in ("from", "startDate", "start", "fromDate", "start_date"):
                    if alt in props:
                        out_args[alt] = v
                        break
            elif k in ("to", "endDate"):
                for alt in ("to", "endDate", "end", "toDate", "end_date"):
                    if alt in props:
                        out_args[alt] = v
                        break

        # Check required fields
        for req in required:
            if req not in out_args:
                if req == "from":
                    out_args["from"] = preferred_args.get("from") or preferred_args.get("startDate")
                elif req == "to":
                    out_args["to"] = preferred_args.get("to") or preferred_args.get("endDate")
                elif "start" in req.lower():
                    out_args[req] = preferred_args.get("startDate") or preferred_args.get("from")
                elif "end" in req.lower():
                    out_args[req] = preferred_args.get("endDate") or preferred_args.get("to")
                elif req in ("limit", "count", "pageSize"):
                    out_args[req] = 50
                elif req in ("offset", "skip"):
                    out_args[req] = 0
                elif req in ("unit", "units"):
                    out_args[req] = "kg"
                elif req in ("timezone", "timeZone"):
                    out_args[req] = "UTC"

        return out_args

    def get_workout_plans(self) -> List[WorkoutPlanTemplate]:
        """Fetch active workout plan template sequence."""
        raw = None
        try:
            raw = self._call_tool("getWorkoutPlans")
        except Exception as e:
            logger.warning("getWorkoutPlans tool call failed: %s", e)
            return []

        templates = []
        # Case 1: Caliber nested plans format: {"plans": [{"workouts": [...]}]}
        if isinstance(raw, dict) and "plans" in raw and isinstance(raw["plans"], list):
            for plan in raw["plans"]:
                if isinstance(plan, dict):
                    workouts = plan.get("workouts", [])
                    if isinstance(workouts, list):
                        for idx, w in enumerate(workouts):
                            if isinstance(w, dict):
                                try:
                                    tpl = WorkoutPlanTemplate.model_validate(w)
                                    if tpl.order_index == 0 and idx > 0:
                                        tpl.order_index = idx
                                    templates.append(tpl)
                                except Exception as e:
                                    logger.warning("Could not validate nested workout template: %s", e)

        # Case 2: Direct list or {"templates": [...]}
        if not templates:
            templates_data = _extract_list(raw, "templates")
            for idx, t in enumerate(templates_data):
                if isinstance(t, dict):
                    try:
                        tpl = WorkoutPlanTemplate.model_validate(t)
                        if tpl.order_index == 0 and idx > 0:
                            tpl.order_index = idx
                        templates.append(tpl)
                    except Exception as e:
                        logger.warning("Could not validate template dict: %s", e)

        # Case 3: Text/Markdown fallback
        if not templates and isinstance(raw, str) and raw.strip():
            logger.info("getWorkoutPlans returned text. Attempting markdown parsing...")
            templates = _parse_text_workout_plans_fallback(raw)

        return templates

    def get_workouts(self, start_date: date, end_date: date) -> List[LoggedWorkout]:
        """Fetch completed workouts within the specified date window with fallbacks."""
        pref_args = {
            "from": start_date.isoformat(),
            "to": (end_date + timedelta(days=1)).isoformat(),
        }
        args = self._build_tool_args("getWorkouts", pref_args)

        raw = None
        try:
            raw = self._call_tool("getWorkouts", args)
        except Exception as e:
            logger.warning("Primary getWorkouts failed with args %s: %s", args, e)
            return []

        workouts_data = _extract_list(raw, "workouts")
        workouts: List[LoggedWorkout] = []
        if workouts_data:
            for w in workouts_data:
                if isinstance(w, dict):
                    status = str(w.get("status", "")).upper()
                    if status and status not in ("COMPLETED", "DONE", "FINISHED"):
                        continue
                    try:
                        workouts.append(LoggedWorkout.model_validate(w))
                    except Exception as e:
                        logger.warning("Could not validate workout dict: %s", e)
        elif isinstance(raw, str) and raw.strip():
            logger.info("getWorkouts returned text. Parsing fallback workouts: %s", raw[:200])
            workouts = _parse_text_workout_fallback(raw)

        return workouts

    def get_exercise_sessions(
        self, exercise_id: str, start_date: Optional[date] = None, end_date: Optional[date] = None
    ) -> List[ExerciseSession]:
        """Fetch historical sessions for a specific exercise UUID."""
        t_to = end_date or date.today()
        t_from = start_date or (t_to - timedelta(days=60))
        pref_args = {
            "exerciseId": str(exercise_id),
            "from": t_from.isoformat(),
            "to": (t_to + timedelta(days=1)).isoformat(),
        }
        args = self._build_tool_args("getExerciseSessions", pref_args)
        try:
            raw = self._call_tool("getExerciseSessions", args)
        except Exception as e:
            logger.warning("getExerciseSessions failed for %s with args %s: %s", exercise_id, args, e)
            return []

        sessions_data = _extract_list(raw, "sessions")
        sessions = []
        for s in sessions_data:
            if isinstance(s, dict):
                try:
                    sessions.append(ExerciseSession.model_validate(s))
                except Exception as e:
                    logger.warning("Could not validate session dict: %s", e)
        return sessions

    def get_nutrition(self, start_date: date, end_date: date) -> List[NutritionSummary]:
        """Fetch daily nutrition summaries."""
        pref_args = {
            "from": start_date.isoformat(),
            "to": (end_date + timedelta(days=1)).isoformat(),
        }
        args = self._build_tool_args("getNutrition", pref_args)
        try:
            raw = self._call_tool("getNutrition", args)
        except Exception as e:
            logger.warning("getNutrition tool call failed: %s", e)
            return []

        nutrition_data = _extract_list(raw, "logs")
        logs = []
        for n in nutrition_data:
            if isinstance(n, dict):
                try:
                    logs.append(NutritionSummary.model_validate(n))
                except Exception as e:
                    logger.warning("Could not validate nutrition log: %s", e)
        return logs

    def get_nutrition_targets(self) -> Optional[NutritionTargets]:
        """Fetch daily macro targets."""
        try:
            raw = self._call_tool("getNutritionTargets")
            data = _extract_dict(raw)
            if not data:
                return None
            return NutritionTargets.model_validate(data)
        except Exception as e:
            logger.warning("getNutritionTargets tool call failed: %s", e)
            return None

    def get_steps(self, start_date: date, end_date: date) -> List[DailySteps]:
        """Fetch daily step counts."""
        pref_args = {
            "from": start_date.isoformat(),
            "to": (end_date + timedelta(days=1)).isoformat(),
        }
        args = self._build_tool_args("getSteps", pref_args)
        try:
            raw = self._call_tool("getSteps", args)
        except Exception as e:
            logger.warning("getSteps tool call failed: %s", e)
            return []

        steps_data = _extract_list(raw, "steps")
        steps = []
        for s in steps_data:
            if isinstance(s, dict):
                try:
                    steps.append(DailySteps.model_validate(s))
                except Exception as e:
                    logger.warning("Could not validate step entry: %s", e)
        return steps

    def get_body_stats(self, start_date: date, end_date: date) -> List[BodyStat]:
        """Fetch daily body stats."""
        pref_args = {
            "from": start_date.isoformat(),
            "to": (end_date + timedelta(days=1)).isoformat(),
        }
        args = self._build_tool_args("getBodyStats", pref_args)
        try:
            raw = self._call_tool("getBodyStats", args)
        except Exception as e:
            logger.warning("getBodyStats tool call failed: %s", e)
            return []

        stats_data = _extract_list(raw, "stats")
        stats = []
        for b in stats_data:
            if isinstance(b, dict):
                try:
                    stats.append(BodyStat.model_validate(b))
                except Exception as e:
                    logger.warning("Could not validate body stat entry: %s", e)
        return stats

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

        # Pull historical sessions for exercises present in the active templates or recent workouts
        recent_sessions: Dict[str, List[ExerciseSession]] = {}
        distinct_exercise_ids = {
            ex_id for t in templates for ex_id in t.exercise_ids if ex_id
        }
        for w in workouts_14d:
            for ex in w.exercises:
                if ex.exercise_id:
                    distinct_exercise_ids.add(ex.exercise_id)

        for ex_id in distinct_exercise_ids:
            try:
                recent_sessions[ex_id] = self.get_exercise_sessions(
                    exercise_id=ex_id,
                    start_date=ref_date - timedelta(days=60),
                    end_date=ref_date,
                )
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
