"""Encrypted configuration storage.

Config is encrypted with Fernet using a key derived from a master password
via PBKDF2. The encrypted file at ~/.glsa/config.enc is opaque -- users
re-run `glsa configure` to change values.
"""

import base64
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes


SALT_SIZE = 16
KDF_ITERATIONS = 600_000


def _derive_key(password: str, salt: bytes) -> bytes:
    """Derive a Fernet key from a master password + salt."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=KDF_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))


def encrypt_config(data: dict, master_password: str, config_path: Path) -> None:
    """Encrypt config dict and write to file."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    salt = os.urandom(SALT_SIZE)
    key = _derive_key(master_password, salt)
    f = Fernet(key)
    payload = json.dumps(data).encode()
    encrypted = f.encrypt(payload)

    # File format: salt (16 bytes) + encrypted data
    with open(config_path, "wb") as fp:
        fp.write(salt)
        fp.write(encrypted)


def decrypt_config(master_password: str, config_path: Path) -> dict:
    """Decrypt config file and return the config dict.

    Raises InvalidToken if password is wrong.
    Raises FileNotFoundError if config doesn't exist.
    """
    with open(config_path, "rb") as fp:
        raw = fp.read()

    salt = raw[:SALT_SIZE]
    encrypted = raw[SALT_SIZE:]

    key = _derive_key(master_password, salt)
    f = Fernet(key)
    decrypted = f.decrypt(encrypted)
    return json.loads(decrypted)


def config_exists(data_dir: str) -> bool:
    return (Path(data_dir) / "config.enc").exists()


def get_config_path(data_dir: str) -> Path:
    return Path(data_dir) / "config.enc"
