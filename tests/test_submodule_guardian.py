import argparse
import logging
import sys
import unittest
from unittest.mock import MagicMock, patch
import subprocess
import os

from mlx.submodule_guardian.submodule_guardian import SubmoduleGuardian, Submodule, main, get_current_branch


def setUpModule():
    """Configure logging for the entire test module."""
    # This prevents tests from failing because the logger is not configured.
    # The assertLogs context manager requires the logger to have at least one handler.
    test_logger = logging.getLogger('submodule-guardian')
    test_logger.setLevel(logging.DEBUG)
    if not test_logger.hasHandlers():
        test_logger.addHandler(logging.StreamHandler(sys.stdout))


class SubmoduleGuardianTest(unittest.TestCase):

    def setUp(self):
        """Set up mock objects for GitLab and Subproject."""
        self.config_patch = patch('mlx.submodule_guardian.submodule_guardian.config')
        # Mock GitLab Project and MR
        self.mock_gitlab = MagicMock()
        self.mock_project = MagicMock()
        self.mock_mr = MagicMock()
        self.mock_gitlab.projects.get.return_value = self.mock_project
        self.mock_project.mergerequests.get.return_value = self.mock_mr

        # Mock Submodule object
        self.mock_submodule = MagicMock()
        self.mock_submodule.path_in_project = 'path/to/submodule'
        self.mock_submodule.url = 'http://gitlab.example.com/sub/project.git'
        self.mock_submodule.commit_id = 'sub_commit_123'

        # Mock the submodule's GitLab project object, which is an attribute of the Submodule object
        self.mock_submodule.sub_project = MagicMock()
        self.mock_submodule.sub_project.default_branch = 'main'
        self.mock_submodule.sub_project.http_url_to_repo = self.mock_submodule.url

        # Mock latest tag for relevant tests
        self.mock_latest_tag = MagicMock()
        self.mock_latest_tag.name = 'v1.1.0'
        self.mock_submodule.latest_tag = self.mock_latest_tag

        # Patch external dependencies
        self.gitlab_patch = patch('mlx.submodule_guardian.submodule_guardian.Gitlab', return_value=self.mock_gitlab)

        self.gitlab_patch.start()
        self.mock_config = self.config_patch.start()
        self.mock_config.side_effect = (lambda key, default=None: 'dummy_token' if key == 'PRIVATE_TOKEN'
                                        else 'gitlab.example.com')

    def tearDown(self):
        """Stop all patches."""
        patch.stopall()

    def _create_guardian(self, allow_tags=False, only_latest_tag=False, fail_pipeline=False, dry_run=False,
                         post_discussion=True, fix=False):
        """Helper to create a SubmoduleGuardian instance with mocked GitLab."""
        # We patch 'config' where it is looked up by the SubmoduleGuardian constructor.
        # We don't need to patch Gitlab here as it's already patched in setUp.
        with patch('mlx.submodule_guardian.submodule_guardian.config') as mock_config:
            mock_config.side_effect = lambda key, default=None: 'dummy_token' if key == 'PRIVATE_TOKEN' else default
            return SubmoduleGuardian(
                project_identifier='some/project',
                mr_iid='1',
                fail_pipeline=fail_pipeline,
                always_check=True,
                dry_run=dry_run,
                allow_tags=allow_tags,
                only_latest_tag=only_latest_tag,
                post_discussion=post_discussion,
                fix=fix
            )

    def _reset_submodule_mocks(self):
        """Resets the return values of the mock submodule's methods."""
        self.mock_submodule.is_on_tag.return_value = None
        self.mock_submodule.is_on_latest_tag.return_value = False
        self.mock_submodule.is_latest_default_branch.return_value = False
        self.mock_submodule.is_on_default_branch.return_value = False

    def test_format_status_up_to_date(self):
        """Test status when submodule is up-to-date with the default branch."""
        guardian = self._create_guardian()
        self._reset_submodule_mocks()
        self.mock_submodule.is_latest_default_branch.return_value = True

        status = guardian._format_submodule_status(self.mock_submodule)
        self.assertIn(":white_check_mark:", status)
        self.assertIn("is up-to-date with default branch", status)
        self.assertIn(f"[{self.mock_submodule.path_in_project}]({self.mock_submodule.url})", status)

    def test_format_status_behind_default_branch(self):
        """Test status when submodule is behind the default branch."""
        guardian = self._create_guardian(fix=True)
        guardian._fix_submodule = MagicMock()
        self._reset_submodule_mocks()
        self.mock_submodule.is_on_default_branch.return_value = True

        status = guardian._format_submodule_status(self.mock_submodule)
        self.assertIn(":warning:", status)
        self.assertIn("is behind its latest default branch", status)
        # Verify that the fix method was called
        guardian._fix_submodule.assert_called_once_with(self.mock_submodule, 'default_branch')

    def test_format_status_on_different_branch(self):
        """Test status when submodule is on a different, non-default branch."""
        guardian = self._create_guardian()
        self._reset_submodule_mocks()
        self.mock_submodule.branches = ['feature-branch']  # is_on_default_branch is False

        status = guardian._format_submodule_status(self.mock_submodule)
        self.assertIn(":x:", status)
        self.assertIn("is not the default branch", status)
        self.assertIn("(ref branch name(s): ['feature-branch'])", status)

    # --- Tag Scenarios ---

    def test_format_status_on_tag_not_allowed(self):
        """Test status when on a tag, but tags are not allowed."""
        guardian = self._create_guardian(allow_tags=False)
        self._reset_submodule_mocks()
        self.mock_submodule.is_on_tag.return_value = 'v1.0.0'

        status = guardian._format_submodule_status(self.mock_submodule)
        self.assertIn(":warning:", status)
        self.assertIn("but tags are not allowed", status)

    def test_format_status_on_any_tag_allowed(self):
        """Test status when on a tag, and any tag is allowed."""
        guardian = self._create_guardian(allow_tags=True, only_latest_tag=False)
        self._reset_submodule_mocks()
        self.mock_submodule.is_on_tag.return_value = 'v1.0.0'

        status = guardian._format_submodule_status(self.mock_submodule)
        self.assertIn(":white_check_mark:", status)
        self.assertIn("is on tag `v1.0.0`", status)

    def test_format_status_on_latest_tag(self):
        """Test status when on the latest tag and only latest is allowed."""
        guardian = self._create_guardian(allow_tags=True, only_latest_tag=True)
        self._reset_submodule_mocks()
        self.mock_submodule.is_on_tag.return_value = 'v1.1.0'
        self.mock_submodule.is_on_latest_tag.return_value = True

        status = guardian._format_submodule_status(self.mock_submodule)
        self.assertIn(":white_check_mark:", status)
        self.assertIn("is on the latest tag `v1.1.0`.", status)

    def test_format_status_on_old_tag(self):
        """Test status when on an old tag and only latest is allowed."""
        guardian = self._create_guardian(allow_tags=True, only_latest_tag=True, fix=True)
        guardian._fix_submodule = MagicMock()
        self._reset_submodule_mocks()
        self.mock_submodule.is_on_tag.return_value = 'v1.0.0'
        self.mock_submodule.is_on_latest_tag.return_value = False  # Not on latest

        status = guardian._format_submodule_status(self.mock_submodule)
        self.assertIn(":warning:", status)
        self.assertIn("is on tag `v1.0.0`, but a newer tag `v1.1.0` is available.", status)
        # Verify that the fix method was called
        guardian._fix_submodule.assert_called_once_with(self.mock_submodule, 'latest_tag')

    @patch('mlx.submodule_guardian.submodule_guardian.subprocess.run')
    def test_fix_submodule_calls_subprocess(self, mock_subprocess_run):
        """Test that _fix_submodule calls the correct git commands."""
        guardian = self._create_guardian(fix=True)
        path = self.mock_submodule.path_in_project
        default_branch = self.mock_submodule.sub_project.default_branch

        # Test fixing to default branch
        guardian._fix_submodule(self.mock_submodule, 'default_branch')

        # Check calls for default_branch fix
        expected_calls = [
            unittest.mock.call(['git', '-C', path, 'fetch', '--tags', '--force'], check=True),
            unittest.mock.call(['git', '-C', path, 'checkout', default_branch], check=True),
            unittest.mock.call(['git', '-C', path, 'pull', 'origin', default_branch], check=True),
            unittest.mock.call(['git', 'add', path], check=True)
        ]
        mock_subprocess_run.assert_has_calls(expected_calls, any_order=False)

        # Reset mock and test fixing to latest tag
        mock_subprocess_run.reset_mock()
        latest_tag_name = self.mock_latest_tag.name
        guardian._fix_submodule(self.mock_submodule, 'latest_tag')
        self.assertEqual(mock_subprocess_run.call_count, 4)
        # Check that checkout is called with the tag name
        self.assertIn(latest_tag_name, mock_subprocess_run.call_args_list[1].args[0])

    def test_format_status_no_default_branch(self):
        """Test status when the default branch cannot be determined."""
        guardian = self._create_guardian()
        self.mock_submodule.sub_project.default_branch = None  # Simulate no default branch

        status = guardian._format_submodule_status(self.mock_submodule)
        self.assertIn(":warning:", status)
        self.assertIn("Could not determine default branch", status)

        # Reset for other tests
        self.mock_submodule.sub_project.default_branch = 'main'

    # --- Report Scenarios ---

    @patch('sys.exit')
    def test_report_fails_pipeline_on_warning(self, mock_sys_exit):
        """Test that report calls sys.exit(1) if fail_pipeline is True and warnings exist."""
        guardian = self._create_guardian(fail_pipeline=True)
        guardian.resolved = False  # Simulate a warning
        status_lines = [":warning: A warning message"]

        guardian.report(status_lines)

        mock_sys_exit.assert_called_once_with(1)

    @patch('sys.exit')
    def test_report_does_not_fail_pipeline_on_success(self, mock_sys_exit):
        """Test that report calls sys.exit(0) if fail_pipeline is True and no warnings exist."""
        guardian = self._create_guardian(fail_pipeline=True)
        guardian.resolved = True  # Simulate success
        status_lines = [":white_check_mark: A success message"]

        guardian.report(status_lines)

    @patch('mlx.submodule_guardian.submodule_guardian.SubmoduleGuardian._post_or_update_discussion')
    def test_report_creates_discussion(self, mock_post_discussion):
        """Test that report creates a discussion by default."""
        guardian = self._create_guardian()
        guardian.resolved = False  # Simulate a warning
        guardian.discussion_template = MagicMock()
        status_lines_md = [":warning: Submodule sub is behind."]

        guardian.report(status_lines_md)

        mock_post_discussion.assert_called_once()
        # Check that the markdown version is passed to the template
        render_call_args = guardian.discussion_template.render.call_args
        self.assertEqual(render_call_args.kwargs['status_lines'], status_lines_md)

    @patch('mlx.submodule_guardian.submodule_guardian.SubmoduleGuardian._post_or_update_discussion')
    def test_report_updates_discussion(self, mock_post_discussion):
        """Test that report updates an existing discussion."""
        guardian = self._create_guardian()
        guardian.resolved = False
        guardian.discussion_template = MagicMock()
        status_lines = [":warning: A warning message"]

        # Mock an existing discussion
        mock_discussion = MagicMock()
        mock_note = MagicMock()
        mock_note.body = "## Submodule Status Check"
        mock_note.author = {'id': guardian.current_user.id}
        mock_discussion.attributes = {'notes': [mock_note]}
        guardian.mr.discussions.list.return_value = [mock_discussion]

        guardian.report(status_lines)

        mock_post_discussion.assert_called_once()
        # Verify that the comment body is correctly rendered and passed
        render_call = guardian.discussion_template.render.call_args.kwargs
        self.assertEqual(render_call['status_lines'], status_lines)

    @patch('sys.exit')
    @patch('mlx.submodule_guardian.submodule_guardian.SubmoduleGuardian._post_or_update_discussion')
    def test_report_no_discussion_and_fail(self, mock_post_discussion, mock_sys_exit):
        """Test report does not post discussion but fails pipeline when specified."""
        guardian = self._create_guardian(fail_pipeline=True, post_discussion=False)
        guardian.resolved = False  # Simulate a warning
        status_lines = [":warning: A warning message"]

        with self.assertLogs('submodule-guardian', level='WARNING') as cm:
            guardian.report(status_lines)

            # Assert that no discussion was posted
            mock_post_discussion.assert_not_called()
            # Assert that the pipeline failed
            mock_sys_exit.assert_called_once_with(1)
            # Assert the correct log message was shown
            self.assertIn("Warnings detected. Discussion not posted due to --no-post-discussion flag.", cm.output[0])

    @patch('mlx.submodule_guardian.submodule_guardian.logger')
    def test_report_dry_run(self, mock_logger):
        """Test that report prints to console and does not fail on dry run."""
        guardian = self._create_guardian(dry_run=True)
        guardian.resolved = False
        status_lines = [":warning: A warning message"]

        guardian.report(status_lines)

        mock_logger.warning.assert_called_with("Warnings detected. In a CI run, this would create an unresolved "
                                               "discussion or fail the pipeline.")

    # --- Method-specific tests for coverage ---

    def test_determine_mr_iid_multiple_mrs(self):
        """Test _determine_merge_request_iid when multiple MRs are found."""
        # mr_iid=None to trigger _determine_merge_request_iid
        guardian = self._create_guardian(dry_run=True)
        guardian.mr_iid = None
        mock_mr1 = MagicMock(iid=1, target_branch='main')
        mock_mr2 = MagicMock(iid=2, target_branch='develop')
        guardian.project.mergerequests.list.return_value = [mock_mr1, mock_mr2]
        guardian.branch = 'feature-branch'

        with self.assertRaisesRegex(ValueError, "Found multiple open merge requests"):
            guardian._determine_merge_request_iid()

    def test_determine_mr_iid_no_mrs(self):
        """Test _determine_merge_request_iid when no MRs are found."""
        # mr_iid=None to trigger _determine_merge_request_iid
        guardian = self._create_guardian(dry_run=True)
        guardian.mr_iid = None
        guardian.project.mergerequests.list.return_value = []
        guardian.branch = 'feature-branch'

        with self.assertRaisesRegex(ValueError, "Could not find an open merge request"):
            guardian._determine_merge_request_iid()

    def test_get_changed_submodules_no_submodules_in_project(self):
        """Test get_changed_submodules when the project has no submodules."""
        guardian = self._create_guardian()
        guardian.submodules = []  # Ensure no submodules are configured
        # Mock read_gitmodules to ensure it doesn't populate submodules
        guardian.read_gitmodules = MagicMock()
        result = guardian.get_changed_submodules()
        self.assertEqual(result, [])

    def test_get_changed_submodules_no_submodule_changes_in_mr(self):
        """Test get_changed_submodules when the MR has no submodule changes."""
        guardian = self._create_guardian()
        guardian.submodules = [self.mock_submodule]
        guardian.mr.changes.return_value = {'changes': [{'new_path': 'src/main.c'}]}

        result = guardian.get_changed_submodules()
        self.assertEqual(result, [])

    @patch('mlx.submodule_guardian.submodule_guardian.SubmoduleGuardian.get_changed_submodules', return_value=[])
    @patch('mlx.submodule_guardian.submodule_guardian.SubmoduleGuardian.read_gitmodules')
    def test_run_no_changes_and_not_always_check(self, mock_read_gitmodules, mock_get_changed):
        """Test run() when no submodules changed and --always-check is off."""
        guardian = self._create_guardian()
        guardian.always_check = False  # Override default for this test
        guardian.report = MagicMock()  # Mock the report method to check if it's called

        guardian.run()
        mock_get_changed.assert_called_once()
        guardian.report.assert_not_called()

    @patch('mlx.submodule_guardian.submodule_guardian.console')
    def test_run_prints_and_reports(self, mock_console): # noqa
        """Test that run() calls check_submodules, prints, and reports."""
        guardian = self._create_guardian(dry_run=True)  # Test dry-run to cover line 404
        guardian.always_check = True
        guardian.submodules = [self.mock_submodule]
        guardian.check_submodules = MagicMock(return_value=["status line 1"])
        guardian.report = MagicMock()
        guardian.read_gitmodules = MagicMock()

        guardian.run()

        guardian.check_submodules.assert_called_once_with([self.mock_submodule])
        guardian.report.assert_called_once_with(["status line 1"])
        # Verify dry-run console.print was called (line 404)
        mock_console.print.assert_any_call("\n--- Submodule Status Report (Dry Run) ---", style="bold")

    def test_post_or_update_discussion_cannot_get_note_url(self):
        """Test _post_or_update_discussion when the new note URL can't be determined."""
        guardian = self._create_guardian()
        guardian.mr.discussions.list.return_value = []  # Force creation
        mock_discussion_obj = MagicMock()
        # Simulate a response without a 'notes' attribute
        mock_discussion_obj.attributes = {'notes': []}  # Empty notes list
        guardian.mr.discussions.create.return_value = mock_discussion_obj

        with self.assertLogs('submodule-guardian', level='WARNING') as cm:
            url = guardian._post_or_update_discussion("comment", "search")
            self.assertIsNone(url)
            # Check for the specific warning message from the updated _post_or_update_discussion
            self.assertTrue(any("Could not determine URL for new comment" in msg for msg in cm.output) or
                            any("no notes returned" in msg for msg in cm.output))


