#!/usr/bin/env python3
"""Seal experimental build files without putting a decryption key on GitHub.

Requires cryptography 44.0.3. This helper never publishes or extracts an archive.
Outputs are committed with a same-directory hard link, so a known file is never
overwritten and unauthenticated plaintext is never committed at its final path.
The target filesystem must support hard links (APFS and NTFS do).
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import struct
import sys
import tempfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b"WDSMAC01"
ALGORITHM = "RSA-OAEP-SHA256+AES-256-GCM"
MAX_HEADER = 16 * 1024
CHUNK = 1024 * 1024
TAG_SIZE = 16


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(CHUNK), b""):
            value.update(chunk)
    return value.hexdigest()


def exists(path: Path) -> bool:
    return os.path.lexists(path)


def require_distinct(first: Path, second: Path) -> None:
    if first.resolve() == second.resolve():
        raise ValueError("Input and output paths must differ.")


@contextmanager
def atomic_output(destination: Path):
    """Create only a new destination; remove only this invocation's temp file."""
    if exists(destination):
        raise FileExistsError("Destination already exists; refusing overwrite.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".seal-", suffix=".partial", dir=destination.parent)
    temporary = Path(name)
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        # link() is atomic and fails if destination exists, unlike POSIX rename.
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def oaep() -> padding.OAEP:
    return padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                        algorithm=hashes.SHA256(), label=None)


def check_rsa(key):
    if not isinstance(key, (rsa.RSAPublicKey, rsa.RSAPrivateKey)):
        raise ValueError("An RSA key is required.")
    if not 3072 <= key.key_size <= 8192:
        raise ValueError("RSA keys must be between 3072 and 8192 bits.")
    return key


def load_public(path: Path) -> rsa.RSAPublicKey:
    key = serialization.load_pem_public_key(path.read_bytes())
    return check_rsa(key)


def load_private(path: Path) -> rsa.RSAPrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    return check_rsa(key)


