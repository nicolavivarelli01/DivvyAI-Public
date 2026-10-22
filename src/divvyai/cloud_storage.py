from google.cloud.storage import Client

from divvyai.identity import CaptureIdentity


def upload_archive(
    client: Client,
    bucket_name: str,
    identity: CaptureIdentity,
    archive: bytes,
) -> str:
    """Upload ZIP bytes to a new object; bucket_name excludes the gs:// prefix."""
    # blob is the Google Cloud Storage object where the archive will be uploaded
    blob = client.bucket(bucket_name).blob(identity.object_name)
    blob.upload_from_string(
        # archive is the ZIP file bytes to be uploaded
        archive,
        content_type="application/zip",
        # if_generation_match is used to ensure the object is not overwritten
        if_generation_match=0,
        timeout=30,
        retry=None,
        # checksum is used to verify the integrity of the uploaded file
        checksum="crc32c",
    )
    return f"gs://{bucket_name}/{identity.object_name}"
