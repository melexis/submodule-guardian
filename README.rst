==================
Submodule Guardian
==================

.. image:: https://github.com/melexis/submodule-guardian/actions/workflows/python-package.yml/badge.svg
    :target: https://github.com/melexis/submodule-guardian/actions/workflows/python-package.yml
    :alt: Build status

.. image:: https://badge.fury.io/py/mlx.submodule_guardian.svg
    :target: https://badge.fury.io/py/mlx.submodule_guardian
    :alt: PyPI packaged release

.. image:: https://img.shields.io/badge/license-Apache%202.0-blue.svg
    :target: https://github.com/melexis/submodule-guardian/blob/main/LICENSE
    :alt: Apache2 license

.. image:: https://www.bestpractices.dev/projects/11936/badge
    :target: https://www.bestpractices.dev/projects/11936
    :alt: OpenSSF Best Practices

Python script that checks the status of submodules in a GitLab merge request.

A project that pins its submodules to a commit silently drifts away from them: a submodule stays
behind its default branch, or on a tag nobody meant to freeze, and nothing in the merge request says
so. Submodule Guardian makes that visible where the work happens. It inspects every submodule
through the GitLab API and reports the outcome as a discussion thread on the merge request, resolved
when everything is in a good state and unresolved when a submodule needs attention, so the reviewer
sees it without having to check by hand. It can also fail the pipeline instead, or check out the
submodules to the commit they should be on.

Documentation is in this README: `Installation`_, `Usage`_ and its `Command Line Reference`_,
`Development`_ and `Versioning`_. `SECURITY.md <SECURITY.md>`_ describes the security model and how
to report a vulnerability, `CHANGELOG.md <CHANGELOG.md>`_ what changed per release.


Features
========

- **MR Integration**: Creates or updates a discussion thread in the MR when ``--no-post-discussion`` is not used.
- **Configurable Behavior**:
    - Can be set to fail the pipeline instead of creating a discussion.
    - Can be configured to check every submodule, not just those that changed in the MR.
    - Can be configured to allow submodules to be on tags.
    - Can be configured to only consider the latest tag as up-to-date.
- **Clear Reporting**: Provides a concise status report.
- **Fix**: When ``--fix`` is used in dry run mode, the script attempts to automatically resolve submodule warnings by
  checking out submodules to their latest tag (if ``--allow-tags`` is enabled) or their remote default branch head.


How it Works
============

For each submodule, the script checks the following conditions in order:

1.  **Is it on a tag?**

    -   If tags are allowed (``--allow-tags``):

        -   If ``--only-latest-tag`` is used, it checks if the submodule
            is on the latest tag (✅ Good state) or an older tag
            (⚠️ Warning state).
        -   Otherwise, any tag is considered a ✅ Good state.

    -   If tags are not allowed, being on a tag is a ⚠️ Warning state.

2.  **Is it up-to-date with its remote default branch?** (✅ Good state)

3.  **Is it on an older commit of its remote default branch?** (⚠️ Warning state)

4.  **Is it on a different commit/branch altogether?** (❌ Error state)

If any submodule is in a "warning" or "error" state, it will trigger the configured action (unresolved discussion or pipeline failure).


Installation
============

.. code-block:: bash

    pip3 install mlx.submodule_guardian --upgrade


Usage
=====

Environment Variables
---------------------
The following environment variables are required for authentication and GitLab instance configuration:

- PRIVATE_TOKEN: Your GitLab private access token with api scope.
- CI_SERVER_HOST: The hostname of your GitLab instance. Defaults to https://gitlab.melexis.com if not set.

When running inside a GitLab CI pipeline, the script automatically detects CI variables like ``CI_PROJECT_PATH`` and ``CI_MERGE_REQUEST_IID``.
The command-line flags (e.g., ``-p`` and ``-m``) can be used for running the script locally outside of a CI environment.

Command Line Reference
----------------------

.. code-block:: bash

    submodule-guardian -h
    usage: submodule-guardian [-h] [--version] [-p PROJECT] [-m MR_IID] [-b BRANCH] [--fail-pipeline] [--always-check]
                            [--allow-tags] [--no-post-discussion] [--only-latest-tag] [--fix] [-v] [-d]

    Check submodule status and report to a GitLab MR.

    options:
    -h, --help            show this help message and exit
    --version             show program's version number and exit
    -p PROJECT, --project PROJECT
                            The ID or path of the GitLab project (or CI_PROJECT_PATH).
    -m MR_IID, --mr-iid MR_IID
                            The IID of the merge request (or CI_MERGE_REQUEST_IID).
    -b BRANCH, --branch BRANCH
                            Current branch name (default: current git branch)
    --fail-pipeline       Fail the pipeline on warnings instead of creating an MR discussion.
    --always-check        Always perform the check, even if no submodules were modified in the MR.
    --allow-tags          Allow submodules to be on tags.
    --no-post-discussion  Do not post a discussion on the merge request.
    --only-latest-tag     If on tag, only consider the latest tag as up-to-date.
    --fix                 Automatically checkout submodules to fix warnings (e.g., to latest tag or branch head).
    -v, --verbose         Enable INFO level logging.
    -d, --debug           Enable DEBUG level logging.


