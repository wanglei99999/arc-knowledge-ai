from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

import boto3
from botocore.exceptions import ClientError

from app.config.settings import settings


def _make_client() -> boto3.client:
    return boto3.client(
        "s3",
        endpoint_url=f"{'https' if settings.minio_secure else 'http'}://{settings.minio_endpoint}",
        aws_access_key_id=settings.minio_access_key,
        aws_secret_access_key=settings.minio_secret_key,
    )


def _ensure_bucket(client: boto3.client, bucket: str) -> None:
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            client.create_bucket(Bucket=bucket)
        else:
            raise


async def upload_file(
    data: bytes,
    object_key: str,
    content_type: str = "application/octet-stream",
    bucket: str | None = None,
) -> str:
    """
    上传文件到 MinIO，返回 object_key（用于后续下载）。
    boto3 是同步库，用 run_in_executor 包装避免阻塞事件循环。
    """
    bucket = bucket or settings.minio_bucket

    def _upload() -> None:
        client = _make_client()
        _ensure_bucket(client, bucket)
        client.put_object(
            Bucket=bucket,
            Key=object_key,
            Body=data,
            ContentType=content_type,
        )

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _upload)
    return object_key


async def download_file(object_key: str, bucket: str | None = None) -> bytes:
    """从 MinIO 下载文件，返回原始字节。"""
    bucket = bucket or settings.minio_bucket

    def _download() -> bytes:
        client = _make_client()
        response = client.get_object(Bucket=bucket, Key=object_key)
        return response["Body"].read()

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _download)


async def delete_file(object_key: str, bucket: str | None = None) -> None:
    """删除 MinIO 中的文件。"""
    bucket = bucket or settings.minio_bucket

    def _delete() -> None:
        client = _make_client()
        client.delete_object(Bucket=bucket, Key=object_key)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _delete)


def build_object_key(tenant_id: str, space_id: str, document_id: str, filename: str) -> str:
    """生成统一的 MinIO object key 格式。"""
    suffix = filename.rsplit(".", 1)[-1] if "." in filename else "bin"
    return f"{tenant_id}/{space_id}/{document_id}.{suffix}"
