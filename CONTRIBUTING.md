Contribution Guide
==================
Anyone is more than welcome to contribute to the development of submodule-guardian,
no matter your programming skill level. It is the reviewer's obligation to help bring your
contribution to our desired quality level and you should do your best to help the reviewer
understand your decisions. Standard GitHub flow is used https://docs.github.com/en/get-started/using-github/github-flow
to start Pull Requests, which are then merged. We also prefer to make a Work In Progress
Merge request once you starting your work just in case someone else is not working on the
same issue.

Getting Started
===============
There may be a few issues opened to request new features, but you are also
more than welcome to make some of your own suggestions. The `help wanted` label
indicates that it is an easy enough task for anyone to start with, so go and pick up the
feature you feel most excited about and start implementing it.

Development Setup
-----------------
The test suite and the checks run through [tox](https://tox.wiki), so a Python interpreter and
`pip3 install tox` are all you need:

```bash
tox -e py        # the test suite on your interpreter
tox              # every environment: the test suite on each Python version you have installed,
                 # followed by the checks
tox -e check     # flake8, bandit, check-manifest and the package build
```

Both the test suite and the checks run on every push in GitHub Actions, so it saves a round trip to
run them before you open a pull request.

Quality of Contribution
-----------------------
All new contributions need to be properly tested. Every new feature and every bug fix comes with
tests in `tests/`, and a pull request that changes behaviour without touching the test suite will be
sent back for them. We are not targeting some coverage percentage but rather focus on regression
testing to confirm expected functionality and border cases. This will help us keep existing features
even after years of constant development and it helps fixing regression bugs.

Coding Standards
----------------
- Follow [PEP 8](https://peps.python.org/pep-0008/), with lines up to 120 characters. `flake8`
  enforces this in `tox -e check` and it must pass without warnings.
- Give modules, classes and public methods a docstring; document arguments and return values the way
  the surrounding code does.
- Keep the code free of findings from `bandit`, the static analysis tool we run over `src/`. When a
  finding is a false positive, skip it in the `[tool.bandit]` section of `pyproject.toml` with a
  comment saying why, rather than leaving it unexplained.
- English is the language of the project: code, comments, documentation, issues and commit messages.

Documentation
-------------
Basic documentation is expected, but every bit of detail you can include will help in
the future. It might look obvious, but it will also help everyone reviewing the code to
correctly understand the intended functionality so that they can focus more on the implementation
aspect.

A change that users can notice belongs in the command line reference in `README.rst` as well, when
you touch the interface. There is no changelog file to update: the release notes are generated from
the merged pull requests, so give your pull request a title that reads well in them and a label
(`bug`, `enhancement`, `documentation`) so it ends up in the right section.

Code Review
-----------
Anyone is more than welcome to check open Pull requests and make a code review. Everyone
benefits from fresh eyes looking at new features or bug fixes and it also improves
coding skills of all included. Remember to act politely. Since some people might not be
frequent contributors to various repositories, do not intimidate them, but rather
help them improve. We are all learning.

Reporting Issues or Requesting a New Feature
============================================
Please open a new [Issue](https://github.com/melexis/submodule-guardian/issues) if you have any
problems with the plugin. We will be happy to fix them as soon as possible. If you want some feature
to be included but do not know where to start, you should also open an Issue with label
`enhancement` and we can implement it when we have time and it fits in our view.

A useful bug report says which version of submodule-guardian and of Python you used, what you ran,
what you expected, and what happened instead. The issue tracker is public, and so is its archive of
reports and answers.

Reporting a Vulnerability
=========================
A security problem goes in the issue tracker as well. [SECURITY.md](SECURITY.md) describes what to
include and what to expect afterwards.

Releases
========
Maintainers release by drafting a release on GitHub with a new tag `X.Y.Z` following
[Semantic Versioning](https://semver.org), and letting *Generate release notes* write the summary
from the pull requests merged since the previous tag. `.github/release.yml` groups those by label.
`setuptools-scm` derives the package version from the tag and GitHub Actions publishes it to PyPI.

Code of Conduct
===============
We strive to present a welcoming and inclusive environment, so we request everyone to behave
at their best. We adhere to https://policies.python.org/python.org/code-of-conduct/
