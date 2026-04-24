# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |

## Reporting a vulnerability

If you discover a security vulnerability, please report it privately:

1. **Do not** open a public GitHub issue
2. Email the maintainers or use [GitHub's private vulnerability reporting](https://github.com/estampo/boo-cloud/security/advisories/new)
3. Include steps to reproduce, impact assessment, and any suggested fix

We will acknowledge reports within 48 hours and aim to release a fix within 7 days for critical issues.

## Scope

boo-cloud handles:
- Credential loading and storage (`~/.config/boo-cloud/credentials.toml`)
- Bambu Cloud authentication tokens
- Docker container execution for the bridge daemon

Security-sensitive areas include credential handling in `credentials.py` and
subprocess/Docker invocation in `bridge.py`.

> **Note:** `libbambu_networking.so` is a closed-source Bambu Lab binary. Security
> issues in that library are outside our control; report them to Bambu Lab directly.