def fingerprint(key: rsa.RSAPublicKey) -> str:
    public = key.public_bytes(serialization.Encoding.DER,
                              serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(public).hexdigest()


def keygen(private: Path, public: Path) -> dict:
    require_distinct(private, public)
    if exists(private) or exists(public):
        raise FileExistsError("Key destination already exists; no keys were changed.")
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    private_bytes = key.private_bytes(serialization.Encoding.PEM,
                                     serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption())
    public_bytes = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    private_identity = None
    try:
        with atomic_output(private) as stream:
            stream.write(private_bytes)
        stat = private.stat()
        private_identity = (stat.st_dev, stat.st_ino)
        with atomic_output(public) as stream:
            stream.write(public_bytes)
    except Exception:
        # If the second exclusive creation failed, roll back our own first file
        # only. Never touch an existing or concurrently replaced private key.
        if private_identity is not None and exists(private):
            stat = private.stat()
            if (stat.st_dev, stat.st_ino) == private_identity:
                private.unlink()
        raise
    return {"created": True, "publicKeyFingerprint": fingerprint(key.public_key())}


def encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def decode(value: object, expected: int) -> bytes:
    if not isinstance(value, str):
        raise ValueError("Invalid envelope encoding.")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        raise ValueError("Invalid envelope encoding.") from None
    if len(decoded) != expected:
        raise ValueError("Invalid envelope field length.")
    return decoded


def read_exact(source, amount: int) -> bytes:
    result = source.read(amount)
    if len(result) != amount:
        raise ValueError("Truncated encrypted envelope.")
    return result


def reject_duplicates(pairs):
    values = {}
    for key, value in pairs:
        if key in values:
            raise ValueError("Duplicate envelope header field.")
        values[key] = value
    return values


def encrypt(public: Path, source_path: Path, destination: Path) -> dict:
    require_distinct(source_path, destination)
    require_distinct(public, destination)
    key = load_public(public)
    content_key = secrets.token_bytes(32)
    nonce = secrets.token_bytes(12)
    header = json.dumps(
        {"algorithm": ALGORITHM, "nonce": encode(nonce),
         "wrappedKey": encode(key.encrypt(content_key, oaep()))},
        separators=(",", ":"), sort_keys=True).encode("ascii")
    if len(header) > MAX_HEADER:
        raise ValueError("Envelope header is too large.")
    prefix = MAGIC + struct.pack(">I", len(header)) + header
    cipher = Cipher(algorithms.AES(content_key), modes.GCM(nonce)).encryptor()
    cipher.authenticate_additional_data(prefix)
    with source_path.open("rb") as source, atomic_output(destination) as output:
        output.write(prefix)
        for chunk in iter(lambda: source.read(CHUNK), b""):
            output.write(cipher.update(chunk))
        output.write(cipher.finalize())
        output.write(cipher.tag)
    return {"encrypted": True, "publicKeyFingerprint": fingerprint(key),
            "encryptedSHA256": digest(destination)}


def decrypt(private: Path, source_path: Path, destination: Path) -> dict:
    require_distinct(source_path, destination)
    require_distinct(private, destination)
    key = load_private(private)
    with source_path.open("rb") as source:
        magic = read_exact(source, len(MAGIC))
        if magic != MAGIC:
            raise ValueError("Not a supported encrypted build envelope.")
        length_bytes = read_exact(source, 4)
        length = struct.unpack(">I", length_bytes)[0]
        if not 1 <= length <= MAX_HEADER:
            raise ValueError("Invalid envelope header length.")
        header = read_exact(source, length)
        try:
            values = json.loads(header.decode("ascii"),
                                object_pairs_hook=reject_duplicates)
        except (UnicodeError, json.JSONDecodeError):
            raise ValueError("Invalid envelope header.") from None
        if (not isinstance(values, dict) or
                set(values) != {"algorithm", "nonce", "wrappedKey"} or
                values["algorithm"] != ALGORITHM):
            raise ValueError("Unsupported envelope header.")
        nonce = decode(values["nonce"], 12)
        wrapped = decode(values["wrappedKey"], key.key_size // 8)
        try:
            content_key = key.decrypt(wrapped, oaep())
        except ValueError:
            raise ValueError("Envelope cannot be opened by this private key.") from None
        if len(content_key) != 32:
            raise ValueError("Invalid content key length.")
        prefix = magic + length_bytes + header
        ciphertext_length = os.fstat(source.fileno()).st_size - len(prefix) - TAG_SIZE
        if ciphertext_length < 0:
            raise ValueError("Truncated encrypted envelope.")
        source.seek(-TAG_SIZE, os.SEEK_END)
        tag = read_exact(source, TAG_SIZE)
        source.seek(len(prefix))
        cipher = Cipher(algorithms.AES(content_key), modes.GCM(nonce, tag)).decryptor()
        cipher.authenticate_additional_data(prefix)
        with atomic_output(destination) as output:
            remaining = ciphertext_length
            while remaining:
                chunk = read_exact(source, min(CHUNK, remaining))
                remaining -= len(chunk)
                output.write(cipher.update(chunk))
            try:
                output.write(cipher.finalize())
            except InvalidTag:
                raise ValueError("Encrypted build authentication failed.") from None
            if read_exact(source, TAG_SIZE) != tag or source.read(1):
                raise ValueError("Encrypted envelope changed while reading.")
    return {"decrypted": True, "plaintextSHA256": digest(destination),
            "publicKeyFingerprint": fingerprint(key.public_key())}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("keygen")
    generate.add_argument("--private", type=Path, required=True)
    generate.add_argument("--public", type=Path, required=True)
    for name in ("encrypt", "decrypt"):
        subparser = commands.add_parser(name)
        subparser.add_argument("--public" if name == "encrypt" else "--private",
                               type=Path, required=True)
        subparser.add_argument("--input", type=Path, required=True)
        subparser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "keygen":
            result = keygen(args.private, args.public)
        elif args.command == "encrypt":
            result = encrypt(args.public, args.input, args.output)
        else:
            result = decrypt(args.private, args.input, args.output)
    except (OSError, ValueError, TypeError) as error:
        print("seal: " + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
