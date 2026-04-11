"""
FastAPI application for PostgreSQL-backed visit tracking and S3-based file storage.

This module provides:
- Application startup validation for required environment variables
- PostgreSQL connection helpers and schema initialization
- AWS S3 upload, download URL generation, and deletion helpers
- Health, database, and S3 connectivity routes
- Visit creation and listing endpoints
- File upload, listing, metadata retrieval, download URL, and deletion endpoints

The implementation is designed for Amazon EKS deployments 
and supports IRSA-based AWS credential discovery through boto3.
"""

import os
import uuid
import mimetypes
import logging
from contextlib import asynccontextmanager

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
import psycopg
from psycopg.rows import dict_row


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


# --------------------------------------------------
# Environment
# --------------------------------------------------
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

S3_BUCKET = os.getenv("S3_BUCKET")
AWS_REGION = os.getenv("AWS_REGION", "eu-central-1")
S3_PREFIX = os.getenv("S3_PREFIX", "uploads")
PRESIGNED_URL_EXPIRES = int(os.getenv("PRESIGNED_URL_EXPIRES", "900"))  # 15 min
MAX_UPLOAD_SIZE = int(os.getenv("MAX_UPLOAD_SIZE", str(20 * 1024 * 1024)))  # 20 MB


def validate_required_env() -> None:
    """
    Validate that all required database and S3 environment variables exist.

    Raises:
        RuntimeError: If any required environment variables are missing.
    """
    required = {
        "DB_HOST": DB_HOST,
        "DB_NAME": DB_NAME,
        "DB_USER": DB_USER,
        "DB_PASSWORD": DB_PASSWORD,
        "S3_BUCKET": S3_BUCKET,
    }
    missing = [name for name, value in required.items() if not value]

    if missing:
        raise RuntimeError(
            "Application startup failed because required environment variables are missing: "
            f"{', '.join(missing)}. "
            "These values are required for PostgreSQL and S3 connectivity."
        )


