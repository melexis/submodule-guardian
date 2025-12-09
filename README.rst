==================
Submodule Guardian
==================

Python script that checks the status of submodules in a GitLab merge request.


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


Local Usage Example
===================

The tool can be used locally as well but you need to give at least the project path with namespace or project ID.

.. code-block:: bash

    submodule-guardian -p some-project --allow-tags --always-check                                                                                                                                                                    8ms

    --- Submodule Status Report (Dry Run) ---
    ✅ Submodule path/submodule1 is on tag `1.0.0`.
    ✅ Submodule some/path/submodule2 is up-to-date with default branch `master`.
    ⚠ Submodule submodule3 is behind its latest default branch `master`.
    -----------------------------------------

    WARNING  submodule-guardian: Warnings detected. In a CI run, this would create a discussion or fail the pipeline.


CI Example
==========

This is the an output example of a discussion created by the CI.

.. code-block:: markdown

    # Submodule Status Check

    One or more submodules require attention.

    * :white_check_mark: Submodule path/submodule1 is on tag `1.0.0`.
    * :white_check_mark: Submodule some/path/submodule2 is up-to-date with default branch `master`.
    * :warning: Submodule submodule3 is behind its latest default branch `master`.

    ---

    *This comment was generated by the `submodule-guardian` script. You can resolve this thread manually if the current submodule state is intentional. Please provide an explanation when doing so.*

    Job run: <a href="https://gitlab.com/some-project/-/jobs/12345678">CI Job</a>


Contribute
==========

There is a Contribution guide available if you would like to get involved in
development of the plugin. We encourage anyone to contribute to our repository.