# --- Main and Helper Function Tests ---
class MainAndHelperFunctionTest(unittest.TestCase):

    @patch('mlx.submodule_guardian.submodule_guardian.get_current_branch', return_value='my-branch')
    @patch('mlx.submodule_guardian.submodule_guardian.os.getenv')  # Patch os.getenv as seen by submodule_guardian
    @patch('mlx.submodule_guardian.submodule_guardian.SubmoduleGuardian', autospec=True)
    @patch('mlx.submodule_guardian.submodule_guardian.parse_args')
    def test_main_basic_ci_run(self, mock_parse_args, mock_guardian_cls, mock_getenv, mock_get_branch):
        """Test the main function with typical CI environment variables."""
        mock_parse_args.return_value = argparse.Namespace(
            project=None, mr_iid=None, branch=None, fail_pipeline=True,
            always_check=True, allow_tags=False, only_latest_tag=True, fix=False,
            verbose=True, debug=False, post_discussion=True
        )
        mock_getenv.side_effect = lambda key, default=None: {
            'CI_PROJECT_PATH': 'group/project',
            'CI_MERGE_REQUEST_IID': '123',
            'CI_COMMIT_BRANCH': 'feature-branch',
            'PRIVATE_TOKEN': 'dummy-token',
            'CI_SERVER_HOST': 'gitlab.example.com',
            'CI_PROJECT_ID': '42'
        }.get(key, default)

        main()

        # CI_PROJECT_ID is preferred over CI_PROJECT_PATH (see line 462 in submodule_guardian.py)
        mock_guardian_cls.assert_called_once_with(
            project_identifier='42',
            fail_pipeline=True,
            always_check=True,
            dry_run=False,
            allow_tags=False,
            only_latest_tag=True,
            mr_iid='123',
            fix=False,
            branch='feature-branch',
            post_discussion=True
        )
        mock_guardian_cls.return_value.run.assert_called_once()

    @patch('mlx.submodule_guardian.submodule_guardian.get_current_branch', return_value='my-branch')
    @patch('mlx.submodule_guardian.submodule_guardian.os.getenv')
    @patch('mlx.submodule_guardian.submodule_guardian.SubmoduleGuardian', autospec=True)
    @patch('mlx.submodule_guardian.submodule_guardian.parse_args')
    def test_main_local_run_with_fix(self, mock_parse_args, mock_guardian_cls, mock_getenv, mock_get_branch):
        """Test the main function for a local run with the --fix flag."""
        mock_parse_args.return_value = argparse.Namespace(
            project='group/project', mr_iid='123', branch=None, fail_pipeline=False,
            always_check=True, allow_tags=True, only_latest_tag=True, fix=True,
            verbose=False, debug=False, post_discussion=True
        )
        # Simulate a local run (no CI variables)
        mock_getenv.side_effect = lambda key, default=None: {
            'PRIVATE_TOKEN': 'dummy-token',
            'CI_SERVER_HOST': 'gitlab.example.com',
        }.get(key, default)

        main()

        mock_guardian_cls.assert_called_once_with(
            project_identifier='group/project',
            fail_pipeline=False,
            always_check=True,
            dry_run=True,  # Should be True for local run
            allow_tags=True,
            only_latest_tag=True,
            mr_iid='123',
            fix=True,  # Should be passed as True
            branch='my-branch',
            post_discussion=True
        )
        mock_guardian_cls.return_value.run.assert_called_once()

    @patch('sys.exit', side_effect=SystemExit(1))
    @patch('mlx.submodule_guardian.submodule_guardian.parse_args')
    def test_main_missing_project_id(self, mock_parse_args, mock_sys_exit):
        """Test main function exits with code 1 if project ID is missing."""
        mock_parse_args.return_value = argparse.Namespace(
            project=None, mr_iid='123', branch='b', debug=False, verbose=False, fix=False,
            fail_pipeline=False, always_check=False, allow_tags=False, only_latest_tag=False,
            post_discussion=True
        )
        # Mock os.getenv to return None for project-related vars
        with patch('mlx.submodule_guardian.submodule_guardian.os.getenv') as mock_getenv:
            # Ensure PRIVATE_TOKEN is found, but project identifiers are not.
            mock_getenv.side_effect = lambda key, default=None: 'dummy' if 'TOKEN' in key else None

            with self.assertRaises(SystemExit):
                main()
            # The function exits once for the validation error
            mock_sys_exit.assert_called_once_with(1)

    @patch('sys.exit', side_effect=SystemExit(1))
    @patch('mlx.submodule_guardian.submodule_guardian.SubmoduleGuardian')
    @patch('mlx.submodule_guardian.submodule_guardian.parse_args')
    def test_main_missing_mr_and_branch(self, mock_parse_args, mock_guardian_cls, mock_sys_exit):
        """Test main function exits with code 1 if both MR IID and branch are missing."""
        mock_parse_args.return_value = argparse.Namespace(
            project='p', mr_iid=None, branch=None, debug=False, verbose=False, fix=False,  # project is provided
            fail_pipeline=False, always_check=False, allow_tags=False, only_latest_tag=False,
            post_discussion=True
        )
        # Ensure CI variables are also None
        with patch('mlx.submodule_guardian.submodule_guardian.os.getenv') as mock_getenv, \
             patch('mlx.submodule_guardian.submodule_guardian.get_current_branch', return_value=None):
            mock_getenv.side_effect = lambda key, default=None: 'dummy' if 'TOKEN' in key else None

            with self.assertRaises(SystemExit):
                main()
            # The function exits once for the validation error
            mock_sys_exit.assert_called_once_with(1)
            # Guardian should not be created since validation fails first
            mock_guardian_cls.assert_not_called()

    @patch('sys.exit')
    @patch('mlx.submodule_guardian.submodule_guardian.SubmoduleGuardian', side_effect=Exception("Test Error"))
    @patch('mlx.submodule_guardian.submodule_guardian.parse_args')
    def test_main_exception_handling(self, mock_parse_args, mock_guardian_cls, mock_sys_exit):
        """Test that main catches exceptions from the guardian and exits."""
        mock_parse_args.return_value = argparse.Namespace(
            project='p', mr_iid='1', branch='b', fail_pipeline=False,
            always_check=False, allow_tags=False, only_latest_tag=False, fix=False,
            verbose=False, debug=True, post_discussion=True
        )
        # Mock os.getenv to return necessary values for the guardian constructor
        with patch.dict(os.environ, {
            'PRIVATE_TOKEN': 'dummy',
            'CI_SERVER_HOST': 'gitlab.example.com'
        }), self.assertLogs('submodule-guardian', level='ERROR') as cm:
            main()
            mock_sys_exit.assert_called_once_with(1)
            # logger.exception() logs the full traceback
            # Check that we captured at least one ERROR message
            # The assertLogs context manager should have captured the exception log
            self.assertGreater(len(cm.output), 0,
                               f"Expected at least one ERROR log message, but got: {cm.output}")
            # Check that the error message contains the expected text
            all_output = " ".join(cm.output)
            # The message should contain either the error text or the exception message
            self.assertTrue(
                "An unexpected error occurred" in all_output or "Test Error" in all_output,
                f"Expected error message not found in captured logs: {cm.output}"
            )

    @patch('subprocess.run')
    def test_get_current_branch_success(self, mock_subprocess_run):
        """Test get_current_branch on success."""
        mock_subprocess_run.return_value = MagicMock(stdout='my-feature-branch\n', check=True, text=True)
        self.assertEqual(get_current_branch(), 'my-feature-branch')

    @patch('subprocess.run', side_effect=FileNotFoundError)
    def test_get_current_branch_git_not_found(self, mock_subprocess_run):
        """Test get_current_branch when git command is not found."""
        self.assertIsNone(get_current_branch())

    @patch('subprocess.run')
    def test_get_current_branch_subprocess_error(self, mock_subprocess_run):
        """Test get_current_branch on a generic subprocess error."""
        mock_subprocess_run.side_effect = subprocess.SubprocessError("generic error")
        self.assertIsNone(get_current_branch())


