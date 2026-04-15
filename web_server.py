"""
Agent Task Manager — FastAPI Web Server.

Provides REST API and WebSocket endpoints for the Web UI.
Run with: python web_server.py
Accessible from mobile via: http://<computer-ip>:7878
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import RPATarget, SysStatus, WorkflowMode
from roadmap_parser import parse_roadmap, update_roadmap
from results_log_manager import read_results_log

# --- Setup ---
BASE_DIR = Path(__file__).parent.resolve()
PROJECTS_CONFIG_PATH = BASE_DIR / "projects_config.json"
LOG_FILE = BASE_DIR / "orchestrator.log"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("web_server")

app = FastAPI(title="Agent Task Manager", version="1.0.0")

# Mount static files
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# --- In-memory state ---
_orchestrator_process: Optional[subprocess.Popen] = None
_log_subscribers: list[WebSocket] = []


# --- Pydantic Models ---
class ProjectConfig(BaseModel):
    id: str
    name: str
    description: str = ""
    roadmap_path: str
    results_log_path: str
    obsidian_vault_path: Optional[str] = None
    git_enabled: bool = True
    git_remote: str = "origin"
    git_default_branch: str = "main"
    workflow_mode: str = "creative"
    rpa_target: str = "cli"
    cli_command: str = "claude"
    gui_target: str = "vscode"
    active: bool = False


class TaskUpdate(BaseModel):
    instructions: Optional[str] = None
    verification_cmd: Optional[str] = None
    status: Optional[str] = None


class OrchestratorStartRequest(BaseModel):
    project_id: str
    mode: str = "creative"
    target: str = "cli"
    gui_target: str = "vscode"
    cli_command: str = "claude"
    dry_run: bool = False


class RoadmapUpdate(BaseModel):
    sys_status: Optional[str] = None
    latest_action: Optional[str] = None
    context_tokens: Optional[int] = None


# --- Helper functions ---
def load_projects_config() -> dict:
    """Load projects_config.json."""
    if not PROJECTS_CONFIG_PATH.exists():
        return {"projects": [], "active_project_id": None}
    with open(PROJECTS_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_projects_config(config: dict) -> None:
    """Save updated config to projects_config.json."""
    with open(PROJECTS_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def get_project_by_id(project_id: str) -> Optional[dict]:
    """Find a project config by ID."""
    config = load_projects_config()
    return next((p for p in config["projects"] if p["id"] == project_id), None)


def get_git_info(repo_path: str) -> dict:
    """Get basic git status for a project path."""
    try:
        from git import Repo, InvalidGitRepositoryError
        repo = Repo(Path(repo_path).parent)
        branch = repo.active_branch.name
        commits = list(repo.iter_commits(max_count=1))
        last_commit = commits[0].hexsha[:8] if commits else "N/A"
        is_dirty = repo.is_dirty()
        return {
            "branch": branch,
            "last_commit": last_commit,
            "is_dirty": is_dirty,
            "git_ok": True,
        }
    except Exception:
        return {"branch": "N/A", "last_commit": "N/A", "is_dirty": False, "git_ok": False}


# --- API Routes ---

@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main UI."""
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return FileResponse(str(html_path))
    return HTMLResponse("<h1>Agent Task Manager</h1><p>Run setup to create static files.</p>")


@app.get("/api/projects")
async def list_projects():
    """List all configured projects with their Git status."""
    config = load_projects_config()
    projects = []
    for p in config.get("projects", []):
        roadmap_path = Path(p.get("roadmap_path", ""))
        git_info = get_git_info(p["roadmap_path"]) if roadmap_path.exists() else {}
        
        roadmap_data = {}
        if roadmap_path.exists():
            try:
                roadmap = parse_roadmap(roadmap_path)
                tasks = roadmap.tasks
                done = sum(1 for t in tasks if t.status == "done")
                total = len(tasks)
                current_task = roadmap.current_task
                roadmap_data = {
                    "sys_status": roadmap.sys_status.value,
                    "current_branch": roadmap.current_branch,
                    "tasks_done": done,
                    "tasks_total": total,
                    "current_task": current_task.name if current_task else None,
                    "last_update": roadmap.last_update,
                }
            except Exception as e:
                roadmap_data = {"error": str(e)}

        projects.append({
            **p,
            "git": git_info,
            "roadmap": roadmap_data,
            "roadmap_exists": roadmap_path.exists(),
            "is_active": p["id"] == config.get("active_project_id"),
        })

    return {"projects": projects, "active_project_id": config.get("active_project_id")}


@app.get("/api/project/{project_id}")
async def get_project(project_id: str):
    """Get full details for a single project."""
    project = get_project_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    return project


