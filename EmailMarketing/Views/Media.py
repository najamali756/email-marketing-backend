import os
import uuid
import base64
from urllib.parse import quote
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.http import JsonResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
import boto3

from EmailMarketing.models import EmailTemplateMedia, EmailTemplate
from EmailMarketing.Views.base import StoreAuthenticatedMixin

def upload_file_to_s3_or_local(file_content, file_name, file_type="image"):
    # AWS S3 Settings lookup
    aws_key = os.environ.get("AWS_ACCESS_KEY_ID")
    aws_secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
    bucket_name = os.environ.get("AWS_STORAGE_BUCKET_NAME", "marketing-app-media")
    region = os.environ.get("AWS_S3_REGION_NAME", "us-east-1")
    media_prefix = os.environ.get("AWS_S3_MEDIA_PREFIX", "Template Media/").strip("/")
    s3_key_prefix = f"{media_prefix}/" if media_prefix else ""

    safe_filename = f"media_{uuid.uuid4().hex}_{file_name}"

    if aws_key and aws_secret and bucket_name:
        try:
            s3 = boto3.client(
                's3',
                aws_access_key_id=aws_key,
                aws_secret_access_key=aws_secret,
                region_name=region
            )
            content_type = "image/png" if file_type == "image" else "video/mp4"
            if file_name.endswith(".jpg") or file_name.endswith(".jpeg"):
                content_type = "image/jpeg"
            elif file_name.endswith(".gif"):
                content_type = "image/gif"
            elif file_name.endswith(".svg"):
                content_type = "image/svg+xml"

            s3.put_object(
                Bucket=bucket_name,
                Key=f"{s3_key_prefix}{safe_filename}",
                Body=file_content,
                ContentType=content_type,
               
            )
            public_key = quote(f"{s3_key_prefix}{safe_filename}")
            return f"https://{bucket_name}.s3.{region}.amazonaws.com/{public_key}"
        except Exception as e:
            print("S3 Upload Exception:", e)

    # Local fallback storage
    django_content = ContentFile(file_content)
    saved_path = default_storage.save(f"email_marketing_media/{safe_filename}", django_content)
    # Serves via Django media url configurations
    return f"http://localhost:8000/media/{saved_path}"


class EmailTemplateMediaListView(StoreAuthenticatedMixin, APIView):
    def get(self, request):
        media_items = EmailTemplateMedia.objects.filter(store=request.store, is_active=True).order_by("-created_at")
        data = []
        for item in media_items:
            data.append({
                "id": item.id,
                "file_url": item.file_url,
                "client_uuid": item.client_uuid,
                "file_type": item.file_type,
                "created_at": item.created_at,
            })
        return Response(data)

    def post(self, request):
        store = request.store
        template_id = request.data.get("template_id")
        template = None
        if template_id:
            try:
                template = EmailTemplate.objects.get(id=template_id, store=store)
            except EmailTemplate.DoesNotExist:
                pass

        results = []

        # 1. Handle JSON Batch Base64 List
        media_files = request.data.get("media_files", [])
        if media_files:
            for item in media_files:
                client_uuid = item.get("uuid")
                base64_str = item.get("base64")
                file_type = item.get("file_type", "image")
                filename = item.get("filename", "upload.png")

                if not base64_str:
                    continue

                try:
                    # Decode base64
                    if ";base64," in base64_str:
                        format_header, imgstr = base64_str.split(';base64,')
                    else:
                        imgstr = base64_str
                    
                    decoded_data = base64.b64decode(imgstr)
                    public_url = upload_file_to_s3_or_local(decoded_data, filename, file_type)

                    media_record = EmailTemplateMedia.objects.create(
                        store=store,
                        template=template,
                        file_url=public_url,
                        client_uuid=client_uuid,
                        file_type=file_type
                    )

                    results.append({
                        "id": media_record.id,
                        "uuid": client_uuid,
                        "url": public_url
                    })
                except Exception as e:
                    print("Error decoding base64 item:", e)

        # 2. Handle Form File Uploads
        uploaded_file = request.FILES.get("file")
        if uploaded_file:
            client_uuid = request.data.get("uuid", f"media_{uuid.uuid4().hex[:6]}")
            file_type = request.data.get("file_type", "image")
            try:
                file_content = uploaded_file.read()
                public_url = upload_file_to_s3_or_local(file_content, uploaded_file.name, file_type)
                
                media_record = EmailTemplateMedia.objects.create(
                    store=store,
                    template=template,
                    file_url=public_url,
                    client_uuid=client_uuid,
                    file_type=file_type
                )
                results.append({
                    "id": media_record.id,
                    "uuid": client_uuid,
                    "url": public_url
                })
            except Exception as e:
                print("Error uploading file request:", e)

        return Response({"results": results}, status=status.HTTP_201_CREATED)


class EmailTemplateMediaDetailView(StoreAuthenticatedMixin, APIView):
    def delete(self, request, pk):
        try:
            item = EmailTemplateMedia.objects.get(pk=pk, store=request.store)
            # Soft delete or hard delete depending on needs, let's hard delete
            item.delete()
            return Response({"success": True}, status=status.HTTP_200_OK)
        except EmailTemplateMedia.DoesNotExist:
            return Response({"error": "Media item not found"}, status=status.HTTP_404_NOT_FOUND)
