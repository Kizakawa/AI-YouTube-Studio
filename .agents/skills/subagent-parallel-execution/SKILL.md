---
name: subagent-parallel-execution
description: >-
  Quy chuẩn điều phối Đa Agent / Subagents chạy song song, Phân vùng Độc quyền File (Disjoint File Ownership),
  Chống xung đột Git Index Lock và Hợp nhất Code An toàn (tích hợp từ chuẩn mã nguồn mở GitHub obra/superpowers:
  dispatching-parallel-agents, subagent-driven-development, using-git-worktrees & Claude Code Multi-Agent Coordination).
---

# Quy Chuẩn Điều Phối Subagent Song Song & Chống Dẫm Chân Nhau (`subagent-parallel-execution`)

Skill này định nghĩa giao thức bắt buộc khi khởi chạy nhiều **Subagents chạy song song** hoặc khi làm việc chung trên cùng một kho mã nguồn (Workspace) có **các Agent nền khác đang hoạt động đồng thời**. Mục tiêu là đạt tốc độ thực thi song song tối đa (`O(1)` wall-clock time) mà **0% xung đột ghi đè file (`write collision`)** và **0% lỗi khóa Git (`index.lock` / mất code của Agent khác)**.

---

## 1. Giao Thức 5 Bước Điều Phối Song Song Không Xung Đột (Zero-Collision Parallel Protocol)

### Bước 1: Quét Hiện Trạng & Nhận Diện Agent Khác Đang Chạy Ngầm (`Live Peer-Agent Recon`)
Trước khi phân công hoặc sửa bất kỳ file nào, **Lead Agent (Agent Điều Phối)** bắt buộc chạy lệnh kiểm tra trạng thái:
```powershell
git status -sb
git diff --stat
```
- **Danh sách File Đang Bị Chạm (`Dirty Fileset`)**: Ghi nhận toàn bộ các file đang có thay đổi chưa commit của các Agent khác đang chạy ngầm.
- **Quy tắc Bất khả xâm phạm (`Zero-Destructive Git Policy`)**:
  - **CẤM TUYỆT ĐỐI** chạy các lệnh xóa hoặc ẩn thay đổi của Agent khác: `git reset --hard`, `git checkout -- <file>`, `git restore`, `git clean -fd`, hoặc `git stash` toàn cục khi có Agent khác đang chạy.
  - **CẤM TUYỆT ĐỐI** dùng `write_file` (ghi đè toàn bộ file) lên các file mã nguồn đã tồn tại; bắt buộc dùng `edit_file` (thay thế đúng khối dòng mục tiêu) để giữ nguyên các đoạn code của Agent khác trong cùng file.

### Bước 2: Lập Ma Trận Độc Quyền File (`Disjoint File-Ownership Matrix`)
Theo chuẩn `obra/superpowers` (`dispatching-parallel-agents`), một tác vụ lớn chỉ được chia cho $N$ Subagents chạy song song khi tập hợp file chỉnh sửa của chúng **hoàn toàn rời nhau** ($F_i \cap F_j = \emptyset \quad \forall i \neq j$):

| Subagent / Luồng | Phạm vi trách nhiệm (Domain Scope) | Tập file được phép sửa độc quyền ($F_i$) | Các file CẤM chạm vào |
| :--- | :--- | :--- | :--- |
| **Subagent 1** | Module A (VD: Audio & SFX Engine) | `app/utils/ffmpeg/audio_and_sfx.py`, `app/agents/video_agent.py` | Mọi file ngoài danh sách $F_1$ |
| **Subagent 2** | Module B (VD: Motion & Multi-Cut) | `app/utils/ffmpeg/color_and_motion.py` | Mọi file ngoài danh sách $F_2$ |
| **Subagent 3** | Module C (VD: LLM Media Director) | `app/graph/tools/llm_media_director.py`, `app/graph/nodes/media_nodes.py` | Mọi file ngoài danh sách $F_3$ |
| **Subagent 4** | Module D (VD: Publisher & Scheduler) | `app/agents/publisher/engagement_comments_mixin.py`, `app/services/smart_scheduler.py` | Mọi file ngoài danh sách $F_4$ |

