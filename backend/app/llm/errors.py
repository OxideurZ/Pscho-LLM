class LLMError(Exception):
    code = "LLM_GENERATION_FAILED"
    retryable = False


class LLMBackendUnavailable(LLMError):
    code = "LLM_BACKEND_UNAVAILABLE"
    retryable = True


class LLMBackendTimeout(LLMError):
    code = "LLM_BACKEND_TIMEOUT"
    retryable = True


class LLMBackendProtocolError(LLMError):
    code = "LLM_BACKEND_PROTOCOL_ERROR"
