#!/usr/bin/env python
"""
Python script that checks the status of git submodules in a project.

When a merge request modifies a submodule, this script can create or update a discussion
thread in the MR. The thread will be unresolved if any submodules are in a "warning" or "error" state
(e.g., behind their default branch, on a non-default branch, or on an old tag).
This serves as a non-blocking reminder to update submodules.

The script can be configured to:
- Only act when a submodule is modified in the MR (default).
- Always check and report on every run.
- Fail the pipeline instead of creating a discussion.
"""

import configparser
import argparse
from dataclasses import dataclass
import logging
import os
import re
import sys
import subprocess
from posixpath import normpath, join
from pathlib import Path
from typing import Optional, List
from rich.logging import RichHandler
from rich.console import Console
from rich.theme import Theme

from decouple import config
from gitlab import Gitlab
from gitlab.v4.objects import Project
from mako.template import Template

from ._version import __version__

logger = logging.getLogger('submodule-guardian')
custom_theme = Theme({
    "good": "dim green",
    "warning": "bold yellow",
    "danger": "bold red"
})
console = Console(theme=custom_theme)


@dataclass
class Submodule:
    sub_project: Project
    commit_id: Optional[str] = ''
    path_in_project: Optional[str] = None
    error: Optional[str] = ''

    @classmethod
    def from_gitmodules(cls, gl, project, branch, path_in_project, url) -> 'Submodule':
        rel_path = url[:-4] if url.endswith('.git') else url
        if url_match := re.match(r'^(?:https://|git@)([\w\.-]+)(?:\/|:)(.+)', rel_path):
            domain = url_match.group(1)
            path = url_match.group(2).rstrip('/')
            if domain not in gl.url:
                err = (f"Submodule {path_in_project} is skipped due to different domain ( {domain} ) than the project "
                       f"( {gl.url} ).")
                logger.error(err)
                return cls(sub_project=None, commit_id=None, path_in_project=path_in_project, error=err)
        else:
            path = normpath(join(project.path_with_namespace, rel_path))
        sub_project = gl.projects.get(path)
        submodule_dir = project.files.get(path_in_project, ref=branch)
        commit_id = submodule_dir.blob_id

        return cls(sub_project=sub_project, commit_id=commit_id, path_in_project=path_in_project)

    @property
    def latest_tag(self):
        tag_list = list(self.sub_project.tags.list(page=1, per_page=1))
        if not tag_list:
            return None
        return tag_list[0]

    @property
    def url(self):
        return self.sub_project.http_url_to_repo

    @property
    def branches(self):
        return [ref['name'] for ref in self.sub_project.commits.get(self.commit_id).refs(type='branch')]

    def is_on_tag(self) -> Optional[str]:
        for tag in self.sub_project.tags.list(iterator=True):
            if tag.commit['id'] == self.commit_id:
                return tag.name
        return None

    def is_on_latest_tag(self) -> bool:
        if self.latest_tag is None:
            return False
        return self.latest_tag.commit['id'] == self.commit_id

    def is_latest_default_branch(self) -> bool:
        default_branch = self.sub_project.default_branch
        latest_commit = self.sub_project.commits.get(default_branch)
        return latest_commit.id == self.commit_id

    def is_on_default_branch(self) -> bool:
        default_branch = self.sub_project.default_branch
        return default_branch in self.branches


