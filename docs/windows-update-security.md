# Windows update security

AI Limit's Windows updater downloads and launches an installer only after a sequence of independent checks succeeds. This page documents the public verification design; it does not contain the private signing key.

## Release assets

An official Windows release uses these assets:

- `ai-limit-<version>-setup.exe`
- `ai-limit-<version>-setup.exe.sig`

The `.sig` file is a signed JSON release document. It binds the schema version, exact installer filename, release version, and SHA-256 installer digest. Official release documents are signed with Ed25519, and the application embeds the corresponding public key and key identifier.

## Verification flow

Before showing or launching the downloaded installer, the updater verifies:

1. The release document has the expected schema and trusted key identifier.
2. Its Ed25519 signature is valid for the embedded public key.
3. The requested version and exact installer filename match the signed values.
4. The downloaded installer's SHA-256 digest matches the signed digest.
5. The file is a Windows PE executable and begins with the expected `MZ` header.
6. The embedded Windows product version matches the requested release version.
7. Windows reports the Authenticode state as either `Valid` or `NotSigned`; invalid, untrusted, or malformed signatures are rejected.

After verification, the updater applies Windows Mark of the Web metadata when possible and displays a user-visible confirmation before launching the installer. A pending-update marker lets the replacement application confirm the installed version after restart.

Any missing signature file, malformed document, signature mismatch, changed filename, changed file content, unexpected version, or unacceptable Authenticode state causes the update to fail closed.

## Ed25519 versus Authenticode

These mechanisms solve different problems:

- The Ed25519 release signature proves that the installer asset and its release metadata were authorized by the AI Limit release key.
- Authenticode identifies a Windows publisher to the operating system and can build Microsoft Defender SmartScreen reputation.

Version 0.3.24 has the Ed25519 update signature but is not Authenticode code-signed. Windows can therefore display “Unknown publisher” or a SmartScreen warning even though the in-app updater has verified the official release asset.

## Key handling

The private Ed25519 signing key is never committed to this repository or packaged with the application. Only the public verification key and the release-signing/verification format are public. A new `.sig` asset is generated for every installer because its signed digest and version change, while the private key is intended to remain stable until an intentional key rotation.
