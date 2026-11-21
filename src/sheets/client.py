"""gspread client wrapper for Service Account authentication."""

import logging
from typing import Optional
import gspread
from google.oauth2.service_account import Credentials

from src.config import settings

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class GoogleSheetsService:
    """Manages authentication and worksheet access for Google Sheets."""

    def __init__(self, sheet_id_or_name: Optional[str] = None):
        self.sheet_target = sheet_id_or_name or settings.GOOGLE_SHEET_ID or settings.GOOGLE_SHEET_NAME
        self.client: Optional[gspread.Client] = None
        self.spreadsheet: Optional[gspread.Spreadsheet] = None

    def authenticate(self) -> gspread.Client:
        """Authenticate using Service Account JSON credentials (file or environment string)."""
        creds_info = settings.get_google_credentials_info()
        if not creds_info:
            raise ValueError(
                "Google Service Account credentials missing. Provide GOOGLE_SERVICE_ACCOUNT_FILE "
                "or GOOGLE_SERVICE_ACCOUNT_JSON."
            )

        credentials = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
        self.client = gspread.authorize(credentials)
        logger.info("Successfully authenticated with Google Sheets API.")
        return self.client

    def open_spreadsheet(self) -> gspread.Spreadsheet:
        """Open target spreadsheet by ID (if configured) or by title."""
        if not self.client:
            self.authenticate()

        if settings.GOOGLE_SHEET_ID:
            try:
                self.spreadsheet = self.client.open_by_key(settings.GOOGLE_SHEET_ID)
                logger.info("Opened spreadsheet by ID: %s", settings.GOOGLE_SHEET_ID)
                return self.spreadsheet
            except Exception as e:
                logger.warning("Could not open spreadsheet by ID %s: %s", settings.GOOGLE_SHEET_ID, e)

        # Fallback to opening by title
        self.spreadsheet = self.client.open(settings.GOOGLE_SHEET_NAME)
        logger.info("Opened spreadsheet by title: %s", settings.GOOGLE_SHEET_NAME)
        return self.spreadsheet

    def get_or_create_worksheet(
        self,
        title: str,
        rows: int = 100,
        cols: int = 20,
    ) -> gspread.Worksheet:
        """Get an existing worksheet by title, or create it if not present."""
        if not self.spreadsheet:
            self.open_spreadsheet()

        try:
            return self.spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            logger.info("Worksheet '%s' not found. Creating new sheet.", title)
            return self.spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)
