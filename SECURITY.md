# Security

BetterChat's Nexo engine runs user-configured commands and coding agents with
their clients' permissions. A Git worktree separates changes; it is not an
operating-system sandbox. Review code, check commands and routing configuration
before running an untrusted project.

## Keep private data local

The `.nexo/` directory can contain private tool output, prompts and local paths.
It is excluded from Git and release archives. Pattern-based redaction is partial:
review any excerpt before sharing it. Do not put credentials in command arguments
or routing files.

## Report a vulnerability

If GitHub's private vulnerability reporting is enabled for this repository, use
**Security → Report a vulnerability**. Otherwise, start with a public issue that
only asks for a private reporting channel, without exploit details or sensitive
data. Do not post credentials or raw project logs.

This is an early release candidate. There is no guaranteed response time or
long-term support policy yet. Include the affected version and a minimal,
sanitized reproduction when a suitable reporting channel is available.
