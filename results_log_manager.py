"""
Results Log Manager for the GitOps AI Orchestrator.

Writes task completion results to results_log.md in each project's directory.
This file is synced via Obsidian Sync (primary) or Git (optional), allowing
mobile devices to read the workflow outcomes.

results_log.md format:
  ---
  project: "project_name"
  last_updated: "2026-04-16 04:00:00 CST"
  total_tasks: 5
  completed_tasks: 2
  ---

  # Workflow Results Log

  ## ✅ TASK_A — 建立基礎演算法模組
  - **Completed**: 2026-04-16 03:30:00 CST
  - **Branch**: feature/audio-module
  - **Commit**: abc1234
  - **Validation**: make test_init → PASSED
  - **Notes**: (optional notes from AI)

  ## ▶ TASK_B — 實作核心運算邏輯 [IN PROGRESS]
  - **Started**: 2026-04-16 03:50:00 CST
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

RESULTS_LOG_FILENAME = "results_log.md"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S CST")


def _status_icon(status: str) -> str:
    icons = {
        "done": "✅",
        "in_progress": "▶",
        "failed": "❌",
        "skipped": "⏭",
        "pending": "○",
    }
    return icons.get(status, "○")


def ensure_results_log(results_log_path: Path, project_name: str) -> None:
    """Create results_log.md with initial structure if it doesn't exist."""
    if results_log_path.exists():
        return

    content = f"""---
project: "{project_name}"
last_updated: "{_now()}"
total_tasks: 0
completed_tasks: 0
---

# Workflow Results Log

> 此檔案由 AI Orchestrator 自動更新，記錄工作流執行成果。
> 可透過 Obsidian Sync 在手機端查看最新工作進度。
> Last Updated: {_now()}

"""
    results_log_path.write_text(content, encoding="utf-8")
    logger.info(f"Created results_log.md at: {results_log_path}")


def log_task_started(
    results_log_path: Path,
    task_name: str,
    task_title: str,
    project_name: str = "",
) -> None:
    """
    Mark a task as in-progress in results_log.md.
    If the task section doesn't exist yet, append it.
    """
    ensure_results_log(results_log_path, project_name)
    content = results_log_path.read_text(encoding="utf-8")

    section_header = f"## {_status_icon('in_progress')} {task_name} — {task_title}"
    started_line = f"- **Started**: {_now()}"

    # Check if the task section already exists
    if f"## " + task_name in content or task_name + " —" in content:
        # Update status icon if present
        content = re.sub(
            rf"##\s+[✅▶❌⏭○]\s+{re.escape(task_name)}",
            f"## {_status_icon('in_progress')} {task_name}",
            content,
        )
    else:
        # Append new section
        content += f"""
---

{section_header}
{started_line}
"""

    _update_frontmatter(content, results_log_path)
    logger.info(f"Logged task start: {task_name}")


def log_task_completed(
    results_log_path: Path,
    task_name: str,
    task_title: str,
    branch: str = "",
    commit_hash: str = "",
    validation_cmd: str = "",
    validation_passed: bool = True,
    validation_output: str = "",
    notes: str = "",
    project_name: str = "",
) -> None:
    """
    Mark a task as completed in results_log.md with full details.
    """
    ensure_results_log(results_log_path, project_name)
    content = results_log_path.read_text(encoding="utf-8")

    validation_status = "✅ PASSED" if validation_passed else "❌ FAILED"
    short_output = validation_output[:200].replace("\n", " ") if validation_output else ""

    new_section = f"""
---

## {_status_icon('done')} {task_name} — {task_title}
- **Completed**: {_now()}
- **Branch**: `{branch}`
- **Commit**: `{commit_hash[:8] if commit_hash else 'N/A'}`
- **Validation**: `{validation_cmd}` → {validation_status}
{f'- **Output**: {short_output}' if short_output else ''}
{f'- **Notes**: {notes}' if notes else ''}
"""

    # Replace existing section or append
    pattern = rf"---\s*\n\s*##\s+[✅▶❌⏭○]\s+{re.escape(task_name)}.*?(?=\n---|\Z)"
    if re.search(pattern, content, re.DOTALL):
        content = re.sub(pattern, new_section.strip(), content, flags=re.DOTALL)
    else:
        content += new_section

    content = _update_frontmatter_str(content)
    results_log_path.write_text(content, encoding="utf-8")
    logger.info(f"Logged task completion: {task_name}")


