from importlib import import_module
from typing import Any, cast
from urllib.parse import quote

from app.services.storage.base import StoredObject, validate_storage_key


class S3StorageService:
    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        access_key: str,
        secret_key: str,
        endpoint_url: str | None = None,
        public_base_url: str | None = None,
        presigned_url_expire_seconds: int = 900,
    ) -> None:
        self.bucket = bucket
        self.public_base_url = public_base_url.rstrip("/") if public_base_url else None
        self.presigned_url_expire_seconds = presigned_url_expire_seconds
        boto3 = import_module("boto3")
        botocore_exceptions = import_module("botocore.exceptions")
        self.client_error = botocore_exceptions.ClientError
        self.client: Any = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    def put(self, key: str, content: bytes, *, content_type: str | None = None) -> StoredObject:
        key = validate_storage_key(key)
        extra_args = {"ContentType": content_type} if content_type is not None else None
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Key": key, "Body": content}
        if extra_args is not None:
            kwargs.update(extra_args)
        response = self.client.put_object(**kwargs)
        etag = response.get("ETag") if isinstance(response, dict) else None
        return StoredObject(
            key=key,
            content_type=content_type,
            content_length=len(content),
            etag=etag.strip('"') if isinstance(etag, str) else None,
        )

    def get(self, key: str) -> bytes:
        key = validate_storage_key(key)
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        body = response["Body"]
        return cast(bytes, body.read())

    def delete(self, key: str) -> None:
        key = validate_storage_key(key)
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        key = validate_storage_key(key)
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except self.client_error as exc:
            error = exc.response.get("Error", {})
            if error.get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        return True

    def url(self, key: str) -> str | None:
        key = validate_storage_key(key)
        if self.public_base_url:
            return f"{self.public_base_url}/{quote(key, safe='/')}"
        return cast(
            str,
            self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=self.presigned_url_expire_seconds,
            ),
        )
