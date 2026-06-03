"""
Data Feed Script — Create Managers, Executives, and Assign them via API.

Usage:
    python feed_users.py

Prerequisites:
    pip install openpyxl requests

Configuration:
    Update the variables below before running.
"""

import sys
import time
import openpyxl
import requests

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION — UPDATE THESE BEFORE RUNNING
# ═══════════════════════════════════════════════════════════════

# API base URL (Traefik on your LAN)
API_BASE = "http://192.167.201.15:8080/api/v1"

# Partner credentials (needed to create managers and executives)
PARTNER_EMAIL = "admin@itr-platform.com"
PARTNER_PASSWORD = "admin123"

# Default password for new manager and executive accounts
DEFAULT_PASSWORD = "Welcome@123"

# Excel file path (relative to this script's location or absolute)
EXCEL_FILE = "Article Manager Mapping.xlsx"

# ═══════════════════════════════════════════════════════════════
# SCRIPT LOGIC
# ═══════════════════════════════════════════════════════════════

session = requests.Session()


def login(email: str, password: str) -> str:
    """Login and return the JWT access token."""
    resp = session.post(f"{API_BASE}/auth/login", json={
        "email": email,
        "password": password,
    })
    if resp.status_code != 200:
        print(f"[ERROR] Login failed for {email}: {resp.status_code} — {resp.text}")
        sys.exit(1)
    token = resp.json()["access_token"]
    print(f"[OK] Logged in as {email}")
    return token


def set_auth(token: str):
    """Set the Authorization header for subsequent requests."""
    session.headers["Authorization"] = f"Bearer {token}"


def create_manager(email: str, full_name: str, password: str) -> dict:
    """Create a manager via POST /managers."""
    resp = session.post(f"{API_BASE}/managers", json={
        "email": email,
        "full_name": full_name,
        "password": password,
    })
    if resp.status_code == 201:
        data = resp.json()
        print(f"  [OK] Manager created: {full_name} ({email}) → ID: {data['id']}")
        return data
    elif resp.status_code == 409 or "already" in resp.text.lower():
        print(f"  [SKIP] Manager already exists: {full_name} ({email})")
        # Try to find existing manager by listing
        return None
    else:
        print(f"  [ERROR] Creating manager {full_name}: {resp.status_code} — {resp.text}")
        return None


def create_executive(email: str, full_name: str, password: str) -> dict:
    """Create an executive via POST /executives."""
    resp = session.post(f"{API_BASE}/executives", json={
        "email": email,
        "full_name": full_name,
        "password": password,
    })
    if resp.status_code == 201:
        data = resp.json()
        print(f"  [OK] Executive created: {full_name} ({email}) → ID: {data['id']}")
        return data
    elif resp.status_code == 409 or "already" in resp.text.lower():
        print(f"  [SKIP] Executive already exists: {full_name} ({email})")
        return None
    else:
        print(f"  [ERROR] Creating executive {full_name}: {resp.status_code} — {resp.text}")
        return None


def list_managers() -> list:
    """List all managers to get their IDs."""
    resp = session.get(f"{API_BASE}/managers")
    if resp.status_code == 200:
        return resp.json().get("items", [])
    print(f"  [ERROR] Listing managers: {resp.status_code} — {resp.text}")
    return []


def list_executives() -> list:
    """List all executives to get their IDs."""
    resp = session.get(f"{API_BASE}/executives")
    if resp.status_code == 200:
        return resp.json().get("items", [])
    print(f"  [ERROR] Listing executives: {resp.status_code} — {resp.text}")
    return []


def assign_executive_to_manager(manager_id: str, executive_id: str, manager_name: str, exec_name: str):
    """Assign an executive to a manager's team."""
    resp = session.post(f"{API_BASE}/managers/{manager_id}/assign-executive", json={
        "executive_id": executive_id,
    })
    if resp.status_code == 201:
        print(f"  [OK] Assigned {exec_name} → {manager_name}")
    elif resp.status_code == 409 or "already" in resp.text.lower():
        print(f"  [SKIP] {exec_name} already assigned to {manager_name}")
    else:
        print(f"  [ERROR] Assigning {exec_name} → {manager_name}: {resp.status_code} — {resp.text}")


