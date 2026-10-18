from __future__ import annotations


class ExternalCallError(RuntimeError):
    code = "EXTERNAL_CALL_ERROR"

    def __init__(
        self,
        message: str = "",
        *,
        reason_code: str | None = None,
        diagnostics: dict[str, str | int | None] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.diagnostics = diagnostics or {}


class ProviderCallError(ExternalCallError):
    code = "PROVIDER_ERROR"


class RateLimitedError(ExternalCallError):
    code = "RATE_LIMITED"


class InvalidProviderResponseError(ExternalCallError):
    code = "INVALID_RESPONSE"

    def __init__(
        self,
        message: str,
        *,
        validation_issues: tuple[str, ...] = (),
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        super().__init__(message)
        self.validation_issues = validation_issues
        self.input_tokens, self.output_tokens = input_tokens, output_tokens


class ModelOutputTruncatedError(InvalidProviderResponseError):
    code = "MODEL_OUTPUT_TRUNCATED"


class SecurityBlockedError(ExternalCallError):
    code = "SECURITY_BLOCKED"


class ReplayCacheMissError(ExternalCallError):
    code = "REPLAY_CACHE_MISS"

    def __init__(self, *, operation: str, request_fingerprint: str) -> None:
        self.operation = operation
        self.request_fingerprint = request_fingerprint
        super().__init__(f"{self.code}: {operation} {request_fingerprint}")
