"""
Creative Factory LangGraph nodes.
Workflow: detect agent state → inject prompt via RPA → run validation → evaluate & route.
Drives external AI tools (Claude Code CLI, Antigravity GUI) to execute tasks.
"""

import logging
from pathlib import Path
from typing import Any

from config import AgentState, RPATarget
from git_manager import GitManager
from roadmap_parser import update_roadmap
from rpa_controller import RPAController
import rpa_registry
from results_log_manager import (
    log_task_started,
    log_task_completed,
    log_task_failed,
)

logger = logging.getLogger(__name__)


def _get_roadmap_path(state: dict):
    """Return per-project Path for roadmap.md, or None for config default."""
    rp_str = state.get("roadmap_path", "")
    return Path(rp_str) if rp_str else None


def _get_results_log_path(state: dict):
    """Return per-project Path for results_log.md, or None if not set."""
    rl_str = state.get("results_log_path", "")
    return Path(rl_str) if rl_str else None


def _get_git_commit_hash(state: dict) -> str:
    """Get the latest git commit hash for the project."""
    try:
        from orchestrator import _git_managers
        git = _git_managers.get(state.get("git_manager_key", "default"))
        if git and git.repo:
            return git.repo.head.commit.hexsha
    except Exception:
        pass
    return ""


def detect_agent_state_and_focus(state: dict[str, Any]) -> dict[str, Any]:
    """
    Node: Check if the target AI agent (CLI or GUI) is ready to receive commands.
    For CLI: detect IDLE vs WORKING state.
    For GUI: attempt to focus the IDE window.
    """
    rpa_key = state.get("rpa_key", "default")
    rpa: RPAController = rpa_registry.get_rpa(rpa_key) or RPAController()
    target_str = state.get("rpa_target", RPATarget.CLI.value)
    target = RPATarget(target_str) if isinstance(target_str, str) else target_str

    if target == RPATarget.CLI:
        # Check CLI agent state
        agent_state = rpa.detect_cli_state()
        state["agent_state"] = agent_state.value

        if agent_state == AgentState.IDLE:
            logger.info("CLI agent is IDLE — ready for prompt injection.")
            state["agent_ready"] = True
        elif agent_state == AgentState.WORKING:
            logger.info("CLI agent is WORKING — waiting...")
            rpa.wait_for_cli_idle(timeout=120)
            state["agent_ready"] = True
        else:
            logger.warning("CLI agent state UNKNOWN — attempting injection anyway.")
            state["agent_ready"] = True

    elif target == RPATarget.GUI:
        # Focus the GUI IDE window
        gui_target = state.get("gui_target", "vscode")
        focused = rpa.focus_gui(gui_target)
        state["agent_ready"] = focused

        if focused:
            logger.info(f"GUI window focused: {gui_target}")
        else:
            logger.error(f"Failed to focus GUI window: {gui_target}")

    return state


def inject_prompt_via_rpa(state: dict[str, Any]) -> dict[str, Any]:
    """
    Node: Send the current task's instructions to the AI agent via RPA.
    Dispatches to CLI (pexpect stdin) or GUI (clipboard paste) based on target.
    """
    task = state.get("current_task")
    if not task:
        logger.error("No current task to inject.")
        state["injection_success"] = False
        return state

    if not state.get("agent_ready", False):
        logger.error("Agent not ready — skipping injection.")
        state["injection_success"] = False
        return state

    rpa_key = state.get("rpa_key", "default")
    rpa: RPAController = rpa_registry.get_rpa(rpa_key) or RPAController()
    target_str = state.get("rpa_target", RPATarget.CLI.value)
    target = RPATarget(target_str) if isinstance(target_str, str) else target_str

    # Build the prompt from task instructions
    prompt = _build_prompt(task)

    if target == RPATarget.CLI:
        success = rpa.inject_to_cli(prompt)
    elif target == RPATarget.GUI:
        gui_target = state.get("gui_target", "vscode")
        success = rpa.send_to_gui(prompt, gui_target)
    else:
        logger.error(f"Unknown RPA target: {target}")
        success = False

    state["injection_success"] = success

    if success:
        logger.info(f"Prompt injected for task: {task.name}")
        # Log task start to results_log.md
        rl_path = _get_results_log_path(state)
        if rl_path:
            try:
                log_task_started(
                    rl_path,
                    task_name=task.name,
                    task_title=task.title,
                    project_name=state.get("project_name", ""),
                )
            except Exception as e:
                logger.warning(f"results_log start failed: {e}")
        # Wait for agent to finish processing
        if target == RPATarget.CLI:
            rpa.wait_for_cli_idle(timeout=300)
    else:
        logger.error(f"Prompt injection failed for task: {task.name}")

    return state


