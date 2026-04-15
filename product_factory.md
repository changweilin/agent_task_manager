# Role and Objective

You are a Principal AI Architect and DevOps Automation Expert. Your task is to build a local GitOps AI Orchestrator using **Python**, **LangGraph**, **GitPython**, **pexpect**, and **pyautogui**.
This system acts as a background state machine controlled by a remote Markdown file (`roadmap.md`). It must strictly enforce a professional software engineering pipeline: **SDD (Specification-Driven Development)**, **BDD (Behavior-Driven Development)**, and **AI-driven CI/CD Code Review**.

# Core Engineering Pipeline Requirements

## 1. SDD: Specification Parsing & Planning

- **Concept**: The remote `roadmap.md` acts as the SDD control panel. Instead of simple prompts, tasks will link to detailed spec files (e.g., `specs/feature_A.md`).
- **Implementation**:
  - The `parse_roadmap` node must extract the `sys_status`, target feature, and read the associated Markdown specification document.
  - The AI Agent must first output an "Implementation Plan" based strictly on the SDD before touching the codebase.

## 2. BDD: Behavior-Driven Validation Loop

- **Concept**: Code is only acceptable if it passes behavioral tests.
- **Implementation**:
  - Before writing application code, the Orchestrator checks if a BDD feature file (e.g., `features/task.feature` using Gherkin syntax) exists. If not, the Agent writes it first based on the SDD.
  - The `run_validation` node executes BDD frameworks (e.g., `pytest-bdd` or `behave`).
  - **Self-Correction Loop**: If tests fail, parse the BDD failure log, inject it back into the Agent's context, and trigger the repair cycle until all scenarios pass.

## 3. CI/CD: Automated AI Code Review & PR Generation

- **Concept**: The coding Agent cannot push directly to the `main` branch. It must be reviewed.
- **Implementation**:
  - Once BDD tests pass, use `GitPython` to commit changes to a specific feature branch and generate a local/remote Pull Request.
  - Trigger a specific LangGraph node: `ai_code_reviewer`. This node acts as an independent Senior Reviewer (using a different system prompt or stricter LLM parameters).
  - It reviews the `git diff` against the original SDD for: Code Smells, Security Vulnerabilities, and SDD compliance.
  - If the review passes -> Auto-merge to main and update `roadmap.md` status to `COMPLETED`.
  - If the review fails -> Add review comments to `roadmap.md` or a log file, revert status to `IN_PROGRESS`, and send it back to the coding Agent.

## 4. CLI & IDE RPA Control

- Use `pexpect` to maintain background CLI processes (like Claude Code). Filter ANSI escape codes and detect `IDLE`/`WORKING` states using shell prompts.
- Use `pyautogui` and `pyperclip` (clipboard injection) to control IDEs (like VS Code) for complex debugging or context-heavy prompt injection, relying strictly on keyboard shortcuts, not mouse coordinates.

## 5. Rate Limit & Context Management

- Handle API HTTP 429 errors by pausing the graph, updating `roadmap.md` with a `SLEEP` status and resume timestamp, then gracefully exiting.
- Trigger a `compact_context` node when token limits approach, summarizing previous BDD failures and implementation steps to prevent context window explosion.

# Architecture Spec (LangGraph Definition)

- **StateGraph**: Use `SqliteSaver` for checkpointing to allow pausing/resuming.
- **Required Nodes**:
  - `sync_and_parse_sdd` (Pull repo, parse roadmap and specs)
  - `rpa_agent_execute` (Drive CLI/IDE to write BDD tests and App code)
  - `run_bdd_validation` (Execute tests, return pass/fail state)
  - `create_pr` (Branching and committing)
  - `ai_code_review` (Diff analysis against SDD)
  - `compact_context`

# Output Request

1. Provide the complete Python script `orchestrator.py` implementing this LangGraph pipeline.
2. Ensure English comments and modular design.
3. Provide `requirements.txt` including `langgraph`, `gitpython`, `pytest-bdd` (or equivalent), `pexpect`, `pyautogui`, `pyperclip`.
