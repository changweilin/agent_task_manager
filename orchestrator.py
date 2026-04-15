"""
GitOps AI Orchestrator — Main Entry Point.

A pure RPA state machine that reads roadmap.md and controls external AI coding
tools (Claude Code CLI, Antigravity/VS Code GUI) to execute development tasks.

Supports two workflow modes:
  - Creative Factory: Basic GitOps + RPA automation (detect → inject → validate → route)
  - Product Factory: SDD/BDD pipeline + AI Code Review (spec → bdd → code → test → review)

Usage:
  python orchestrator.py --mode creative --target cli
  python orchestrator.py --mode product --target gui --gui-target antigravity
  python orchestrator.py --mode creative --dry-run
"""

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).parent.resolve()

from langgraph.graph import END, StateGraph
from langgraph.checkpoint.sqlite import SqliteSaver

from config import (
    CHECKPOINT_DB,
    ROADMAP_PATH,
    RPATarget,
    SysStatus,
    WorkflowMode,
)
from git_manager import GitManager
from roadmap_parser import parse_roadmap, update_roadmap
from rpa_controller import RPAController
import rpa_registry  # Singleton registry — keeps RPAController outside serializable state

from nodes.shared_nodes import (
    check_should_exit,
    compact_context,
    handle_rate_limit,
    parse_roadmap_and_sync,
    run_validation,
)
from nodes.creative_nodes import (
    creative_route_decision,
    detect_agent_state_and_focus,
    evaluate_and_route,
    inject_prompt_via_rpa,
)
from nodes.product_nodes import (
    ai_code_review,
    create_pr,
    ensure_bdd_tests,
    product_route_decision,
    rpa_agent_execute,
    run_bdd_validation,
    sync_and_parse_sdd,
)

# --- Logging setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("orchestrator.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("orchestrator")


# --- Exit node ---
def exit_node(state: dict[str, Any]) -> dict[str, Any]:
    """Terminal node — logs the exit reason and returns final state."""
    reason = state.get("exit_reason", "completed")
    logger.info(f"Orchestrator exiting. Reason: {reason}")
    state["final_status"] = reason
    return state


# --- Graph Builders ---

def build_creative_graph() -> StateGraph:
    """
    Build the Creative Factory LangGraph workflow.

    Flow:
      parse_roadmap → check_exit → detect_agent_state → inject_prompt
      → run_validation → evaluate_and_route → compact_context
      → (loop or exit)
    """
    graph = StateGraph(dict)

    # Add nodes
    graph.add_node("parse_roadmap", parse_roadmap_and_sync)
    graph.add_node("detect_agent_state", detect_agent_state_and_focus)
    graph.add_node("inject_prompt", inject_prompt_via_rpa)
    graph.add_node("run_validation", run_validation)
    graph.add_node("evaluate_route", evaluate_and_route)
    graph.add_node("compact_context", compact_context)
    graph.add_node("handle_rate_limit", handle_rate_limit)
    graph.add_node("exit", exit_node)

    # Set entry point
    graph.set_entry_point("parse_roadmap")

    # Define edges
    graph.add_conditional_edges(
        "parse_roadmap",
        check_should_exit,
        {
            "exit": "exit",
            "continue": "detect_agent_state",
        },
    )

    graph.add_edge("detect_agent_state", "inject_prompt")
    graph.add_edge("inject_prompt", "run_validation")
    graph.add_edge("run_validation", "evaluate_route")

    graph.add_conditional_edges(
        "evaluate_route",
        creative_route_decision,
        {
            "detect_agent_state": "detect_agent_state",  # Retry
            "parse_roadmap": "compact_context",           # Next task (compact first)
            "exit": "exit",
        },
    )

    graph.add_edge("compact_context", "parse_roadmap")
    graph.add_edge("handle_rate_limit", "exit")
    graph.add_edge("exit", END)

    return graph


