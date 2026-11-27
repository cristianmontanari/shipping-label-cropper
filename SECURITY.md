# Security Policy

## Privacy & Local Execution

**Shipping Label Cropper** runs **100% locally and offline**:
- Your PDF shipping labels are never uploaded to any remote server.
- The application makes no network requests and collects no telemetry or analytics.
- Configuration is stored locally in your user profile directory.

## Supported Versions

Security fixes are applied to the latest release on the `main` branch.

| Version | Supported |
| ------- | --------- |
| 1.x     | Yes       |

## Reporting a Vulnerability

If you discover a security vulnerability (for example, command injection in OS integration or printing helpers, unsafe file handling, or malformed PDF handling), **please do not open a public GitHub issue**.

Instead, please report it privately using one of these methods:
1. **GitHub Security Advisories (preferred):** Go to the repository's **Security** tab and click **Report a vulnerability**.
2. **Email:** Send details to `cristianmontanari02@gmail.com` with the subject `[Security] Shipping Label Cropper`.

Please include:
- A description of the vulnerability and potential impact.
- Steps to reproduce or a minimal proof of concept (without real personal shipping data).
- Your OS and application/Python version.

You can expect an initial response within a few days and a coordinated fix before public disclosure.