@app.post("/api/project/{project_id}/activate")
async def activate_project(project_id: str):
    """Set the active project."""
    config = load_projects_config()
    ids = [p["id"] for p in config["projects"]]
    if project_id not in ids:
        raise HTTPException(status_code=404, detail="Project not found")
    
    config["active_project_id"] = project_id
    for p in config["projects"]:
        p["active"] = (p["id"] == project_id)
    save_projects_config(config)
    return {"success": True, "active_project_id": project_id}


@app.post("/api/projects")
async def create_project(project: ProjectConfig):
    """Add a new project configuration."""
    config = load_projects_config()
    existing_ids = [p["id"] for p in config["projects"]]
    if project.id in existing_ids:
        raise HTTPException(status_code=409, detail=f"Project ID '{project.id}' already exists")
    
    config["projects"].append(project.dict())
    save_projects_config(config)
    return {"success": True, "project_id": project.id}


@app.put("/api/projects/{project_id}")
async def update_project(project_id: str, project: ProjectConfig):
    """Update an existing project configuration."""
    config = load_projects_config()
    for i, p in enumerate(config["projects"]):
        if p["id"] == project_id:
            config["projects"][i] = project.dict()
            save_projects_config(config)
            return {"success": True}
    raise HTTPException(status_code=404, detail="Project not found")


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    """Remove a project configuration."""
    config = load_projects_config()
    config["projects"] = [p for p in config["projects"] if p["id"] != project_id]
    if config.get("active_project_id") == project_id:
        config["active_project_id"] = config["projects"][0]["id"] if config["projects"] else None
    save_projects_config(config)
    return {"success": True}


