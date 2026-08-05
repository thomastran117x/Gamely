class ApplicationError(Exception):
    """Base error with a safe message intended for API clients."""

    status_code = 500
    code = "internal_error"
    message = "An unexpected error occurred."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message


class BadRequestError(ApplicationError):
    status_code = 400
    code = "bad_request"
    message = "The request could not be processed."


class UnauthorizedError(ApplicationError):
    status_code = 401
    code = "unauthorized"
    message = "Authentication is required to access this resource."


class ForbiddenError(ApplicationError):
    status_code = 403
    code = "forbidden"
    message = "You do not have permission to access this resource."


class NotFoundError(ApplicationError):
    status_code = 404
    code = "not_found"
    message = "The requested resource was not found."


class ConflictError(ApplicationError):
    status_code = 409
    code = "conflict"
    message = "The request conflicts with the current resource state."


class ServiceUnavailableError(ApplicationError):
    status_code = 503
    code = "service_unavailable"
    message = "The service is temporarily unavailable."
