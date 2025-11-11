"""AWS S3 storage alternative for file management."""

import boto3
import os
from pathlib import Path
from typing import Optional
import uuid


class S3StorageService:
    """Handle file uploads and downloads via AWS S3."""
    
    def __init__(self):
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION', 'us-east-1')
        )
        self.bucket_name = os.getenv('S3_BUCKET_NAME', 'tmates-user-files')
    
    def upload_image(self, file_path: Path, user_id: str) -> str:
        """Upload image to S3 and return public URL."""
        file_extension = file_path.suffix
        s3_key = f"users/{user_id}/images/{uuid.uuid4()}{file_extension}"
        
        # Upload file
        self.s3_client.upload_file(
            str(file_path), 
            self.bucket_name, 
            s3_key,
            ExtraArgs={'ContentType': 'image/jpeg'}
        )
        
        # Return public URL (requires public bucket or signed URLs)
        return f"https://{self.bucket_name}.s3.amazonaws.com/{s3_key}"
    
    def generate_presigned_url(self, s3_key: str, expiration: int = 3600) -> str:
        """Generate a presigned URL for secure access."""
        return self.s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': self.bucket_name, 'Key': s3_key},
            ExpiresIn=expiration
        )