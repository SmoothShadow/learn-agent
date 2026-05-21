import re
from dataclasses import dataclass


@dataclass
class BashSecurityValidatorConfig:
    validators: list[tuple[str, str]]


# -- Bash security validation --
class BashSecurityValidator:
    """
    Validate bash commands for obviously dangerous patterns.

    The teaching version deliberately keeps this small and easy to read.
    First catch a few high-risk patterns, then let the permission pipeline
    decide whether to deny or ask the user.
    """

    def __init__(self, config: BashSecurityValidatorConfig):
        self.VALIDATORS = config.validators

    def validate(self, command: str) -> list:
        """
        Check a bash command against all validators.

        Returns list of (validator_name, matched_pattern) tuples for failures.
        An empty list means the command passed all validators.
        """
        failures = []
        for name, pattern in self.VALIDATORS:
            if re.search(pattern, command):
                failures.append((name, pattern))
        return failures

    def is_safe(self, command: str) -> bool:
        """Convenience: returns True only if no validators triggered."""
        return len(self.validate(command)) == 0

    def describe_failures(self, command: str) -> str:
        """Human-readable summary of validation failures."""
        failures = self.validate(command)
        if not failures:
            return "No security issues detected."
        parts = [f"{name}(pattern:{pattern})" for name, pattern in failures]
        return "Security flags: " + ", ".join(parts)