# --------------------------------------------------
# App lifecycle
# --------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application startup and shutdown lifecycle events.

    Validates configuration and initializes the database schema during startup.

    Args:
        app: The FastAPI application instance.
    """
    validate_required_env()
    init_db()
    logger.info("Application started successfully")
    yield
    logger.info("Application shutting down")


app = FastAPI(
    title="FastAPI EKS Demo",
    description="FastAPI app using PostgreSQL and S3 on EKS",
    version="2.0.0",
    lifespan=lifespan,
)


# --------------------------------------------------
# Helpers
# --------------------------------------------------
def get_conn():
    """
    Create a PostgreSQL connection using configured environment variables.

    Returns:
        A psycopg database connection with dict-style rows.
    """
    return psycopg.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        row_factory=dict_row,
    )


def get_s3_client():
    """
    Create an AWS S3 client for the configured region.

    Returns:
        A boto3 S3 client.
    """
    # On EKS, boto3 can pick up pod credentials from IRSA automatically.
    return boto3.client("s3", region_name=AWS_REGION)


def init_db() -> None:
    """Initialize required PostgreSQL tables and indexes if they do not exist."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS visits (
                    id SERIAL PRIMARY KEY,
                    created_at TIMESTAMPTZ DEFAULT now()
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    id BIGSERIAL PRIMARY KEY,
                    original_filename TEXT NOT NULL,
                    s3_key TEXT NOT NULL UNIQUE,
                    content_type TEXT,
                    size_bytes BIGINT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    deleted_at TIMESTAMPTZ
                );
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_files_created_at
                ON files (created_at DESC);
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_files_not_deleted
                ON files (deleted_at)
                WHERE deleted_at IS NULL;
                """
            )
            conn.commit()


def sanitize_filename(filename: str) -> str:
    """
    Sanitize an uploaded filename to prevent path traversal-like input.

    Args:
        filename: The original uploaded filename.

    Returns:
        A safe basename or a fallback default filename.
    """
    # Keep only the basename to avoid path traversal-like input.
    return os.path.basename(filename).strip() or "file.bin"


def build_s3_key(filename: str) -> str:
    """
    Generate a unique S3 object key for a filename.

    Args:
        filename: The original or sanitized filename.

    Returns:
        A prefixed unique S3 key.
    """
    safe_name = sanitize_filename(filename)
    key = uuid.uuid4().hex[:8] + "-" + safe_name
    return f"{S3_PREFIX.rstrip('/')}/{key}"


def infer_content_type(filename: str, declared_content_type: str | None) -> str:
    """
    Determine the content type for a file.

    Args:
        filename: The filename used for mime type inference.
        declared_content_type: Content type provided by the upload request.

    Returns:
        A resolved MIME type string.
    """
    if declared_content_type:
        return declared_content_type
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def upload_to_s3(file_obj, bucket: str, key: str, content_type: str) -> None:
    """
    Upload a file-like object to S3.

    Args:
        file_obj: The file object to upload.
        bucket: Target S3 bucket.
        key: Target S3 object key.
        content_type: MIME type metadata.
    """
    s3 = get_s3_client()
    s3.upload_fileobj(
        Fileobj=file_obj,
        Bucket=bucket,
        Key=key,
        ExtraArgs={"ContentType": content_type},
    )


def generate_download_url(bucket: str, key: str, expires_in: int) -> str:
    """Generate a presigned S3 download URL.

    Args:
        bucket: Source S3 bucket.
        key: Object key.
        expires_in: Expiration time in seconds.

    Returns:
        A presigned HTTPS URL.
    """
    s3 = get_s3_client()
    return s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires_in,
    )


def head_bucket() -> None:
    """Verify access to the configured S3 bucket."""
    s3 = get_s3_client()
    s3.head_bucket(Bucket=S3_BUCKET)


def delete_object(bucket: str, key: str) -> None:
    """Delete an object from S3.

    Args:
        bucket: S3 bucket name.
        key: Object key to delete.
    """
    s3 = get_s3_client()
    s3.delete_object(Bucket=bucket, Key=key)


def get_file_record(file_id: int):
    """Fetch a file metadata record by ID.

    Args:
        file_id: Database file record ID.

    Returns:
        A single file row dictionary or None.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, original_filename, s3_key, content_type, size_bytes, created_at, deleted_at
                FROM files
                WHERE id = %s;
                """,
                (file_id,),
            )
            return cur.fetchone()


# --------------------------------------------------
# Error handling
# --------------------------------------------------
@app.exception_handler(RuntimeError)
def runtime_error_handler(_, exc: RuntimeError):
    """Convert runtime errors into JSON HTTP 500 responses."""
    logger.exception("Runtime error")
    return JSONResponse(status_code=500, content={"detail": str(exc)})


# --------------------------------------------------
# Basic routes
# --------------------------------------------------
@app.get("/")
def root():
    """Return a simple application greeting."""
    return {"message": "hello from eks"}


@app.get("/healthz")
def healthz():
    """Return application health status."""
    return {"status": "ok"}


@app.get("/db-check")
def db_check():
    """Verify PostgreSQL connectivity and return database metadata."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT now() AS now, current_database() AS db;")
            row = cur.fetchone()
            return {
                "connected": True,
                "database": row["db"],
                "time": str(row["now"]),
            }


@app.get("/s3-check")
def s3_check():
    """Verify S3 bucket connectivity."""
    try:
        head_bucket()
        return {
            "connected": True,
            "bucket": S3_BUCKET,
            "region": AWS_REGION,
        }
    except (ClientError, BotoCoreError) as exc:
        logger.exception("S3 check failed")
        raise HTTPException(status_code=500, detail=f"S3 check failed: {exc}")


# --------------------------------------------------
# Visit routes
# --------------------------------------------------
@app.post("/visit")
def create_visit():
    """Insert a visit record and return the created row."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO visits DEFAULT VALUES RETURNING id, created_at;"
            )
            row = cur.fetchone()
            conn.commit()
            return row


@app.get("/visits")
def list_visits():
    """Return the 20 most recent visit records."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, created_at FROM visits ORDER BY id DESC LIMIT 20;"
            )
            return cur.fetchall()


