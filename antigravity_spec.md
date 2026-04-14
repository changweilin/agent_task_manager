# Role and Objective

You are a Senior Python AI Engineer. Your task is to build a local GitOps AI Orchestrator using **Python**, **LangGraph**, and **GitPython**.
This script will act as a background state machine that reads a specific Markdown file (`roadmap.md`), executes tasks using an LLM (Claude/Gemini), runs local tests, and manages Git branches based on conditions.

# Core Requirements

## 1. State Management & Parsing (roadmap.md)

- Parse `roadmap.md` which contains YAML frontmatter and a Markdown task list.
- Extract `sys_status`, current active task, verification commands (`make test_...`), branching logic, and context control flags.
- **Git Integration**: Before executing, pull the latest changes. After completing a step or changing the state, update `roadmap.md`, commit, and push back to origin.

## 2. API Rate Limit Handling (Pause & Resume)

- When interacting with the LLM API, implement exception handling for `HTTP 429 Too Many Requests`.
- If triggered:
  - Calculate the wait time.
  - Update `roadmap.md` YAML frontmatter: set `sys_status` to `SLEEP_RATE_LIMIT` and `rate_limit_resume_time` to the target timestamp.
  - Commit and push.
  - Exit the script gracefully.
- The script should check `sys_status` upon startup. If sleeping and time hasn't elapsed, exit immediately.

## 3. Context Compacting

- Track the estimated token count of the conversation history.
- If the token limit approaches the threshold defined in `roadmap.md`, or if a task has the `[COMPACT_AFTER_SUCCESS]` flag:
  - Trigger a specific LangGraph node: `compact_context`.
  - Use a smaller/faster LLM to summarize the past conversation, code context, and decisions made.
  - Replace the old message history with this summarized prompt to clear up the context window.

## 4. AI-Driven Conditional Branching (LangGraph Conditional Edges)

- After the LLM writes/modifies code, execute the validation command specified in the task (e.g., `make test`).
- Pass the `stdout` and `stderr` to the LLM to evaluate the result.
- Based on the branching logic defined in `roadmap.md`:
  - **Success**: Update task to `[x]`, move to the next task.
  - **Failure (Minor)**: Feed the error back to the LLM and retry the current task.
  - **Failure (Major / Triggering condition)**: Use `GitPython` to checkout a new debug branch (`git checkout -b bugfix/task-name`), update `roadmap.md` to point to the new task (e.g., `TASK_D`), commit, and continue execution on the new branch.

# Architecture Spec (LangGraph)

- Use `StateGraph` to define the workflow.
- Include a SQLite checkpointer (`SqliteSaver`) to persist the graph state across script executions, ensuring that if the script is killed or sleeps due to rate limits, it resumes exactly where it left off.
- Required Nodes:
  - `parse_roadmap`
  - `execute_coding_task`
  - `run_validation`
  - `evaluate_and_route` (Conditional Edge)
  - `compact_context`

# Output Request

1. Please provide the complete Python script `orchestrator.py`.
2. Ensure all code comments are in English.
3. Provide a `requirements.txt` containing `langgraph`, `langchain`, `gitpython`, `pyyaml`, etc.
