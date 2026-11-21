"""Template generator and seeder for user-maintained control tabs."""

import logging
from typing import Optional
import gspread

from src.sheets.client import GoogleSheetsService

logger = logging.getLogger(__name__)

DEFAULT_PHILOSOPHY_RULES = [
    ["### GENERAL TRAINING PHILOSOPHY & AUTOREGULATION GOVERNANCE", ""],
    ["1. Intensity & RIR Targets", "Compound lifts: 1-2 RIR (never train to technical failure). Isolations: 0-1 RIR."],
    ["2. Set Structure", "Top-Set + 1 Back-off Set. Top set targets heavy progression; back-off set drops load by 10% and targets +2 reps."],
    ["3. Progression Model", "Double progression. Stay at the same weight until all working sets hit the top of the target rep range, then increment by the minimum equipment jump."],
    ["4. Stall-Breaker Rule", "If consecutive_stalls >= 2, drop working weight by 10% for a volume reset, or swap to alternate grip/stance."],
    ["5. Biometric Readiness Tiers", ""],
    ["   - GREEN", "Full intensity and prescribed volume. Aggressively push top-set progression."],
    ["   - AMBER", "Autoregulate. Maintain top-set load, drop back-off set, maintain strict 2 RIR."],
    ["   - RED", "Fatigue alert. Deload session (-20% load, 1-2 sets @ 3-4 RIR) or take an active recovery day."],
    ["   - PENDING_WATCH_SYNC", "No autonomic data yet. Execute scheduled session at standard progression without biometric gating."],
    ["6. Nutrition Guardrails", "If yesterday's protein < target - 20g, prioritize high-protein morning meal before lifting."],
]

DEFAULT_EXERCISE_CONFIG = [
    ["Exercise Name", "Min Jump (kg)", "Progression Model", "Equipment Type", "Primary Biomechanical Cue"],
    ["Barbell Bench Press", "2.5", "Double (6-8 reps)", "Barbell", "Pack lats, drive heels into floor, slight touch below nipple line."],
    ["Incline Dumbbell Press", "2.0", "Double (8-10 reps)", "Dumbbell", "Elbows tucked 45°, deep stretch at bottom, do not bang dumbbells at top."],
    ["Barbell Back Squat", "2.5", "Double (5-7 reps)", "Barbell", "Brace core 360°, knees track with toes, control descent."],
    ["Romanian Deadlift", "2.5", "Double (8-10 reps)", "Barbell", "Hips push straight back, soft knees, stop when hamstrings reach full stretch."],
    ["Lat Pulldown", "5.0", "Double (8-12 reps)", "Cable / Stack", "Depress scapulae first, drive elbows toward back pockets."],
    ["Barbell Overhead Press", "1.25", "Double (6-8 reps)", "Barbell", "Squeeze glutes and abs, clear chin, push head through at lockout."],
    ["Cable Lateral Raise", "1.25", "Double (12-15 reps)", "Cable / Stack", "Lead with elbows, slight forward lean, pause briefly at peak."],
    ["Standing Calf Raise", "5.0", "Double (10-12 reps)", "Machine", "2-second deep pause in full stretch, explode to balls of feet."],
]


class SheetsSeeder:
    """Ensures control tabs exist and populates sensible default templates if empty."""

    def __init__(self, service: Optional[GoogleSheetsService] = None):
        self.service = service or GoogleSheetsService()

    def seed_control_tabs_if_missing(self) -> None:
        """Seed Philosophy_&_Rules and Exercise_Config tabs if they do not exist."""
        spreadsheet = self.service.open_spreadsheet()
        existing_titles = [w.title for w in spreadsheet.worksheets()]

        if "Philosophy_&_Rules" not in existing_titles:
            logger.info("Creating and seeding 'Philosophy_&_Rules' tab...")
            ws = spreadsheet.add_worksheet(title="Philosophy_&_Rules", rows=30, cols=4)
            ws.update(DEFAULT_PHILOSOPHY_RULES)
        else:
            logger.info("'Philosophy_&_Rules' tab already exists. Skipping seed.")

        if "Exercise_Config" not in existing_titles:
            logger.info("Creating and seeding 'Exercise_Config' tab...")
            ws = spreadsheet.add_worksheet(title="Exercise_Config", rows=50, cols=6)
            ws.update(DEFAULT_EXERCISE_CONFIG)
        else:
            logger.info("'Exercise_Config' tab already exists. Skipping seed.")