@app.get("/api/project/{project_id}/roadmap")
async def get_roadmap(project_id: str):
    """Get parsed roadmap for a project."""
    project = get_project_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    roadmap_path = Path(project["roadmap_path"])
    if not roadmap_path.exists():
        raise HTTPException(status_code=404, detail="roadmap.md not found")
    
    try:
        roadmap = parse_roadmap(roadmap_path)
        tasks = []
        for t in roadmap.tasks:
            tasks.append({
                "name": t.name,
                "title": t.title,
                "status": t.status,
                "is_current": t.is_current,
                "instructions": t.instructions,
                "verification_cmd": t.verification_cmd,
                "context_flags": t.context_flags,
                "branching_rules": [
                    {"condition": r.condition, "target_task": r.target_task, "action": r.action}
                    for r in t.branching_rules
                ],
                "spec_file": t.spec_file,
            })
        
        return {
            "sys_status": roadmap.sys_status.value,
            "rate_limit_resume_time": roadmap.rate_limit_resume_time,
            "current_branch": roadmap.current_branch,
            "context_tokens": roadmap.context_tokens,
            "token_limit": roadmap.token_limit,
            "last_update": roadmap.last_update,
            "latest_action": roadmap.latest_action,
            "tasks": tasks,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/project/{project_id}/roadmap")
async def patch_roadmap(project_id: str, update: RoadmapUpdate):
    """Update sys_status or latest_action in roadmap."""
    project = get_project_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    roadmap_path = Path(project["roadmap_path"])
    kwargs: dict[str, Any] = {}
    if update.sys_status:
        kwargs["sys_status"] = SysStatus(update.sys_status)
    if update.latest_action:
        kwargs["latest_action"] = update.latest_action
    if update.context_tokens is not None:
        kwargs["context_tokens"] = update.context_tokens
    
    update_roadmap(path=roadmap_path, **kwargs)
    return {"success": True}


@app.patch("/api/project/{project_id}/task/{task_name}")
async def update_task(project_id: str, task_name: str, update: TaskUpdate):
    """Update a task's status in roadmap.md."""
    project = get_project_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    roadmap_path = Path(project["roadmap_path"])
    if update.status:
        update_roadmap(
            path=roadmap_path,
            task_name=task_name,
            task_status=update.status,
            latest_action=f"Task {task_name} manually set to {update.status}",
        )
    return {"success": True}


@app.get("/api/project/{project_id}/results")
async def get_results(project_id: str):
    """Get results_log.md content for a project."""
    project = get_project_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    results_path = Path(project.get("results_log_path", ""))
    return read_results_log(results_path)


@app.get("/api/orchestrator/status")
async def orchestrator_status():
    """Get current orchestrator process status."""
    global _orchestrator_process
    if _orchestrator_process is None:
        return {"running": False, "pid": None}
    
    running = _orchestrator_process.poll() is None
    return {
        "running": running,
        "pid": _orchestrator_process.pid if running else None,
        "returncode": _orchestrator_process.returncode,
    }


@app.post("/api/orchestrator/start")
async def start_orchestrator(req: OrchestratorStartRequest):
    """Start the orchestrator as a subprocess."""
    global _orchestrator_process
    
    if _orchestrator_process and _orchestrator_process.poll() is None:
        return {"success": False, "message": "Orchestrator is already running"}
    
    project = get_project_by_id(req.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    cmd = [
        sys.executable,
        str(BASE_DIR / "orchestrator.py"),
        "--mode", req.mode,
        "--target", req.target,
        "--gui-target", req.gui_target,
        "--cli-command", req.cli_command,
    ]
    if req.dry_run:
        cmd.append("--dry-run")
    
    try:
        _orchestrator_process = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        
        # Start background thread to broadcast log lines via WebSocket
        threading.Thread(
            target=_stream_process_output,
            args=(_orchestrator_process,),
            daemon=True,
        ).start()
        
        return {"success": True, "pid": _orchestrator_process.pid}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/orchestrator/pause")
async def pause_orchestrator():
    """Signal the orchestrator to pause (sets PAUSED in roadmap)."""
    config = load_projects_config()
    active_id = config.get("active_project_id")
    if not active_id:
        raise HTTPException(status_code=400, detail="No active project")
    
    project = get_project_by_id(active_id)
    if project:
        roadmap_path = Path(project["roadmap_path"])
        update_roadmap(
            path=roadmap_path,
            sys_status=SysStatus.PAUSED,
            latest_action="Paused by UI",
        )
    return {"success": True}


@app.post("/api/orchestrator/stop")
async def stop_orchestrator():
    """Terminate the orchestrator process."""
    global _orchestrator_process
    if _orchestrator_process and _orchestrator_process.poll() is None:
        _orchestrator_process.terminate()
        return {"success": True, "message": "Orchestrator terminated"}
    return {"success": False, "message": "No running orchestrator"}


@app.get("/api/logs/recent")
async def get_recent_logs(lines: int = 100):
    """Get the last N lines from orchestrator.log."""
    if not LOG_FILE.exists():
        return {"lines": []}
    
    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            all_lines = f.readlines()
        recent = all_lines[-lines:] if len(all_lines) > lines else all_lines
        return {"lines": [l.rstrip() for l in recent]}
    except Exception as e:
        return {"lines": [], "error": str(e)}


# --- WebSocket for Live Logs ---

@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    """WebSocket endpoint: pushes live log lines to the client."""
    await websocket.accept()
    _log_subscribers.append(websocket)
    
    # Send recent logs on connect
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE, encoding="utf-8") as f:
                recent = f.readlines()[-50:]
            for line in recent:
                await websocket.send_json({"type": "log", "line": line.rstrip(), "timestamp": ""})
        except Exception:
            pass
    
    try:
        while True:
            # Keep connection alive by waiting for client ping
            data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        if websocket in _log_subscribers:
            _log_subscribers.remove(websocket)


async def _broadcast_log(line: str):
    """Send a log line to all connected WebSocket clients."""
    dead = []
    for ws in _log_subscribers:
        try:
            await ws.send_json({
                "type": "log",
                "line": line.rstrip(),
                "timestamp": datetime.now().strftime("%H:%M:%S"),
            })
        except Exception:
            dead.append(ws)
    for ws in dead:
        _log_subscribers.remove(ws)


def _stream_process_output(process: subprocess.Popen):
    """Background thread: reads orchestrator stdout and broadcasts via WebSocket."""
    loop = asyncio.new_event_loop()
    for line in iter(process.stdout.readline, ""):
        if line:
            loop.run_until_complete(_broadcast_log(line))
    loop.close()


# --- File watcher for log tailing (Obsidian-friendly) ---

def _watch_log_file():
    """Background thread: tail orchestrator.log and broadcast new lines."""
    log_pos = 0
    if LOG_FILE.exists():
        log_pos = LOG_FILE.stat().st_size

    loop = asyncio.new_event_loop()
    while True:
        try:
            import time
            time.sleep(0.5)
            if not LOG_FILE.exists():
                continue
            current_size = LOG_FILE.stat().st_size
            if current_size > log_pos:
                with open(LOG_FILE, encoding="utf-8") as f:
                    f.seek(log_pos)
                    new_lines = f.readlines()
                    log_pos = f.tell()
                for line in new_lines:
                    if line.strip():
                        loop.run_until_complete(_broadcast_log(line))
        except Exception:
            pass


# Start log watcher background thread
threading.Thread(target=_watch_log_file, daemon=True).start()


if __name__ == "__main__":
    config = load_projects_config()
    server_cfg = config.get("web_server", {})
    host = server_cfg.get("host", "0.0.0.0")
    port = server_cfg.get("port", 7878)
    auto_open = server_cfg.get("auto_open_browser", True)

    if auto_open:
        import webbrowser, time
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()

    print(f"\n>> Agent Task Manager UI running at:")
    print(f"   Local:   http://127.0.0.1:{port}")
    print(f"   Network: http://0.0.0.0:{port}  (accessible from mobile on same WiFi)\n")

    uvicorn.run(app, host=host, port=port, reload=False)