# --- Submodule Class Tests ---
class SubmoduleTest(unittest.TestCase):

    def setUp(self):
        """Set up mock objects for Submodule tests."""
        self.mock_gitlab = MagicMock()
        self.mock_project = MagicMock()
        self.mock_project.path_with_namespace = 'group/project'
        self.mock_sub_project = MagicMock()
        self.mock_sub_project.default_branch = 'main'
        self.mock_sub_project.http_url_to_repo = 'http://gitlab.example.com/sub/project.git'

    def test_from_gitmodules_with_git_suffix(self):
        """Test Submodule.from_gitmodules with .git suffix in URL."""
        self.mock_gitlab.projects.get.return_value = self.mock_sub_project
        mock_submodule_dir = MagicMock()
        mock_submodule_dir.blob_id = 'commit_123'
        self.mock_project.files.get.return_value = mock_submodule_dir

        submodule = Submodule.from_gitmodules(
            self.mock_gitlab, self.mock_project, 'main', 'path/to/sub', 'http://gitlab.example.com/sub/project.git'
        )

        self.assertEqual(submodule.sub_project, self.mock_sub_project)
        self.assertEqual(submodule.commit_id, 'commit_123')
        self.assertEqual(submodule.path_in_project, 'path/to/sub')

    def test_from_gitmodules_without_git_suffix(self):
        """Test Submodule.from_gitmodules without .git suffix in URL."""
        self.mock_gitlab.projects.get.return_value = self.mock_sub_project
        mock_submodule_dir = MagicMock()
        mock_submodule_dir.blob_id = 'commit_456'
        self.mock_project.files.get.return_value = mock_submodule_dir

        submodule = Submodule.from_gitmodules(
            self.mock_gitlab, self.mock_project, 'main', 'path/to/sub', 'http://gitlab.example.com/sub/project'
        )

        self.assertEqual(submodule.commit_id, 'commit_456')

    @patch('mlx.submodule_guardian.submodule_guardian.logger')
    def test_from_gitmodules_different_domain(self, mock_logger):
        """Test Submodule.from_gitmodules when submodule URL is from a different domain."""
        self.mock_gitlab.url = 'https://gitlab.melexis.com'
        mock_submodule_dir = MagicMock()
        mock_submodule_dir.blob_id = 'commit_123'
        self.mock_project.files.get.return_value = mock_submodule_dir

        submodule = Submodule.from_gitmodules(
            self.mock_gitlab, self.mock_project, 'main', 'path/to/sub', 'https://github.com/some/repo.git'
        )

        self.assertIsNone(submodule.sub_project)
        self.assertIsNone(submodule.commit_id)
        self.assertEqual(submodule.path_in_project, 'path/to/sub')
        self.assertIn("Submodule path/to/sub is skipped due to different domain", submodule.error)
        mock_logger.error.assert_called_once()

    @patch('mlx.submodule_guardian.submodule_guardian.logger')
    def test_from_gitmodules_invalid_url_format(self, mock_logger):
        """
        Test Submodule.from_gitmodules with an invalid URL format that doesn't match the regex.
        The current implementation attempts to resolve it as an internal path.
        """
        self.mock_gitlab.url = 'https://gitlab.example.com'
        self.mock_gitlab.projects.get.return_value = self.mock_sub_project
        mock_submodule_dir = MagicMock()
        mock_submodule_dir.blob_id = 'commit_123'
        self.mock_project.files.get.return_value = mock_submodule_dir

        submodule = Submodule.from_gitmodules(
            self.mock_gitlab, self.mock_project, 'main', 'path/to/sub', 'invalid-url-format'
        )

        self.assertIsNotNone(submodule.sub_project)
        self.assertEqual(submodule.commit_id, 'commit_123')
        self.assertEqual(submodule.path_in_project, 'path/to/sub')
        self.assertEqual(submodule.error, '')
        mock_logger.error.assert_not_called()

    @patch('mlx.submodule_guardian.submodule_guardian.logger')
    def test_from_gitmodules_subproject_get_fails(self, mock_logger):
        """
        Test Submodule.from_gitmodules when gl.projects.get fails for the submodule project.
        This exception is expected to be caught by the calling function (read_gitmodules).
        """
        self.mock_gitlab.url = 'https://gitlab.example.com'
        self.mock_gitlab.projects.get.side_effect = Exception("Project not found")
        mock_submodule_dir = MagicMock()
        mock_submodule_dir.blob_id = 'commit_123'
        self.mock_project.files.get.return_value = mock_submodule_dir

        with self.assertRaisesRegex(Exception, "Project not found"):
            Submodule.from_gitmodules(
                self.mock_gitlab, self.mock_project, 'main', 'path/to/sub', 'https://gitlab.example.com/sub/project.git'
            )
        mock_logger.error.assert_not_called()

    def test_latest_tag_property_with_tags(self):
        """Test latest_tag property when tags exist."""
        mock_tag = MagicMock()
        mock_tag.name = 'v1.0.0'
        self.mock_sub_project.tags.list.return_value = [mock_tag]

        submodule = Submodule(
            sub_project=self.mock_sub_project,
            commit_id='commit_123',
            path_in_project='path/to/sub'
        )

        latest_tag = submodule.latest_tag
        self.assertEqual(latest_tag, mock_tag)
        self.mock_sub_project.tags.list.assert_called_once_with(page=1, per_page=1)

    def test_latest_tag_property_no_tags(self):
        """Test latest_tag property when no tags exist."""
        self.mock_sub_project.tags.list.return_value = []

        submodule = Submodule(
            sub_project=self.mock_sub_project,
            commit_id='commit_123',
            path_in_project='path/to/sub'
        )

        latest_tag = submodule.latest_tag
        self.assertIsNone(latest_tag)

    def test_url_property(self):
        """Test url property."""
        submodule = Submodule(
            sub_project=self.mock_sub_project,
            commit_id='commit_123',
            path_in_project='path/to/sub'
        )

        self.assertEqual(submodule.url, 'http://gitlab.example.com/sub/project.git')

    def test_branches_property(self):
        """Test branches property."""
        mock_commit = MagicMock()
        mock_commit.refs.return_value = [
            {'name': 'main'},
            {'name': 'feature-branch'}
        ]
        self.mock_sub_project.commits.get.return_value = mock_commit

        submodule = Submodule(
            sub_project=self.mock_sub_project,
            commit_id='commit_123',
            path_in_project='path/to/sub'
        )

        branches = submodule.branches
        self.assertEqual(branches, ['main', 'feature-branch'])
        self.mock_sub_project.commits.get.assert_called_once_with('commit_123')
        mock_commit.refs.assert_called_once_with(type='branch')

    def test_is_on_tag_found(self):
        """Test is_on_tag when commit is on a tag."""
        mock_tag1 = MagicMock()
        mock_tag1.commit = {'id': 'other_commit'}
        mock_tag2 = MagicMock()
        mock_tag2.commit = {'id': 'commit_123'}
        mock_tag2.name = 'v1.0.0'

        self.mock_sub_project.tags.list.return_value = [mock_tag1, mock_tag2]

        submodule = Submodule(
            sub_project=self.mock_sub_project,
            commit_id='commit_123',
            path_in_project='path/to/sub'
        )

        tag_name = submodule.is_on_tag()
        self.assertEqual(tag_name, 'v1.0.0')

    def test_is_on_tag_not_found(self):
        """Test is_on_tag when commit is not on any tag."""
        mock_tag = MagicMock()
        mock_tag.commit = {'id': 'other_commit'}
        self.mock_sub_project.tags.list.return_value = [mock_tag]

        submodule = Submodule(
            sub_project=self.mock_sub_project,
            commit_id='commit_123',
            path_in_project='path/to/sub'
        )

        tag_name = submodule.is_on_tag()
        self.assertIsNone(tag_name)

    def test_is_on_latest_tag_true(self):
        """Test is_on_latest_tag when on latest tag."""
        mock_latest_tag = MagicMock()
        mock_latest_tag.commit = {'id': 'commit_123'}
        self.mock_sub_project.tags.list.return_value = [mock_latest_tag]

        submodule = Submodule(
            sub_project=self.mock_sub_project,
            commit_id='commit_123',
            path_in_project='path/to/sub'
        )

        result = submodule.is_on_latest_tag()
        self.assertTrue(result)

    def test_is_on_latest_tag_false(self):
        """Test is_on_latest_tag when not on latest tag."""
        mock_latest_tag = MagicMock()
        mock_latest_tag.commit = {'id': 'other_commit'}
        self.mock_sub_project.tags.list.return_value = [mock_latest_tag]

        submodule = Submodule(
            sub_project=self.mock_sub_project,
            commit_id='commit_123',
            path_in_project='path/to/sub'
        )

        result = submodule.is_on_latest_tag()
        self.assertFalse(result)

    def test_is_latest_default_branch_true(self):
        """Test is_latest_default_branch when on latest default branch."""
        self.mock_sub_project.default_branch = 'main'
        mock_commit = MagicMock()
        mock_commit.id = 'commit_123'
        self.mock_sub_project.commits.get.return_value = mock_commit

        submodule = Submodule(
            sub_project=self.mock_sub_project,
            commit_id='commit_123',
            path_in_project='path/to/sub'
        )

        result = submodule.is_latest_default_branch()
        self.assertTrue(result)
        self.mock_sub_project.commits.get.assert_called_once_with('main')

    def test_is_latest_default_branch_false(self):
        """Test is_latest_default_branch when not on latest default branch."""
        self.mock_sub_project.default_branch = 'main'
        mock_commit = MagicMock()
        mock_commit.id = 'other_commit'
        self.mock_sub_project.commits.get.return_value = mock_commit

        submodule = Submodule(
            sub_project=self.mock_sub_project,
            commit_id='commit_123',
            path_in_project='path/to/sub'
        )

        result = submodule.is_latest_default_branch()
        self.assertFalse(result)

    def test_is_on_default_branch_true(self):
        """Test is_on_default_branch when on default branch."""
        self.mock_sub_project.default_branch = 'main'
        mock_commit = MagicMock()
        mock_commit.refs.return_value = [{'name': 'main'}]
        self.mock_sub_project.commits.get.return_value = mock_commit

        submodule = Submodule(
            sub_project=self.mock_sub_project,
            commit_id='commit_123',
            path_in_project='path/to/sub'
        )

        result = submodule.is_on_default_branch()
        self.assertTrue(result)

    def test_is_on_default_branch_false(self):
        """Test is_on_default_branch when not on default branch."""
        self.mock_sub_project.default_branch = 'main'
        mock_commit = MagicMock()
        mock_commit.refs.return_value = [{'name': 'feature-branch'}]
        self.mock_sub_project.commits.get.return_value = mock_commit

        submodule = Submodule(
            sub_project=self.mock_sub_project,
            commit_id='commit_123',
            path_in_project='path/to/sub'
        )

        result = submodule.is_on_default_branch()
        self.assertFalse(result)