# --------------------------------------------------
# File routes
# --------------------------------------------------
@app.post("/upload")
def upload_file(file: UploadFile = File(...)):
    """
    Upload a file to S3 and persist metadata in PostgreSQL.

    Args:
        file: The uploaded FastAPI file object.

    Returns:
        Upload status, metadata, and S3 URI.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file must have a filename.")

    safe_name = sanitize_filename(file.filename)
    content_type = infer_content_type(safe_name, file.content_type)
    s3_key = build_s3_key(safe_name)

    try:
        # Measure file size without loading the whole thing into memory.
        file.file.seek(0, os.SEEK_END)
        size_bytes = file.file.tell()
        file.file.seek(0)

        if size_bytes <= 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        if size_bytes > MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Max upload size is {MAX_UPLOAD_SIZE} bytes.",
            )

        upload_to_s3(file.file, S3_BUCKET, s3_key, content_type)

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO files (original_filename, s3_key, content_type, size_bytes)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, original_filename, s3_key, content_type, size_bytes, created_at;
                    """,
                    (safe_name, s3_key, content_type, size_bytes),
                )
                row = cur.fetchone()
                conn.commit()

        return {
            "uploaded": True,
            "file": row,
            "s3_uri": f"s3://{S3_BUCKET}/{s3_key}",
        }

    except HTTPException:
        raise
    except (ClientError, BotoCoreError) as exc:
        logger.exception("S3 upload failed")
        raise HTTPException(status_code=500, detail=f"S3 upload failed: {exc}")
    except Exception as exc:
        logger.exception("Unexpected upload failure")
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}")
    finally:
        file.file.close()


@app.get("/files")
def list_files(limit: int = Query(default=20, ge=1, le=100)):
    """
    List non-deleted files up to the requested limit.

    Args:
        limit: Maximum number of records to return.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, original_filename, s3_key, content_type, size_bytes, created_at
                FROM files
                WHERE deleted_at IS NULL
                ORDER BY id DESC
                LIMIT %s;
                """,
                (limit,),
            )
            return cur.fetchall()


@app.get("/files/{file_id}")
def get_file(file_id: int):
    """Retrieve file metadata by file ID."""
    row = get_file_record(file_id)
    if not row or row["deleted_at"] is not None:
        raise HTTPException(status_code=404, detail="File not found.")
    return row


@app.get("/files/{file_id}/download-url")
def get_download_url(
    file_id: int,
    expires_in: int = Query(default=PRESIGNED_URL_EXPIRES, ge=60, le=3600),
):
    """
    Generate a temporary download URL for a stored file.

    Args:
        file_id: Database file ID.
        expires_in: URL validity duration in seconds.
    """
    row = get_file_record(file_id)
    if not row or row["deleted_at"] is not None:
        raise HTTPException(status_code=404, detail="File not found.")

    try:
        url = generate_download_url(S3_BUCKET, row["s3_key"], expires_in)
        return {
            "file_id": row["id"],
            "filename": row["original_filename"],
            "expires_in": expires_in,
            "download_url": url,
        }
    except (ClientError, BotoCoreError) as exc:
        logger.exception("Failed to create presigned URL")
        raise HTTPException(status_code=500, detail=f"Failed to create download URL: {exc}")


@app.delete("/files/{file_id}")
def delete_file(file_id: int, hard_delete: bool = Query(default=False)):
    """Soft-delete or permanently delete a file.

    Args:
        file_id: Database file ID.
        hard_delete: If True, also removes the object from S3 and deletes the DB row.
    """
    row = get_file_record(file_id)
    if not row or row["deleted_at"] is not None:
        raise HTTPException(status_code=404, detail="File not found.")

    try:
        if hard_delete:
            delete_object(S3_BUCKET, row["s3_key"])

        with get_conn() as conn:
            with conn.cursor() as cur:
                if hard_delete:
                    cur.execute("DELETE FROM files WHERE id = %s;", (file_id,))
                else:
                    cur.execute(
                        """
                        UPDATE files
                        SET deleted_at = now()
                        WHERE id = %s
                        RETURNING id, deleted_at;
                        """,
                        (file_id,),
                    )
                result = cur.fetchone()
                conn.commit()

        return {
            "deleted": True,
            "hard_delete": hard_delete,
            "result": result,
        }

    except (ClientError, BotoCoreError) as exc:
        logger.exception("Failed to delete from S3")
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {exc}")
