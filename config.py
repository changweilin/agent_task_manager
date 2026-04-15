"""
Configuration module for the GitOps AI Orchestrator.
Defines workflow modes, RPA targets, and all configurable settings.
"""

import os
import platform
from enum import Enum
from pathlib import Path


class WorkflowMode(Enum):
    """Workflow mode selection."""
    CREATIVE = "creative"  # Creative Factory: basic GitOps + RPA automation
    PRODUCT = "product"    # Product Factory: SDD/BDD pipeline + AI Code Review


class RPATarget(Enum):
    """Target AI coding tool to control via RPA."""
    CLI = "cli"  # Claude Code in terminal
    GUI = "gui"  # Antigravity / VS Code IDE


class AgentState(Enum):
    """Detected state of the target AI agent."""
    IDLE = "idle"        # Waiting for input (prompt visible)
    WORKING = "working"  # Processing a task
    UNKNOWN = "unknown"  # State cannot be determined


class SysStatus(Enum):
    """System status values from roadmap.md frontmatter."""
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    SLEEP_RATE_LIMIT = "SLEEP_RATE_LIMIT"


# --- Path Configuration ---
BASE_DIR = Path(__file__).parent.resolve()
ROADMAP_PATH = BASE_DIR / "roadmap.md"
SPECS_DIR = BASE_DIR / "specs"
FEATURES_DIR = BASE_DIR / "features"
CHECKPOINT_DB = BASE_DIR / "orchestrator_checkpoints.db"

# --- Git Configuration ---
GIT_REPO_PATH = BASE_DIR
GIT_REMOTE = "origin"
GIT_DEFAULT_BRANCH = "main"

# --- GitHub API (Optional, for Product Factory PRs) ---
GITHUB_API_TOKEN = os.environ.get("GITHUB_API_TOKEN", "")
GITHUB_REPO_OWNER = os.environ.get("GITHUB_REPO_OWNER", "")
GITHUB_REPO_NAME = os.environ.get("GITHUB_REPO_NAME", "")
USE_GITHUB_PR = bool(GITHUB_API_TOKEN)  # Auto-enable if token is set

# --- Token / Context Management ---
TOKEN_LIMIT = 100000  # Default, overridden by roadmap.md frontmatter
COMPACT_THRESHOLD_RATIO = 0.85  # Trigger compacting at 85% of token_limit

# --- RPA Configuration ---
# CLI prompt patterns that indicate IDLE state
CLI_IDLE_PATTERNS = [
    r">\s*$",       # Generic prompt
    r"\$\s*$",      # Shell prompt
    r">>>\s*$",     # Python REPL
    r"\?\s*$",      # Question prompt
    r"❯\s*$",       # Fancy prompt
]

# GUI window title patterns for IDE detection
GUI_WINDOW_TITLES = {
    "vscode": "Visual Studio Code",
    "antigravity": "Antigravity",
}

# Keyboard shortcuts for IDE navigation (cross-platform)
IDE_SHORTCUTS = {
    "open_chat": ["ctrl", "shift", "i"],     # Open AI chat panel
    "focus_input": ["ctrl", "l"],            # Focus chat input
    "paste": ["ctrl", "v"],                  # Paste from clipboard
    "enter": ["enter"],                      # Submit
}

# --- Platform Detection ---
IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

# --- Timing ---
RPA_POLL_INTERVAL = 2.0       # Seconds between state checks
RPA_ACTION_DELAY = 0.5        # Seconds between RPA keystrokes
VALIDATION_TIMEOUT = 300      # Seconds to wait for validation commands
RATE_LIMIT_SLEEP = 60         # Default sleep duration for rate limits
