"""Cloud storage service for production file management."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from supabase import create_client, Client
import uuid


class SupabaseStorageService:
    """Handle file uploads and downloads via Supabase Storage."""
    
    def __init__(self):
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            raise ValueError("Supabase URL and Service Role Key required")
        
        self.client: Client = create_client(url, key)
        self.bucket_name = "user-files"  # Create this bucket in Supabase
    
    def upload_image(self, file_path: Path, user_id: str) -> str:
        """Upload image to Supabase Storage and return public URL."""
        # Generate unique filename
        file_extension = file_path.suffix
        unique_filename = f"{user_id}/{uuid.uuid4()}{file_extension}"
        
        # Read file content
        with open(file_path, 'rb') as f:
            file_content = f.read()
        
        # Upload to Supabase Storage
        result = self.client.storage.from_(self.bucket_name).upload(
            path=unique_filename,
            file=file_content,
            file_options={"content-type": "image/jpeg"}
        )
        
        if result.error:
            raise Exception(f"Upload failed: {result.error}")
        
        # Get public URL
        public_url = self.client.storage.from_(self.bucket_name).get_public_url(unique_filename)
        return public_url
    
    def list_user_files(self, user_id: str) -> list:
        """List all files for a specific user."""
        result = self.client.storage.from_(self.bucket_name).list(path=user_id)
        if result.error:
            return []
        return result.data
    
    def delete_file(self, file_path: str) -> bool:
        """Delete a file from storage."""
        result = self.client.storage.from_(self.bucket_name).remove([file_path])
        return not result.error


# Usage in Leonardo tools:
def save_image_to_cloud(image_path: Path, user_id: str) -> str:
    """Save generated image to cloud storage instead of local filesystem."""
    if os.getenv("USE_CLOUD_STORAGE", "false").lower() == "true":
        storage = SupabaseStorageService()
        return storage.upload_image(image_path, user_id)
    else:
        # Development: keep local storage
        return f"/v1/files/download/{image_path.name}"