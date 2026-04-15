"""
Shared LangGraph nodes used by both Creative and Product Factory workflows.
Handles: roadmap parsing/sync, validation execution, rate limit handling,
and context compacting.
"""

import logging
import subprocess
import time
from datetime import datetime, timedelta
from typing import Any

from config import (
    COMPACT_THRESHOLD_RATIO,
    RATE_LIMIT_SLEEP,
    SysStatus,
    VALIDATION_TIMEOUT,
)
from git_manager import GitManager
from roadmap_parser import RoadmapState, parse_roadmap, update_roadmap


def _get_git(state: dict) -> "GitManager":
    """Retrieve GitManager from module-level registry or create a new instance."""
    from orchestrator import _git_managers
    git_key = state.get("git_manager_key", "default")
    return _git_managers.get(git_key) or GitManager()


def _get_roadmap_path(state: dict):
    """Return Path object for this project's roadmap.md (or None for default)."""
    from pathlib import Path as _P
    rp_str = state.get("roadmap_path", "")
    return _P(rp_str) if rp_str else None

logger = logging.getLogger(__name__)


def parse_roadmap_and_sync(state: dict[str, Any]) -> dict[str, Any]:
    """
    Node: Pull latest from Git, parse roadmap.md, and update graph state.
    Uses the per-project roadmap_path from state (set by create_initial_state).
    This is the entry node for both workflows.
    """
    git = _get_git(state)

    # Pull latest (Obsidian Sync or manual push may have updated the file)
    pull_ok = git.pull()
    if not pull_ok:
        logger.warning("Git pull failed — working with local copy")

    # Parse project-specific roadmap (falls back to config default)
    from pathlib import Path as _P
    roadmap_path_str = state.get("roadmap_path", "")
    rp = _P(roadmap_path_str) if roadmap_path_str else None
    roadmap = parse_roadmap(path=rp)

    # Check system status
    if roadmap.sys_status == SysStatus.SLEEP_RATE_LIMIT:
        resume_time = roadmap.rate_limit_resume_time
        if resume_time:
            logger.info(f"System is sleeping until: {resume_time}")
        state["should_exit"] = True
        state["exit_reason"] = "rate_limit_sleep"

    elif roadmap.sys_status == SysStatus.PAUSED:
        logger.info("System is PAUSED. Exiting.")
        state["should_exit"] = True
        state["exit_reason"] = "paused"

    # Update state (no rpa_controller or git_manager objects here — only serializable values)
    state["roadmap"] = roadmap
    state["current_task"] = roadmap.current_task
    state["sys_status"] = roadmap.sys_status.value
    state["should_exit"] = state.get("should_exit", False)

    if roadmap.current_task:
        logger.info(
            f"Current task: {roadmap.current_task.name} — "
            f"{roadmap.current_task.title}"
        )
    else:
        logger.info("No pending tasks found.")
        state["should_exit"] = True
        state["exit_reason"] = "no_tasks"

    return state


def run_validation(state: dict[str, Any]) -> dict[str, Any]:
    """
    Node: Execute the verification command for the current task.
    Captures stdout/stderr for routing decisions.
    """
    task = state.get("current_task")
    if not task or not task.verification_cmd:
        logger.info("No verification command to run.")
        state["validation_passed"] = True
        state["validation_output"] = ""
        return state

    cmd = task.verification_cmd
    logger.info(f"Running validation: {cmd}")

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=VALIDATION_TIMEOUT,
            cwd=str(state.get("git_manager", GitManager()).repo_path),
        )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        combined = f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"

        state["validation_passed"] = result.returncode == 0
        state["validation_output"] = combined
        state["validation_returncode"] = result.returncode

        if result.returncode == 0:
            logger.info(f"Validation PASSED: {cmd}")
        else:
            logger.warning(f"Validation FAILED (exit {result.returncode}): {cmd}")
            logger.debug(combined)

    except subprocess.TimeoutExpired:
        logger.error(f"Validation TIMEOUT ({VALIDATION_TIMEOUT}s): {cmd}")
        state["validation_passed"] = False
        state["validation_output"] = f"TIMEOUT after {VALIDATION_TIMEOUT}s"
        state["validation_returncode"] = -1

    except Exception as e:
        logger.error(f"Validation error: {e}")
        state["validation_passed"] = False
        state["validation_output"] = str(e)
        state["validation_returncode"] = -1

    return state


def handle_rate_limit(state: dict[str, Any]) -> dict[str, Any]:
    """
    Node: Handle API rate limit (HTTP 429).
    Sets sys_status to SLEEP_RATE_LIMIT in roadmap.md, commits, and signals exit.
    """
    resume_time = (
        datetime.now() + timedelta(seconds=RATE_LIMIT_SLEEP)
    ).strftime("%Y-%m-%d %H:%M:%S CST")

    logger.warning(f"Rate limit hit. Sleeping until: {resume_time}")

    # Update roadmap with per-project path
    update_roadmap(
        path=_get_roadmap_path(state),
        sys_status=SysStatus.SLEEP_RATE_LIMIT,
        rate_limit_resume_time=resume_time,
        latest_action=f"Rate limit hit. Sleeping until {resume_time}",
    )

    # Commit and push the updated roadmap
    git = _get_git(state)
    git.commit_and_push(f"chore: rate limit sleep until {resume_time}")

    state["should_exit"] = True
    state["exit_reason"] = "rate_limit"
    return state


def compact_context(state: dict[str, Any]) -> dict[str, Any]:
    """
    Node: Compact the context/history to prevent token overflow.
    Since we don't have an internal LLM, this node:
    1. Marks completed tasks as summarized in the roadmap
    2. Resets the context_tokens counter
    3. Logs what was compacted
    """
    roadmap: RoadmapState = state.get("roadmap")
    if not roadmap:
        return state

    # Check if compacting is needed
    token_ratio = roadmap.context_tokens / roadmap.token_limit
    current_task = state.get("current_task")

    should_compact = (
        token_ratio >= COMPACT_THRESHOLD_RATIO
        or (current_task and "COMPACT_AFTER_SUCCESS" in current_task.context_flags
            and state.get("validation_passed", False))
    )

    if not should_compact:
        logger.debug("Context compacting not needed.")
        return state

    logger.info("Compacting context...")

    # Build summary of completed tasks
    completed = [t for t in roadmap.tasks if t.status == "done"]
    summary_lines = [f"- {t.name}: {t.title} [COMPLETED]" for t in completed]
    summary = "\n".join(summary_lines)

    logger.info(f"Compacted {len(completed)} completed tasks:\n{summary}")

    # Reset token count in roadmap
    update_roadmap(
        path=_get_roadmap_path(state),
        context_tokens=0,
        latest_action=f"Context compacted. {len(completed)} tasks summarized.",
    )

    # Commit
    git = _get_git(state)
    git.commit_and_push("chore: compact context — summarize completed tasks")

    state["context_compacted"] = True
    return state


def check_should_exit(state: dict[str, Any]) -> str:
    """
    Routing function: Check if the graph should exit or continue.
    Returns the next node name as a string.
    """
    if state.get("should_exit", False):
        return "exit"
    return "continue"
