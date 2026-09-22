# Security policy

Use GitHub's private security-advisory feature for credential exposure, unsafe file handling, command injection, or disclosure of local browsing/notification data. If unavailable, open a minimal issue asking the maintainer to enable it; do not publish exploit details or secrets.

Keep Telegram tokens, chat IDs, cookies, browser profiles, databases, logs, and `.env` local. Rotate exposed credentials immediately. Redact VINs and locations from issues.

YRadar drives a local Chrome session and processes changing third-party content. Use a dedicated environment and do not run untrusted forks with an existing authenticated browser profile.

Only the latest release receives security fixes. YRadar cannot guarantee continuous Tesla access or bypass access controls.
