from unittest.mock import Mock

import pytest
from google.api_core.exceptions import Forbidden, PreconditionFailed, ServiceUnavailable
from google.cloud.storage import Blob, Bucket, Client

from divvyai.cloud_storage import upload_archive
from divvyai.identity import CaptureIdentity

BUCKET_NAME = "divvyai-test-bucket"
IDENTITY = CaptureIdentity(
    capture_id="a" * 64,
    object_name=f"raw/date=2026-10-09/hour=14/{'a' * 64}.zip",
)
# x03\x04... mean ZIP file header
ARCHIVE_BYTES = b"PK\x03\x04\x00\xff\x80binary-test-data"


@pytest.fixture
def upload_client() -> Mock:
    client = Mock(spec=Client)
    client.bucket.return_value = Mock(spec=Bucket)
    client.bucket.return_value.blob.return_value = Mock(spec=Blob)
    return client


def test_upload_preserves_bytes_and_requires_a_new_object(upload_client: Mock) -> None:
    result = upload_archive(upload_client, BUCKET_NAME, IDENTITY, ARCHIVE_BYTES)

    upload_client.bucket.assert_called_once_with(BUCKET_NAME)
    bucket = upload_client.bucket.return_value
    bucket.blob.assert_called_once_with(IDENTITY.object_name)
    bucket.blob.return_value.upload_from_string.assert_called_once_with(
        ARCHIVE_BYTES,
        content_type="application/zip",
        if_generation_match=0,
        timeout=30,
        retry=None,
        checksum="crc32c",
    )
    assert result == f"gs://{BUCKET_NAME}/{IDENTITY.object_name}"


@pytest.mark.parametrize(
    "error",
    [
        PreconditionFailed("Object already exists"),
        Forbidden("Permission denied"),
        ServiceUnavailable("Service unavailable"),
    ],
)
def test_upload_failure_propagates_without_another_attempt(
    upload_client: Mock, error: Exception
) -> None:
    blob = upload_client.bucket.return_value.blob.return_value
    blob.upload_from_string.side_effect = error

    with pytest.raises(type(error)) as raised:
        upload_archive(upload_client, BUCKET_NAME, IDENTITY, ARCHIVE_BYTES)

    assert raised.value is error
    assert blob.upload_from_string.call_count == 1
