# Security policy

## Trust boundaries

An APK and every patch bundle execute as software on the eventual Android device.
Treat a new upstream or community patch source like a new code dependency:

1. verify the repository owner and release history;
2. inspect patch source code and its build workflow;
3. pin the release tag and SHA-256 for higher-risk sources;
4. review `build-info.json` before installing a new release;
5. test on a non-critical device/profile first.

Never put credentials in app definitions, download URLs, workflow inputs, issues, or
logs. GitHub Actions secrets are the only supported location for signing credentials.

## Signing key compromise

If the signing key may have been exposed, immediately disable the build workflow and
remove its secrets. A replacement key cannot update existing installations; affected
users must uninstall the old build before installing one signed by a new key.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting feature for this repository. Do not open
a public issue for signing-key exposure or an actively exploitable supply-chain issue.