def read_excel(filepath: str):
    """
    Read the Excel file and return:
      - managers: list of {name, email}
      - executives: list of {name, email, manager_name}
    """
    wb = openpyxl.load_workbook(filepath, read_only=True)
    ws = wb.active

    managers = []
    executives = []

    for row in ws.iter_rows(min_row=2, values_only=True):
        article_name, manager_name, email = row[0], row[1], row[2]

        if article_name is None:
            # This is a manager row (last 4 rows have no article name)
            if manager_name and email:
                managers.append({"name": manager_name.strip(), "email": email.strip()})
        else:
            # This is an executive (article) row
            executives.append({
                "name": article_name.strip(),
                "email": email.strip(),
                "manager_name": manager_name.strip() if manager_name else None,
            })

    wb.close()
    return managers, executives


def main():
    print("=" * 60)
    print("ITR Manager — User Data Feed Script")
    print("=" * 60)

    # ── Step 1: Read Excel ──
    print("\n[1/5] Reading Excel file...")
    managers_data, executives_data = read_excel(EXCEL_FILE)
    print(f"  Found {len(managers_data)} managers, {len(executives_data)} executives")

    print("\n  Managers:")
    for m in managers_data:
        print(f"    • {m['name']} — {m['email']}")
    print(f"\n  Executives (first 5):")
    for e in executives_data[:5]:
        print(f"    • {e['name']} — {e['email']} (→ {e['manager_name']})")
    print(f"    ... and {len(executives_data) - 5} more")

    # ── Step 2: Login as Partner ──
    print("\n[2/5] Logging in as Partner...")
    token = login(PARTNER_EMAIL, PARTNER_PASSWORD)
    set_auth(token)

    # ── Step 3: Create Managers ──
    print("\n[3/5] Creating managers...")
    for m in managers_data:
        create_manager(email=m["email"], full_name=m["name"], password=DEFAULT_PASSWORD)
        time.sleep(0.2)  # Small delay to avoid rate limiting

    # ── Step 4: Create Executives ──
    print("\n[4/5] Creating executives...")
    for e in executives_data:
        create_executive(email=e["email"], full_name=e["name"], password=DEFAULT_PASSWORD)
        time.sleep(0.2)

    # ── Step 5: Assign Executives to Managers ──
    print("\n[5/5] Assigning executives to managers...")

    # Fetch all managers and executives to get their UUIDs
    all_managers = list_managers()
    all_executives = list_executives()

    # Build lookup maps by email (lowercase for safe matching)
    manager_map = {}  # email -> {id, full_name}
    for m in all_managers:
        manager_map[m["email"].lower()] = {"id": m["id"], "name": m["full_name"]}

    executive_map = {}  # email -> {id, full_name}
    for e in all_executives:
        executive_map[e["email"].lower()] = {"id": e["id"], "name": e["full_name"]}

    # Build manager name -> id lookup from the Excel manager data
    manager_name_to_id = {}
    for m in managers_data:
        key = m["name"].lower()
        mgr_info = manager_map.get(m["email"].lower())
        if mgr_info:
            manager_name_to_id[key] = mgr_info
        else:
            print(f"  [WARN] Manager '{m['name']}' ({m['email']}) not found in API response")

    # Assign each executive to their manager
    for e in executives_data:
        exec_info = executive_map.get(e["email"].lower())
        mgr_info = manager_name_to_id.get(e["manager_name"].lower()) if e["manager_name"] else None

        if not exec_info:
            print(f"  [WARN] Executive '{e['name']}' ({e['email']}) not found — skipping assignment")
            continue
        if not mgr_info:
            print(f"  [WARN] Manager '{e['manager_name']}' not resolved — skipping {e['name']}")
            continue

        assign_executive_to_manager(
            manager_id=mgr_info["id"],
            executive_id=exec_info["id"],
            manager_name=mgr_info["name"],
            exec_name=exec_info["name"],
        )
        time.sleep(0.2)

    print("\n" + "=" * 60)
    print("DONE! All users created and assigned.")
    print("=" * 60)


if __name__ == "__main__":
    main()