By default, submodule-guardian checks only submodules modified in the current Merge Request, and if issues are found,
it posts an unresolved discussion to the MR without failing the pipeline.

Output
------

Outside a CI environment (no ``CI_PROJECT_ID`` and no ``CI_PROJECT_PATH``) the tool runs in dry-run
mode: it prints the status report to the terminal and touches no merge request. In CI it reports
through the merge request discussion, and logs what it does. Both the report and the logging go to
standard output, rendered with `rich <https://pypi.org/project/rich/>`_; use ``-v`` or ``-d`` to
raise the log level.

Exit codes:

- ``0``: the check ran. Without ``--fail-pipeline`` this is also the outcome when a submodule needs
  attention, since the finding is carried by the unresolved discussion instead.
- ``1``: a submodule is in a warning or error state and ``--fail-pipeline`` was given, or the tool
  could not run at all (no ``PRIVATE_TOKEN``, no project identifier, no merge request or branch,
  ``--fix`` outside dry-run mode, or an unexpected error).


Local Usage Example
===================

The tool can be used locally as well but you need to give at least the project path with namespace or project ID.

.. code-block:: bash

    submodule-guardian -p some-project --allow-tags --always-check

    --- Submodule Status Report (Dry Run) ---
    ✅ Submodule path/submodule1 is on tag `1.0.0`.
    ✅ Submodule some/path/submodule2 is up-to-date with default branch `master`.
    ⚠ Submodule submodule3 is behind its latest default branch `master`.
    -----------------------------------------

    WARNING  submodule-guardian: Warnings detected. In a CI run, this would create a discussion or fail the pipeline.


CI Example
==========

This is an output example of a discussion created by the CI.

.. code-block:: markdown

    # Submodule Status Check

    One or more submodules require attention.

    * :white_check_mark: Submodule path/submodule1 is on tag `1.0.0`.
    * :white_check_mark: Submodule some/path/submodule2 is up-to-date with default branch `master`.
    * :warning: Submodule submodule3 is behind its latest default branch `master`.

    ---

    *This comment was generated by the `submodule-guardian` script. You can resolve this thread manually if the current submodule state is intentional. Please provide an explanation when doing so.*

    Job run: <a href="https://gitlab.com/some-project/-/jobs/12345678">CI Job</a>


Development
===========

The project is built and tested with `tox <https://tox.wiki>`_, which needs nothing but a Python
interpreter and free software:

.. code-block:: bash

    git clone https://github.com/melexis/submodule-guardian.git
    cd submodule-guardian
    pip3 install tox

    tox -e py               # the test suite on your interpreter
    tox                     # every environment: the test suite on each Python version you have
                            # installed, followed by the checks
    tox -e check            # flake8, bandit, check-manifest and the package build

The test suite is `pytest <https://pytest.org>`_ based and lives in ``tests/``. Running it directly
works too, once the package is installed in the environment:

.. code-block:: bash

    pip3 install -e . pytest
    pytest tests/

`GitHub Actions <https://github.com/melexis/submodule-guardian/actions>`_ runs the test suite on
every supported Python version at each push, and the checks on top of that. CodeQL analyses the
sources on every pull request, on every push to ``main`` and once a week.


Versioning
==========

Releases follow `Semantic Versioning <https://semver.org>`_ and are identified by a git tag
``X.Y.Z``. The package version is derived from that tag by ``setuptools-scm``, so every build
carries a unique version, and a build between releases is marked as a development version of the
next one. Tagging a release makes GitHub Actions publish it to PyPI.
`CHANGELOG.md <CHANGELOG.md>`__ summarizes what changed per release.


Contribute
==========

There is a `Contribution guide <CONTRIBUTING.md>`_ available if you would like to get involved in
development of the plugin. We encourage anyone to contribute to our repository.

The project is maintained by `Melexis <https://github.com/melexis>`_ and is actively used in our own
pipelines. Bug reports and enhancement requests belong in the
`issue tracker <https://github.com/melexis/submodule-guardian/issues>`_, where the discussion on
them stays publicly readable; pull requests follow the standard GitHub flow. Issues, comments and
code are in English. Vulnerabilities go there too, as `SECURITY.md <SECURITY.md>`__ describes.
