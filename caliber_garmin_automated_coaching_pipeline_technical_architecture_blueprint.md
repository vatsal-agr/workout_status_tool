# RFC: Headless Telemetry Pipeline & LLM Coaching Engine (Caliber + Garmin + Gemini)

* **Status:** Draft for Technical Critique
* **Target Schedule:** Daily at 10:45 AM IST (`05:15 UTC`) ETL / 11:00 AM IST Briefing
* **Constraint Profile:** Zero local compute, `$0` LLM API spend (leverages existing consumer Gemini subscription via Scheduled Actions), full adherence to custom training philosophy.

---

## 1. System Aim & Design Philosophy

The objective is to build an automated, daily strength-and-recovery coaching system that:
1. **Tracks a rolling workout split** (non-fixed days of the week) without manual logging overhead.
2. **Synthesizes multi-modal telemetry** every morning at 11:00 AM IST—combining lifting logs, nutrition, and step counts from **Caliber** with overnight autonomic recovery metrics (HRV, sleep architecture, resting heart rate) from **Garmin**.
3. **Prescribes context-aware gym targets** (warm-up ramps, exact working loads, rep targets, and biomechanical cues) governed by user-defined training principles.

### Core Architectural Tenets
* **Separation of Compute vs. Reasoning:** LLMs are unreliable at multi-day calendar arithmetic, rolling pointer tracking, and baseline delta math. Python handles 100% of deterministic state transitions, rolling averages, and stall detection; Gemini handles synthesis, autoregulation judgment, and natural-language coaching.
* **Zero-Cost Frontier Inference:** Rather than paying per-token for commercial LLM APIs or downgrading to free-tier legacy models, the pipeline writes structured context to Google Sheets and triggers a **Consumer Gemini Scheduled Action**, utilizing the user's existing Gemini subscription.
* **Control-Plane Decoupling:** Training rules, exercise increments, and recovery thresholds live in editable Google Sheets tabs rather than hardcoded Python conditionals, allowing instant rule tuning from a mobile device.

---

## 2. System Architecture & Data Flow

```text
+-----------------------+       +-----------------------------------+
|  Caliber MCP Server   |       |    Garmin Connect Personal API    |
|  (Remote HTTP/SSE)    |       |    (REST / Static Bearer Auth)    |
+-----------+-----------+       +-----------------+-----------------+
            |                                     |
            | OAuth 2.0 PKCE                      | HTTPS GET
            | (Rotating Refresh Token)            | (GARMIN_PERSONAL_API_KEY)
            v                                     v
+-------------------------------------------------------------------+
|               GitHub Actions Runner (10:45 AM IST)                |
|                                                                   |
|  1. Auth & Fetch Layer:                                           |
|     - Exchange & rotate CALIBER_REFRESH_TOKEN via GH Secrets API  |
|     - Invoke Caliber MCP tools (plans, workouts, nutrition, etc.) |
|     - Query Garmin endpoints (HRV, sleep, RHR, activities)        |
|                                                                   |
|  2. Deterministic State & Math Engine (Python):                   |
|     - Compute rolling split pointer & days-since-last-session     |
|     - Calculate per-lift deltas, e1RM, and consecutive stall count|
|     - Compute 7-day rolling HRV/RHR baselines & readiness tier    |
|                                                                   |
|  3. Storage Writer (gspread / Service Account):                   |
|     - Overwrite `Briefing_Context` & `Telemetry_Archive` tabs     |
+---------------------------------+---------------------------------+
                                  |
                                  | Google Sheets API (GCP_SA_KEY)
                                  v
+-------------------------------------------------------------------+
|               Google Sheet: `Caliber_Daily_Sync`                  |
|                                                                   |
|  [Briefing_Context]   <-- Written daily by Python                 |
|  [Telemetry_Archive]  <-- Written daily by Python (14d window)    |
|  [Philosophy_&_Rules] <-- User-maintained coaching constraints    |
|  [Exercise_Config]    <-- User-maintained gym equipment/increments|
+---------------------------------+---------------------------------+
                                  |
                                  | Google Drive Connector (11:00 AM IST)
                                  v
+-------------------------------------------------------------------+
|             Consumer Gemini Scheduled Action (Cloud)              |
|                                                                   |
|  - Reads `Philosophy_&_Rules`, `Exercise_Config`, `Briefing_...`  |
|  - Applies autoregulation & progression heuristics                |
|  - Pushes Morning Debrief + Gym-Floor Target Card to Android App  |
+-------------------------------------------------------------------+
```

---

## 3. Component Specifications & Auth Lifecycle

