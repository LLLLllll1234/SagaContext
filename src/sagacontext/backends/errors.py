class BackendDefiniteError(RuntimeError):
    """The operation was rejected or is unsafe to retry."""


class BackendUnknownError(TimeoutError):
    """The write may have reached the backend; locate before retrying."""


class BackendVerificationTimeout(TimeoutError):
    """Read-side verification could not establish the backend state."""
