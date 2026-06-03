from dataclasses import dataclass
from pathlib import Path
import json


@dataclass
class TaskManagerConfig:
    task_dir: Path


class TaskManager:
    def __init__(self, config: TaskManagerConfig):
        self.tasks = []
        self.config = config
        if not self.config.task_dir.exists():
            self.config.task_dir.mkdir(parents=True, exist_ok=True)

    def create_tasks(self, tasks: list[dict]):
        self.tasks = []
        ids = self._get_ids(len(tasks))
        for i, task in enumerate(tasks):
            task["id"] = ids[i]
            self.create_task(task)
        self._add_dependency()
        return self.load_all()

    def create_task(self, task: dict):
        task = {
            "id": task.get("id"),
            "subject": task.get("subject", ""),
            "description": task.get("description", ""),
            "blocks": [],
            "blocked_by": [],
            "owner": task.get("owner", ""),
            "status": task.get("status", "pending"),
            "depends_on_subjects": task.get("depends_on_subjects", []),
            "allowed_roles": task.get("allowed_roles"),
        }
        file_path = self.config.task_dir / f"{task.get('id')}.json"
        file_path.write_text(json.dumps(task, ensure_ascii=False, indent=2))
        self.tasks.append(task)
        return f"Task created with ID: {task['id']}"

    def _add_dependency(self):
        for task in self.tasks:
            task_file = self.config.task_dir / f"{task['id']}.json"
            task_content = json.loads(task_file.read_text())
            for block in task["depends_on_subjects"]:
                block_id = next(
                    (t["id"] for t in self.tasks if t["subject"] == block), None
                )
                if block_id is None:
                    print(f"Block task {block} not found")
                    continue
                task_content["blocked_by"].append(block_id)
                block_file = self.config.task_dir / f"{block_id}.json"
                if block_file.exists():
                    block_task = json.loads(block_file.read_text())
                    block_task["blocks"].append(task["id"])
                    block_file.write_text(
                        json.dumps(block_task, ensure_ascii=False, indent=2)
                    )
            task_file.write_text(json.dumps(task_content, ensure_ascii=False, indent=2))
            # return f"Dependency added to task with ID: {task['id']}"

    def update_task(self, task_id: int, status: str, name: str):
        task_file = self.config.task_dir / f"{task_id}.json"
        if not task_file.exists():
            return f"Task with ID {task_id} does not exist"
        task = json.loads(task_file.read_text())
        if task["owner"] and task["owner"] != name:
            return f"Task with ID {task_id} is owned by {task['owner']}"
        if task["status"] == status:
            return f"Task with ID {task_id} is already {status}"
        task["status"] = status
        task_file.write_text(json.dumps(task, ensure_ascii=False, indent=2))
        if status == "completed":
            for block in task["blocks"]:
                block_file = self.config.task_dir / f"{block}.json"
                if block_file.exists():
                    block_task = json.loads(block_file.read_text())
                    try:
                        block_task["blocked_by"].remove(task_id)
                    except ValueError:
                        print(f"Task {task_id} not found in blocked_by of {block}")
                        continue
                    block_file.write_text(
                        json.dumps(block_task, ensure_ascii=False, indent=2)
                    )
        return f"Task with ID {task_id} updated to {status}"

    def get_task(self, task_id) -> dict | None:
        tasks = self.load_all()
        for task in tasks:
            if task.get("id") == task_id:
                return task
        return None

    def load_all(self) -> list[dict]:
        json_files = list(self.config.task_dir.glob("*.json"))
        tasks: list[dict] = []
        for file in json_files:
            tasks.append(json.loads(file.read_text()))
        return tasks

    def clear_all(self):
        json_files = list(self.config.task_dir.glob("*.json"))
        for file in json_files:
            file.unlink()

    def is_ready(self, task_id: int) -> bool:
        task_file = self.config.task_dir / f"{task_id}.json"
        if not task_file.exists():
            return False
        task = json.loads(task_file.read_text())
        return len(task["blocked_by"]) == 0 and task["status"] == "pending"

    def _get_ids(self, count: int) -> list[int]:
        json_files = list(self.config.task_dir.glob("*.json"))
        if not json_files:
            return list(range(1, count + 1))
        max_id = max(int(json.loads(file.read_text())["id"]) for file in json_files)
        return list(
            range(
                max_id + 1,
                max_id + count + 1,
            )
        )
