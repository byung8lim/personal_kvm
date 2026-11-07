import hashlib
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

import httpx

from app.core.database import SessionLocal
from app.models.entities import OsImage
from app.core.runtime_config import storage_path


def download_image(image_id: int) -> None:
    db = SessionLocal()
    image: Optional[OsImage] = None
    temporary: Optional[Path] = None
    try:
        image = db.query(OsImage).filter(OsImage.id == image_id).first()
        if not image:
            return
        destination = Path(image.file_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        # A recorded digest is a pin for subsequent downloads. Do not silently
        # replace it with a checksum calculated from unverified new bytes.
        expected_sha256 = image.checksum_sha256
        image.status = "DOWNLOADING"
        db.commit()
        digest = hashlib.sha256()
        size = 0
        with httpx.stream("GET", image.source_url, follow_redirects=True, timeout=120.0) as response:
            response.raise_for_status()
            if response.headers.get("last-modified"):
                image.released_at = parsedate_to_datetime(response.headers["last-modified"]).replace(tzinfo=None)
            with temporary.open("wb") as output:
                for chunk in response.iter_bytes(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
        actual_sha256 = digest.hexdigest()
        if expected_sha256 and actual_sha256.lower() != expected_sha256.lower():
            raise ValueError(
                f"Image SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}"
            )
        temporary.replace(destination)
        image.size_mb = max(1, round(size / (1024 * 1024)))
        image.checksum_sha256 = actual_sha256
        image.status = "READY"
        db.commit()
    except Exception as exc:
        db.rollback()
        if image:
            image.status = "ERROR"
            db.commit()
        if temporary and temporary.exists():
            temporary.unlink()
        raise
    finally:
        db.close()


def delete_image_file(image_id: int) -> None:
    db = SessionLocal()
    try:
        image = db.query(OsImage).filter(OsImage.id == image_id).first()
        if not image:
            return
        path = Path(image.file_path).resolve()
        root = storage_path("image_dir").resolve()
        if root not in path.parents:
            raise RuntimeError("이미지 저장소 외부 파일은 삭제할 수 없습니다.")
        image.status = "DELETING"
        db.commit()
        if path.exists():
            path.unlink()
        image.status = "DELETED"
        db.commit()
    except Exception:
        db.rollback()
        image = db.query(OsImage).filter(OsImage.id == image_id).first()
        if image:
            image.status = "ERROR"
            db.commit()
        raise
    finally:
        db.close()