def evaluate_and_route(state: dict[str, Any]) -> dict[str, Any]:
    """
    Node: Evaluate validation results and decide next action.
    Routes based on:
    - Validation passed + no branching → next task
    - Validation passed + branching rule matched → goto target task
    - Validation failed → retry or create debug branch
    """
    task = state.get("current_task")
    validation_passed = state.get("validation_passed", False)
    validation_output = state.get("validation_output", "")

    if not task:
        state["next_action"] = "exit"
        return state

    git_key = state.get("git_manager_key", "default")
    # Import here to avoid circular imports; access the module-level registry
    from orchestrator import _git_managers
    git: GitManager = _git_managers.get(git_key) or GitManager()

    if validation_passed:
        logger.info(f"Task {task.name} validation PASSED.")

        # Check branching rules
        matched_rule = None
        for rule in task.branching_rules:
            if rule.condition == "pass":
                matched_rule = rule
                break

        if matched_rule:
            logger.info(f"Branching: goto {matched_rule.target_task}")
            state["next_action"] = "goto"
            state["next_task_name"] = matched_rule.target_task
        else:
            state["next_action"] = "next"

        # Mark task as done in roadmap
        update_roadmap(
            path=_get_roadmap_path(state),
            task_name=task.name,
            task_status="done",
            latest_action=f"Task {task.name} completed successfully.",
        )
        commit_hash = ""
        try:
            git.commit_and_push(f"feat: complete {task.name} — {task.title}")
            commit_hash = _get_git_commit_hash(state)
        except Exception as e:
            logger.warning(f"Git commit failed: {e}")

        # Log to results_log.md
        rl_path = _get_results_log_path(state)
        if rl_path:
            try:
                log_task_completed(
                    rl_path,
                    task_name=task.name,
                    task_title=task.title,
                    branch=git.current_branch if hasattr(git, "current_branch") else "",
                    commit_hash=commit_hash,
                    validation_cmd=task.verification_cmd,
                    validation_passed=True,
                    validation_output=validation_output,
                    project_name=state.get("project_name", ""),
                )
            except Exception as e:
                logger.warning(f"results_log complete failed: {e}")

    else:
        logger.warning(f"Task {task.name} validation FAILED.")

        # Check for specific failure branching
        matched_rule = None
        for rule in task.branching_rules:
            if rule.condition != "pass" and rule.condition in validation_output:
                matched_rule = rule
                break

        if matched_rule:
            # Create debug branch and switch to the target task
            debug_branch = f"debug/{task.name.lower()}"
            git.create_branch(debug_branch)
            logger.info(
                f"Created debug branch: {debug_branch}, "
                f"switching to {matched_rule.target_task}"
            )
            state["next_action"] = "debug_branch"
            state["next_task_name"] = matched_rule.target_task

            update_roadmap(
                path=_get_roadmap_path(state),
                latest_action=(
                    f"Task {task.name} failed ({matched_rule.condition}). "
                    f"Debug branch created: {debug_branch}"
                ),
            )
            git.commit_and_push(
                f"debug: {task.name} — {matched_rule.condition}"
            )
        else:
            # Simple retry — re-inject the prompt with error context
            state["next_action"] = "retry"
            retry_count = state.get("retry_count", 0) + 1
            state["retry_count"] = retry_count

            if retry_count >= 3:
                logger.error(
                    f"Task {task.name} failed {retry_count} times. Giving up."
                )
                state["next_action"] = "exit"
                state["exit_reason"] = f"max_retries_for_{task.name}"

                # Log final failure to results_log.md
                rl_path = _get_results_log_path(state)
                if rl_path:
                    try:
                        log_task_failed(
                            rl_path,
                            task_name=task.name,
                            task_title=task.title,
                            error=f"Failed {retry_count} times. Last output:\n{validation_output[:400]}",
                            project_name=state.get("project_name", ""),
                        )
                    except Exception as e:
                        logger.warning(f"results_log failed write failed: {e}")
            else:
                logger.info(
                    f"Retrying task {task.name} (attempt {retry_count})..."
                )
                # Append error context to task instructions for retry
                task.instructions += (
                    f"\n\n[RETRY #{retry_count}] "
                    f"Previous attempt failed:\n{validation_output[:500]}"
                )

    return state


def creative_route_decision(state: dict[str, Any]) -> str:
    """
    Routing function for the Creative Factory workflow.
    Returns the next node name based on evaluation results.
    """
    action = state.get("next_action", "exit")

    if action == "exit":
        return "exit"
    elif action == "retry":
        return "detect_agent_state"
    elif action in ("next", "goto", "debug_branch"):
        return "parse_roadmap"
    else:
        return "exit"


def _build_prompt(task) -> str:
    """
    Build a prompt string from the task data to send to the AI agent.
    """
    parts = [
        f"Task: {task.name} — {task.title}",
        f"\nInstructions:\n{task.instructions}",
    ]

    if task.verification_cmd:
        parts.append(f"\nVerification command: `{task.verification_cmd}`")
        parts.append("Please ensure your changes pass this verification.")

    return "\n".join(parts)
