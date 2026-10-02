# Security policy

## Reporting a vulnerability

Please report security issues privately by e-mail to **andrei@hexadexa.dev** — not in a public
GitHub issue. Include the plugin version, the pretix version and steps to reproduce.

You will get an answer within a few days. Fixes are released as a new version on PyPI and noted in
[CHANGELOG.md](CHANGELOG.md); reporters are credited unless they prefer not to be.

## Supported versions

Only the latest release receives security fixes.

## Scope notes

The plugin stores identities only as HMAC-SHA256 hashes keyed with the `secret_salt` from
`pretix.cfg`. Anyone with database access *and* that salt can test whether a known person
(name + birth date, or e-mail) is in the data — keep the salt as secret as the database password.
