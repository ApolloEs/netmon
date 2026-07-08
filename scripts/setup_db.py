"""
One-command bare-metal database bootstrap.

Reads the app's database.url from config.yaml and creates the role and
database it expects, so the script and the app agree by construction.
Connects to the `postgres` maintenance database with ADMIN credentials
(the app's own user doesn't exist yet). Idempotent — safe to re-run.

The Docker path does not need this: compose provisions the role and
database via POSTGRES_USER/PASSWORD/DB.

Usage:
    python scripts/setup_db.py
    python scripts/setup_db.py --admin-url postgresql://postgres:secret@localhost:5432/postgres
    PGADMIN_URL=postgresql://... python scripts/setup_db.py
    python scripts/setup_db.py --config path/to/config.yaml
"""

import argparse
import os
import re
import sys
from pathlib import Path

# Allow running from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from netmon import config as cfg

# Conservative identifier whitelist — quoting alone is not a substitute
# for validation when interpolating into DDL.
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create the LineProof database role + database (idempotent)."
    )
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    parser.add_argument(
        "--admin-url",
        default=os.environ.get("PGADMIN_URL"),
        help="Admin connection URL to the postgres maintenance DB "
             "(default: postgres superuser on the app URL's host/port; "
             "also settable via PGADMIN_URL)",
    )
    args = parser.parse_args()

    conf = cfg.load(args.config) if args.config else cfg.load()
    app_url = make_url(conf.database.url)
    user, password, dbname = app_url.username, app_url.password, app_url.database

    if not user or not dbname:
        sys.exit("database.url in config.yaml must include a username and database name.")
    if not password:
        sys.exit("database.url in config.yaml must include a password "
                 "(the created role needs one to log in).")
    for ident in (user, dbname):
        if not IDENT_RE.match(ident):
            sys.exit(f"Refusing to use identifier {ident!r} — letters, digits, "
                     "and underscores only.")

    admin_url = args.admin_url or (
        f"postgresql://postgres@{app_url.host or 'localhost'}:{app_url.port or 5432}/postgres"
    )
    masked_admin = make_url(admin_url).render_as_string(hide_password=True)
    print(f"Admin connection: {masked_admin}")

    # AUTOCOMMIT: CREATE DATABASE cannot run inside a transaction block.
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            role_exists = conn.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": user}
            ).scalar()
            if role_exists:
                print(f"Role     '{user}': already exists — leaving it (and its password) unchanged.")
            else:
                pw_literal = password.replace("'", "''")
                conn.exec_driver_sql(f"CREATE ROLE \"{user}\" LOGIN PASSWORD '{pw_literal}'")
                print(f"Role     '{user}': created.")

            db_exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :d"), {"d": dbname}
            ).scalar()
            if db_exists:
                print(f"Database '{dbname}': already exists — skipped.")
            else:
                conn.exec_driver_sql(f'CREATE DATABASE "{dbname}" OWNER "{user}"')
                print(f"Database '{dbname}': created (owner '{user}').")
    except OperationalError as exc:
        reason = str(exc.orig).strip().splitlines()[0] if exc.orig else str(exc)
        sys.exit(
            f"Could not connect as admin to {masked_admin} ({reason}).\n"
            "Pass working admin credentials via --admin-url or PGADMIN_URL, e.g.\n"
            "  python scripts/setup_db.py --admin-url postgresql://postgres:SECRET@localhost:5432/postgres"
        )

    print("\nDone. Next step:  python -m netmon.main   (tables are created automatically)")


if __name__ == "__main__":
    main()