- **Trường hợp 2 phương án nâng cấp cùng cần sửa 1 file duy nhất**:
  - Giao file đó cho **1 Subagent duy nhất** (hoặc **Lead Agent**) thực hiện gộp cả 2 thay đổi, hoặc chỉ định rõ **phạm vi hàm / số dòng không giao nhau** (`Function-Level Ownership`).

### Bước 3: Quy Tắc "Nhiều Worker Sửa File — Duy Nhất 1 Lead Chạy Git" (`Single-Committer Lock Rule`)
Nguyên nhân số 1 gây lỗi khi nhiều Subagent chạy song song trên cùng thư mục là tranh chấp `.git/index.lock` khi cùng gọi `git add` / `git commit` / `git pull`. Do đó:
1. **Quyền hạn của Worker Subagent**:
   - Chỉ được đọc file cần thiết và dùng `edit_file` trên đúng các file thuộc tập $F_i$ được giao.
   - Chỉ chạy lệnh kiểm tra cú pháp độc lập: `python -m py_compile <file_thuộc_F_i>`.
   - **CẤM TUYỆT ĐỐI Worker Subagent chạy bất kỳ lệnh `git` ghi trạng thái nào** (`git add`, `git commit`, `git push`, `git pull`, `git rebase`, `git stash`, `git checkout`).
2. **Cơ chế Đánh thức Phản ứng (`Reactive Wakeup — No Polling`)**:
   - Lead Agent khởi chạy đồng loạt các Subagent trong **cùng 1 lượt gọi tool** (`invoke_subagent`), sau đó kết thúc lượt hoặc làm việc độc lập để hệ thống tự động đánh thức khi các Subagent hoàn tất. Không bao giờ dùng vòng lặp `sleep` hay polling.

### Bước 4: Kiểm Định Chéo & Hợp Nhất Kết Quả Tại Lead Agent (`Trust-But-Verify & Additive Merge`)
Khi tất cả Worker Subagents đã báo cáo hoàn tất:
1. **Kiểm tra phạm vi (`Scope Audit`)**:
   - Lead Agent chạy `git diff --stat` để xác nhận mỗi Subagent chỉ sửa đúng file được giao và không làm mất bất kỳ dòng code nào của các Agent khác đang chạy ngầm.
2. **Kiểm tra tuân thủ kiến trúc (`Anti-Overfitting & Backward Compatibility`)**:
   - Đảm bảo không có Subagent nào hardcode từ khóa chủ đề hoặc làm gãy chữ ký hàm (`**kwargs`).
3. **Chạy kiểm chứng tích hợp (`Unified Compile & Smoke Test`)**:
   - Chạy `python -m py_compile` cho toàn bộ các file thuộc $\bigcup F_i$ và chạy kịch bản kiểm thử tích hợp (Smoke Test) xác nhận cả 4 luồng phối hợp trơn tru.

### Bước 5: Đồng Bộ Nhánh & Commit Độc Lập Không Xung Đột (`Selective Staging & Safe Sync`)
Để đẩy kết quả nâng cấp lên GitHub (`origin/main`) mà không chạm vào các file đang làm dở của Agent khác:
1. **Chỉ Stage đúng các file thuộc phạm vi đã hoàn thành (`Explicit File Staging`)**:
   ```powershell
   git add <file_1> <file_2> ...
   ```
   *(Tuyệt đối KHÔNG dùng `git add .` hay `git add -A` khi có Agent khác đang sửa file trong cùng workspace).*
2. **Commit gói nâng cấp**:
   ```powershell
   git commit -m "feat(scope): summary of parallel multi-module upgrades"
   ```
3. **Đồng bộ `origin/main` an toàn khi có file chưa stage của Agent khác**:
   - Kiểm tra xem `origin/main` có commit mới không (`git fetch origin main`).
   - Nếu nhánh hiện tại cần rebase lên `origin/main` trong khi working tree còn file đang sửa của Agent khác, sử dụng cờ `--autostash` chỉ khi an toàn hoặc merge trực tiếp `git merge origin/main --no-edit` (vì `git merge` chỉ chạm vào các file có thay đổi trên `origin/main`, giữ nguyên các file dirty không liên quan của Agent khác!).
   - Đẩy lên GitHub:
     ```powershell
     git push origin main
     ```
