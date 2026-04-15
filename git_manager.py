"""
Git Manager for the GitOps AI Orchestrator.
Wraps GitPython operations: pull, commit, push, branch management,
and optional GitHub PR creation via the API.
"""

import logging
from pathlib import Path
from typing import Optional

import requests
from git import Repo, GitCommandError

from config import (
    GIT_REPO_PATH,
    GIT_REMOTE,
    GIT_DEFAULT_BRANCH,
    GITHUB_API_TOKEN,
    GITHUB_REPO_OWNER,
    GITHUB_REPO_NAME,
    USE_GITHUB_PR,
)

logger = logging.getLogger(__name__)


class GitManager:
    """Manages all Git operations for the orchestrator."""

    def __init__(self, repo_path: Optional[Path] = None):
        self.repo_path = repo_path or GIT_REPO_PATH
        self.repo = Repo(self.repo_path)
        self.remote_name = GIT_REMOTE

    @property
    def current_branch(self) -> str:
        """Return the name of the current active branch."""
        return self.repo.active_branch.name

    def pull(self) -> bool:
        """
        Pull latest changes from the remote.
        Returns True if successful.
        """
        try:
            remote = self.repo.remote(self.remote_name)
            result = remote.pull()
            logger.info(f"Git pull completed: {result}")
            return True
        except GitCommandError as e:
            logger.error(f"Git pull failed: {e}")
            return False

    def commit_and_push(self, message: str, files: Optional[list[str]] = None) -> bool:
        """
        Stage specified files (or all changes), commit with message, and push.
        If files is None, stages 'roadmap.md' by default.
        Returns True if successful.
        """
        try:
            if files:
                self.repo.index.add(files)
            else:
                self.repo.index.add(["roadmap.md"])

            self.repo.index.commit(message)
            remote = self.repo.remote(self.remote_name)
            remote.push()
            logger.info(f"Committed and pushed: {message}")
            return True
        except GitCommandError as e:
            logger.error(f"Git commit/push failed: {e}")
            return False

    def create_branch(self, branch_name: str, checkout: bool = True) -> bool:
        """
        Create a new branch. Optionally checkout to it.
        Returns True if successful.
        """
        try:
            if branch_name in [b.name for b in self.repo.branches]:
                logger.warning(f"Branch '{branch_name}' already exists.")
                if checkout:
                    self.repo.git.checkout(branch_name)
                return True

            new_branch = self.repo.create_head(branch_name)
            if checkout:
                new_branch.checkout()
            logger.info(f"Created branch: {branch_name} (checkout={checkout})")
            return True
        except GitCommandError as e:
            logger.error(f"Branch creation failed: {e}")
            return False

    def checkout_branch(self, branch_name: str) -> bool:
        """Switch to an existing branch."""
        try:
            self.repo.git.checkout(branch_name)
            logger.info(f"Checked out branch: {branch_name}")
            return True
        except GitCommandError as e:
            logger.error(f"Checkout failed: {e}")
            return False

    def get_diff(self, target_branch: Optional[str] = None) -> str:
        """
        Get the diff between current branch and target branch.
        Defaults to diffing against the default branch (main).
        """
        target = target_branch or GIT_DEFAULT_BRANCH
        try:
            diff = self.repo.git.diff(target)
            return diff
        except GitCommandError as e:
            logger.error(f"Git diff failed: {e}")
            return ""

    def get_staged_diff(self) -> str:
        """Get the diff of currently staged changes."""
        try:
            return self.repo.git.diff("--cached")
        except GitCommandError as e:
            logger.error(f"Staged diff failed: {e}")
            return ""

    def create_github_pr(
        self,
        title: str,
        body: str,
        head_branch: Optional[str] = None,
        base_branch: Optional[str] = None,
    ) -> Optional[str]:
        """
        Create a Pull Request on GitHub via the API.
        Returns the PR URL if successful, None otherwise.
        Requires GITHUB_API_TOKEN, GITHUB_REPO_OWNER, GITHUB_REPO_NAME.
        """
        if not USE_GITHUB_PR:
            logger.info("GitHub PR creation is disabled (no API token configured).")
            return None

        head = head_branch or self.current_branch
        base = base_branch or GIT_DEFAULT_BRANCH

        url = f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/pulls"
        headers = {
            "Authorization": f"token {GITHUB_API_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }
        payload = {
            "title": title,
            "body": body,
            "head": head,
            "base": base,
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            if response.status_code == 201:
                pr_url = response.json().get("html_url", "")
                logger.info(f"GitHub PR created: {pr_url}")
                return pr_url
            else:
                logger.error(
                    f"GitHub PR creation failed: {response.status_code} - {response.text}"
                )
                return None
        except requests.RequestException as e:
            logger.error(f"GitHub API request failed: {e}")
            return None

    def merge_branch(
        self, source_branch: str, target_branch: Optional[str] = None
    ) -> bool:
        """Merge source branch into target branch (default: main)."""
        target = target_branch or GIT_DEFAULT_BRANCH
        try:
            self.repo.git.checkout(target)
            self.repo.git.merge(source_branch)
            logger.info(f"Merged '{source_branch}' into '{target}'")
            return True
        except GitCommandError as e:
            logger.error(f"Merge failed: {e}")
            return False
