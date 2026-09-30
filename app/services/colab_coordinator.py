"""
app.services.colab_coordinator — Bộ điều phối và Đồng bộ trạng thái đa chiều giữa Chatbot, Agent và Colab GPU Cluster qua Google Drive.

Thư mục lưu trữ trên Google Drive:
  AI_YouTube_RenderFarm/
  ├── coordinator/
  │   └── cluster_state.json    <-- FILE TRUNG TÂM PHỐI HỢP
  ├── pending/                  <-- Các task JSON đang chờ Colab nhận
  ├── in_progress/              <-- Các task JSON Colab đang xử lý
  └── completed/                <-- Video MP4 hoàn thiện

Tính năng:
1. Đọc/Ghi trạng thái thời gian thực giữa Bot Telegram, AI Agent và Google Colab.
2. Tự động nhận diện khi nào cần gọi người dùng mở Colab (kèm link mở thẳng đúng tài khoản/node).
3. Cơ chế xoay vòng tài khoản (Account Rotation): Tự động phát hiện khi tài nguyên/quota của Node này hết để chuyển sang Node/tài khoản khác.
4. Điều khiển Colab tự động tắt (Runtime Unassign) ngay sau khi render xong để không tốn Compute Units.
"""

import io
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from app.agents.drive_agent import DriveAgent
from config.settings import settings

logger = logging.getLogger(__name__)

COORDINATOR_FOLDER = "AI_YouTube_RenderFarm/coordinator"
STATE_FILE_NAME = "cluster_state.json"
LOCAL_STATE_CACHE = "storage/cache/cluster_state.json"

DEFAULT_CLUSTER_NODES = {
    1: {
        "node_id": 1,
        "name": "Node 1 (Chính - Pro+ GPU)",
        "account": "pool1.studio@example.com",
        "notebook_id": "1n2wi-ZuXZpx_5OfLE20qctkGRIPikDoL",
        "notebook_file": "colab_gpu_worker.ipynb",
        "status": "IDLE",            # IDLE, RUNNING, COMPLETED, QUOTA_EXHAUSTED
        "last_heartbeat": None,
        "completed_count": 0,
        "current_job_id": None,
        "error_message": None
    },
    2: {
        "node_id": 2,
        "name": "Node 2 (Dự phòng 1 - Song Song)",
        "account": "pool2.studio@example.com",
        "notebook_id": "1e1Wd972RIFIPm7lotcsUY5_Je9rqvf3f",
        "notebook_file": "colab_worker_node2.ipynb",
        "status": "IDLE",
        "last_heartbeat": None,
        "completed_count": 0,
        "current_job_id": None,
        "error_message": None
    },
    3: {
        "node_id": 3,
        "name": "Node 3 (Dự phòng 2 - Siêu Tốc)",
        "account": "pool3.studio@example.com",
        "notebook_id": "1-gDTr-ZncnsHkXKQMtqcSOPk8Cu4OOn4",
        "notebook_file": "colab_worker_node3.ipynb",
        "status": "IDLE",
        "last_heartbeat": None,
        "completed_count": 0,
        "current_job_id": None,
        "error_message": None
    }
}


