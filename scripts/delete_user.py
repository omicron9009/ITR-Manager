"""Delete a user (and all related rows) from the ITR-Manager Postgres database.

Run from your local machine across the network. By default the script is a
DRY-RUN: it shows what will be touched and rolls the transaction back. Pass
``--execute`` to actually commit the changes.

Examples
--------
Dry-run against a remote DB:

    python scripts/delete_user.py --email disha.gehi@pgjco.com \
        --host 192.167.201.15 --port 5432 \
        --db itr_platform --user postgres --password postgres

Actually delete + reassign "history" FKs to another partner:

    python scripts/delete_user.py --email disha.gehi@pgjco.com \
        --host 192.167.201.15 --port 5432 \
        --db itr_platform --user postgres --password postgres \
        --reassign-to partner@pgjco.com --execute

Notes
-----
* The default ``new_dockercompose.yml`` does NOT publish port 5432 on the host.
  Either add ``ports: ["5432:5432"]`` to the postgres service, or run this
  script on the docker host with ``--host 127.0.0.1`` (still won't work unless
  the port is published), or temporarily open a tunnel:
      ssh -L 5432:127.0.0.1:5432 user@docker-host
  and then use ``--host 127.0.0.1``.
* Credentials default to the values found in ``deploy/.env``
  (``postgres`` / ``postgres`` / ``itr_platform``); override on the CLI for
  any real deployment.
* The script runs inside a single transaction. On any error it rolls back.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional, Tuple

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "psycopg2 is required. Install with:  pip install psycopg2-binary\n"
    )
    sys.exit(2)


# ---------------------------------------------------------------------------
# Catalog of every users.id reference in the schema.
# Derived from backend/app/models/*.  Update this list if new FKs are added.
# Each entry: (table, column, ondelete-behaviour)
# ---------------------------------------------------------------------------
USER_FK_REFS: List[Tuple[str, str, str]] = [
    ("users", "activated_by", "SET NULL"),
    ("audit_logs", "actor_id", "SET NULL"),
    ("audit_logs", "client_id", "SET NULL"),
    ("client_income_heads", "user_id", "CASCADE"),
    ("client_profiles", "user_id", "CASCADE"),
    ("executive_client_assignments", "executive_id", "CASCADE"),
    ("executive_client_assignments", "client_id", "CASCADE"),
    ("executive_client_assignments", "assigned_by", "RESTRICT"),
    ("executive_tags", "executive_id", "CASCADE"),
    ("executive_tags", "assigned_by", "RESTRICT"),
    ("filing_completed_docs", "uploaded_by", "RESTRICT"),
    ("filing_completed_docs", "manager_approved_by", "SET NULL"),
    ("filing_completed_docs", "manager_rejected_by", "SET NULL"),
    ("filing_completed_docs", "partner_approved_by", "SET NULL"),
    ("filing_computations", "uploaded_by", "RESTRICT"),
    ("filing_computations", "manager_approved_by", "SET NULL"),
    ("filing_computations", "manager_rejected_by", "SET NULL"),
    ("filing_computations", "partner_approved_by", "SET NULL"),
    ("filing_computations", "approved_by", "SET NULL"),
    ("filing_computations", "rejected_by", "SET NULL"),
    ("filing_documents", "reviewed_by", "SET NULL"),
    ("filing_documents", "assigned_by", "RESTRICT"),
    ("filing_feedback", "client_id", "CASCADE"),
    ("filing_other_docs", "uploaded_by", "RESTRICT"),
    ("filing_state_history", "changed_by", "RESTRICT"),
    ("filing_text_fields", "filled_by", "SET NULL"),
    ("filing_text_fields", "reviewed_by", "SET NULL"),
    ("filing_text_fields", "assigned_by", "RESTRICT"),
    ("internal_working_docs", "uploaded_by", "RESTRICT"),
    ("itr_filings", "client_id", "RESTRICT"),
    ("itr_filings", "assigned_executive_id", "SET NULL"),
    ("itr_filings", "fee_proposed_by", "SET NULL"),
    ("itr_filings", "halted_by", "SET NULL"),
    ("itr_filings", "created_by", "RESTRICT"),
    ("itr_filings", "updated_by", "SET NULL"),
    ("manager_client_assignments", "manager_id", "CASCADE"),
    ("manager_client_assignments", "client_id", "CASCADE"),
    ("manager_client_assignments", "assigned_by", "SET NULL"),
    ("manager_executive_assignments", "manager_id", "CASCADE"),
    ("manager_executive_assignments", "executive_id", "CASCADE"),
    ("manager_executive_assignments", "assigned_by", "SET NULL"),
    ("manager_tags", "manager_id", "CASCADE"),
    ("manager_tags", "assigned_by", "RESTRICT"),
    ("master_document_types", "created_by", "RESTRICT"),
    ("master_document_types", "updated_by", "SET NULL"),
    ("master_text_field_types", "created_by", "RESTRICT"),
    ("master_text_field_types", "updated_by", "SET NULL"),
    ("notifications", "user_id", "CASCADE"),
    ("notifications", "related_client_id", "SET NULL"),
    ("onboarding_form_fields", "created_by", "RESTRICT"),
    ("onboarding_form_fields", "updated_by", "SET NULL"),
    ("recovery_codes", "user_id", "CASCADE"),
    ("stored_files", "uploaded_by", "SET NULL"),
    ("tags", "created_by", "RESTRICT"),
    ("viewer_completed_queue", "viewer_id", "CASCADE"),
    ("viewer_completed_queue", "completed_by", "SET NULL"),
    ("viewer_completed_queue", "executive_id", "SET NULL"),
    ("viewer_completed_queue", "manager_id", "SET NULL"),
]

# Subset of RESTRICT FKs that we will *reassign* (rather than delete the row).
# These represent "actor history": who created / assigned / uploaded.
# For these we simply UPDATE the column to the reassign-to user's id.
REASSIGN_RESTRICT_FKS: List[Tuple[str, str]] = [
    ("executive_client_assignments", "assigned_by"),
    ("executive_tags",               "assigned_by"),
    ("filing_completed_docs",        "uploaded_by"),
    ("filing_computations",          "uploaded_by"),
    ("filing_documents",             "assigned_by"),
    ("filing_other_docs",            "uploaded_by"),
    ("filing_state_history",         "changed_by"),
    ("filing_text_fields",           "assigned_by"),
    ("internal_working_docs",        "uploaded_by"),
    ("itr_filings",                  "created_by"),
    ("manager_tags",                 "assigned_by"),
    ("master_document_types",        "created_by"),
    ("master_text_field_types",      "created_by"),
    ("onboarding_form_fields",       "created_by"),
    ("tags",                         "created_by"),
]

# The one RESTRICT FK we do NOT reassign: itr_filings.client_id.
# If the user IS a client we must explicitly delete those filings first
# (cascades wipe documents/computations/etc).  Handled separately below.


# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--email", required=True, help="Email of the user to delete")
    p.add_argument("--host", default=os.getenv("PGHOST", "192.167.201.15"))
    p.add_argument("--port", type=int, default=int(os.getenv("PGPORT", "5432")))
    p.add_argument("--db",   default=os.getenv("PGDATABASE", "itr_platform"))
    p.add_argument("--user", default=os.getenv("PGUSER", "postgres"))
    p.add_argument("--password", default=os.getenv("PGPASSWORD", "postgres"))
    p.add_argument("--reassign-to", default=None,
                   help=("Email of the user that will inherit RESTRICT 'actor' FKs "
                         "(uploaded_by/created_by/assigned_by/changed_by). "
                         "If omitted the script picks the oldest active PARTNER."))
    p.add_argument("--execute", action="store_true",
                   help="Actually commit the transaction. Without this flag the script does a dry-run.")
    p.add_argument("--yes", action="store_true",
                   help="Skip the interactive confirmation prompt (still requires --execute).")
    return p.parse_args()


def fetch_user(cur, email: str) -> Optional[dict]:
    cur.execute(
        "SELECT id, email, full_name, role, account_status, is_active "
        "FROM users WHERE lower(email) = lower(%s)",
        (email,),
    )
    return cur.fetchone()


def pick_reassign_user(cur, target_id, reassign_email: Optional[str]) -> dict:
    """Pick a user to inherit RESTRICT actor-FKs.  Cannot be the target user."""
    if reassign_email:
        cur.execute(
            "SELECT id, email, full_name, role FROM users "
            "WHERE lower(email) = lower(%s)",
            (reassign_email,),
        )
        row = cur.fetchone()
        if not row:
            raise SystemExit(f"--reassign-to user {reassign_email!r} not found")
        if row["id"] == target_id:
            raise SystemExit("--reassign-to cannot be the same user being deleted")
        return row

    cur.execute(
        "SELECT id, email, full_name, role FROM users "
        "WHERE role = 'PARTNER' AND is_active = true AND id <> %s "
        "ORDER BY created_at ASC LIMIT 1",
        (target_id,),
    )
    row = cur.fetchone()
    if not row:
        raise SystemExit(
            "No active PARTNER user found to inherit ownership. "
            "Pass --reassign-to <email> explicitly."
        )
    return row


def count_refs(cur, target_id) -> List[Tuple[str, str, str, int]]:
    """Return [(table, col, ondelete, count)] for every users.id FK reference."""
    out = []
    for table, col, ondel in USER_FK_REFS:
        cur.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {col} = %s", (target_id,))
        n = cur.fetchone()["n"]
        out.append((table, col, ondel, n))
    return out


def print_summary(target: dict, refs: List[Tuple[str, str, str, int]],
                  reassigner: dict, filings_as_client: int) -> None:
    print("=" * 72)
    print("TARGET USER")
    print("-" * 72)
    print(f"  id            : {target['id']}")
    print(f"  email         : {target['email']}")
    print(f"  full_name     : {target['full_name']}")
    print(f"  role          : {target['role']}")
    print(f"  account_status: {target['account_status']}")
    print(f"  is_active     : {target['is_active']}")
    print()
    print("REASSIGN-TO USER (will inherit RESTRICT 'actor' FKs)")
    print("-" * 72)
    print(f"  id    : {reassigner['id']}")
    print(f"  email : {reassigner['email']}")
    print(f"  name  : {reassigner['full_name']}")
    print(f"  role  : {reassigner['role']}")
    print()
    print("REFERENCES TO THIS USER")
    print("-" * 72)
    print(f"  {'table':<36}{'column':<24}{'ondelete':<10}{'rows':>6}")
    total = 0
    for table, col, ondel, n in refs:
        if n:
            print(f"  {table:<36}{col:<24}{ondel:<10}{n:>6}")
            total += n
    print(f"  {'TOTAL referenced rows':<70}{total:>6}")
    print()
    print(f"  itr_filings where user is CLIENT : {filings_as_client}")
    print("=" * 72)


def reassign_restrict_fks(cur, target_id, new_owner_id) -> List[Tuple[str, str, int]]:
    """UPDATE every RESTRICT actor-FK from target_id -> new_owner_id."""
    log = []
    for table, col in REASSIGN_RESTRICT_FKS:
        cur.execute(
            f"UPDATE {table} SET {col} = %s WHERE {col} = %s",
            (new_owner_id, target_id),
        )
        if cur.rowcount:
            log.append((table, col, cur.rowcount))
    return log


def delete_client_filings(cur, target_id) -> int:
    """Delete every itr_filings row where this user is the client.

    Cascades clean: filing_documents, filing_text_fields, filing_computations,
    filing_completed_docs, filing_other_docs, internal_working_docs,
    filing_state_history, filing_feedback, viewer_completed_queue.
    """
    cur.execute("DELETE FROM itr_filings WHERE client_id = %s", (target_id,))
    return cur.rowcount


def delete_user(cur, target_id) -> int:
    cur.execute("DELETE FROM users WHERE id = %s", (target_id,))
    return cur.rowcount


def main() -> int:
    args = parse_args()

    print(f"Connecting to postgres://{args.user}@{args.host}:{args.port}/{args.db} ...")
    conn = psycopg2.connect(
        host=args.host, port=args.port, dbname=args.db,
        user=args.user, password=args.password,
        connect_timeout=10,
    )
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=RealDictCursor)

    try:
        target = fetch_user(cur, args.email)
        if not target:
            print(f"User with email {args.email!r} not found.")
            return 1

        reassigner = pick_reassign_user(cur, target["id"], args.reassign_to)
        refs = count_refs(cur, target["id"])
        filings_as_client = next(n for t, c, _, n in refs if t == "itr_filings" and c == "client_id")
        print_summary(target, refs, reassigner, filings_as_client)

        if not args.execute:
            print("\nDRY-RUN mode (no --execute) — performing the operations inside a")
            print("transaction and rolling them back so you can preview the effect.\n")
        elif not args.yes:
            ans = input(f"\nType DELETE to permanently remove {target['email']!r}: ").strip()
            if ans != "DELETE":
                print("Aborted.")
                return 1

        # ---- Step 1: reassign RESTRICT 'actor' FKs --------------------
        print("\n[1/3] Reassigning RESTRICT actor FKs ...")
        reassign_log = reassign_restrict_fks(cur, target["id"], reassigner["id"])
        if reassign_log:
            for t, c, n in reassign_log:
                print(f"      {t}.{c}: reassigned {n} row(s) -> {reassigner['email']}")
        else:
            print("      (nothing to reassign)")

        # ---- Step 2: delete filings where user is CLIENT -------------
        print("\n[2/3] Deleting itr_filings where this user is client ...")
        deleted_filings = delete_client_filings(cur, target["id"])
        print(f"      Deleted {deleted_filings} filing(s) (cascade wiped child rows).")

        # ---- Step 3: delete the user row -----------------------------
        print("\n[3/3] Deleting users row ...")
        deleted_users = delete_user(cur, target["id"])
        print(f"      Deleted {deleted_users} user row.")

        if args.execute:
            conn.commit()
            print("\nCOMMITTED. User has been removed.")
        else:
            conn.rollback()
            print("\nROLLED BACK. No changes were persisted (dry-run).")
            print("Re-run with --execute to apply.")
        return 0

    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print(f"\nERROR — transaction rolled back: {exc}", file=sys.stderr)
        return 2
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
