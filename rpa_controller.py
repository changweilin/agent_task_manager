"""
RPA Controller for the GitOps AI Orchestrator.
Abstracts CLI process management (wexpect/pexpect) and GUI automation (pyautogui/pywinauto).
Controls external AI coding tools: Claude Code (CLI), Antigravity/VS Code (GUI).
"""

import logging
import platform
import re
import subprocess
import time
from typing import Optional

import pyautogui
import pyperclip

from config import (
    AgentState,
    CLI_IDLE_PATTERNS,
    GUI_WINDOW_TITLES,
    IDE_SHORTCUTS,
    IS_WINDOWS,
    RPA_ACTION_DELAY,
    RPA_POLL_INTERVAL,
)

logger = logging.getLogger(__name__)

# Configure pyautogui safety
pyautogui.FAILSAFE = True
pyautogui.PAUSE = RPA_ACTION_DELAY


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from terminal output."""
    ansi_pattern = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07")
    return ansi_pattern.sub("", text)


class CLIController:
    """
    Controls a CLI-based AI agent (e.g., Claude Code) via interactive terminal.
    Uses wexpect on Windows, pexpect on Unix.
    Falls back to subprocess.Popen if neither is available.
    """

    def __init__(self):
        self.process = None
        self._use_wexpect = False
        self._use_pexpect = False
        self._use_subprocess = False
        self._detect_backend()

    def _detect_backend(self):
        """Detect the best available backend for CLI process management."""
        if IS_WINDOWS:
            try:
                import wexpect  # noqa: F401
                self._use_wexpect = True
                logger.info("CLI backend: wexpect (Windows)")
                return
            except ImportError:
                logger.warning("wexpect not available, trying subprocess fallback")
        else:
            try:
                import pexpect  # noqa: F401
                self._use_pexpect = True
                logger.info("CLI backend: pexpect (Unix)")
                return
            except ImportError:
                logger.warning("pexpect not available, trying subprocess fallback")

        self._use_subprocess = True
        logger.info("CLI backend: subprocess.Popen (fallback)")

    def start(self, command: str) -> bool:
        """
        Start a CLI process and keep it running in the background.
        Args:
            command: The command to run (e.g., 'claude' for Claude Code CLI).
        Returns True if process started successfully.
        """
        try:
            if self._use_wexpect:
                import wexpect
                self.process = wexpect.spawn(command, timeout=None)
                logger.info(f"Started CLI process via wexpect: {command}")

            elif self._use_pexpect:
                import pexpect
                self.process = pexpect.spawn(
                    command, timeout=None, encoding="utf-8"
                )
                logger.info(f"Started CLI process via pexpect: {command}")

            else:
                self.process = subprocess.Popen(
                    command,
                    shell=True,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
                logger.info(f"Started CLI process via subprocess: {command}")

            return True
        except Exception as e:
            logger.error(f"Failed to start CLI process: {e}")
            return False

    def detect_state(self) -> AgentState:
        """
        Detect whether the CLI agent is IDLE (waiting for input) or WORKING.
        Reads the latest output and checks against known prompt patterns.
        """
        try:
            output = self._read_latest_output()
            if output is None:
                return AgentState.UNKNOWN

            clean_output = strip_ansi(output)
            last_line = clean_output.strip().split("\n")[-1] if clean_output.strip() else ""

            for pattern in CLI_IDLE_PATTERNS:
                if re.search(pattern, last_line):
                    return AgentState.IDLE

            return AgentState.WORKING

        except Exception as e:
            logger.error(f"State detection failed: {e}")
            return AgentState.UNKNOWN

    def _read_latest_output(self) -> Optional[str]:
        """Read the latest output from the CLI process."""
        if self.process is None:
            return None

        try:
            if self._use_wexpect or self._use_pexpect:
                # Try to read what's available without blocking
                try:
                    # Non-blocking read with short timeout
                    if self._use_pexpect:
                        import pexpect
                        self.process.expect(pexpect.TIMEOUT, timeout=0.5)
                    else:
                        import wexpect
                        self.process.expect(wexpect.TIMEOUT, timeout=0.5)
                except Exception:
                    pass
                return self.process.before or ""

            else:
                # subprocess fallback - non-blocking read
                import select
                if IS_WINDOWS:
                    # Windows doesn't support select on pipes, use a different approach
                    try:
                        output = self.process.stdout.readline()
                        return output
                    except Exception:
                        return ""
                else:
                    readable, _, _ = select.select(
                        [self.process.stdout], [], [], 0.5
                    )
                    if readable:
                        return self.process.stdout.readline()
                    return ""

        except Exception as e:
            logger.error(f"Read output failed: {e}")
            return None

    def inject_command(self, text: str) -> bool:
        """
        Inject a command/prompt into the CLI process stdin.
        Only call this when the agent is in IDLE state.
        """
        if self.process is None:
            logger.error("No CLI process running")
            return False

        try:
            if self._use_wexpect or self._use_pexpect:
                self.process.sendline(text)
            else:
                self.process.stdin.write(text + "\n")
                self.process.stdin.flush()

            logger.info(f"Injected command: {text[:80]}...")
            return True
        except Exception as e:
            logger.error(f"Command injection failed: {e}")
            return False

    def wait_for_idle(self, timeout: float = 300) -> bool:
        """
        Block until the CLI agent returns to IDLE state, or timeout.
        Returns True if IDLE detected, False on timeout.
        """
        start = time.time()
        while time.time() - start < timeout:
            state = self.detect_state()
            if state == AgentState.IDLE:
                return True
            time.sleep(RPA_POLL_INTERVAL)
        logger.warning(f"Timeout waiting for IDLE after {timeout}s")
        return False

    def is_alive(self) -> bool:
        """Check if the CLI process is still running."""
        if self.process is None:
            return False

        if self._use_wexpect or self._use_pexpect:
            return self.process.isalive()
        else:
            return self.process.poll() is None

    def terminate(self):
        """Terminate the CLI process."""
        if self.process is None:
            return

        try:
            if self._use_wexpect or self._use_pexpect:
                self.process.close()
            else:
                self.process.terminate()
            logger.info("CLI process terminated")
        except Exception as e:
            logger.error(f"Process termination failed: {e}")


class GUIController:
    """
    Controls a GUI-based AI IDE (e.g., VS Code, Antigravity) via RPA.
    Uses pywinauto for window management and pyautogui for keyboard input.
    All navigation uses keyboard shortcuts only — no mouse coordinates.
    """

    def __init__(self):
        self._app = None
        self._window = None

    def focus_window(self, target: str = "vscode") -> bool:
        """
        Find and bring the target IDE window to the foreground.
        Args:
            target: Key from GUI_WINDOW_TITLES ('vscode' or 'antigravity').
        """
        title_pattern = GUI_WINDOW_TITLES.get(target, target)

        if IS_WINDOWS:
            return self._focus_window_windows(title_pattern)
        else:
            return self._focus_window_unix(title_pattern)

    def _focus_window_windows(self, title_pattern: str) -> bool:
        """Focus window on Windows using pywinauto."""
        try:
            from pywinauto import Desktop

            desktop = Desktop(backend="uia")
            windows = desktop.windows()

            for win in windows:
                if title_pattern.lower() in win.window_text().lower():
                    win.set_focus()
                    time.sleep(RPA_ACTION_DELAY)
                    logger.info(f"Focused window: {win.window_text()}")
                    return True

            logger.warning(f"Window not found: {title_pattern}")
            return False

        except ImportError:
            logger.error("pywinauto not installed — cannot focus window on Windows")
            return False
        except Exception as e:
            logger.error(f"Window focus failed: {e}")
            return False

    def _focus_window_unix(self, title_pattern: str) -> bool:
        """Focus window on macOS/Linux using wmctrl or AppleScript."""
        try:
            if platform.system() == "Darwin":
                # macOS: use AppleScript
                script = f'''
                tell application "System Events"
                    set targetProcess to first process whose name contains "{title_pattern}"
                    set frontmost of targetProcess to true
                end tell
                '''
                subprocess.run(["osascript", "-e", script], check=True, timeout=5)
            else:
                # Linux: use wmctrl
                subprocess.run(
                    ["wmctrl", "-a", title_pattern], check=True, timeout=5
                )
            logger.info(f"Focused window: {title_pattern}")
            return True
        except Exception as e:
            logger.error(f"Window focus failed: {e}")
            return False

    def open_chat(self) -> bool:
        """Open the AI chat panel in the IDE using keyboard shortcut."""
        try:
            keys = IDE_SHORTCUTS.get("open_chat", [])
            pyautogui.hotkey(*keys)
            time.sleep(RPA_ACTION_DELAY * 2)
            logger.info("Opened chat panel")
            return True
        except Exception as e:
            logger.error(f"Open chat failed: {e}")
            return False

    def focus_input(self) -> bool:
        """Focus the chat input box using keyboard shortcut."""
        try:
            keys = IDE_SHORTCUTS.get("focus_input", [])
            pyautogui.hotkey(*keys)
            time.sleep(RPA_ACTION_DELAY)
            logger.info("Focused chat input")
            return True
        except Exception as e:
            logger.error(f"Focus input failed: {e}")
            return False

    def inject_via_clipboard(self, text: str) -> bool:
        """
        Inject a prompt into the IDE's chat input via clipboard.
        1. Copy text to clipboard
        2. Paste (Ctrl+V / Cmd+V)
        3. Press Enter to submit
        """
        try:
            # Copy to clipboard
            pyperclip.copy(text)
            time.sleep(RPA_ACTION_DELAY)

            # Paste
            paste_keys = IDE_SHORTCUTS.get("paste", [])
            pyautogui.hotkey(*paste_keys)
            time.sleep(RPA_ACTION_DELAY)

            # Submit
            enter_key = IDE_SHORTCUTS.get("enter", ["enter"])
            pyautogui.hotkey(*enter_key)

            logger.info(f"Injected via clipboard: {text[:80]}...")
            return True
        except Exception as e:
            logger.error(f"Clipboard injection failed: {e}")
            return False

    def send_prompt(self, text: str, target: str = "vscode") -> bool:
        """
        Full workflow: focus window → open chat → focus input → paste prompt.
        """
        if not self.focus_window(target):
            return False
        if not self.open_chat():
            return False
        if not self.focus_input():
            return False
        if not self.inject_via_clipboard(text):
            return False

        logger.info("Prompt sent successfully via GUI RPA")
        return True


class RPAController:
    """
    Unified RPA controller that dispatches to CLI or GUI based on target.
    """

    def __init__(self):
        self.cli = CLIController()
        self.gui = GUIController()

    def start_cli(self, command: str) -> bool:
        """Start a CLI agent process."""
        return self.cli.start(command)

    def detect_cli_state(self) -> AgentState:
        """Detect CLI agent state."""
        return self.cli.detect_state()

    def inject_to_cli(self, text: str) -> bool:
        """Inject a command to the CLI agent."""
        return self.cli.inject_command(text)

    def wait_for_cli_idle(self, timeout: float = 300) -> bool:
        """Wait for CLI agent to become IDLE."""
        return self.cli.wait_for_idle(timeout)

    def send_to_gui(self, text: str, target: str = "vscode") -> bool:
        """Send a prompt to a GUI IDE."""
        return self.gui.send_prompt(text, target)

    def focus_gui(self, target: str = "vscode") -> bool:
        """Focus a GUI window."""
        return self.gui.focus_window(target)