### 3.1 Caliber MCP Client (`https://api.caliberstrong.com/mcp`)
* **Protocol:** Remote Model Context Protocol over HTTP/SSE.
* **Authentication:** OAuth 2.0 Authorization Code Flow with PKCE (`client_id = "caliber-mcp"`, no client secret).
* **Token Rotation Mechanics (Critical Path):**
  * Initial bootstrap is executed once via a cloud notebook (e.g., Google Colab) to obtain the initial `refresh_token`, stored as a GitHub Actions Secret (`CALIBER_REFRESH_TOKEN`).
  * Because modern OAuth 2.0 servers invalidate refresh tokens upon single use, the Python runner captures the newly issued `refresh_token` during every daily run, encrypts it using `PyNaCl` against the repository's public key, and overwrites `CALIBER_REFRESH_TOKEN` via `PATCH /repos/{owner}/{repo}/actions/secrets/CALIBER_REFRESH_TOKEN` using a fine-grained `GH_PAT` (scoped strictly to `Secrets: Read & Write` on this repo).
* **MCP Tool Invocations (Read-Only):**
  * `getWorkoutPlans` $\rightarrow$ Active rolling template sequence, exercise IDs, target set/rep schemes.
  * `getWorkouts` $\rightarrow$ Trailing 14 days of completed workout logs.
  * `getExerciseSessions` $\rightarrow$ Last 3 historical performances per exercise in the upcoming session.
  * `getNutrition` & `getNutritionTargets` $\rightarrow$ Trailing 7-day calorie/protein intake vs. targets.
  * `getSteps` & `getBodyStats` $\rightarrow$ Daily step volume and 7-day bodyweight trend.

### 3.2 Garmin Connect Personal API
* **Authentication:** Static Bearer token (`GARMIN_PERSONAL_API_KEY`) generated via Garmin Developer Portal. No token rotation required.
* **Telemetry Extracted:**
  * **Overnight HRV:** Last night's average HRV ($\text{ms}$), 7-day rolling baseline, and Garmin status (`BALANCED`, `UNBALANCED`, `LOW`).
  * **Sleep Architecture:** Total sleep duration, deep/REM/light/awake breakdown, sleep start/end timestamps.
  * **Cardiovascular & Strain:** Overnight resting heart rate ($\text{bpm}$), Body Battery morning peak, and any logged cardio/running activities from the previous 24 hours.
* **Race-Condition Mitigation (Watch Sync Lag):**
  * Garmin's cloud endpoints remain `null` for the current date until the user's watch syncs via Bluetooth to the Garmin Connect mobile app post-wake.
  * Scheduling the runner at **10:45 AM IST** provides a buffer for wake times between 9:00 AM and 10:30 AM.
  * If `today_hrv` is still `null` at 10:45 AM, Python degrades gracefully—writing `biometric_status = "PENDING_WATCH_SYNC"` rather than raising an exception—so Gemini still generates lifting targets without biometric gating.

---

## 4. Google Sheets Data Layer (`Caliber_Daily_Sync`)

The workbook acts as a lightweight, human-inspectable database and rule engine split into four tabs:

| Tab Name | Owner | Mutability | Purpose & Schema |
| :--- | :--- | :--- | :--- |
| **`Briefing_Context`** | Python ETL | Overwritten daily | Pre-computed JSON-like key-value blocks and flat tables summarizing yesterday's state, today's target workout, per-exercise deltas, and biometric readiness. |
| **`Telemetry_Archive`** | Python ETL | Overwritten daily | Normalized 14-day rolling log of workouts, daily macros, steps, HRV, and sleep for secondary trend inspection. |
| **`Philosophy_&_Rules`** | User | Manual edits | Natural-language governance rules: RIR targets, set structures (e.g., top-set + back-off), volume landmarks, stall-breaker rules, and HRV/sleep autoregulation triggers. |
| **`Exercise_Config`** | User | Manual edits | Per-exercise physical constraints: minimum weight jump ($\text{kg}$), progression model (double vs. dynamic), equipment type (barbell, dumbbell, stack), and biomechanical cues. |

---

## 5. Deterministic State & Math Engine (Python)

Before writing `Briefing_Context`, Python executes four deterministic routines:

### 5.1 Rolling Split State Machine
1. Extract ordered workout templates $W = [W_0, W_1, \dots, W_{n-1}]$ from `getWorkoutPlans`.
2. Scan `getWorkouts` in reverse chronological order to find the most recent session $S_{\text{last}}$ whose `template_id` maps to an index $k \in [0, n-1]$.
   * *Ad-Hoc Filtering:* If a logged session does not match any `template_id` in $W$ (e.g., an impromptu mobility or arm session), do **not** advance the pointer $k$; instead, flag it under `unplanned_fatigue_events`.
3. Set `today_workout` $= W_{(k + 1) \bmod n}$.
4. Compute `days_since_last_workout` and `days_since_same_split` (elapsed days since $W_{(k + 1) \bmod n}$ was last performed).

### 5.2 Per-Exercise Progression & Stall Detection
For each exercise $E_i$ in `today_workout`:
* Retrieve the last 3 sessions of $E_i$ via `getExerciseSessions`.
* Compute working-set tonnage ($\sum \text{weight} \times \text{reps}$), estimated 1RM via Epley formula ($\text{e1RM} = w \cdot (1 + \frac{r}{30})$), and rep-ceiling attainment (`all_sets_hit_top_rep: bool`).
* Calculate `consecutive_stalls`: integer count of consecutive sessions where neither top-set load nor total working reps increased.

