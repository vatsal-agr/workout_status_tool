"""Telemetry data models and Pydantic schemas."""

from datetime import date, datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


# --- Caliber Models ---

class ExerciseSet(BaseModel):
    """Individual set performance within a workout session."""
    model_config = ConfigDict(populate_by_name=True)

    set_number: int = Field(default=1, alias="setNumber")
    weight_kg: float = Field(default=0.0, alias="weightKg")
    reps: int = Field(default=0)
    rir: Optional[float] = Field(default=None)  # Reps in Reserve
    is_warmup: bool = Field(default=False, alias="isWarmup")


class ExerciseSession(BaseModel):
    """Historical or planned performance record of an exercise."""
    model_config = ConfigDict(populate_by_name=True)

    exercise_id: str = Field(..., alias="exerciseId")
    exercise_name: str = Field(..., alias="exerciseName")
    performed_at: Optional[datetime] = Field(default=None, alias="performedAt")
    sets: List[ExerciseSet] = Field(default_factory=list)


class WorkoutPlanTemplate(BaseModel):
    """Workout template structure within an active plan sequence."""
    model_config = ConfigDict(populate_by_name=True)

    template_id: str = Field(..., alias="templateId")
    name: str = Field(...)
    order_index: int = Field(default=0, alias="orderIndex")
    exercise_ids: List[str] = Field(default_factory=list, alias="exerciseIds")
    exercise_names: List[str] = Field(default_factory=list, alias="exerciseNames")


class LoggedWorkout(BaseModel):
    """Historical completed workout session from Caliber."""
    model_config = ConfigDict(populate_by_name=True)

    workout_id: str = Field(..., alias="workoutId")
    template_id: Optional[str] = Field(default=None, alias="templateId")
    name: str = Field(...)
    completed_at: datetime = Field(..., alias="completedAt")
    exercises: List[ExerciseSession] = Field(default_factory=list)


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
    """Garmin resting heart rate, body battery, and recent strain."""
    resting_heart_rate: Optional[int] = None
    body_battery_peak: Optional[int] = None
    activities_24h: List[Dict[str, Any]] = Field(default_factory=list)


class GarminDailyMetric(BaseModel):
    """Single historical day's biometric metric for baseline calculation."""
    log_date: date
    hrv_avg_ms: Optional[float] = None
    resting_heart_rate: Optional[int] = None
    sleep_total_hours: Optional[float] = None


class GarminTelemetry(BaseModel):
    """Bundled Garmin overnight biometric payload."""
    hrv: GarminHRV = Field(default_factory=GarminHRV)
    sleep: GarminSleep = Field(default_factory=GarminSleep)
    cardio: GarminCardio = Field(default_factory=GarminCardio)
    history_7d: List[GarminDailyMetric] = Field(default_factory=list)
    is_synced: bool = False