class SubmoduleGuardian:
    """
    Checks submodule status and reports it.
    """

    def __init__(self, project_identifier: str, fail_pipeline: bool,
                 always_check: bool, dry_run: bool, allow_tags: bool, only_latest_tag: bool, post_discussion: bool,
                 fix: bool, mr_iid: Optional[str] = None,
                 branch: Optional[str] = None):
        self.project_identifier = project_identifier
        self.branch = branch
        self.fail_pipeline = fail_pipeline
        self.post_discussion = post_discussion
        self.always_check = always_check
        self.dry_run = dry_run
        self.allow_tags = allow_tags
        self.only_latest_tag = only_latest_tag
        self.fix = fix

        self._private_token = config('PRIVATE_TOKEN', default=None)
        self._setup_gitlab()

        self.project = self.gitlab.projects.get(self.project_identifier)
        self.path_with_namespace = self.project.path_with_namespace
        try:
            self.mr_iid = int(mr_iid) if mr_iid else self._determine_merge_request_iid()
            self.mr = self.project.mergerequests.get(self.mr_iid)
        except ValueError as e:
            # Make it possible to check submodules in dry run without MR
            if not (self.dry_run and self.always_check):
                raise e
        self.current_user = self.gitlab.user

        # If branch is not provided but we have an MR, use the MR's source branch
        if not self.branch and self.mr:
            self.branch = self.mr.source_branch
            logger.debug(f"Using MR source branch: {self.branch}")

        # If branch is still None, fall back to project's default branch
        if not self.branch:
            self.branch = self.project.default_branch
            logger.debug(f"Using project default branch: {self.branch}")

        self.submodules = []

        self.resolved = True
        self.discussion_template = Template(filename=str(Path(__file__).parent / 'discussion_template.mako'))

    def _determine_merge_request_iid(self) -> int:
        """Determine merge request IID.

        Returns:
            int: The merge request IID.
        """
        if not self.branch:
            raise ValueError('Branch is required to determine merge request IID. '
                             'Please provide --branch or set CI_COMMIT_BRANCH environment variable.')

        params = {'state': 'opened', 'source_branch': self.branch}

        mrs = self.project.mergerequests.list(**params)

        if len(mrs) == 1:
            return mrs[0].iid

        if len(mrs) > 1:
            mr_list_str = ", ".join([f"MR !{mr.iid} (-> {mr.target_branch})" for mr in mrs])
            error_msg = (f"Found multiple open merge requests for source branch '{self.branch}': {mr_list_str}. "
                         "Please specify the target branch with --target-branch to select the correct one.")
            raise ValueError(error_msg)

        # len(mrs) == 0
        raise ValueError(f'Could not find an open merge request for branch "{self.branch}" '
                         f'with the specified filters. Please check branch names or specify the MR IID with --mr-iid.')

    def _setup_gitlab(self):
        """Initialize GitLab connection."""
        if self._private_token is None:
            raise NameError('PRIVATE_TOKEN not found. Declare it as envvar, e.g. in a .env file where the script is '
                            'executed from')
        gitlab_url = config('CI_SERVER_HOST', default='https://gitlab.melexis.com')
        if gitlab_url and not gitlab_url.startswith('https://'):
            gitlab_url = 'https://' + gitlab_url
        self.gitlab = Gitlab(gitlab_url, private_token=self._private_token)
        self.gitlab.auth()

    def check_submodules(self, submodules_to_check: List[Submodule]) -> List[str]:
        """
        Iterates through a given list of submodules and checks their status.

        Args:
            submodules_to_check (List[Submodule]): A list of Submodule objects to check.

        Returns:
            List[str]: A list of formatted status strings for each submodule.
        """
        logger.info(f"Checking status for {len(submodules_to_check)} submodule(s)...")
        status_lines = []

        if not submodules_to_check:
            logger.info("No submodules to check.")
            return []

        for submodule in submodules_to_check:
            if submodule.error:
                status = f":warning: {submodule.error}"
                console.print(status, markup=True, highlight=False, style="warning")
                status_lines.append(status)
                continue
            status = self._format_submodule_status(submodule)
            status_lines.append(status)

        return status_lines

    def _format_submodule_status(self, submodule: Submodule) -> str:
        """Formats the status of a single submodule into a human-readable string.

        Args:
            submodule (Submodule): The submodule to check.

        Returns:
            str: A formatted string indicating the status of the submodule.
        """
        submodule_link = f"[link={submodule.url}]{submodule.path_in_project}[/link]"
        gl_submodule_link = f"[{submodule.path_in_project}]({submodule.url})"
        status_template = ""

        if not submodule.sub_project.default_branch:
            self.resolved = False
            status_template = ":warning: Submodule {submodule_link}: Could not determine default branch."
            console.print(status_template.format(submodule_link=submodule_link), markup=True, highlight=False,
                          style="warning")
            return status_template.format(submodule_link=gl_submodule_link)

        # Priority 1: Handle tags based on flags
        if tag_name := submodule.is_on_tag():
            if self.only_latest_tag:
                if submodule.is_on_latest_tag():
                    status_template = (":white_check_mark: Submodule {submodule_link} is on the latest tag "
                                       f"`{submodule.latest_tag.name if submodule.latest_tag else 'unknown'}`.")
                    console.print(status_template.format(submodule_link=submodule_link), markup=True, highlight=False,
                                  style="good")
                    return status_template.format(submodule_link=gl_submodule_link)
                self.resolved = False
                status_template = (f":warning: Submodule {{submodule_link}} is on tag `{tag_name}`, but a newer tag `"
                                   f"{submodule.latest_tag.name if submodule.latest_tag else 'unknown'}` is available.")
                console.print(status_template.format(submodule_link=submodule_link), markup=True, highlight=False,
                              style="warning")
                if self.fix:
                    self._fix_submodule(submodule, 'latest_tag')
                return status_template.format(submodule_link=gl_submodule_link)
            elif self.allow_tags:
                status_template = f":white_check_mark: Submodule {{submodule_link}} is on tag `{tag_name}`."
                console.print(status_template.format(submodule_link=submodule_link), markup=True, highlight=False,
                              style="good")
                return status_template.format(submodule_link=gl_submodule_link)
            else:
                self.resolved = False
                status_template = (f":warning: Submodule {{submodule_link}} is on tag `{tag_name}`, "
                                   "but tags are not allowed.")
                console.print(status_template.format(submodule_link=submodule_link), markup=True, highlight=False,
                              style="warning")
                return status_template.format(submodule_link=gl_submodule_link)

        # Priority 2: Up-to-date with latest default branch
        if submodule.is_latest_default_branch():
            status_template = (":white_check_mark: Submodule {submodule_link} is up-to-date with default branch "
                               f"`{submodule.sub_project.default_branch}`.")
            console.print(status_template.format(submodule_link=submodule_link), markup=True, highlight=False,
                          style="good")
            return status_template.format(submodule_link=gl_submodule_link)

        # Priority 3: On older commit of default branch
        if submodule.is_on_default_branch():
            self.resolved = False
            status_template = (":warning: Submodule {submodule_link} is behind its latest default branch "
                               f"`{submodule.sub_project.default_branch}`.")
            console.print(status_template.format(submodule_link=submodule_link), markup=True, highlight=False,
                          style="warning")
            if self.fix:
                self._fix_submodule(submodule, 'default_branch')
            return status_template.format(submodule_link=gl_submodule_link)

        # Priority 4: Other cases
        self.resolved = False
        status_template = (f":x: Submodule {{submodule_link}} is on commit `{submodule.commit_id}` (ref branch "
                           f"name(s): {submodule.branches}), which is not the default branch "
                           f"`{submodule.sub_project.default_branch}`.")
        console.print(status_template.format(submodule_link=submodule_link), markup=True, highlight=False,
                      style="danger")
        return status_template.format(submodule_link=gl_submodule_link)

    def _fix_submodule(self, submodule: Submodule, fix_type: str):
        """Updates a submodule to the latest tag or default branch.

        Args:
            submodule (Submodule): The submodule to fix.
            fix_type (str): The type of fix to perform ('latest_tag' or 'default_branch').
        """
        path = submodule.path_in_project
        logger.info(f"Attempting to fix submodule at '{path}'...")

        if fix_type == 'latest_tag' and submodule.latest_tag:
            target = submodule.latest_tag.name
            logger.info(f"Checking out latest tag '{target}' for submodule '{path}'.")
        elif fix_type == 'default_branch':
            target = submodule.sub_project.default_branch
            logger.info(f"Checking out latest on default branch '{target}' for submodule '{path}'.")
        else:
            logger.warning(f"Unknown fix type '{fix_type}' or missing information for submodule '{path}'.")
            return

        try:
            subprocess.run(['git', '-C', path, 'fetch', '--tags', '--force'], check=True)
            subprocess.run(['git', '-C', path, 'checkout', target], check=True)
            subprocess.run(['git', '-C', path, 'pull', 'origin', target], check=True)
            subprocess.run(['git', 'add', path], check=True)
            console.print(f":wrench: Submodule '{path}' has been checked out to '{target}'. "
                          f"Please review, commit, and push the changes.", style="bold yellow")
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            logger.error(f"Failed to fix submodule '{path}': {e}")

    def report(self, status_lines: List[str]):
        """
        Reports the submodule status to the merge request or fails the pipeline.

        Args:
            status_lines (List[str]): A list of formatted status strings for each submodule.
        """
        if self.dry_run:
            if not self.resolved:
                logger.warning("Warnings detected. In a CI run, this would create an unresolved discussion or fail the "
                               "pipeline.")
            else:
                logger.info("Success: All submodules are in a good state. In a CI run, this would create a resolved "
                            "discussion.")
            return

        if self.post_discussion:
            # Post to MR
            title = "Submodule Status Check"
            search_string = f'# {title}'
            extra_line = ('One or more submodules require attention.' if not self.resolved
                          else 'All submodules are in a good state.')
            comment_body = self.discussion_template.render(
                title=title,
                status_lines=status_lines,
                extra_line=extra_line,
                job_url=os.getenv('CI_JOB_URL', '#')
            )

            comment_url = self._post_or_update_discussion(comment=str(comment_body), search_string=search_string)
            if not self.resolved:
                logger.warning(f"Warnings detected. [link={comment_url}]Discussion posted.[/link]",
                               extra={"markup": True})
            else:
                logger.info(f"[link={comment_url}]Discussion posted.[/link]")
        elif not self.resolved:
            if not self.fail_pipeline:
                logger.warning("Warnings detected. No failing pipeline or discussion post can result in unseen "
                               "warnings.")
            else:
                logger.warning("Warnings detected. Discussion not posted due to --no-post-discussion flag.")
        else:
            logger.info("Success: All submodules are in a good state.")

        if self.fail_pipeline:
            if not self.resolved:
                logger.error("Action required: One or more submodules are behind their default branch or tags.")
                sys.exit(1)
            else:
                logger.info("Success: All submodules are in a good state.")

    def _post_or_update_discussion(self, comment: str, search_string: str) -> Optional[str]:
        """
        Posts or updates a discussion on the MR.

        Args:
            comment (str): The content of the comment.
            search_string (str): A string to search for to find an existing comment to update.

        Returns:
            str|None: The URL to the GitLab discussion note, or None if it could not be determined.
        """
        discussions = self.mr.discussions.list(all=True)
        matched_discussion_id = None
        matched_note_id = None
        for discussion in discussions:
            for note in discussion.attributes['notes']:
                if 'body' in note and (search_string in note['body'] and note['author']['id'] == self.current_user.id):
                    matched_discussion_id = discussion.id
                    matched_note_id = note['id']
                    logger.info(f'Found existing discussion with ID {matched_discussion_id}')
                    logger.debug(f'{discussion}')
                    break
            if matched_discussion_id and matched_note_id:
                break

        if not matched_discussion_id or not matched_note_id:
            # Create new discussion
            discussion_obj = self.mr.discussions.create({'body': comment, 'resolved': self.resolved})
            logger.info('Created new discussion')
            notes = discussion_obj.attributes.get('notes') if discussion_obj else None
            if not notes:
                logger.warning("Could not determine URL for new comment: no notes returned")
                return None
            try:
                first_note_id = notes[0]['id']
                comment_url = f"{self.mr.web_url}#note_{first_note_id}"
                logger.debug(f"New discussion URL: {comment_url}")
                return comment_url
            except (IndexError, KeyError) as e:
                logger.warning(f"Could not determine URL for new comment: {e}")
                return None
        else:
            # Update existing discussion
            discussion = self.mr.discussions.get(matched_discussion_id)
            note = discussion.notes.get(matched_note_id)
            note.body = comment
            note.save()
            discussion.resolved = self.resolved
            discussion.save()
            logger.info(f'Updated existing discussion with ID {matched_discussion_id}')
            comment_url = f"{self.mr.web_url}#note_{note.id}"
            logger.debug(f"Updated discussion URL: {comment_url}")
            return comment_url

    def get_changed_submodules(self) -> List[Submodule]:
        """
        Checks if any of the project's submodules were changed in the merge request.

        Returns:
            A list of Submodule objects that were changed in the MR.
        """
        logger.info("Checking for submodule changes in the merge request...")
        if not self.submodules:
            logger.info("No submodules configured in this project.")
            return []

        submodules_by_path = {sub.path_in_project: sub for sub in self.submodules}
        changed_submodules_list = []

        logger.debug("Fetching MR changes...")
        changes = self.mr.changes()['changes']
        for change in changes:
            # A submodule is just a file entry in the parent repo.
            # We check if the path of a changed file matches any submodule path.
            path = change['new_path']
            if path in submodules_by_path:
                logger.info(f"Detected change in submodule: {path}")
                changed_submodules_list.append(submodules_by_path[path])

        if not changed_submodules_list:
            logger.info("No submodule changes detected in the merge request.")

        return changed_submodules_list

    def read_gitmodules(self):
        """
        Reads the .gitmodules file from the project to identify submodules.
        """

        ref = self.branch
        if not ref:
            logger.error("No branch specified, cannot read .gitmodules file.")
            sys.exit(1)

        gitmodules_file = self.project.files.get('.gitmodules', ref=ref)
        gitmodules_content = gitmodules_file.decode().decode('utf-8')
        logger.info("Successfully read .gitmodules file.")

        config = configparser.ConfigParser()
        config.optionxform = str
        config.read_string(gitmodules_content)
        name_regex = r'submodule "([a-zA-Z0-9\.\-\/_]+)"'
        for section in config.sections():
            if re.match(name_regex, section):
                submodule_path = config[section]['path']
                submodule_url = config[section]['url']
                try:
                    submodule = Submodule.from_gitmodules(self.gitlab, self.project, self.branch, submodule_path,
                                                          submodule_url)
                    self.submodules.append(submodule)
                    logger.info(f"Found submodule: {submodule_path}")
                except Exception as e:
                    logger.error(f"Failed to process submodule '{submodule_path}' from URL '{submodule_url}': {e}")
                    sys.exit(1)

    def run(self):
        """
        Main execution logic.
        """
        submodules_to_check = []
        self.read_gitmodules()
        if self.always_check:
            logger.info("Flag --always-check is set. Checking all submodules.")
            submodules_to_check = self.submodules
        else:
            submodules_to_check = self.get_changed_submodules()
            if not submodules_to_check:
                logger.warning("No submodule changes found and --always-check is not set.")
                return
        if not submodules_to_check:
            logger.warning("No submodules to check.")
            return
        if self.dry_run:
            console.print("\n--- Submodule Status Report (Dry Run) ---", style="bold")
        else:
            console.print("\n--- Submodule Status Report ---", style="bold")
        status_lines = self.check_submodules(submodules_to_check)
        console.print("-----------------------------------------\n", style="bold")
        self.report(status_lines)