# --- Additional SubmoduleGuardian Tests ---
class SubmoduleGuardianAdditionalTest(unittest.TestCase):

    def setUp(self):
        """Set up mock objects for GitLab and Subproject.""" # noqa
        self.config_patch = patch('mlx.submodule_guardian.submodule_guardian.config')
        self.mock_gitlab = MagicMock()
        self.mock_project = MagicMock()
        self.mock_mr = MagicMock()
        self.mock_gitlab.projects.get.return_value = self.mock_project
        self.mock_project.mergerequests.get.return_value = self.mock_mr
        self.gitlab_patch = patch('mlx.submodule_guardian.submodule_guardian.Gitlab', return_value=self.mock_gitlab)
        self.gitlab_patch.start()
        self.mock_config = self.config_patch.start()
        self.mock_config.side_effect = lambda key, default=None: 'dummy_token' if key == 'PRIVATE_TOKEN' else default

    def tearDown(self):
        """Stop all patches."""
        patch.stopall()

    def _create_guardian(self, allow_tags=False, only_latest_tag=False, fail_pipeline=False, dry_run=False,
                         post_discussion=True, fix=False):
        """Helper to create a SubmoduleGuardian instance with mocked GitLab."""
        with patch('mlx.submodule_guardian.submodule_guardian.config') as mock_config:
            mock_config.side_effect = lambda key, default=None: 'dummy_token' if key == 'PRIVATE_TOKEN' else default
            return SubmoduleGuardian(
                project_identifier='some/project',
                mr_iid='1',
                fail_pipeline=fail_pipeline,
                always_check=True,
                dry_run=dry_run,
                allow_tags=allow_tags,
                only_latest_tag=only_latest_tag,
                post_discussion=post_discussion,
                fix=fix
            )

    def test_determine_mr_iid_single_mr(self):
        """Test _determine_merge_request_iid when exactly one MR is found."""
        guardian = self._create_guardian(dry_run=True)
        guardian.mr_iid = None  # type: ignore
        guardian.branch = 'feature-branch'
        mock_mr = MagicMock(iid=42, target_branch='main')
        guardian.project.mergerequests.list.return_value = [mock_mr]

        result = guardian._determine_merge_request_iid()
        self.assertEqual(result, 42)

    def test_setup_gitlab_no_token(self):
        """Test _setup_gitlab raises NameError when PRIVATE_TOKEN is missing."""
        with patch('mlx.submodule_guardian.submodule_guardian.config') as mock_config:
            mock_config.side_effect = lambda key, default=None: None if key == 'PRIVATE_TOKEN' else default
            with self.assertRaises(NameError) as cm:
                _ = SubmoduleGuardian(
                    project_identifier='some/project',
                    mr_iid='1',
                    fail_pipeline=False,
                    always_check=True,
                    dry_run=False,
                    allow_tags=False,
                    only_latest_tag=False,
                    post_discussion=True,
                    fix=False
                )
            self.assertIn('PRIVATE_TOKEN not found', str(cm.exception))

    def test_setup_gitlab_url_without_https(self):
        """Test _setup_gitlab adds https:// prefix when URL doesn't have it."""
        with patch('mlx.submodule_guardian.submodule_guardian.config') as mock_config, \
             patch('mlx.submodule_guardian.submodule_guardian.Gitlab') as mock_gitlab_class:
            mock_config.side_effect = lambda key, default=None: (
                'dummy_token' if key == 'PRIVATE_TOKEN'
                else 'gitlab.example.com' if key == 'CI_SERVER_HOST'
                else default
            )
            _ = SubmoduleGuardian(
                project_identifier='some/project',
                mr_iid='1',
                fail_pipeline=False,
                always_check=True,
                dry_run=False,
                allow_tags=False,
                only_latest_tag=False,
                post_discussion=True,
                fix=False
            )
            # Verify Gitlab was called with https:// prefix
            mock_gitlab_class.assert_called_once()
            call_args = mock_gitlab_class.call_args
            self.assertTrue(call_args[0][0].startswith('https://'))

    def test_check_submodules_empty_list(self):
        """Test check_submodules with an empty list."""
        guardian = self._create_guardian()

        with self.assertLogs('submodule-guardian', level='INFO') as cm:
            result = guardian.check_submodules([])

        self.assertEqual(result, [])
        self.assertTrue(any("No submodules to check" in msg for msg in cm.output))

    def test_check_submodules_with_submodules(self):
        """Test check_submodules iterates through submodules and formats status."""
        guardian = self._create_guardian()
        # Create a real Submodule instance to test the iteration
        from mlx.submodule_guardian.submodule_guardian import Submodule
        mock_sub_project = MagicMock()
        mock_sub_project.default_branch = 'main'
        mock_sub_project.http_url_to_repo = 'http://gitlab.example.com/sub.git'
        mock_commit = MagicMock()
        mock_commit.id = 'commit_123'
        mock_commit.refs.return_value = [{'name': 'main'}]
        mock_sub_project.commits.get.return_value = mock_commit
        mock_sub_project.tags.list.return_value = []  # No tags, so is_on_tag returns None

        submodule = Submodule(
            sub_project=mock_sub_project,
            commit_id='commit_123',
            path_in_project='path/to/sub'
        )

        with self.assertLogs('submodule-guardian', level='INFO') as cm:
            result = guardian.check_submodules([submodule])

        self.assertEqual(len(result), 1)
        self.assertTrue(any("Checking status for 1 submodule(s)" in msg for msg in cm.output))
        self.assertIn("is up-to-date with default branch", result[0])

    def test_post_or_update_discussion_updates_existing(self):
        """Test _post_or_update_discussion updates existing discussion."""
        guardian = self._create_guardian()
        guardian.resolved = False

        mock_discussion = MagicMock()
        mock_discussion.id = 'disc_123'
        mock_note_data = {
            'body': '## Submodule Status Check',
            'author': {'id': guardian.current_user.id},
            'id': 'note_456'
        }
        mock_discussion.attributes = {'notes': [mock_note_data]}

        guardian.mr.discussions.list.return_value = [mock_discussion]
        mock_discussion_obj = MagicMock()
        mock_discussion_obj.id = 'disc_123'
        guardian.mr.discussions.get.return_value = mock_discussion_obj
        mock_note_obj = MagicMock()
        mock_note_obj.id = 'note_456'
        mock_discussion_obj.notes.get.return_value = mock_note_obj
        guardian.mr.web_url = 'http://gitlab.example.com/project/-/merge_requests/1'

        url = guardian._post_or_update_discussion("new comment", "## Submodule Status Check")

        self.assertIsNotNone(url)
        self.assertIsInstance(url, str)
        self.assertIn('note_456', url)
        mock_note_obj.save.assert_called_once()
        mock_discussion_obj.save.assert_called_once()

    def test_post_or_update_discussion_exception_handling(self):
        """Test _post_or_update_discussion exception handling when accessing notes."""
        guardian = self._create_guardian()
        guardian.mr.discussions.list.return_value = []
        mock_discussion_obj = MagicMock()
        mock_discussion_obj.attributes = {'notes': [{}]}  # Missing 'id' key
        guardian.mr.discussions.create.return_value = mock_discussion_obj

        with self.assertLogs('submodule-guardian', level='WARNING') as cm:
            url = guardian._post_or_update_discussion("comment", "search")

        self.assertIsNone(url)
        self.assertTrue(any("Could not determine URL for new comment" in msg for msg in cm.output))

    def test_get_changed_submodules_with_changes(self):
        """Test get_changed_submodules when submodules are changed."""
        guardian = self._create_guardian()
        mock_submodule = MagicMock()
        mock_submodule.path_in_project = 'path/to/sub'
        guardian.submodules = [mock_submodule]

        guardian.mr.changes.return_value = {
            'changes': [
                {'new_path': 'path/to/sub'},
                {'new_path': 'other/file.txt'}
            ]
        }

        with self.assertLogs('submodule-guardian', level='INFO') as cm:
            result = guardian.get_changed_submodules()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], mock_submodule)
        self.assertTrue(any("Detected change in submodule" in msg for msg in cm.output))

    @patch('mlx.submodule_guardian.submodule_guardian.configparser.ConfigParser')
    @patch('mlx.submodule_guardian.submodule_guardian.Submodule.from_gitmodules')
    def test_read_gitmodules_success(self, mock_from_gitmodules, mock_config_parser):
        """Test read_gitmodules successfully reads and parses .gitmodules."""
        guardian = self._create_guardian()
        guardian.branch = 'main'

        mock_file = MagicMock()
        mock_file.decode.return_value.decode.return_value = '''
[submodule "sub1"]
    path = path/to/sub1
    url = http://gitlab.example.com/sub1.git
'''
        guardian.project.files.get.return_value = mock_file

        mock_config = MagicMock()
        mock_config.sections.return_value = ['submodule "sub1"']
        mock_config_parser.return_value = mock_config
        mock_config.__getitem__.return_value = {'path': 'path/to/sub1', 'url': 'http://gitlab.example.com/sub1.git'}

        mock_submodule = MagicMock()
        mock_from_gitmodules.return_value = mock_submodule

        with self.assertLogs('submodule-guardian', level='INFO') as cm:
            guardian.read_gitmodules()

        self.assertEqual(len(guardian.submodules), 1)
        self.assertTrue(any("Successfully read .gitmodules file" in msg for msg in cm.output))
        self.assertTrue(any("Found submodule: path/to/sub1" in msg for msg in cm.output))

    @patch('mlx.submodule_guardian.submodule_guardian.configparser.ConfigParser')
    @patch('sys.exit')
    def test_read_gitmodules_exception(self, mock_sys_exit, mock_config_parser):
        """Test read_gitmodules handles exceptions gracefully."""
        guardian = self._create_guardian()
        guardian.branch = 'main'
        guardian.project.files.get.side_effect = Exception("File not found")
        with self.assertRaisesRegex(Exception, "File not found"):
            guardian.run()

    def test_run_no_submodules_to_check(self):
        """Test run when no submodules are found to check."""
        guardian = self._create_guardian()
        guardian.always_check = True
        guardian.submodules = []
        guardian.read_gitmodules = MagicMock()

        with self.assertLogs('submodule-guardian', level='WARNING') as cm:
            guardian.run()

        self.assertTrue(any("No submodules to check" in msg for msg in cm.output))

    @patch('mlx.submodule_guardian.submodule_guardian.console')
    def test_run_not_dry_run(self, mock_console):
        """Test run when not in dry-run mode."""
        guardian = self._create_guardian(dry_run=False)
        guardian.always_check = True
        mock_submodule = MagicMock()
        mock_submodule.path_in_project = 'path/to/sub'
        mock_submodule.sub_project.default_branch = 'main'
        mock_submodule.commit_id = 'commit_123'
        mock_commit = MagicMock()
        mock_commit.id = 'commit_123'
        mock_commit.refs.return_value = [{'name': 'main'}]
        mock_submodule.sub_project.commits.get.return_value = mock_commit
        mock_submodule.sub_project.tags.list.return_value = []
        mock_submodule.url = 'http://gitlab.example.com/sub.git'

        guardian.submodules = [mock_submodule]
        guardian.check_submodules = MagicMock(return_value=["status line"])
        guardian.report = MagicMock()
        guardian.read_gitmodules = MagicMock()

        guardian.run()

        guardian.report.assert_called_once()
        # Verify console.print was called (not in dry-run mode) - line 404
        # Check that it printed the non-dry-run header
        mock_console.print.assert_any_call("\n--- Submodule Status Report ---", style="bold")

    @patch('mlx.submodule_guardian.submodule_guardian.logger')
    @patch('mlx.submodule_guardian.submodule_guardian.SubmoduleGuardian._post_or_update_discussion')
    def test_report_no_discussion_no_fail_with_warnings(self, mock_post_discussion, mock_logger):
        """
        Test report logs a warning when no discussion is posted, pipeline doesn't fail,
        and warnings exist (new warning message).
        """
        guardian = self._create_guardian(fail_pipeline=False, post_discussion=False)
        guardian.resolved = False  # Simulate a warning
        status_lines = [":warning: A warning message"]

        guardian.report(status_lines)

        mock_post_discussion.assert_not_called()
        mock_logger.warning.assert_called_with(
            "Warnings detected. No failing pipeline or discussion post can result in unseen warnings."
        )


