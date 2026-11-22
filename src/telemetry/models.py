"""Telemetry data models and Pydantic schemas."""

from datetime import date, datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, model_validator


def _parse_flexible_datetime(val: Any) -> datetime:
    """Parse string, integer timestamp, or datetime into a datetime object."""
    if isinstance(val, datetime):
        return val
    if isinstance(val, (int, float)):
        # Milliseconds vs seconds detection
        if val > 1e11:
            return datetime.fromtimestamp(val / 1000.0)
        return datetime.fromtimestamp(val)
    if isinstance(val, str):
        val_clean = val.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(val_clean)
        except Exception:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(val_clean.split("+")[0], fmt)
            except Exception:
                continue
    return datetime.now()


# --- Caliber Models ---

class ExerciseSet(BaseModel):
    """Individual set performance within a workout session."""
    model_config = ConfigDict(populate_by_name=True)

    set_number: int = Field(default=1, alias="setNumber")
    weight_kg: float = Field(default=0.0, alias="weightKg")
    reps: int = Field(default=0)
    rir: Optional[float] = Field(default=None)  # Reps in Reserve
    is_warmup: bool = Field(default=False, alias="isWarmup")

    @model_validator(mode="before")
    @classmethod
    def _coerce_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d = dict(data)
        d.setdefault("setNumber", d.get("set_number", d.get("order", 1)))
        # Weight conversion if lbs is provided
        if "weightKg" not in d and "weight_kg" not in d:
            if "weight" in d:
                d["weightKg"] = float(d["weight"] or 0.0)
            elif "weightLbs" in d or "weight_lbs" in d:
                lbs = float(d.get("weightLbs") or d.get("weight_lbs") or 0.0)
                d["weightKg"] = round(lbs * 0.453592, 2)
            else:
                d["weightKg"] = 0.0
        d.setdefault("reps", d.get("repCount", d.get("repetitions", 0)))
        d.setdefault("isWarmup", d.get("is_warmup", d.get("warmup", False)))
        return d


class ExerciseSession(BaseModel):
    """Historical or planned performance record of an exercise."""
    model_config = ConfigDict(populate_by_name=True)

    exercise_id: str = Field(..., alias="exerciseId")
    exercise_name: str = Field(..., alias="exerciseName")
    performed_at: Optional[datetime] = Field(default=None, alias="performedAt")
    sets: List[ExerciseSet] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d = dict(data)
        d.setdefault("exerciseId", d.get("exercise_id", d.get("id", "ex_unknown")))
        d.setdefault("exerciseName", d.get("exercise_name", d.get("name", d.get("exerciseId", "Exercise"))))
        if "performedAt" in d or "performed_at" in d or "date" in d or "completedAt" in d or "completionDate" in d:
            date_val = d.get("performedAt") or d.get("performed_at") or d.get("date") or d.get("completedAt") or d.get("completionDate")
            d["performedAt"] = _parse_flexible_datetime(date_val)
        return d


class WorkoutPlanTemplate(BaseModel):
    """Workout template structure within an active plan sequence."""
    model_config = ConfigDict(populate_by_name=True)

    template_id: str = Field(..., alias="templateId")
    name: str = Field(...)
    order_index: int = Field(default=0, alias="orderIndex")
    exercise_ids: List[str] = Field(default_factory=list, alias="exerciseIds")
    exercise_names: List[str] = Field(default_factory=list, alias="exerciseNames")

    @model_validator(mode="before")
    @classmethod
    def _coerce_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d = dict(data)
        tid = (
            d.get("workoutId")
            or d.get("templateId")
            or d.get("template_id")
            or d.get("id")
            or d.get("planId")
            or d.get("workoutPlanId")
            or d.get("plan_id")
        )
        tname = d.get("name") or d.get("title") or d.get("planName") or d.get("workoutName") or "Plan"
        d["templateId"] = str(tid or tname.lower().replace(" ", "_"))
        d["name"] = str(tname)
        d.setdefault("orderIndex", d.get("order_index", d.get("order", 0)))

        # Extract nested exercises if present
        ex_list = d.get("exercises") or d.get("items") or []
        if isinstance(ex_list, list) and ex_list:
            ids = []
            names = []
            for item in ex_list:
                if isinstance(item, dict):
                    eid = item.get("id") or item.get("exerciseId") or item.get("exercise_id") or item.get("name")
                    ename = item.get("name") or item.get("exerciseName") or item.get("exercise_name") or str(eid)
                    if eid:
                        ids.append(str(eid))
                    if ename:
                        names.append(str(ename))
                elif isinstance(item, str):
                    ids.append(item)
                    names.append(item)
            if not d.get("exerciseIds") and not d.get("exercise_ids"):
                d["exerciseIds"] = ids
            if not d.get("exerciseNames") and not d.get("exercise_names"):
                d["exerciseNames"] = names
        return d


