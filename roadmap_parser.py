"""
Roadmap Parser for the GitOps AI Orchestrator.
Parses roadmap.md to extract YAML frontmatter (system control panel)
and Markdown task list with instructions, verification commands, and branching logic.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from config import ROADMAP_PATH, SysStatus


@dataclass
class BranchingRule:
    """A conditional branching rule from a task."""
    condition: str       # e.g., "memory_leak_detected"
    target_task: str     # e.g., "TASK_D"
    action: str          # e.g., "goto" or "create_debug_branch"


@dataclass
class Task:
    """Represents a single task parsed from roadmap.md."""
    name: str                                    # e.g., "TASK_B"
    title: str                                   # e.g., "實作核心運算邏輯"
    status: str                                  # "done", "current", "pending"
    instructions: str = ""                       # 指令 content
    verification_cmd: str = ""                   # 驗證 command
    context_flags: list[str] = field(default_factory=list)  # e.g., ["COMPACT_AFTER_SUCCESS"]
    branching_rules: list[BranchingRule] = field(default_factory=list)
    spec_file: Optional[str] = None             # Linked SDD spec file (Product mode)
    is_current: bool = False                     # Whether this is the active task


@dataclass
class RoadmapState:
    """Parsed state from roadmap.md."""
    # YAML frontmatter
    sys_status: SysStatus = SysStatus.RUNNING
    rate_limit_resume_time: Optional[str] = None
    current_branch: str = "main"
    context_tokens: int = 0
    token_limit: int = 100000

    # Parsed tasks
    tasks: list[Task] = field(default_factory=list)

    # Execution log
    last_update: str = ""
    latest_action: str = ""

    @property
    def current_task(self) -> Optional[Task]:
        """Return the current active task."""
        for task in self.tasks:
            if task.is_current:
                return task
        # If no task is explicitly marked current, return first pending
        for task in self.tasks:
            if task.status == "pending":
                return task
        return None


def parse_frontmatter(content: str) -> dict:
    """Extract and parse YAML frontmatter from Markdown content."""
    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}

    yaml_text = match.group(1)
    # Remove inline comments that aren't standard YAML
    lines = []
    for line in yaml_text.split("\n"):
        # Keep lines but strip trailing comments (simple approach)
        if "#" in line:
            # Only strip comment if it's after a value
            parts = line.split("#")
            if ":" in parts[0]:
                line = parts[0].rstrip()
        lines.append(line)

    try:
        return yaml.safe_load("\n".join(lines)) or {}
    except yaml.YAMLError:
        return {}


def parse_tasks(content: str) -> list[Task]:
    """Parse the task list section from roadmap.md."""
    tasks = []

    # Match task entries: - [x] or - [ ] followed by **TASK_NAME**: title
    task_pattern = re.compile(
        r"-\s+\[([ xX])\]\s+\*\*(\w+)\*\*:\s*(.+?)(?:\(Current\))?\s*$",
        re.MULTILINE
    )

    # Split content into task blocks
    task_blocks = re.split(r"(?=^-\s+\[)", content, flags=re.MULTILINE)

    for block in task_blocks:
        match = task_pattern.search(block)
        if not match:
            continue

        status_char, task_name, title = match.groups()
        status = "done" if status_char.lower() == "x" else "pending"
        is_current = "(Current)" in block or "(current)" in block

        if is_current:
            status = "current"

        task = Task(
            name=task_name,
            title=title.strip(),
            status=status,
            is_current=is_current,
        )

        # Parse sub-fields
        instr_match = re.search(r"\*\*指令\*\*:\s*(.+?)(?:\n|$)", block)
        if instr_match:
            task.instructions = instr_match.group(1).strip()

        verify_match = re.search(r"\*\*驗證\*\*:\s*`(.+?)`", block)
        if verify_match:
            task.verification_cmd = verify_match.group(1).strip()

        # Context control flags
        flag_matches = re.findall(r"\[(\w+)\]", block)
        context_flags = [f for f in flag_matches if f not in (task_name, "x", "X", " ")]
        task.context_flags = context_flags

        # Branching rules
        branch_pattern = re.compile(
            r"`IF\s*\((.+?)\)`\s*->\s*(?:goto\s+)?(\w+)",
            re.IGNORECASE
        )
        for cond_match in branch_pattern.finditer(block):
            condition, target = cond_match.groups()
            task.branching_rules.append(BranchingRule(
                condition=condition.strip(),
                target_task=target.strip(),
                action="goto",
            ))

        # Spec file link (for Product mode)
        spec_match = re.search(r"specs?/(\S+\.md)", block)
        if spec_match:
            task.spec_file = spec_match.group(0)

        tasks.append(task)

    return tasks


def parse_execution_log(content: str) -> tuple[str, str]:
    """Extract the execution log section."""
    update_match = re.search(r"Last Update:\s*(.+?)$", content, re.MULTILINE)
    action_match = re.search(r"Latest Action:\s*(.+?)$", content, re.MULTILINE)
    last_update = update_match.group(1).strip() if update_match else ""
    latest_action = action_match.group(1).strip() if action_match else ""
    return last_update, latest_action


def parse_roadmap(path: Optional[Path] = None) -> RoadmapState:
    """Parse roadmap.md and return the full state."""
    roadmap_path = path or ROADMAP_PATH
    content = roadmap_path.read_text(encoding="utf-8")

    # Parse frontmatter
    fm = parse_frontmatter(content)

    # Parse tasks
    tasks = parse_tasks(content)

    # Parse execution log
    last_update, latest_action = parse_execution_log(content)

    # Build state
    state = RoadmapState(
        sys_status=SysStatus(fm.get("sys_status", "RUNNING")),
        rate_limit_resume_time=fm.get("rate_limit_resume_time"),
        current_branch=fm.get("current_branch", "main"),
        context_tokens=fm.get("context_tokens", 0),
        token_limit=fm.get("token_limit", 100000),
        tasks=tasks,
        last_update=last_update,
        latest_action=latest_action,
    )

    return state


def update_roadmap(
    path: Optional[Path] = None,
    sys_status: Optional[SysStatus] = None,
    rate_limit_resume_time: Optional[str] = None,
    context_tokens: Optional[int] = None,
    task_name: Optional[str] = None,
    task_status: Optional[str] = None,
    latest_action: Optional[str] = None,
) -> str:
    """
    Update roadmap.md in-place with new values.
    Returns the updated content string.
    """
    roadmap_path = path or ROADMAP_PATH
    content = roadmap_path.read_text(encoding="utf-8")

    # Update frontmatter values
    if sys_status is not None:
        content = re.sub(
            r'(sys_status:\s*)"?\w+"?',
            f'\\1"{sys_status.value}"',
            content,
        )

    if rate_limit_resume_time is not None:
        content = re.sub(
            r"(rate_limit_resume_time:\s*).+",
            f"\\1{rate_limit_resume_time}",
            content,
        )

    if context_tokens is not None:
        content = re.sub(
            r"(context_tokens:\s*)\d+",
            f"\\1{context_tokens}",
            content,
        )

    # Update task status
    if task_name and task_status:
        new_char = "x" if task_status == "done" else " "
        content = re.sub(
            rf"(-\s+\[)[ xX](\]\s+\*\*{task_name}\*\*)",
            f"\\g<1>{new_char}\\2",
            content,
        )

    # Update execution log
    if latest_action:
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S CST")
        content = re.sub(
            r"(Last Update:\s*).+",
            f"\\g<1>{now}",
            content,
        )
        content = re.sub(
            r"(Latest Action:\s*).+",
            f"\\g<1>{latest_action}",
            content,
        )

    roadmap_path.write_text(content, encoding="utf-8")
    return content