def build_product_graph() -> StateGraph:
    """
    Build the Product Factory LangGraph workflow.

    Flow:
      parse_roadmap → check_exit → sync_sdd → ensure_bdd
      → rpa_execute → run_bdd → create_pr → ai_review
      → compact_context → (loop or exit)
    """
    graph = StateGraph(dict)

    # Add nodes
    graph.add_node("parse_roadmap", parse_roadmap_and_sync)
    graph.add_node("sync_sdd", sync_and_parse_sdd)
    graph.add_node("ensure_bdd", ensure_bdd_tests)
    graph.add_node("rpa_execute", rpa_agent_execute)
    graph.add_node("run_bdd", run_bdd_validation)
    graph.add_node("create_pr", create_pr)
    graph.add_node("ai_review", ai_code_review)
    graph.add_node("compact_context", compact_context)
    graph.add_node("handle_rate_limit", handle_rate_limit)
    graph.add_node("exit", exit_node)

    # Set entry point
    graph.set_entry_point("parse_roadmap")

    # Define edges
    graph.add_conditional_edges(
        "parse_roadmap",
        check_should_exit,
        {
            "exit": "exit",
            "continue": "sync_sdd",
        },
    )

    graph.add_edge("sync_sdd", "ensure_bdd")
    graph.add_edge("ensure_bdd", "rpa_execute")
    graph.add_edge("rpa_execute", "run_bdd")

    graph.add_conditional_edges(
        "run_bdd",
        lambda state: "create_pr" if state.get("bdd_passed") else "retry_or_exit",
        {
            "create_pr": "create_pr",
            "retry_or_exit": "rpa_execute",  # Self-correction loop
        },
    )

    graph.add_edge("create_pr", "ai_review")

    graph.add_conditional_edges(
        "ai_review",
        product_route_decision,
        {
            "rpa_execute": "rpa_execute",    # Review failed, fix code
            "parse_roadmap": "compact_context",  # Success, next task
            "exit": "exit",
        },
    )

    graph.add_edge("compact_context", "parse_roadmap")
    graph.add_edge("handle_rate_limit", "exit")
    graph.add_edge("exit", END)

    return graph


# --- Main execution ---

# Module-level GitManager registry (same pattern as rpa_registry — avoids serialization issues)
_git_managers: dict[str, GitManager] = {}


def _load_project_config(project_id: str) -> dict:
    """Load a single project config from projects_config.json by ID."""
    import json
    config_path = BASE_DIR / "projects_config.json"
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        return next((p for p in data.get("projects", []) if p["id"] == project_id), {})
    except Exception:
        return {}


def create_initial_state(
    mode: WorkflowMode,
    target: RPATarget,
    gui_target: str = "vscode",
    cli_command: str = "claude",
    dry_run: bool = False,
    project_id: str = "default",
) -> dict[str, Any]:
    """
    Create the initial state dictionary for the graph.

    IMPORTANT: RPAController is stored in rpa_registry (not in state dict)
    to avoid msgpack serialization errors with LangGraph's SqliteSaver.
    Only the string key 'rpa_key' is stored in the state.
    """
    # Load project-specific settings from projects_config.json
    project_cfg = _load_project_config(project_id)
    results_log_path = project_cfg.get("results_log_path", "")
    roadmap_path = project_cfg.get("roadmap_path", str(ROADMAP_PATH))
    project_name = project_cfg.get("name", project_id)

    state = {
        "mode": mode.value,
        "rpa_target": target.value,   # Store as string, not enum, for serialization
        "gui_target": gui_target,
        "cli_command": cli_command,
        "dry_run": dry_run,
        "project_id": project_id,
        "project_name": project_name,
        "roadmap_path": roadmap_path,       # Per-project path (overrides config.py default)
        "results_log_path": results_log_path,  # For results_log_manager
        "should_exit": False,
        "exit_reason": None,
        "retry_count": 0,
        "context_compacted": False,
        "rpa_key": project_id,              # Key into rpa_registry
        "git_manager_key": project_id,      # Key into _git_managers
    }

    # Initialize RPA controller in registry (NOT in state)
    rpa = RPAController()
    if target == RPATarget.CLI and not dry_run:
        logger.info(f"Starting CLI process: {cli_command}")
        rpa.start_cli(cli_command)
    rpa_registry.register_rpa(rpa, key=project_id)
    logger.info(f"RPAController registered with key: {project_id}")

    # Initialize Git manager in module-level dict (also NOT in state)
    from pathlib import Path as _Path
    repo_path = _Path(roadmap_path).parent if roadmap_path else None
    _git_managers[project_id] = GitManager(repo_path=repo_path)

    logger.info(f"Project: {project_name} | roadmap: {roadmap_path}")
    logger.info(f"Results log: {results_log_path or '(not configured)'}")

    return state


