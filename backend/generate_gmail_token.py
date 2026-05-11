"""
Generate Gmail OAuth token for the ITR Platform.

Run this locally (NOT in Docker) to generate a token JSON
that you can then upload via POST /api/v1/email/token.

Usage:
    python generate_gmail_token.py <path_to_client_secret.json>

Prerequisites:
    pip install google-auth-oauthlib
"""

import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def main():
    if len(sys.argv) < 2:
        print("Usage: python generate_gmail_token.py <path_to_client_secret.json>")
        sys.exit(1)

    credentials_file = sys.argv[1]

    print(f"Using credentials from: {credentials_file}")
    print("A browser window will open for authorization...\n")

    flow = InstalledAppFlow.from_client_secrets_file(credentials_file, scopes=SCOPES)
    creds = flow.run_local_server(port=8090)

    # Build complete token JSON
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if creds.scopes else SCOPES,
    }

    token_json = json.dumps(token_data, indent=2)

    # Save to file
    output_file = "gmail_token.json"
    with open(output_file, "w") as f:
        f.write(token_json)

    print(f"\n{'='*60}")
    print(f"Token saved to: {output_file}")
    print(f"{'='*60}")
    print(f"\nNow upload it via the API:")
    print(f'  POST /api/v1/email/token')
    print(f'  Body: {{"token_json": <contents of {output_file}>}}')
    print(f"\nOr copy this token JSON:\n")
    print(token_json)


if __name__ == "__main__":
    main()