class LoggedWorkout(BaseModel):
    """Historical completed workout session from Caliber."""
    model_config = ConfigDict(populate_by_name=True)

    workout_id: str = Field(..., alias="workoutId")
    template_id: Optional[str] = Field(default=None, alias="templateId")
    name: str = Field(...)
    completed_at: datetime = Field(..., alias="completedAt")
    exercises: List[ExerciseSession] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d = dict(data)
        wid = (
            d.get("calendarItemId")
            or d.get("workoutId")
            or d.get("workout_id")
            or d.get("id")
            or f"workout_{datetime.now().timestamp()}"
        )
        tid = (
            d.get("templateId")
            or d.get("template_id")
            or d.get("workoutId")
            or d.get("planId")
            or d.get("workoutPlanId")
            or d.get("plan_id")
        )
        wname = (
            d.get("workoutTitle")
            or d.get("name")
            or d.get("title")
            or d.get("workoutName")
            or "Completed Workout"
        )
        date_raw = (
            d.get("date")
            or d.get("completedAt")
            or d.get("completed_at")
            or d.get("completedDate")
            or d.get("timestamp")
            or datetime.now()
        )

        d["workoutId"] = str(wid)
        d["templateId"] = str(tid) if tid else None
        d["name"] = str(wname)
        d["completedAt"] = _parse_flexible_datetime(date_raw)
        return d


class NutritionSummary(BaseModel):
    """Daily nutrition summary log."""
    model_config = ConfigDict(populate_by_name=True)

    log_date: date = Field(..., alias="date")
    calories: float = Field(default=0.0)
    protein_g: float = Field(default=0.0, alias="proteinG")
    carbs_g: Optional[float] = Field(default=0.0, alias="carbsG")
    fat_g: Optional[float] = Field(default=0.0, alias="fatG")


class NutritionTargets(BaseModel):
    """Target daily nutrition macros."""
    model_config = ConfigDict(populate_by_name=True)

    calorie_target: float = Field(default=0.0, alias="calorieTarget")
    protein_target_g: float = Field(default=0.0, alias="proteinTargetG")


class DailySteps(BaseModel):
    """Daily step count."""
    model_config = ConfigDict(populate_by_name=True)

    log_date: date = Field(..., alias="date")
    step_count: int = Field(default=0, alias="stepCount")


class BodyStat(BaseModel):
    """Bodyweight / body metrics entry."""
    model_config = ConfigDict(populate_by_name=True)

    log_date: date = Field(..., alias="date")
    weight_kg: float = Field(..., alias="weightKg")


class CaliberTelemetry(BaseModel):
    """Bundled Caliber telemetry payload for pipeline consumption."""
    templates: List[WorkoutPlanTemplate] = Field(default_factory=list)
    completed_workouts_14d: List[LoggedWorkout] = Field(default_factory=list)
    recent_exercise_sessions: Dict[str, List[ExerciseSession]] = Field(default_factory=dict)
    nutrition_logs_7d: List[NutritionSummary] = Field(default_factory=list)
    nutrition_targets: Optional[NutritionTargets] = None
    step_logs_7d: List[DailySteps] = Field(default_factory=list)
    body_stats_7d: List[BodyStat] = Field(default_factory=list)


# --- Garmin Models ---

class GarminHRV(BaseModel):
    """Garmin overnight HRV metrics."""
    last_night_avg_ms: Optional[float] = None
    baseline_7d_ms: Optional[float] = None
    status: str = "PENDING_WATCH_SYNC"  # BALANCED, UNBALANCED, LOW, PENDING_WATCH_SYNC


class GarminSleep(BaseModel):
    """Garmin overnight sleep architecture."""
    total_sleep_hours: Optional[float] = None
    deep_sleep_hours: Optional[float] = None
    rem_sleep_hours: Optional[float] = None
    light_sleep_hours: Optional[float] = None
    awake_hours: Optional[float] = None
    sleep_start: Optional[datetime] = None
    sleep_end: Optional[datetime] = None


class GarminCardio(BaseModel):
    """Garmin resting heart rate, body battery, steps, and recent strain."""
    resting_heart_rate: Optional[int] = None
    body_battery_peak: Optional[int] = None
    steps: Optional[int] = None
    activities_24h: List[Dict[str, Any]] = Field(default_factory=list)


class GarminDailyMetric(BaseModel):
    """Single historical day's biometric metric for baseline calculation."""
    log_date: date
    hrv_avg_ms: Optional[float] = None
    resting_heart_rate: Optional[int] = None
    sleep_total_hours: Optional[float] = None
    steps: Optional[int] = None


class GarminTelemetry(BaseModel):
    """Bundled Garmin overnight biometric payload."""
    hrv: GarminHRV = Field(default_factory=GarminHRV)
    sleep: GarminSleep = Field(default_factory=GarminSleep)
    cardio: GarminCardio = Field(default_factory=GarminCardio)
    history_7d: List[GarminDailyMetric] = Field(default_factory=list)
    is_synced: bool = False
