import asyncio
from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile

from server import (
    MAX_NOTE_CHARS,
    MAX_UPLOAD_FILES,
    WEB_DURATION_CAP,
    _target_duration,
    create_job,
)


def test_web_duration_scales_with_uploaded_material():
    assert _target_duration(2) < _target_duration(6) <= WEB_DURATION_CAP
    assert _target_duration(20) <= WEB_DURATION_CAP


def test_job_rejects_oversized_notes_and_file_batches():
    async def exercise():
        with pytest.raises(HTTPException) as note_error:
            await create_job(note="x" * (MAX_NOTE_CHARS + 1), files=[])
        assert note_error.value.status_code == 413

        files = [UploadFile(BytesIO(b"x"), filename=f"{i}.jpg") for i in range(MAX_UPLOAD_FILES + 1)]
        with pytest.raises(HTTPException) as files_error:
            await create_job(note="", files=files)
        assert files_error.value.status_code == 413

    asyncio.run(exercise())


def test_job_rejects_unsupported_file_types():
    async def exercise():
        upload = UploadFile(BytesIO(b"not an image"), filename="notes.txt")
        with pytest.raises(HTTPException) as error:
            await create_job(note="", files=[upload])
        assert error.value.status_code == 415

    asyncio.run(exercise())
