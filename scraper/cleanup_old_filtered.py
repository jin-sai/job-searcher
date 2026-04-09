"""
Cleanup — Remove old Filtered Out jobs
Deletes rows from the Google Sheet where:
  - Status = "Filtered Out"
  - Date Found is older than 7 days
Rows are deleted bottom-up to preserve row indices.
"""

from datetime import datetime, timedelta
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

SHEET_NAME       = "Job Search Tracker"
CREDENTIALS_FILE = Path(__file__).parent.parent / "credentials.json"
CUTOFF_DAYS      = 15

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

COL_DATE_FOUND = 9   # column I (1-indexed)
COL_STATUS     = 11  # column K (1-indexed)


def main():
    creds  = Credentials.from_service_account_file(str(CREDENTIALS_FILE), scopes=SCOPES)
    client = gspread.authorize(creds)
    ws     = client.open(SHEET_NAME).sheet1

    cutoff = datetime.today() - timedelta(days=CUTOFF_DAYS)
    rows   = ws.get_all_values()  # includes header at index 0

    to_delete = []
    for i, row in enumerate(rows[1:], start=2):  # data rows start at sheet row 2
        status     = row[COL_STATUS - 1].strip()     if len(row) >= COL_STATUS     else ""
        date_found = row[COL_DATE_FOUND - 1].strip() if len(row) >= COL_DATE_FOUND else ""

        if status != "Filtered Out":
            continue
        try:
            found_dt = datetime.strptime(date_found, "%Y-%m-%d")
        except ValueError:
            continue  # skip rows with missing/unparseable date

        if found_dt < cutoff:
            to_delete.append(i)

    if not to_delete:
        print("No old filtered-out jobs to remove.")
        return

    print(f"Deleting {len(to_delete)} rows older than {CUTOFF_DAYS} days...")

    # Delete bottom-up so row indices stay valid
    for row_idx in reversed(to_delete):
        ws.delete_rows(row_idx)

    print("Done.")


if __name__ == "__main__":
    main()