def run_orchestrator(
    mode: WorkflowMode,
    target: RPATarget,
    gui_target: str = "vscode",
    cli_command: str = "claude",
    dry_run: bool = False,
    project_id: str = "default",
):
    """
    Run the orchestrator with the specified workflow and target.
    """
    logger.info("=" * 60)
    logger.info(f"GitOps AI Orchestrator Starting")
    logger.info(f"  Mode: {mode.value}")
    logger.info(f"  Target: {target.value}")
    logger.info(f"  GUI Target: {gui_target}")
    logger.info(f"  CLI Command: {cli_command}")
    logger.info(f"  Dry Run: {dry_run}")
    logger.info("=" * 60)

    # Build the appropriate graph
    if mode == WorkflowMode.CREATIVE:
        graph = build_creative_graph()
    else:
        graph = build_product_graph()

    if dry_run:
        # Dry-run: compile without checkpointer, just validate
        compiled = graph.compile()
        logger.info("DRY RUN: Graph compiled successfully. Validating roadmap...")
        # Use project-specific roadmap if available
        project_cfg = _load_project_config(project_id)
        roadmap_path_str = project_cfg.get("roadmap_path", "")
        from pathlib import Path as _Path
        rp = _Path(roadmap_path_str) if roadmap_path_str else None
        roadmap = parse_roadmap(path=rp)
        logger.info(f"  System status: {roadmap.sys_status.value}")
        logger.info(f"  Tasks: {len(roadmap.tasks)}")
        for task in roadmap.tasks:
            marker = "->" if task.is_current or task.status == "current" else " "
            logger.info(f"  {marker} [{task.status}] {task.name}: {task.title}")
        logger.info("DRY RUN complete. All systems nominal.")
        return

    # Create initial state
    initial_state = create_initial_state(
        mode=mode,
        target=target,
        gui_target=gui_target,
        cli_command=cli_command,
        dry_run=dry_run,
        project_id=project_id,
    )

    # Run the graph with SQLite checkpointing (context manager)
    config = {"configurable": {"thread_id": f"orchestrator-{mode.value}"}}

    with SqliteSaver.from_conn_string(str(CHECKPOINT_DB)) as checkpointer:
        compiled = graph.compile(checkpointer=checkpointer)

        try:
            for step_output in compiled.stream(initial_state, config=config):
                node_name = list(step_output.keys())[0]
                logger.info(f"Completed node: {node_name}")

                # Check for rate limit in the output
                node_state = step_output[node_name]
                if isinstance(node_state, dict) and node_state.get("should_exit"):
                    reason = node_state.get("exit_reason", "unknown")
                    logger.info(f"Graph signaled exit: {reason}")
                    break

        except KeyboardInterrupt:
            logger.info("Orchestrator interrupted by user (Ctrl+C)")
            # Gracefully save state
            update_roadmap(
                sys_status=SysStatus.PAUSED,
                latest_action="Orchestrator paused by user interrupt.",
            )
            git = initial_state.get("git_manager")
            if git:
                git.commit_and_push("chore: orchestrator paused by user")

        except Exception as e:
            logger.error(f"Orchestrator error: {e}", exc_info=True)
            update_roadmap(
                latest_action=f"Orchestrator error: {str(e)[:200]}",
            )

        finally:
            # Cleanup RPA resources from registry
            rpa_key = initial_state.get("rpa_key", "default")
            rpa = rpa_registry.get_rpa(rpa_key)
            if rpa and rpa.cli.is_alive():
                rpa.cli.terminate()
            rpa_registry.unregister_rpa(rpa_key)
            _git_managers.pop(rpa_key, None)
            logger.info(f"Cleaned up RPA and Git resources for key: {rpa_key}")

    logger.info("Orchestrator shutdown complete.")


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="GitOps AI Orchestrator — Control AI coding tools via RPA",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python orchestrator.py --mode creative --target cli
  python orchestrator.py --mode product --target gui --gui-target antigravity
  python orchestrator.py --mode creative --dry-run
  python orchestrator.py --mode product --target cli --cli-command "claude"
        """,
    )

    parser.add_argument(
        "--mode",
        type=str,
        choices=["creative", "product"],
        required=True,
        help="Workflow mode: creative (RPA automation) or product (SDD/BDD pipeline)",
    )

    parser.add_argument(
        "--target",
        type=str,
        choices=["cli", "gui"],
        default="cli",
        help="RPA target: cli (Claude Code) or gui (VS Code/Antigravity)",
    )

    parser.add_argument(
        "--gui-target",
        type=str,
        default="vscode",
        choices=["vscode", "antigravity"],
        help="GUI target IDE (only used when --target gui)",
    )

    parser.add_argument(
        "--cli-command",
        type=str,
        default="claude",
        help="CLI command to start the AI agent (default: claude)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate graph and roadmap without executing",
    )

    parser.add_argument(
        "--project-id",
        type=str,
        default="default",
        help="Project ID from projects_config.json (determines roadmap and results_log paths)",
    )

    args = parser.parse_args()

    run_orchestrator(
        mode=WorkflowMode(args.mode),
        target=RPATarget(args.target),
        gui_target=args.gui_target,
        cli_command=args.cli_command,
        dry_run=args.dry_run,
        project_id=args.project_id,
    )


if __name__ == "__main__":
    main()