class ColabCoordinator:
    """Singleton Quản lý trạng thái và Điều phối cụm Render Colab Farm"""

    _instance = None
    _cached_state: Optional[Dict[str, Any]] = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(ColabCoordinator, cls).__new__(cls)
        return cls._instance

    @staticmethod
    def _get_active_drive_agent() -> DriveAgent:
        for acc in ["account_2", "account_3", "account_1"]:
            try:
                da = DriveAgent(acc)
                if da.service:
                    da.service.files().list(pageSize=1, fields="files(id)").execute()
                    return da
            except Exception:
                continue
        return DriveAgent("account_2")

    def __init__(self, drive_agent: Optional[DriveAgent] = None):
        self.da = drive_agent or self._get_active_drive_agent()

    def _get_default_state(self) -> Dict[str, Any]:
        return {
            "version": "2.0",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "active_node": 1,
            "active_account": "pool1.studio@example.com",
            "auto_rotation_enabled": True,
            "cluster_nodes": DEFAULT_CLUSTER_NODES,
            "queue_summary": {
                "pending_count": 0,
                "pending_job_ids": [],
                "in_progress_count": 0,
                "in_progress_job_ids": [],
                "completed_count": 0,
                "completed_job_ids": []
            },
            "action_required": {
                "needs_user_run": False,
                "target_node": 1,
                "target_account": "pool1.studio@example.com",
                "direct_colab_url": self.make_node_url("pool1.studio@example.com", 1),
                "reason": "Hàng đợi trống"
            },
            "rotation_history": []
        }

    @staticmethod
    def make_node_url(account: str = "", node_id: int = 1) -> str:
        """
        Tạo link mở Google Colab với tài khoản Google cụ thể:
        - Sử dụng Google AccountChooser với tham số Email và continue.
        - Nếu trình duyệt đã đăng nhập đúng tài khoản: Mở thẳng Colab của tài khoản đó.
        - Nếu chưa đăng nhập hoặc đang dùng tài khoản khác: Google hiển thị màn hình yêu cầu đăng nhập đúng tài khoản này (điền sẵn email), sau đó tự động chuyển hướng vào Colab.
        - Tuyệt đối không dẫn ra Google Drive hay báo lỗi OAuth GapiError.
        """
        node_cfg = DEFAULT_CLUSTER_NODES.get(node_id, DEFAULT_CLUSTER_NODES[1])
        nb_id = node_cfg["notebook_id"]
        raw_colab_url = f"https://colab.research.google.com/drive/{nb_id}"
        if account:
            import urllib.parse
            encoded_continue = urllib.parse.quote(raw_colab_url, safe="")
            return f"https://accounts.google.com/AccountChooser?Email={account}&continue={encoded_continue}"
        return raw_colab_url

    def read_state_from_drive(self) -> Dict[str, Any]:
        """Tải file cluster_state.json từ Google Drive (hoặc đọc local cache nếu chưa có)"""
        try:
            if not self.da.service:
                self.da._init_service()
            if not self.da.service:
                return self._read_local_cache()

            folder_id = self.da.get_folder_by_path(COORDINATOR_FOLDER)
            if not folder_id:
                return self._read_local_cache()

            q = f"name = '{STATE_FILE_NAME}' and '{folder_id}' in parents and trashed = false"
            res = self.da.service.files().list(q=q, fields="files(id, name)").execute()
            files = res.get("files", [])
            if not files:
                # Chưa có file trên Drive, tạo mới file mặc định
                default_state = self._get_default_state()
                self.write_state_to_drive(default_state)
                return default_state

            file_id = files[0]["id"]
            content_bytes = self.da.service.files().get_media(fileId=file_id).execute()
            state = json.loads(content_bytes.decode("utf-8"))
            self._cached_state = state
            self._save_local_cache(state)
            return state
        except Exception as e:
            logger.debug(f"[ColabCoordinator] Lỗi đọc state từ Drive: {e}")
            return self._read_local_cache()

    def write_state_to_drive(self, state: Dict[str, Any]) -> bool:
        """Lưu file cluster_state.json lên Google Drive và cập nhật cache local"""
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._cached_state = state
        self._save_local_cache(state)

        try:
            if not self.da.service:
                self.da._init_service()
            if not self.da.service:
                return False

            folder_id = self.da.get_folder_by_path(COORDINATOR_FOLDER)
            if not folder_id:
                return False

            from googleapiclient.http import MediaIoBaseUpload
            json_bytes = json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8")
            media = MediaIoBaseUpload(io.BytesIO(json_bytes), mimetype="application/json", resumable=False)

            # Xóa file cũ nếu có để tránh duplicate
            q = f"name = '{STATE_FILE_NAME}' and '{folder_id}' in parents and trashed = false"
            res = self.da.service.files().list(q=q, fields="files(id)").execute()
            for old_f in res.get("files", []):
                try:
                    self.da.service.files().delete(fileId=old_f["id"]).execute()
                except Exception:
                    pass

            meta = {
                "name": STATE_FILE_NAME,
                "parents": [folder_id],
                "description": "Trung tâm điều phối AI YouTube Render Farm"
            }
            self.da.service.files().create(body=meta, media_body=media, fields="id").execute()
            logger.info(f"✅ [ColabCoordinator] Đã đồng bộ cluster_state.json lên Drive thành công!")
            return True
        except Exception as e:
            logger.warning(f"⚠️ [ColabCoordinator] Lỗi ghi state lên Drive: {e}")
            return False

    def sync_cluster_state(self) -> Dict[str, Any]:
        """
        Đồng bộ toàn diện giữa các thư mục trên Drive và file cluster_state.json:
        - Quét pending/, in_progress/, completed/
        - Kiểm tra Heartbeat của Worker Colab
        - Tự động xoay vòng Node nếu Node hiện tại bị QUOTA_EXHAUSTED
        - Tính toán action_required để Chatbot biết khi nào cần gửi link mở Colab
        """
        state = self.read_state_from_drive()

        # 1. Quét danh sách pending tasks trên Drive
        pending_files = self.da.list_pending_tasks()
        pending_ids = []
        for pf in pending_files:
            m = re.search(r'task_(\d+)\.json', pf.get("name", ""))
            if m:
                pending_ids.append(int(m.group(1)))
        pending_ids.sort()

        # 2. Quét danh sách completed videos trên Drive
        completed_files = self.da.list_completed_videos()
        completed_ids = []
        for cf in completed_files:
            m = re.search(r'video_(\d+)\.mp4', cf.get("name", ""))
            if m:
                completed_ids.append(int(m.group(1)))
        completed_ids.sort()

        # 3. Cập nhật Queue Summary
        state["queue_summary"] = {
            "pending_count": len(pending_ids),
            "pending_job_ids": pending_ids,
            "in_progress_count": len(state.get("queue_summary", {}).get("in_progress_job_ids", [])),
            "in_progress_job_ids": state.get("queue_summary", {}).get("in_progress_job_ids", []),
            "completed_count": len(completed_ids),
            "completed_job_ids": completed_ids
        }

        # 4. Kiểm tra trạng thái Heartbeat của từng Node
        nodes = state.get("cluster_nodes", DEFAULT_CLUSTER_NODES)
        now_ts = datetime.now(timezone.utc).timestamp()
        active_worker_alive = False

        for nid_str, ninfo in nodes.items():
            hb = ninfo.get("last_heartbeat")
            if hb:
                try:
                    hb_dt = datetime.fromisoformat(hb)
                    diff_sec = now_ts - hb_dt.timestamp()
                    if diff_sec > 180 and ninfo.get("status") == "RUNNING":
                        # Quá 3 phút không có tín hiệu -> coi như đã tắt hoặc ngắt kết nối
                        ninfo["status"] = "IDLE"
                        ninfo["current_job_id"] = None
                    elif diff_sec <= 180 and ninfo.get("status") == "RUNNING":
                        active_worker_alive = True
                except Exception:
                    pass

        # 5. Xoay vòng tài khoản nếu Node hiện tại bị QUOTA_EXHAUSTED
        cur_node_id = state.get("active_node", 1)
        cur_node_info = nodes.get(str(cur_node_id)) or nodes.get(cur_node_id) or DEFAULT_CLUSTER_NODES[1]

        if cur_node_info.get("status") == "QUOTA_EXHAUSTED" and state.get("auto_rotation_enabled", True):
            # Tìm node kế tiếp còn khả dụng
            next_node_id = (cur_node_id % 3) + 1
            for _ in range(3):
                candidate_info = nodes.get(str(next_node_id)) or nodes.get(next_node_id)
                if candidate_info and candidate_info.get("status") != "QUOTA_EXHAUSTED":
                    # Xoay sang node này
                    state["rotation_history"].append({
                        "from_node": cur_node_id,
                        "to_node": next_node_id,
                        "reason": f"Node {cur_node_id} hết tài nguyên GPU (QUOTA_EXHAUSTED)",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
                    state["active_node"] = next_node_id
                    state["active_account"] = candidate_info.get("account", "pool1.studio@example.com")
                    cur_node_id = next_node_id
                    cur_node_info = candidate_info
                    logger.info(f"🔄 [ColabCoordinator] Tự động xoay sang Node {next_node_id} ({cur_node_info['account']}) do hết quota!")
                    break
                next_node_id = (next_node_id % 3) + 1

        # 6. Tính toán Action Required cho Chatbot Telegram
        target_account = cur_node_info.get("account", "pool1.studio@example.com")
        colab_url = self.make_node_url(target_account, cur_node_id)

        if pending_ids and not active_worker_alive:
            # Có video chờ và CHƯA có Colab nào đang chạy -> CẦN NGƯỜI DÙNG BẤM MỞ LINK
            state["action_required"] = {
                "needs_user_run": True,
                "target_node": cur_node_id,
                "target_node_name": cur_node_info.get("name", f"Node {cur_node_id}"),
                "target_account": target_account,
                "direct_colab_url": colab_url,
                "reason": f"Có {len(pending_ids)} video kịch bản đang chờ render trên GPU"
            }
        elif active_worker_alive:
            # Colab đang chạy -> Không cần bấm mở lại
            state["action_required"] = {
                "needs_user_run": False,
                "target_node": cur_node_id,
                "target_node_name": cur_node_info.get("name", f"Node {cur_node_id}"),
                "target_account": target_account,
                "direct_colab_url": colab_url,
                "reason": "Colab GPU Worker đang tích cực render..."
            }
        else:
            # Hàng đợi trống
            state["action_required"] = {
                "needs_user_run": False,
                "target_node": cur_node_id,
                "target_node_name": cur_node_info.get("name", f"Node {cur_node_id}"),
                "target_account": target_account,
                "direct_colab_url": colab_url,
                "reason": "Toàn bộ video đã render hoàn tất. Colab tự động tắt để tiết kiệm Compute Units."
            }

        state["cluster_nodes"] = nodes
        self.write_state_to_drive(state)
        return state

    def mark_node_status(self, node_id: int, status: str, current_job_id: Optional[int] = None, error: Optional[str] = None) -> Dict[str, Any]:
        """Cập nhật trạng thái của 1 Node (do Colab hoặc Bot gọi)"""
        state = self.read_state_from_drive()
        nodes = state.get("cluster_nodes", DEFAULT_CLUSTER_NODES)
        nid_key = str(node_id) if str(node_id) in nodes else node_id

        if nid_key in nodes:
            nodes[nid_key]["status"] = status
            nodes[nid_key]["last_heartbeat"] = datetime.now(timezone.utc).isoformat()
            nodes[nid_key]["current_job_id"] = current_job_id
            if error:
                nodes[nid_key]["error_message"] = error
            if status == "COMPLETED":
                nodes[nid_key]["completed_count"] = nodes[nid_key].get("completed_count", 0) + 1

        state["cluster_nodes"] = nodes
        self.write_state_to_drive(state)
        return state

    def rotate_to_next_node(self, reason: str = "Thủ công hoặc hết Compute Units") -> Dict[str, Any]:
        """Xoay sang Node tiếp theo (1 -> 2 -> 3 -> 1)"""
        state = self.read_state_from_drive()
        cur_id = state.get("active_node", 1)
        next_id = (cur_id % 3) + 1

        nodes = state.get("cluster_nodes", DEFAULT_CLUSTER_NODES)
        ninfo = nodes.get(str(next_id)) or nodes.get(next_id) or DEFAULT_CLUSTER_NODES[next_id]

        state["rotation_history"].append({
            "from_node": cur_id,
            "to_node": next_id,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        state["active_node"] = next_id
        state["active_account"] = ninfo.get("account", "pool1.studio@example.com")
        logger.info(f"🔄 [ColabCoordinator] Đã chuyển trạm render sang Node {next_id} ({state['active_account']}): {reason}")

        self.write_state_to_drive(state)
        return self.sync_cluster_state()

    def _read_local_cache(self) -> Dict[str, Any]:
        if os.path.exists(LOCAL_STATE_CACHE):
            try:
                return json.loads(Path(LOCAL_STATE_CACHE).read_text(encoding="utf-8"))
            except Exception:
                pass
        return self._get_default_state()

    def _save_local_cache(self, state: Dict[str, Any]):
        try:
            Path(LOCAL_STATE_CACHE).parent.mkdir(parents=True, exist_ok=True)
            Path(LOCAL_STATE_CACHE).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


# Singleton instance toàn cục
colab_coordinator = ColabCoordinator()
