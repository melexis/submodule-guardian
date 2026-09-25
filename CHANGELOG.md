# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Every release is a git tag
of the form `X.Y.Z`; the version of an installed package is derived from that tag.

## [Unreleased]

## [0.1.0] - 2025-12-09

Initial release.

### Added

- Status check of the submodules of a GitLab project: on a tag, up-to-date with the remote default
  branch, behind it, or on an unrelated commit.
- Report as a discussion on the merge request, resolved when every submodule is in a good state.
- `--fail-pipeline` to fail the job instead of posting a discussion, and `--no-post-discussion` to
  keep the check silent.
- `--always-check` to check every submodule instead of only those modified in the merge request.
- `--allow-tags` and `--only-latest-tag` to accept submodules that are on a tag.
- `--fix` to check out submodules to their latest tag or default branch head in a dry run.

[Unreleased]: https://github.com/melexis/submodule-guardian/compare/0.1.0...HEAD
[0.1.0]: https://github.com/melexis/submodule-guardian/releases/tag/0.1.0