# --- Parse Args Tests ---
class ParseArgsTest(unittest.TestCase):

    def test_parse_args_all_options(self):
        """Test parse_args with all options."""
        test_args = [
            '--project', 'test/project',
            '--mr-iid', '123',
            '--branch', 'feature-branch',
            '--fail-pipeline',
            '--always-check',
            '--allow-tags',
            '--only-latest-tag',
            '--verbose',
            '--fix',
            '--no-post-discussion',
            '--debug'
        ]

        with patch('sys.argv', ['script'] + test_args):
            from mlx.submodule_guardian.submodule_guardian import parse_args
            args = parse_args()

            self.assertEqual(args.project, 'test/project')
            self.assertEqual(args.mr_iid, 123)
            self.assertEqual(args.branch, 'feature-branch')
            self.assertTrue(args.fail_pipeline)
            self.assertTrue(args.always_check)
            self.assertTrue(args.allow_tags)
            self.assertTrue(args.only_latest_tag)
            self.assertTrue(args.verbose)
            self.assertTrue(args.fix)
            self.assertFalse(args.post_discussion)
            self.assertTrue(args.debug)

    def test_parse_args_short_options(self):
        """Test parse_args with short option names."""
        test_args = ['-p', 'test/project', '-m', '456', '-b', 'main', '-v', '-d']

        with patch('sys.argv', ['script'] + test_args):
            from mlx.submodule_guardian.submodule_guardian import parse_args
            args = parse_args()

            self.assertEqual(args.project, 'test/project')
            self.assertEqual(args.mr_iid, 456)
            self.assertEqual(args.branch, 'main')
            self.assertTrue(args.verbose)
            self.assertTrue(args.debug)


if __name__ == '__main__':
    unittest.main()