### 5.3 Biometric Readiness Classifier
Compute 7-day rolling mean ($\mu_{\text{HRV}}, \mu_{\text{RHR}}$) and standard deviation ($\sigma_{\text{HRV}}$) from Garmin logs, then classify morning readiness:
* **`GREEN`:** $\text{HRV}_{\text{today}} \ge \mu_{\text{HRV}} - 0.5\sigma_{\text{HRV}}$ AND $\text{Sleep}_{\text{total}} \ge 6.5\text{ h}$.
* **`AMBER`:** $\text{HRV}_{\text{today}} < \mu_{\text{HRV}} - 0.5\sigma_{\text{HRV}}$ OR $\text{Sleep}_{\text{total}} < 5.5\text{ h}$ OR $\text{RHR}_{\text{today}} \ge \mu_{\text{RHR}} + 4\text{ bpm}$.
* **`RED`:** $\ge 2$ consecutive days in `AMBER` OR $\text{HRV}_{\text{today}} < \mu_{\text{HRV}} - 1.5\sigma_{\text{HRV}}$.

---

## 6. Daily Execution Timeline & Gemini Prompt Contract

* **10:45 AM IST (`05:15 UTC`):** GitHub Actions cron triggers `sync_pipeline.py`. Execution time ~8–12 seconds. Writes updated tabs to `Caliber_Daily_Sync` and rotates `CALIBER_REFRESH_TOKEN`.
* **11:00 AM IST:** Consumer Gemini Scheduled Action wakes up and executes the following system contract:
  1. Read `Philosophy_&_Rules` and `Exercise_Config` as strict constraints.
  2. Read `Briefing_Context` (yesterday's recap, biometric readiness tier, macro compliance, and pre-computed lift histories).
  3. Generate a two-part response delivered natively to the Gemini Android app:
     * **Part 1 — Morning Debrief (Max 4 bullets):** Yesterday's training/rest summary, nutrition & Garmin recovery synthesis, and readiness call (`GREEN`/`AMBER`/`RED` impact).
     * **Part 2 — Gym-Floor Target Card (Compact Table):** `Exercise | Warm-Up Ramp | Last Session | Today's Target (Sets × Reps @ kg) | Cue`.

---

## 7. Known Trade-Offs, Failure Modes & Critique Questions

For technical reviewers evaluating this architecture, consider the following operational edge cases:

1. **GitHub Actions Cron Jitter:**
   * *Risk:* GitHub's `schedule` trigger on public/private repos can experience 5–20 minute queue delays during peak UTC hours. If `05:15 UTC` drifts past `05:30 UTC` (11:00 AM IST), Gemini will read stale data from the previous day.
   * *Mitigation Candidates:* Include a `last_synced_utc` timestamp cell in `Briefing_Context` so Gemini explicitly warns the user if data is $>2\text{ hours}$ old, or shift the GitHub cron earlier to `10:15 AM IST` (`04:45 UTC`).
2. **OAuth Secret Rotation Atomicity:**
   * *Risk:* If the script successfully exchanges `CALIBER_REFRESH_TOKEN` for a new pair, but the network drops before `PATCH /actions/secrets/CALIBER_REFRESH_TOKEN` completes, the stored secret becomes invalid and requires a manual re-bootstrap.
   * *Mitigation Candidates:* Write the rotated token immediately to an encrypted fallback artifact or private GitHub Gist before attempting the Secret API update, or retry the Secret `PATCH` with exponential backoff.
3. **Consumer Scheduled Action Drive Read Reliability:**
   * *Risk:* Consumer Gemini Scheduled Actions rely on the native Google Workspace connector to parse Sheets. Multi-tab spreadsheets with complex formatting occasionally truncate long tables.
   * *Mitigation Candidates:* Keep `Briefing_Context` ultra-compact ($<150$ rows, plain text/numbers, zero merged cells) or export `Briefing_Context` as a clean Markdown file (`briefing_today.md`) in Google Drive alongside the Sheet.
4. **Caliber MCP Schema Stability:**
   * *Risk:* Caliber's MCP server is actively evolving (currently read-only; write tools are on their roadmap). Field names in `getWorkoutPlans` or `getExerciseSessions` may change without versioning headers.
   * *Mitigation Candidates:* Validate MCP tool payloads with strict `pydantic` schemas in Python and send an alert (or write an explicit error banner to `Briefing_Context`) on schema drift.

### Questions for Critique
* Is Google Sheets the optimal middle layer for consumer Gemini Scheduled Actions, or would writing a single structured Markdown/JSON file to Google Drive reduce parsing latency and token overhead?
* Would adding an external cron trigger (e.g., `cron-job.org` hitting GitHub's `workflow_dispatch` API at exactly `10:45 AM IST`) be preferable to GitHub's native `schedule` trigger to eliminate cron drift?
* Given that Caliber's roadmap includes MCP write tools, how should the architecture evolve once Gemini/Python can push the prescribed workout directly into Caliber as a scheduled template?