def parse_args():
    parser = argparse.ArgumentParser(description='Check submodule status and report to a GitLab MR.')
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument('-p', '--project', help='The ID or path of the GitLab project (or CI_PROJECT_PATH).')
    parser.add_argument('-m', '--mr-iid', type=int, help='The IID of the merge request (or CI_MERGE_REQUEST_IID).')
    parser.add_argument('-b', '--branch', type=str, help='Current branch name (default: current git branch)')
    parser.add_argument('--fail-pipeline', action='store_true',
                        help='Fail the pipeline on warnings instead of creating an MR discussion.')
    parser.add_argument('--always-check', action='store_true',
                        help='Always perform the check, even if no submodules were modified in the MR.')
    parser.add_argument('--allow-tags', action='store_true',
                        help='Allow submodules to be on tags.')
    parser.add_argument('--no-post-discussion', dest='post_discussion', action='store_false',
                        help='Do not post a discussion on the merge request.')
    parser.add_argument('--only-latest-tag', action='store_true',
                        help='If on tag, only consider the latest tag as up-to-date.')
    parser.add_argument('--fix', action='store_true',
                        help='Automatically checkout submodules to fix warnings (e.g., to latest tag or branch head).')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable INFO level logging.')
    parser.add_argument('-d', '--debug', action='store_true', help='Enable DEBUG level logging.')
    return parser.parse_args()


