from dataclasses import dataclass
from pathlib import Path
import json
import uuid
from MessageBus import MessageBus
import threading


@dataclass
class AgreementStoreConfig:
    work_dir: Path
    bus: MessageBus


class AgreementStore:
    def __init__(self, config: AgreementStoreConfig) -> None:
        self.config = config
        self.requests = {}
        self.lock = threading.Lock()
        if not self.config.work_dir.exists():
            self.config.work_dir.mkdir(parents=True, exist_ok=True)
        self._load_all()

    def _load_all(self):
        with self.lock:
            for file in self.config.work_dir.glob("*.json"):
                request = json.loads(file.read_text())
                self.requests[request["request_id"]] = request

    def request_shutdown(self, target: str):
        request_id = self.new_id()
        request = {
            "request_id": request_id,
            "kind": "shutdown",
            "to": target,
            "from": "lead",
            "status": "pending",
        }
        file_path = self.config.work_dir / f"{request_id}.json"
        with self.lock:
            file_path.write_text(json.dumps(request, indent=2), encoding="utf-8")
        self.config.bus.send(
            "lead",
            target,
            "please shutdown gracefully",
            "shutdown_request",
            {"request_id": request_id},
        )

    def response_shutdown(
        self, request_id: str, approve: bool, origin: str, response: str
    ):
        file_path = self.config.work_dir / f"{request_id}.json"
        if not file_path.exists():
            return f"{request_id} not found"
        with self.lock:
            request = json.loads(file_path.read_text())
            request["status"] = "approved" if approve else "rejected"
            file_path.write_text(json.dumps(request, indent=2), encoding="utf-8")
        self.config.bus.send(
            origin, "lead", response, "shutdown_response", {"request_id": request_id}
        )

    def request_plan(self, origin: str, plan: str):
        request_id = self.new_id()
        request = {
            "request_id": request_id,
            "kind": "plan",
            "to": "lead",
            "from": origin,
            "status": "pending",
            "plan": plan,
        }
        file_path = self.config.work_dir / f"{request_id}.json"
        with self.lock:
            file_path.write_text(json.dumps(request, indent=2), encoding="utf-8")
        self.config.bus.send(
            origin,
            "lead",
            "application for implementation plan",
            "plan_approval",
            {"request_id": request_id},
        )

    def response_plan(self, request_id: str, approve: bool, target: str, response: str):
        file_path = self.config.work_dir / f"{request_id}.json"
        if not file_path.exists():
            return f"{request_id} not found"
        with self.lock:
            request = json.loads(file_path.read_text())
            request["status"] = "approved" if approve else "rejected"
            file_path.write_text(json.dumps(request, indent=2), encoding="utf-8")
        self.config.bus.send(
            "lead",
            target,
            response,
            "plan_approval_response",
            {"request_id": request_id},
        )

    def get_request(self, request_id: str) -> dict:
        file_path = self.config.work_dir / f"{request_id}.json"
        if not file_path.exists():
            return f"{request_id} not found"
        with self.lock:
            return json.loads(file_path.read_text())

    @staticmethod
    def new_id() -> str:
        return str(uuid.uuid4())
