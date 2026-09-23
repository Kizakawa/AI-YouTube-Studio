"""
RenderQueue — Hệ thống hàng đợi FIFO cho video rendering.

Đảm bảo tại mỗi thời điểm chỉ có DUY NHẤT 1 video đang render trên máy,
các yêu cầu tiếp theo sẽ xếp hàng chờ và tự động thực thi tuần tự.

Thread-safe, Singleton pattern — dùng chung cho toàn bộ bot + web.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Callable, Awaitable, List

logger = logging.getLogger(__name__)


class QueueItemStatus(str, Enum):
    WAITING = "WAITING"       # Đang chờ trong hàng đợi
    RENDERING = "RENDERING"   # Đang render
    COMPLETED = "COMPLETED"   # Hoàn thành
    FAILED = "FAILED"         # Thất bại
    CANCELLED = "CANCELLED"   # Đã hủy


@dataclass
class RenderQueueItem:
    """Một mục trong hàng đợi render."""
    job_id: int
    user_name: str
    chat_id: Optional[int] = None
    status: QueueItemStatus = QueueItemStatus.WAITING
    position: int = 0  # Vị trí trong hàng đợi (1-indexed)
    queued_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error_message: Optional[str] = None


class RenderQueue:
    """
    Singleton hàng đợi render FIFO.
    
    Sử dụng asyncio.Queue để đảm bảo thread-safe.
    Tự động xử lý tuần tự — 1 video tại 1 thời điểm.
    """
    _instance: Optional["RenderQueue"] = None
    _initialized: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if RenderQueue._initialized:
            return
        RenderQueue._initialized = True

        self._queue: asyncio.Queue = asyncio.Queue()
        self._history: List[RenderQueueItem] = []  # Lịch sử render
        self._current_item: Optional[RenderQueueItem] = None
        self._is_processing: bool = False
        self._worker_task: Optional[asyncio.Task] = None
        self._position_counter: int = 0

        logger.info("🏭 RenderQueue Singleton đã khởi tạo — FIFO mode")

    @property
    def is_busy(self) -> bool:
        """Đang có video nào render không?"""
        return self._current_item is not None

    @property
    def queue_size(self) -> int:
        """Số lượng video đang chờ trong hàng đợi."""
        return self._queue.qsize()

    @property
    def current_job(self) -> Optional[RenderQueueItem]:
        """Video đang render hiện tại."""
        return self._current_item

    def get_queue_snapshot(self) -> List[RenderQueueItem]:
        """Snapshot toàn bộ hàng đợi (đang chờ + đang render)."""
        items = []
        if self._current_item:
            items.append(self._current_item)
        # Lấy snapshot từ internal queue (không pop)
        for item_tuple in list(self._queue._queue):
            items.append(item_tuple[0])  # (item, render_func)
        return items

    def get_history(self, limit: int = 20) -> List[RenderQueueItem]:
        """Lấy lịch sử render gần nhất."""
        return self._history[-limit:]

    async def enqueue(
        self,
        job_id: int,
        user_name: str,
        chat_id: Optional[int],
        render_func: Callable[[], Awaitable[None]],
        notify_func: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> RenderQueueItem:
        """
        Thêm 1 video vào hàng đợi render.
        
        Args:
            job_id: ID của VideoJob trong DB
            user_name: Tên người yêu cầu
            chat_id: Telegram chat ID (để gửi thông báo)
            render_func: Async function thực hiện render (đã bind sẵn tham số)
            notify_func: Async function gửi thông báo trạng thái (nhận str message)
        
        Returns:
            RenderQueueItem với thông tin vị trí trong hàng đợi
        """
        self._position_counter += 1
        
        # Tính vị trí thực tế trong hàng đợi
        position = self._queue.qsize() + (1 if self._current_item else 0) + 1
        
        item = RenderQueueItem(
            job_id=job_id,
            user_name=user_name,
            chat_id=chat_id,
            position=position,
        )

        await self._queue.put((item, render_func, notify_func))
        logger.info(
            f"📥 Đã thêm Video #{job_id} vào hàng đợi — "
            f"Vị trí: #{position} | Đang chờ: {self._queue.qsize()}"
        )

        # Đảm bảo worker đang chạy
        self._ensure_worker_running()

        return item

    def _ensure_worker_running(self):
        """Khởi động worker nếu chưa chạy."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())
            logger.info("🔄 RenderQueue Worker đã khởi động")

    async def _worker_loop(self):
        """
        Vòng lặp worker — lấy từng item từ queue và render tuần tự.
        Tự động dừng khi hàng đợi trống.
        """
        logger.info("🏭 RenderQueue Worker Loop bắt đầu")
        
        while True:
            try:
                # Chờ item tiếp theo (blocking cho đến khi có)
                # Timeout 30s để không chạy vĩnh viễn khi queue trống
                try:
                    item, render_func, notify_func = await asyncio.wait_for(
                        self._queue.get(), timeout=30.0
                    )
                except asyncio.TimeoutError:
                    logger.info("🏭 RenderQueue Worker: Hàng đợi trống 30s, tạm nghỉ")
                    break

                # Cập nhật trạng thái
                item.status = QueueItemStatus.RENDERING
                item.started_at = datetime.utcnow()
                self._current_item = item

                logger.info(f"🎬 Bắt đầu render Video #{item.job_id} (bởi {item.user_name})")

                # Thông báo cho các item đang chờ về vị trí mới
                await self._notify_queue_update()

                try:
                    # RENDER — chỉ 1 video tại 1 thời điểm
                    await render_func()
                    
                    item.status = QueueItemStatus.COMPLETED
                    item.finished_at = datetime.utcnow()
                    logger.info(
                        f"✅ Video #{item.job_id} đã render xong | "
                        f"Thời gian: {(item.finished_at - item.started_at).total_seconds():.1f}s"
                    )

                except Exception as e:
                    item.status = QueueItemStatus.FAILED
                    item.finished_at = datetime.utcnow()
                    item.error_message = str(e)
                    logger.error(f"❌ Render Video #{item.job_id} thất bại: {e}")

                finally:
                    # Lưu lịch sử và xóa current
                    self._history.append(item)
                    self._current_item = None
                    self._queue.task_done()

                    # Thông báo queue còn bao nhiêu
                    remaining = self._queue.qsize()
                    if remaining > 0:
                        logger.info(f"📋 Hàng đợi còn {remaining} video chờ render")
                    else:
                        logger.info("📋 Hàng đợi render đã trống")

            except Exception as e:
                logger.error(f"❌ RenderQueue Worker lỗi không mong đợi: {e}", exc_info=True)
                self._current_item = None
                await asyncio.sleep(2)  # Tránh vòng lặp lỗi nhanh

        logger.info("🏭 RenderQueue Worker Loop kết thúc")

    async def _notify_queue_update(self):
        """Cập nhật vị trí cho tất cả item đang chờ trong hàng đợi."""
        pos = 1  # Bắt đầu từ 1 (sau item đang render)
        for item_tuple in list(self._queue._queue):
            item = item_tuple[0]
            item.position = pos + 1  # +1 vì có 1 item đang render
            pos += 1

    async def cancel_job(self, job_id: int) -> bool:
        """
        Hủy 1 job trong hàng đợi (chỉ hủy được nếu đang WAITING, không hủy được đang RENDERING).
        
        Returns: True nếu hủy thành công
        """
        # Duyệt queue để tìm và đánh dấu hủy
        new_items = []
        cancelled = False
        while not self._queue.empty():
            try:
                item_tuple = self._queue.get_nowait()
                item = item_tuple[0]
                if item.job_id == job_id and item.status == QueueItemStatus.WAITING:
                    item.status = QueueItemStatus.CANCELLED
                    item.finished_at = datetime.utcnow()
                    self._history.append(item)
                    cancelled = True
                    self._queue.task_done()
                    logger.info(f"🚫 Đã hủy Video #{job_id} khỏi hàng đợi")
                else:
                    new_items.append(item_tuple)
            except asyncio.QueueEmpty:
                break

        # Đưa các item còn lại trở lại queue
        for item_tuple in new_items:
            await self._queue.put(item_tuple)

        return cancelled

    def get_status_text(self) -> str:
        """Tạo text mô tả trạng thái hàng đợi render cho Telegram."""
        lines = ["📋 **TRẠNG THÁI HÀNG ĐỢI RENDER:**\n"]

        if self._current_item:
            item = self._current_item
            elapsed = (datetime.utcnow() - item.started_at).total_seconds() if item.started_at else 0
            lines.append(
                f"🔴 **Đang render:** Video #{item.job_id} "
                f"(bởi {item.user_name}) — {elapsed:.0f}s"
            )
        else:
            lines.append("🟢 **Máy render:** Rảnh — sẵn sàng nhận việc")

        queue_items = self.get_queue_snapshot()
        waiting = [i for i in queue_items if i.status == QueueItemStatus.WAITING]

        if waiting:
            lines.append(f"\n⏳ **Đang chờ:** {len(waiting)} video")
            for idx, item in enumerate(waiting, 1):
                wait_time = (datetime.utcnow() - item.queued_at).total_seconds()
                lines.append(
                    f"  {idx}. Video #{item.job_id} "
                    f"({item.user_name}) — chờ {wait_time:.0f}s"
                )
        else:
            lines.append("\n✅ Không có video nào đang chờ")

        # Lịch sử gần nhất
        recent = [h for h in self._history[-3:] if h.status in (QueueItemStatus.COMPLETED, QueueItemStatus.FAILED)]
        if recent:
            lines.append("\n📜 **Gần đây:**")
            for item in recent:
                icon = "✅" if item.status == QueueItemStatus.COMPLETED else "❌"
                duration = ""
                if item.started_at and item.finished_at:
                    dur_s = (item.finished_at - item.started_at).total_seconds()
                    duration = f" ({dur_s:.0f}s)"
                lines.append(f"  {icon} Video #{item.job_id}{duration}")

        return "\n".join(lines)


# ─── Singleton accessor ──────────────────────
def get_render_queue() -> RenderQueue:
    """Lấy instance duy nhất của RenderQueue."""
    return RenderQueue()
