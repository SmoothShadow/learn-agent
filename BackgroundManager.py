from dataclasses import dataclass
import subprocess
import threading
import uuid
from pathlib import Path
import json


@dataclass
class BackgroundManagerConfig:
    work_dir: Path


class BackgroundManager:
    def __init__(self, config: BackgroundManagerConfig):
        self.config = config
        self.tasks = {}
        self.notifications = []
        self.lock = threading.Lock()

    def run(self, command: str) -> str:
        task_id = self.new_id()
        with self.lock:
            self.tasks[task_id] = {
                "id": task_id,
                "command": command,
                "status": "running",
            }
            self._update_task(task_id)
        thread = threading.Thread(
            target=self._execute, args=(task_id, command), daemon=True
        )
        thread.start()
        return task_id

    def get_notifications(self) -> list:
        with self.lock:
            notifications = self.notifications.copy()
            self.notifications.clear()
            return notifications

    def read_result(self, task_id: str) -> str:
        path = self.config.work_dir
        if not path.exists():
            return ""
        task_file = path / f"{task_id}.log"
        if not task_file.exists():
            return ""
        return task_file.read_text()

    def read_task(self, task_id: str) -> dict:
        path = self.config.work_dir
        if not path.exists():
            return {}
        task_file = path / f"{task_id}.json"
        if not task_file.exists():
            return {}
        return json.loads(task_file.read_text())

    def _update_task(self, task_id: str):
        path = self.config.work_dir
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
        task_file = path / f"{task_id}.json"
        task_file.write_text(json.dumps(self.tasks[task_id]))

    def _write_result(self, task_id: str, result: str):
        path = self.config.work_dir
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
        task_file = path / f"{task_id}.log"
        task_file.write_text(result)

    def _execute(self, task_id: str, command: str):
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=3000
            )
            self._write_result(
                task_id,
                f"Return code: {result.returncode}\nOutput: {result.stdout}\nError: {result.stderr}",
            )
            status = "completed"
            preview = (result.stdout + result.stderr)[:500]
        except subprocess.TimeoutExpired:
            status = "timeout"
            preview = "Timeout"
        except Exception as e:
            status = "error"
            preview = str(e)[:500]

        with self.lock:
            self.tasks[task_id]["status"] = status
            self._update_task(task_id)
            self.notifications.append(
                {
                    "type": "background_completed",
                    "task_id": task_id,
                    "status": status,
                    "preview": preview,
                }
            )

    @staticmethod
    def new_id() -> str:
        return str(uuid.uuid4())
