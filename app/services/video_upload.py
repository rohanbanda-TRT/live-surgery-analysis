"""
Video upload service for uploading videos to Google Cloud Storage.
"""
from google.cloud import storage
from datetime import datetime, timezone
import uuid
from typing import BinaryIO
import os

from app.core.config import settings
from app.core.logging import logger


class VideoUploadService:
    """Service for uploading videos to Google Cloud Storage."""
    
    def __init__(self):
        """Initialize video upload service."""
        self.storage_client = storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
        self.bucket_name = settings.GCS_BUCKET_NAME
        self.bucket = self.storage_client.bucket(self.bucket_name)
    
    async def upload_video(
        self,
        file: BinaryIO,
        filename: str,
        content_type: str = "video/mp4"
    ) -> str:
        """
        Upload a video file to Google Cloud Storage.
        
        Args:
            file: File object to upload
            filename: Original filename
            content_type: MIME type of the file
            
        Returns:
            GCS URI of the uploaded video (gs://bucket/path)
        """
        try:
            # Generate unique filename to avoid collisions
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            unique_id = str(uuid.uuid4())[:8]
            file_extension = os.path.splitext(filename)[1] or '.mp4'
            
            # Create blob path: uploads/YYYYMMDD_HHMMSS_uniqueid_originalname.ext
            blob_name = f"uploads/{timestamp}_{unique_id}_{filename}"
            
            logger.info(
                "uploading_video_to_gcs",
                filename=filename,
                blob_name=blob_name,
                bucket=self.bucket_name
            )
            
            # Create blob and upload
            blob = self.bucket.blob(blob_name)
            blob.content_type = content_type
            
            # Upload file
            blob.upload_from_file(file, rewind=True)
            
            # Construct GCS URI
            gcs_uri = f"gs://{self.bucket_name}/{blob_name}"
            
            logger.info(
                "video_uploaded_successfully",
                gcs_uri=gcs_uri,
                blob_name=blob_name
            )
            
            return gcs_uri
            
        except Exception as e:
            logger.error(
                "video_upload_failed",
                filename=filename,
                error=str(e)
            )
            raise
    
    def get_upload_url(self, blob_name: str) -> str:
        """
        Get the GCS URI for a blob.
        
        Args:
            blob_name: Name of the blob in GCS
            
        Returns:
            GCS URI
        """
        return f"gs://{self.bucket_name}/{blob_name}"
