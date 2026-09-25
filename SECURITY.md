# Security Policy

Submodule Guardian reads a GitLab project through the API and reports on its submodules, so its
impact on your system is limited. It does need a GitLab private token with the `api` scope, which it
reads from the `PRIVATE_TOKEN` environment variable or from a `.env` file handled by
[python-decouple](https://pypi.org/project/python-decouple/) - never commit that file, and use a
masked variable in CI. With `--fix` it also checks out submodules in your local working copy. It
never pushes, and it runs `git` with a fixed argument list, never through a shell.

## Supported Versions

We currently support the latest version, but if requested we can backport a fix to older major
versions. In practice there is no major effort in always updating to the latest version.

## Reporting a Vulnerability

Please report all vulnerabilities through the
[GitHub Issue Tracker](https://github.com/melexis/submodule-guardian/issues). Make sure you include
the version you used and ways to replicate it, and if possible propose a Pull Request with a
potential fix. Never paste a real token in a report; revoke it instead and say which scope it had.

A fix is released as a new version. Its
[release notes](https://github.com/melexis/submodule-guardian/releases) name the vulnerability, and
the CVE when one was assigned.
