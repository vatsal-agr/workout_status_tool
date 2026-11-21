"""Template generator and seeder for user-maintained control tabs."""

import logging
from typing import Optional
import gspread

from src.sheets.client import GoogleSheetsService

logger = logging.getLogger(__name__)

DEFAULT_PHILOSOPHY_RULES = [
    ["### DR. MIKE ISRAETEL HYPERTROPHY & AUTOREGULATION GOVERNANCE (RP STRENGTH)", ""],
    ["1. Standardized Technique & Tempo", "Every single rep: 2-3 second controlled eccentric. 1-second dead-stop pause in the deepest stretched position. Zero bouncing, zero body english. If form degrades, the set is officially OVER."],
    ["2. Proximity to Failure (RIR)", "Compounds: 1-2 RIR (1-2 reps shy of technical failure). Isolations: 0-1 RIR. Never train to catastrophic technical breakdown."],
    ["3. Set Architecture & SFR", "Top-Set + 1 Back-off Set. Maximize Stimulus-to-Fatigue Ratio (SFR). Top set takes you to heavy standardized load; back-off set drops load by 10% for high-SFR rep accumulation."],
    ["4. Dynamic Progression Model", "Double Progression. Maintain load until all sets hit the top rep ceiling with pristine eccentric control. Only then micro-load by the minimum equipment increment."],
    ["5. Fatigue Management & Deloads", "If consecutive_stalls >= 2: Systemic fatigue has outpaced recovery. Trigger an immediate reactive deload (-20% load, 50% set volume, 3-4 RIR) to clear fatigue."],
    ["6. 24/7 Wearable Autonomic Gating (Garmin)", ""],
    ["   - GREEN", "Autonomic nervous system (ANS) fully recovered. Push double progression and crush top sets."],
    ["   - AMBER", "Elevated RHR or suppressed HRV. Hold loads flat; drop back-off set (1 working set only); keep strict 2 RIR."],
    ["   - RED", "Systemic ANS crash or compounded fatigue. Prescribe active recovery walk or a half-volume pump deload at 3-4 RIR."],
    ["   - PENDING_WATCH_SYNC", "Wearable sync pending. Train at standard scheduled baseline with strict Dr. Mike biomechanical tempo."],
]

DEFAULT_EXERCISE_CONFIG = [
    ["Exercise Name", "Min Jump (kg)", "Progression Model", "Equipment Type", "Primary Biomechanical Cue (Dr. Mike RP Standard)", "Common High-Level Mistakes (Avoid)"],
    ["Barbell Bench Press", "2.5", "Double (6-8 reps)", "Barbell", "3s eccentric, 1s pause deep on chest, elbows 45°, pack scapulae into bench.", "Bouncing bar off ribs, lifting hips off bench to cheat, and losing scapular retraction at lockout."],
    ["Incline Dumbbell Press", "2.0", "Double (8-10 reps)", "Dumbbell", "Control eccentric down to deep pec stretch, 1s pause in stretch, press in slight inward arc without clacking.", "Clacking dumbbells at top, setting bench too steep (>45° robs upper chest for front delts), and cutting bottom stretch."],
    ["Barbell Back Squat", "2.5", "Double (5-7 reps)", "Barbell", "Brace 360°, sink hips deep below parallel into full quad stretch, 1s pause, explode up.", "Good-morning squat (hips shoot up before chest), dive-bombing into bottom bounce, and knees caving inward (valgus)."],
    ["Romanian Deadlift", "2.5", "Double (8-10 reps)", "Barbell", "Push hips straight back, soft knees, lower until hamstrings are tearing apart in full stretch, 1s pause.", "Bending knees into a squat, hyperextending lumbar spine at top lockout, and lowering bar past hamstring stretch into spinal flexion."],
    ["Lat Pulldown", "5.0", "Double (8-12 reps)", "Cable / Stack", "Full overhead lat stretch at top, pull elbows down and back to pockets, squeeze lats, 2s eccentric.", "Leaning torso back 45° turning it into a row, yanking bar down with biceps/momentum, and not letting scapulae elevate into full overhead stretch."],
    ["Barbell Overhead Press", "1.25", "Double (6-8 reps)", "Barbell", "Glutes locked, abs braced, touch upper chest, full lockout overhead pushing head through window.", "Arching lower back excessively, flaring elbows wide on descent, and pressing bar in front of face instead of straight through window."],
    ["Cable Lateral Raise", "1.25", "Double (12-15 reps)", "Cable / Stack", "Cables at wrist height, lead with side delts, pause at peak 45° angle, control 3s down.", "Shrugging traps up to neck, internally rotating shoulders (pouring the pitcher robs side delt tension and causes impingement), swinging torso."],
    ["Standing Calf Raise", "5.0", "Double (10-12 reps)", "Machine", "3s eccentric down, 2s deep agonizing stretch at the bottom, drive to big toes.", "Bouncing out of Achilles tendon reflex, rolling onto outer foot edges, and failing to hold a dead-stop pause in the full stretch."],
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
