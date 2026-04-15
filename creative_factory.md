# Role and Objective

You are a Senior Python AI Engineer and RPA Expert. Your task is to build a local GitOps AI Orchestrator using **Python**, **LangGraph**, **GitPython**, **pexpect**, and **pyautogui**.
This script will act as a background state machine that reads a specific Markdown file (`roadmap.md`) synced via GitHub/Remote, executes tasks, manages Git branches, and **directly controls both CLI-based Agents and GUI-based IDEs (like VS Code)**.

# Core Requirements

## 1. State Management & Parsing (roadmap.md)

- Parse `roadmap.md` which contains YAML frontmatter and a Markdown task list.
- Extract `sys_status`, current active task, verification commands, branching logic, and context control flags.
- **Git Integration**: Before executing, pull the latest changes. After completing a step or changing the state, update `roadmap.md`, commit, and push back to origin.

## 2. API Rate Limit Handling (Pause & Resume)

- Implement exception handling for API limits (e.g., `HTTP 429`).
- Update `roadmap.md` YAML frontmatter: set `sys_status` to `SLEEP_RATE_LIMIT` and calculate `rate_limit_resume_time`. Commit, push, and exit gracefully.

## 3. Context Compacting

- Track estimated token count. If approaching the limit or encountering a `[COMPACT_AFTER_SUCCESS]` flag, trigger a `compact_context` LangGraph node using a smaller LLM to summarize and replace the message history.

## 4. AI-Driven Conditional Branching

- Execute validation commands (e.g., `make test`), pass `stdout`/`stderr` to the LLM.
- Based on branching logic in `roadmap.md`: proceed to the next task, retry, or use `GitPython` to checkout a new debug branch and switch tasks.

# New Automation Requirements

## [cite_start]5. CLI Agent Process Management & State Detection [cite: 364, 365]

- **Process Wrapper**: Do not run CLI agents (like Claude Code) as one-off subprocesses. Use `pexpect` (or async `subprocess.PIPE`) to wrap the interactive terminal process and keep it running in the background.
- **State Detection**: Continuously monitor the `stdout` of the CLI process.
  - [cite_start]Filter out ANSI escape codes (color formatting) before parsing.
  - [cite_start]Detect when the agent is `WORKING` vs. `IDLE` (waiting for input, usually indicated by a specific prompt character like `>` or `?`)[cite: 364, 365].
- [cite_start]**Command Injection**: When the state is `IDLE` and there is a pending task/prompt from `roadmap.md`, inject the command into the process via `stdin` (simulate typing and pressing Enter)[cite: 361, 364].

## [cite_start]6. IDE & GUI Automation Control [cite: 364, 365]

- [cite_start]**Window Management**: Use OS-level automation (`pywinauto` for Windows or `AppleScript`/`pyobjc` for macOS) to find, focus, and bring the target IDE (e.g., VS Code or Antigravity GUI) to the foreground[cite: 364].
- [cite_start]**Keyboard Shortcut Navigation**: To avoid screen resolution dependency issues, strictly use keyboard shortcuts (via `pyautogui`) to navigate the IDE, open the Agent chat window, and focus on the input box. Do not rely on fixed (X, Y) mouse coordinates.
- **Clipboard Injection**: To input long or complex prompts extracted from `roadmap.md`:
  - [cite_start]Copy the prompt text to the system clipboard using `pyperclip`.
  - [cite_start]Simulate the paste shortcut (`Ctrl+V` or `Cmd+V`) into the IDE's input box.
  - Simulate pressing `Enter` to execute the prompt.

# Architecture Spec (LangGraph & RPA)

- Use `StateGraph` with a SQLite checkpointer (`SqliteSaver`) for the core workflow.
- Include an `RPA_Controller` class to abstract `pexpect` terminal interactions and `pyautogui` GUI interactions.
- Required Nodes:
  - `parse_roadmap_and_sync`
  - `detect_agent_state_and_focus` (New: check CLI/IDE readiness)
  - `inject_prompt_via_rpa` (New: dispatch to pexpect or pyautogui based on target)
  - `run_validation`
  - `evaluate_and_route`
  - `compact_context`

# Output Request

1. Please provide the complete Python script `orchestrator.py`.
2. Ensure all code comments are in English.
3. Provide a `requirements.txt` containing `langgraph`, `langchain`, `gitpython`, `pyyaml`, `pexpect`, `pyautogui`, `pyperclip`, `pywinauto` (or `pyobjc`).
