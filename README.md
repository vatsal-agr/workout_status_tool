# Caliber + Garmin Headless Telemetry Pipeline & Coaching Engine

Automated, daily strength-and-recovery coaching system that bridges Caliber lifting telemetry and Garmin autonomic recovery metrics with Consumer Gemini Scheduled Actions at **$0 compute and API cost**.

---

## Architecture Overview

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
|  1. Auth & Fetch:                                                 |
|     - Rotate CALIBER_REFRESH_TOKEN via GH Secrets API (PyNaCl)    |
|     - Query Caliber MCP tools (plans, workouts, nutrition, etc.)  |
|     - Query Garmin endpoints (HRV, sleep, RHR, activities)        |
|                                                                   |
|  2. Deterministic State & Math Engine (Python):                   |
|     - Rolling split pointer & ad-hoc fatigue event filter         |
|     - Epley e1RM, working tonnage & consecutive stall counter     |
|     - 7-day rolling HRV/RHR baselines & readiness tier (G/A/R)    |
|                                                                   |
|  3. Google Sheets Writer (gspread):                               |
|     - Overwrites Briefing_Context (<150 rows) & Telemetry_Archive |
+---------------------------------+---------------------------------+
                                  |
                                  | Google Sheets API (GCP_SA_KEY)
                                  v
+-------------------------------------------------------------------+
|               Google Sheet: Caliber_Daily_Sync                    |
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
|  - Reads Philosophy_&_Rules, Exercise_Config, Briefing_Context    |
|  - Applies autoregulation & double progression heuristics         |
|  - Pushes Morning Debrief + Gym-Floor Target Card to Android App  |
+-------------------------------------------------------------------+
```

---

## Human Key & Credential Setup Checklist

Before running the pipeline, configure the following keys in your local `.env` or GitHub Repository Secrets:

### 1. Caliber MCP OAuth 2.0 PKCE (`CALIBER_REFRESH_TOKEN`)
* Run the interactive bootstrap utility:
  ```bash
  python scripts/bootstrap_caliber_auth.py
  ```
* Open the displayed URL in your browser, log into Caliber, and authorize the app.
* Paste the authorization code back into the terminal.
* The tool will output your initial `CALIBER_REFRESH_TOKEN`.
* **Add to GitHub Secrets:** Name: `CALIBER_REFRESH_TOKEN`.

### 2. GitHub Personal Access Token (`GH_PAT`)
* Required for the pipeline to self-rotate the Caliber refresh token every morning.
* Create a Fine-Grained Personal Access Token in GitHub:
  * Repository: `Workout_Status_Tool` (or your repo name).
  * Permissions: **Repository Secrets: Read & Write**.
* **Add to GitHub Secrets:** Name: `GH_PAT`.

### 3. Garmin Connect Personal API Key (`GARMIN_PERSONAL_API_KEY`)
* Create an application in the [Garmin Developer Portal](https://developer.garmin.com/).
* Obtain your static Bearer API Key.
* **Add to GitHub Secrets:** Name: `GARMIN_PERSONAL_API_KEY`.

### 4. Google Cloud Service Account (`GCP_SA_KEY`)
* Create a project in [Google Cloud Console](https://console.cloud.google.com/).
* Enable **Google Sheets API** and **Google Drive API**.
* Create a Service Account and download its JSON key file.
* Create a Google Sheet named `Caliber_Daily_Sync`.
* Share the Google Sheet (Editor permissions) with the service account email (e.g., `service-account@project.iam.gserviceaccount.com`).
* **Add to GitHub Secrets:** Name: `GCP_SA_KEY` (paste the raw JSON text from the key file).

### 5. Google Sheet ID (`GOOGLE_SHEET_ID`)
* The spreadsheet ID found in the URL: `https://docs.google.com/spreadsheets/d/<GOOGLE_SHEET_ID>/edit`.
* **Add to GitHub Secrets:** Name: `GOOGLE_SHEET_ID`.

---

## Local Development & Testing

1. **Set up virtual environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure local environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

3. **Run unit & integration test suite:**
   ```bash
   pytest -v
   ```

4. **Run a manual dry-run of the pipeline:**
   ```bash
   python -m src.pipeline
   ```

---

## Setting Up Consumer Gemini Scheduled Action

1. Open the **Google Gemini App** (on Android or Web).
2. Tap **Scheduled Actions** (or Scheduled Prompts).
3. Schedule for **11:00 AM IST (Daily)**.
4. Ensure the Google Drive extension is enabled for Gemini.
5. Paste the system prompt contract from [`prompts/gemini_scheduled_action_prompt.md`](prompts/gemini_scheduled_action_prompt.md).

Every morning at 11:00 AM IST, Gemini will read the freshly populated sheet from Google Drive and deliver your 4-bullet debrief and gym-floor target table directly to your phone.
