from dataclasses import dataclass
from pathlib import Path
import time
import json
import threading


@dataclass
class MessageBusConfig:
    work_dir: Path
    VALID_MSG_TYPES: set


class MessageBus:
    def __init__(self, config: MessageBusConfig):
        self.config = config
        self.work_dir = config.work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def send(
        self,
        sender: str,
        to: str,
        content: str,
        msg_type: str = "message",
        extra: dict = None,
    ):
        if msg_type not in self.config.VALID_MSG_TYPES:
            raise ValueError(f"Invalid message type: {msg_type}")
        msg = {
            "type": msg_type,
            "from": sender,
            "content": content,
            "timeStamp": time.time(),
        }
        if extra:
            msg.update(extra)
        with self.lock:
            file_path = self.work_dir / f"{to}.jsonl"
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg) + "\n")
        return f"Message sent {msg_type} to {to}"

    def read_inbox(self, name: str):
        file_path = self.work_dir / f"{name}.jsonl"
        messages = []
        if not file_path.exists():
            return messages
        with self.lock:
            for line in file_path.read_text(encoding="utf-8").strip().splitlines():
                messages.append(json.loads(line))
            file_path.write_text("")
        return messages

    def broadcast(self, sender: str, content: str, teammates: list) -> str:
        count = 0
        for name in teammates:
            if name != sender:
                self.send(sender, name, content, "broadcast")
                count += 1
        return f"Broadcast sent to {count} teammates"