def log_task_failed(
    results_log_path: Path,
    task_name: str,
    task_title: str,
    error: str = "",
    project_name: str = "",
) -> None:
    """Mark a task as failed in results_log.md."""
    ensure_results_log(results_log_path, project_name)
    content = results_log_path.read_text(encoding="utf-8")

    new_section = f"""
---

## {_status_icon('failed')} {task_name} — {task_title}
- **Failed at**: {_now()}
- **Error**: {error[:300] if error else 'Unknown error'}
"""

    pattern = rf"---\s*\n\s*##\s+[✅▶❌⏭○]\s+{re.escape(task_name)}.*?(?=\n---|\Z)"
    if re.search(pattern, content, re.DOTALL):
        content = re.sub(pattern, new_section.strip(), content, flags=re.DOTALL)
    else:
        content += new_section

    content = _update_frontmatter_str(content)
    results_log_path.write_text(content, encoding="utf-8")
    logger.info(f"Logged task failure: {task_name}")


def _update_frontmatter_str(content: str) -> str:
    """Update the last_updated timestamp in frontmatter."""
    content = re.sub(
        r'(last_updated:\s*")[^"]*(")',
        rf'\g<1>{_now()}\g<2>',
        content,
    )
    content = re.sub(
        r"(Last Updated:\s*).+",
        rf"\g<1>{_now()}",
        content,
    )
    # Count completed tasks
    completed = len(re.findall(r"## ✅", content))
    in_progress = len(re.findall(r"## ▶", content))
    total = completed + in_progress + len(re.findall(r"## ❌", content))

    content = re.sub(r"(completed_tasks:\s*)\d+", rf"\g<1>{completed}", content)
    content = re.sub(r"(total_tasks:\s*)\d+", rf"\g<1>{total}", content)
    return content


def _update_frontmatter(content: str, path: Path) -> None:
    """Update frontmatter and write to file."""
    updated = _update_frontmatter_str(content)
    path.write_text(updated, encoding="utf-8")


def read_results_log(results_log_path: Path) -> dict:
    """
    Parse results_log.md and return structured data for the UI.
    Returns a dict with frontmatter fields and a list of task results.
    """
    if not results_log_path.exists():
        return {"exists": False, "tasks": [], "project": "", "last_updated": ""}

    content = results_log_path.read_text(encoding="utf-8")

    # Parse frontmatter
    fm_match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    frontmatter = {}
    if fm_match:
        import yaml
        try:
            frontmatter = yaml.safe_load(fm_match.group(1)) or {}
        except Exception:
            pass

    # Parse task sections
    tasks = []
    task_pattern = re.compile(
        r"##\s+([✅▶❌⏭○])\s+(\w+)\s+—\s+(.+?)\n(.*?)(?=\n---|\Z)",
        re.DOTALL,
    )
    for match in task_pattern.finditer(content):
        icon, name, title, body = match.groups()
        status_map = {"✅": "done", "▶": "in_progress", "❌": "failed", "⏭": "skipped", "○": "pending"}
        
        # Parse body fields
        completed_m = re.search(r"\*\*Completed\*\*:\s*(.+)", body)
        started_m = re.search(r"\*\*Started\*\*:\s*(.+)", body)
        branch_m = re.search(r"\*\*Branch\*\*:\s*`?([^`\n]+)`?", body)
        commit_m = re.search(r"\*\*Commit\*\*:\s*`?([^`\n]+)`?", body)
        validation_m = re.search(r"\*\*Validation\*\*:\s*(.+)", body)
        notes_m = re.search(r"\*\*Notes\*\*:\s*(.+)", body)

        tasks.append({
            "name": name,
            "title": title.strip(),
            "status": status_map.get(icon, "pending"),
            "icon": icon,
            "completed": completed_m.group(1).strip() if completed_m else None,
            "started": started_m.group(1).strip() if started_m else None,
            "branch": branch_m.group(1).strip() if branch_m else None,
            "commit": commit_m.group(1).strip() if commit_m else None,
            "validation": validation_m.group(1).strip() if validation_m else None,
            "notes": notes_m.group(1).strip() if notes_m else None,
        })

    return {
        "exists": True,
        "project": frontmatter.get("project", ""),
        "last_updated": frontmatter.get("last_updated", ""),
        "total_tasks": frontmatter.get("total_tasks", len(tasks)),
        "completed_tasks": frontmatter.get("completed_tasks", 0),
        "tasks": tasks,
    }
