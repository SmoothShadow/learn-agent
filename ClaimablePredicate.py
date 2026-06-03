from dataclasses import dataclass
from pathlib import Path
import threading
import time
import json


@dataclass
class ClaimablePredicateConfig:
    work_dir: Path


class ClaimablePredicate:
    def __init__(self, config: ClaimablePredicateConfig):
        self.config = config
        self.lock = threading.RLock()

    def _is_claimable_task(self, task: dict, role: str) -> bool:
        return (
            task.get("status") == "pending"
            and not task.get("blocked_by")
            and not task.get("owner")
            and self._task_allow_role(task, role)
        )

    def _task_allow_role(self, task: dict, role: str) -> bool:
        return not task.get("allowed_roles") or role in task.get("allowed_roles", [])

    def claim_task(self, name: str, role: str) -> dict | None:
        with self.lock:
            tasks = list(self.config.work_dir.glob("*.json"))
            for task_file in tasks:
                task = json.loads(task_file.read_text())
                if self._is_claimable_task(task, role):
                    task["owner"] = name
                    task["status"] = "in_progress"
                    task["claimed_at"] = time.time()
                    task["claim_source"] = "auto"
                    task_file.write_text(json.dumps(task), ensure_ascii=False, indent=2)
                    self.update_claim_record(task, role, "claimed", "auto")
                    return task
            return None

    def update_claim_record(
        self, task: dict, name: str, status: str, claim_source: str
    ):
        if not (self.config.work_dir / "claimable.jsonl").exists():
            (self.config.work_dir / "claimable.jsonl").touch()
        with self.lock:
            with open(self.config.work_dir / "claimable.jsonl", "a") as f:
                record = {
                    "task_id": task.get("id"),
                    "owner": name,
                    "status": status,
                    "claimed_at": time.time(),
                    "claim_source": claim_source,
                }
                f.write(f"{record}\n")