def get_current_branch():
    """Get the current git branch name."""
    try:
        import subprocess
        result = subprocess.run(['git', 'branch', '--show-current'], capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


def main():
    """
    Main entry point for the script.
    """
    args = parse_args()

    log_level = logging.WARNING
    if args.debug:
        log_level = logging.DEBUG
    elif args.verbose:
        log_level = logging.INFO

    logging.basicConfig(
        level=log_level,
        format='%(name)s: %(message)s',
        handlers=[RichHandler(show_time=False, show_path=False)],
        force=True
    )
    dry_run = False
    if not os.getenv('CI_PROJECT_ID') and not os.getenv('CI_PROJECT_PATH'):
        logger.info("Not running in CI environment; enabling dry-run mode.")
        dry_run = True
    if not dry_run and args.fix:
        logger.error("--fix can only be used locally in dry run mode")
        sys.exit(1)

    project_identifier = args.project or os.getenv('CI_PROJECT_ID') or os.getenv('CI_PROJECT_PATH')
    branch = args.branch or os.getenv('CI_COMMIT_BRANCH') or get_current_branch()
    mr_iid = args.mr_iid or os.getenv('CI_MERGE_REQUEST_IID')

    if not project_identifier:
        logger.error("Project identifier is required. Use --project or set CI_PROJECT_ID/CI_PROJECT_PATH.")
        sys.exit(1)
    if not mr_iid and not branch:
        logger.error("Merge Request IID or branch is required. Use --mr-iid or set CI_MERGE_REQUEST_IID environment "
                     "variable to set the merge request IID. Use --branch or set CI_COMMIT_BRANCH environment variable."
                     "to set the branch name.")
        sys.exit(1)
    try:
        guardian = SubmoduleGuardian(project_identifier=project_identifier,
                                     fail_pipeline=args.fail_pipeline, always_check=args.always_check,
                                     dry_run=dry_run, allow_tags=args.allow_tags,
                                     only_latest_tag=args.only_latest_tag, fix=args.fix, mr_iid=mr_iid, branch=branch,
                                     post_discussion=args.post_discussion)
        guardian.run()
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
