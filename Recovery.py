from dataclasses import dataclass
from Compact import Compactor
import random
import time


@dataclass
class RecoveryConfig:
    backoff_base_delay: float = 1.0
    backoff_max_delay: float = 60.0
    max_continue_attempt: int = 3
    max_compact_attempt: int = 3
    max_retry_attempt: int = 3
    continue_message: str = (
        "Output limit hit. Continue directly from where you stopped -- no recap, no repetition. Pick up mid-sentence if needed."
    )


class Recovery:
    def __init__(self, config: RecoveryConfig):
        self.continue_message = config.continue_message
        self.backoff_base_delay = config.backoff_base_delay
        self.backoff_max_delay = config.backoff_max_delay
        self.max_continue_attempt = config.max_continue_attempt
        self.max_compact_attempt = config.max_compact_attempt
        self.max_retry_attempt = config.max_retry_attempt
        self.continue_attempt = 0
        self.compact_attempt = 0
        self.retry_attempt = 0

    def choose_recovery(self, stop_reason: str, error_text: str) -> dict:
        if stop_reason == "max_tokens":
            if self.continue_attempt >= self.max_continue_attempt:
                return {"kind": "fail", "reason": "max compact attempt reached"}
            else:
                self.continue_attempt += 1
                return {"kind": "continue", "reason": "limit tokens"}

        if error_text and "prompt" in error_text and "long" in error_text:
            if self.compact_attempt >= self.max_compact_attempt:
                return {"kind": "fail", "reason": "max compact attempt reached"}
            else:
                self.compact_attempt += 1
                return {"kind": "compact", "reason": "prompt too long"}

        if error_text and any(
            word in error_text
            for word in ["timeout", "rate", "unavailable", "connection"]
        ):
            if self.retry_attempt >= self.max_retry_attempt:
                return {"kind": "fail", "reason": "max retry attempt reached"}
            else:
                self.retry_attempt += 1
                return {"kind": "retry", "reason": "something went wrong on internet"}
        return {"kind": "fail", "reason": "unknown error"}

    def recovery_handler(
        self, recovery_type: str, messages: list, compact: Compactor, e: str
    ):
        if recovery_type == "continue":
            messages.append({"role": "user", "content": self.continue_message})
            return messages
        elif recovery_type == "compact":
            return compact.auto_compact(messages)
        elif recovery_type == "retry":
            time.sleep(self.backoff_delay(self.retry_attempt))
            return f"[Error] Connection failed after {self.retry_attempt} retrying after error: {e}"
        else:
            return {"kind": "fail", "reason": "unknown error"}

    def reset_attempt(self):
        self.continue_attempt = 0
        self.compact_attempt = 0
        self.retry_attempt = 0

    def backoff_delay(self, attempt: int) -> float:
        """Exponential backoff with jitter: base * 2^attempt + random(0, 1)."""
        delay = min(self.backoff_base_delay * (2**attempt), self.backoff_max_delay)
        jitter = random.uniform(0, 1)
        return delay + jitter
