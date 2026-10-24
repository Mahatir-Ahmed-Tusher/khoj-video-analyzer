import os
import uuid
import logging
from typing import Optional
import boto3
from botocore.config import Config
from app.config import settings

logger = logging.getLogger("khoj-video-analyzer")

class R2StorageService:
    """
    Cloudflare R2 Object Storage Service for temporary video buffering.
    Ensures zero RAM bloat on Cloud Run by offloading video binaries,
    and automatically deletes objects once transcription/OCR analysis finishes.
    """
    _instance: Optional['R2StorageService'] = None
    _client = None

    @classmethod
    def get_instance(cls) -> 'R2StorageService':
        if cls._instance is None:
            cls._instance = R2StorageService()
        return cls._instance

    def _get_client(self):
        if self._client is None and settings.R2_ACCOUNT_ID and settings.R2_ACCESS_KEY_ID:
            endpoint_url = f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
            self._client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=settings.R2_ACCESS_KEY_ID,
                aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
                config=Config(signature_version="s3v4"),
                region_name="auto"
            )
        return self._client

    def is_configured(self) -> bool:
        return bool(settings.R2_ACCOUNT_ID and settings.R2_ACCESS_KEY_ID and settings.R2_SECRET_ACCESS_KEY)

    def upload_temporary_file(self, local_file_path: str, content_type: str = "video/mp4") -> Optional[str]:
        """
        Uploads a video to R2 to release container disk/RAM during multi-stage processing.
        Returns the object key.
        """
        if not self.is_configured() or not os.path.exists(local_file_path):
            return None

        client = self._get_client()
        if not client:
            return None

        file_ext = os.path.splitext(local_file_path)[1] or ".mp4"
        object_key = f"temp_analysis/{uuid.uuid4().hex}{file_ext}"

        try:
            logger.info(f"Uploading temporary video to R2 bucket '{settings.R2_BUCKET_NAME}' ({object_key})...")
            with open(local_file_path, "rb") as f:
                client.put_object(
                    Bucket=settings.R2_BUCKET_NAME,
                    Key=object_key,
                    Body=f,
                    ContentType=content_type
                )
            logger.info(f"Temporary file uploaded to R2: {object_key}")
            return object_key
        except Exception as e:
            logger.error(f"Failed to upload temporary file to R2: {e}")
            return None

    def delete_file(self, object_key: Optional[str]) -> bool:
        """
        Immediately purges temporary media from R2 once analysis is complete.
        Zero permanent accumulation, keeping storage at ~0 MB.
        """
        if not object_key or not self.is_configured():
            return False

        client = self._get_client()
        if not client:
            return False

        try:
            logger.info(f"Purging temporary file from R2: {object_key}...")
            client.delete_object(Bucket=settings.R2_BUCKET_NAME, Key=object_key)
            logger.info(f"Successfully deleted {object_key} from R2.")
            return True
        except Exception as e:
            logger.warning(f"Error deleting {object_key} from R2: {e}")
            return False

r2_service = R2StorageService.get_instance()
