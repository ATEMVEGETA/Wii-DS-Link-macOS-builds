# Encrypted experimental build files

The Mac runner needs only the public RSA key. Keep its matching private key
outside this Git repository, under the local project's macos/secrets directory.
Do not upload private keys as workflow artifacts, release assets, source files,
or log text. No private key or content-encryption-key digest is printed.

seal.py requires cryptography 44.0.3. It supports:

- keygen --private PATH --public PATH
- encrypt --public PATH --input PATH --output PATH
- decrypt --private PATH --input PATH --output PATH

Generate the real key pair only once locally. The workflow encrypts a completed
tar/ZIP file; it must never upload the plaintext file. Download the encrypted
release asset into the local D: project, then decrypt it locally.

Each encryption uses a fresh 256-bit AES key and 96-bit random GCM nonce.
RSA-OAEP with SHA-256 wraps the AES key. The versioned, bounded envelope header
is authenticated as additional data; the 128-bit GCM authentication tag is
appended. Decryption commits the plaintext output only after authentication
passes. This is an encrypted single-file envelope, not an archive extractor.

All destinations are exclusive: known files are never overwritten. A temporary
output file is created with POSIX mode 0600 in its destination directory. An
atomic hard-link commit requires APFS, NTFS, or another hard-link-capable
filesystem. Owned temporary files are removed on failure. Windows access
control lists should additionally restrict the private-key directory.

This protects unpublished test binaries from being usable public downloads.
It does not prevent source-based reproduction of the open-source emulators,
nor does it establish that a compiled emulator or retail game works.
