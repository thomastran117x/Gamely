from contextlib import AbstractAsyncContextManager
from typing import Protocol, cast

import aioboto3  # type: ignore[import-untyped]


class S3Client(Protocol):
    """Minimal typed boundary for an async S3 client."""


class S3Session(Protocol):
    def client(self, service_name: str) -> AbstractAsyncContextManager[S3Client]: ...


def create_s3_session(region_name: str) -> S3Session:
    return cast(S3Session, aioboto3.Session(region_name=region_name))
