from pathlib import Path
from dataclasses import dataclass
import subprocess
import threading
import json
import time


@dataclass
class WorkTreesIsolationConfig:
    work_dir: Path
    task_dir: Path


class WorkTreesIsolation:
    def __init__(self, config: WorkTreesIsolationConfig) -> None:
        self.config = config
        self.dir = config.work_dir / ".worktrees"
        self.dir.mkdir(parents=True, exist_ok=True)
        file_path = self.dir / "records.json"
        if not file_path.exists():
            file_path.write_text("{}", encoding="utf-8")
        self.lock = threading.RLock()

    def create_worktree(self, name: str, task_id: str):
        path = self.dir / name
        if self._check_worktree_exists(path):
            return f"worktree {name} is already exist"
        if self._check_record_exists(task_id):
            return f"task {task_id} already has a worktree"
        branch = f"wt/{name}"
        if self._check_branch_exists(branch):
            return f"branch {branch} already exists"
        self._run_git(["worktree", "add", "-b", branch, str(path), "HEAD"])
        record = {
            "name": name,
            "path": str(path),
            "branch": branch,
            "status": "active",
            "task_id": task_id,
        }

        self._save_record(record, task_id)
        self._update_task(task_id, path, branch)

        return record

    def run_bash(self, command: list, task_id: str):
        if not self._check_record_exists(task_id):
            return f"task {task_id} does not have a worktree"
        with self.lock:
            task_path = self.config.task_dir / f"{task_id}.json"
            task = json.loads(task_path.read_text())
            task["last_entered_at"] = time.time()
            task["last_command_at"] = time.time()
            task["last_command_preview"] = " ".join(command)
            with open(task_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(task, indent=2))
        cwd = task["worktree"]
        result = subprocess.run(
            command, cwd=cwd, check=True, capture_output=True, text=True
        )

        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }

    def closeout_worktree(self, task_id: str, action: str, reason: str):
        with self.lock:
            if action == "remove" and self._has_dirty_changes(task_id)["stdout"]:
                return f"Task {task_id} has uncommitted changes, not removing worktree"
            task_path = self.config.task_dir / f"{task_id}.json"
            task = json.loads(task_path.read_text())
            task["worktree_state"] = "kept" if action == "keep" else "removed"
            task["closeout"] = {"action": action, "reason": reason, "at": time.time()}
            if task["status"] == "in_progress":
                task["status"] = "completed"
            with open(task_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(task, indent=2))
            records_path = self.dir / "records.json"
            records = json.loads(records_path.read_text())
            records[task_id]["status"] = "kept" if action == "keep" else "removed"
            records[task_id]["closeout_at"] = time.time()
            with open(records_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(records, indent=2))
            if action == "remove":
                args = ["worktree", "remove"]
                args.append(records[task_id]["path"])
                self._run_git(args)
                return f"Task {task_id} worktree removed successfully"
            return f"Task {task_id} updated successfully"

    def _has_dirty_changes(self, task_id: str) -> bool:
        if not self._check_record_exists(task_id):
            return f"task {task_id} does not have a worktree"
        task_path = self.config.task_dir / f"{task_id}.json"
        task = json.loads(task_path.read_text())
        cwd = task["worktree"]
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }

    def _check_worktree_exists(self, path: Path) -> bool:
        result = self._run_git(["worktree", "list"])["stdout"]
        return result and str(path) in result

    def _check_record_exists(self, task_id: str) -> bool:
        records_path = self.dir / "records.json"
        records = json.loads(records_path.read_text())
        return records and task_id in records

    def _check_branch_exists(self, branch: str) -> bool:
        result = self._run_git(["branch", "--list", branch])["stdout"]
        return result and branch in result

    def _run_git(self, command: list):
        try:
            result = subprocess.run(
                ["git", *command], check=True, capture_output=True, text=True
            )
        except subprocess.CalledProcessError as e:
            return {
                "stdout": e.stdout,
                "stderr": e.stderr,
                "returncode": e.returncode,
            }
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }

    def _save_record(self, record: dict, task_id: str) -> None:
        with self.lock:
            records_path = self.dir / "records.json"
            records = json.loads(records_path.read_text())
            records[task_id] = record
            with open(records_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(records, indent=2))

    def _update_task(self, task_id: str, worktree_path: Path, branch: str):
        with self.lock:
            task_path = self.config.task_dir / f"{task_id}.json"
            task = json.loads(task_path.read_text())
            task["last_worktree"] = task.get("worktree", None)
            task["worktree"] = str(worktree_path)
            task["branch"] = branch
            task["worktree_state"] = "active"
            task["closeout"] = None
            if task["status"] == "pending":
                task["status"] = "in_progress"
            with open(task_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(task, indent=2))
