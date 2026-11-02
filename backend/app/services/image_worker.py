import threading
from datetime import datetime

from app.core.database import SessionLocal
from app.models.entities import ResourceOperation
from app.services.image_service import delete_image_file, download_image


class ImageWorker:
    """Single-process durable worker backed by resource_operations."""

    def __init__(self, poll_seconds: float = 5.0):
        self.poll_seconds = poll_seconds
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._recover_interrupted()
        self._thread = threading.Thread(target=self._run, name="pkvm-image-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=10)

    def wake(self) -> None:
        self._wake.set()

    def _recover_interrupted(self) -> None:
        db = SessionLocal()
        try:
            db.query(ResourceOperation).filter(
                ResourceOperation.resource_type == "IMAGE",
                ResourceOperation.status == "RUNNING",
            ).update({ResourceOperation.status: "PENDING", ResourceOperation.error_message: "서비스 재시작 후 재개"})
            db.commit()
        finally:
            db.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self.process_once():
                self._wake.wait(self.poll_seconds)
                self._wake.clear()

    def process_once(self) -> bool:
        db = SessionLocal()
        operation = None
        try:
            operation = db.query(ResourceOperation).filter(
                ResourceOperation.resource_type == "IMAGE",
                ResourceOperation.status == "PENDING",
            ).order_by(ResourceOperation.id).first()
            if not operation:
                return False
            operation.status = "RUNNING"
            operation.error_message = None
            db.commit()
            try:
                if operation.action == "DOWNLOAD":
                    download_image(operation.resource_id)
                elif operation.action == "DELETE":
                    delete_image_file(operation.resource_id)
                else:
                    raise RuntimeError(f"지원하지 않는 이미지 작업: {operation.action}")
                db.refresh(operation)
                operation.status = "SUCCEEDED"
            except Exception as exc:
                db.rollback()
                operation = db.query(ResourceOperation).filter(ResourceOperation.id == operation.id).first()
                operation.status = "FAILED"
                operation.error_message = str(exc)[:2000]
            operation.completed_at = datetime.utcnow()
            db.commit()
            return True
        finally:
            db.close()


image_worker = ImageWorker()
