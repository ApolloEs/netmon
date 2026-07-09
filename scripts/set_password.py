"""
Set (or clear) the dashboard passphrase.

Stores only a salted hash in config.yaml under dashboard.password_hash —
the plaintext passphrase is never written anywhere. When a hash is set,
LAN/remote devices must log in before viewing the dashboard; the PC
itself (localhost) always bypasses.

Usage:
    python scripts/set_password.py            # prompt and set
    python scripts/set_password.py --clear    # remove the passphrase
    python scripts/set_password.py --config path/to/config.yaml

Restart LineProof (or Restart-Service NetMon) for the change to apply.
"""

import argparse
import getpass
import sys
from pathlib import Path

# Allow running from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent))

from werkzeug.security import generate_password_hash

from netmon import config as cfg


def _write_hash(path: Path, value: str) -> None:
    """Set dashboard.password_hash in-place, preserving YAML comments."""
    from ruamel.yaml import YAML

    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    with open(path) as f:
        doc = yaml_rt.load(f)
    doc.setdefault("dashboard", {})["password_hash"] = value
    with open(path, "w") as f:
        yaml_rt.dump(doc, f)


def main() -> None:
    parser = argparse.ArgumentParser(description="Set the LineProof dashboard passphrase.")
    parser.add_argument("--config", type=Path, default=cfg.CONFIG_PATH, help="Path to config.yaml")
    parser.add_argument("--clear", action="store_true", help="Remove the passphrase (disable login)")
    args = parser.parse_args()

    if not args.config.exists():
        sys.exit(f"Config not found: {args.config}")

    if args.clear:
        _write_hash(args.config, "")
        print("Passphrase cleared — the dashboard no longer requires login.")
        print("Restart LineProof for the change to apply.")
        return

    pw = getpass.getpass("New dashboard passphrase: ")
    if len(pw) < 6:
        sys.exit("Passphrase must be at least 6 characters.")
    if getpass.getpass("Confirm passphrase: ") != pw:
        sys.exit("Passphrases did not match.")

    _write_hash(args.config, generate_password_hash(pw))
    print("Passphrase set. LAN/remote devices will need it to view the dashboard;")
    print("the PC itself (localhost) still bypasses, and scanning the QR logs a device in.")
    print("Restart LineProof for the change to apply.")


if __name__ == "__main__":
    main()
