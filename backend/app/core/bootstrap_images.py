"""Download the default boot image so a fresh installation can create a VM."""
import os
from pathlib import Path

from app.core.database import SessionLocal
from app.models.entities import OsImage
from app.services.image_service import download_image


def main() -> None:
    if os.getenv("PKVM_DOWNLOAD_DEFAULT_IMAGE", "1").lower() in {"0", "false", "no"}:
        return
    db = SessionLocal()
    try:
        image = db.query(OsImage).filter(
            OsImage.linux_type == "ROCKY", OsImage.version == "9.8"
        ).first()
        if not image:
            raise RuntimeError("default Rocky 9.8 image catalog entry is missing")
        image_id = image.id
        exists = Path(image.file_path).is_file()
    finally:
        db.close()
    if not exists:
        download_image(image_id)


if __name__ == "__main__":
    main()
