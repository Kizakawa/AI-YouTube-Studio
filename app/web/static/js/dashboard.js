/**
 * AI YOUTUBE MULTI-CHANNEL STUDIO - DASHBOARD LOGIC
 */

document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    checkAuthAlerts();
    fetchAllData();
    
    // Tự động làm mới dữ liệu sau mỗi 10 giây
    setInterval(fetchAllData, 10000);
    
    // Nút làm mới thủ công
    const refreshBtn = document.getElementById('btn-refresh');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            refreshBtn.classList.add('loading');
            fetchAllData().finally(() => {
                setTimeout(() => refreshBtn.classList.remove('loading'), 500);
            });
        });
    }
});

function checkAuthAlerts() {
    const params = new URLSearchParams(window.location.search);
    const container = document.getElementById('auth-alert-container');
    if (!container) return;

    if (params.get('auth_success')) {
        const ch = params.get('channel') || '';
        container.innerHTML = `
            <div class="auth-alert auth-alert-success">
                <span>🎉 <strong>Thành công!</strong> Đã xác thực Google OAuth2 thành công cho kênh: <strong>${escapeHtml(ch)}</strong>. Hệ thống đã có thể tự động upload và đặt lịch!</span>
                <button onclick="this.parentElement.remove()" style="background:none; border:none; color:inherit; cursor:pointer;">&times;</button>
            </div>
        `;
        window.history.replaceState({}, document.title, window.location.pathname);
    } else if (params.get('auth_error')) {
        container.innerHTML = `
            <div class="auth-alert auth-alert-error">
                <span>❌ <strong>Lỗi kết nối:</strong> ${escapeHtml(params.get('auth_error'))}</span>
                <button onclick="this.parentElement.remove()" style="background:none; border:none; color:inherit; cursor:pointer;">&times;</button>
            </div>
        `;
        window.history.replaceState({}, document.title, window.location.pathname);
    }
}

function initNavigation() {
    const navItems = document.querySelectorAll('.nav-item');
    const tabContents = document.querySelectorAll('.tab-content');

    navItems.forEach(item => {
        item.addEventListener('click', () => {
            const targetTab = item.getAttribute('data-tab');
            
            navItems.forEach(nav => nav.classList.remove('active'));
            tabContents.forEach(tab => tab.classList.remove('active'));

            item.classList.add('active');
            const activeTab = document.getElementById(`tab-${targetTab}`);
            if (activeTab) {
                activeTab.classList.add('active');
            }
        });
    });
}

async function fetchAllData() {
    try {
        await Promise.all([
            fetchStats(),
            fetchActivities(),
            fetchMembers(),
            fetchChannels(),
            fetchVideos()
        ]);
        updateLastSyncTime();
    } catch (error) {
        console.error('Lỗi khi tải dữ liệu dashboard:', error);
    }
}

async function fetchStats() {
    const res = await fetch('/api/stats');
    if (!res.ok) return;
    const data = await res.json();

    document.getElementById('stat-total-videos').textContent = data.total_videos || 0;
    document.getElementById('stat-published').textContent = data.published || 0;
    document.getElementById('stat-members').textContent = data.total_members || 0;
    document.getElementById('stat-channels').textContent = data.total_channels || 0;
    document.getElementById('stat-drive-storage').textContent = data.storage_drive_total || '0 TB';
    document.getElementById('stat-gemini-keys').textContent = `${data.gemini_keys_count} Keys (Pool)`;
}

async function fetchActivities() {
    const res = await fetch('/api/activities');
    if (!res.ok) return;
    const activities = await res.json();
    const container = document.getElementById('activity-feed-container');
    if (!container) return;

    if (activities.length === 0) {
        container.innerHTML = `
            <div style="padding: 24px; text-align: center; color: var(--text-muted); font-size: 13px;">
                Chưa có hoạt động nào được ghi lại từ Telegram. Hãy gõ <code>/start</code> hoặc <code>/idea</code> trên Telegram!
            </div>
        `;
        return;
    }

    container.innerHTML = activities.map(item => {
        let icon = '⚡';
        let actionBadge = 'LỆNH TELEGRAM';
        let badgeClass = 'badge-blue';

        if (item.action_type === 'CMD_IDEA') {
            icon = '💡';
            actionBadge = 'Ý TƯỞNG MỚI';
            badgeClass = 'badge-purple';
        } else if (item.action_type === 'CHANGE_CHANNEL') {
            icon = '📺';
            actionBadge = 'ĐỔI KÊNH';
            badgeClass = 'badge-amber';
        } else if (item.action_type === 'SCRIPT_CREATED') {
            icon = '🎬';
            actionBadge = 'KỊCH BẢN XONG';
            badgeClass = 'badge-green';
        } else if (item.action_type === 'ADD_CHANNEL') {
            icon = '➕';
            actionBadge = 'THÊM KÊNH MỚI';
            badgeClass = 'badge-blue';
        } else if (item.action_type === 'AUTH_SUCCESS') {
            icon = '🔗';
            actionBadge = 'KẾT NỐI OAUTH';
            badgeClass = 'badge-green';
        }

        return `
            <div class="activity-item">
                <div class="activity-icon-badge">${icon}</div>
                <div class="activity-content">
                    <div class="activity-header-line">
                        <span class="activity-user">@${escapeHtml(item.username)}</span>
                        <span class="activity-time">${item.created_at}</span>
                    </div>
                    <div class="activity-desc">${escapeHtml(item.details)}</div>
                    <div style="margin-top: 6px;">
                        <span class="badge ${badgeClass}">${actionBadge}</span>
                        ${item.channel_id ? `<span class="badge" style="background: rgba(255,255,255,0.06); color: var(--text-secondary);">Kênh: ${item.channel_id}</span>` : ''}
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

async function fetchMembers() {
    const res = await fetch('/api/members');
    if (!res.ok) return;
    const members = await res.json();
    const tbody = document.getElementById('members-table-body');
    if (!tbody) return;

    if (members.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted);">Chưa có thành viên nào tương tác với bot.</td></tr>`;
        return;
    }

    tbody.innerHTML = members.map((m, index) => {
        const roleBadge = m.role === 'admin' ? '<span class="badge" style="background: rgba(251,113,133,0.15); color: var(--accent-rose);">Admin</span>' : '<span class="badge badge-blue">Creator</span>';
        const initial = (m.username || 'U')[0].toUpperCase();
        
        return `
            <tr>
                <td><strong>#${index + 1}</strong></td>
                <td>
                    <div class="user-cell">
                        <div class="user-avatar">${initial}</div>
                        <div>
                            <div style="font-weight: 600; color: var(--text-primary);">@${escapeHtml(m.username)}</div>
                            <div style="font-size: 11px; color: var(--text-muted);">ID: ${m.telegram_id}</div>
                        </div>
                    </div>
                </td>
                <td>${roleBadge}</td>
                <td><code style="color: var(--accent-blue);">${m.active_channel_id}</code></td>
                <td><strong style="color: var(--accent-green); font-size: 15px;">${m.total_jobs}</strong> video</td>
                <td><span style="font-size: 12px; color: var(--text-muted);">${m.last_active}</span></td>
            </tr>
        `;
    }).join('');
}

async function fetchChannels() {
    const res = await fetch('/api/channels');
    if (!res.ok) return;
    const channels = await res.json();
    const container = document.getElementById('channels-container');
    if (!container) return;

    if (channels.length === 0) {
        container.innerHTML = `
            <div style="padding: 30px; text-align: center; color: var(--text-muted); grid-column: 1/-1;">
                Chưa có kênh nào. Bấm nút <strong>"➕ Thêm Kênh Mới"</strong> ở góc phải để tạo kênh đầu tiên!
            </div>
        `;
        return;
    }

    container.innerHTML = channels.map(ch => {
        const isAuth = ch.is_authenticated;
        const statusBadge = isAuth 
            ? `<span class="badge badge-green">✓ Đã Kết Nối Google OAuth</span>`
            : `<span class="badge badge-amber">🔴 Chưa Kết Nối Token</span>`;

        const authAction = isAuth
            ? `<div style="display: flex; gap: 8px; align-items: center; margin-top: 12px;">
                 <span style="font-size: 12px; color: var(--accent-green); font-weight: 600;">🟢 Sẵn sàng đăng video</span>
                 <a href="/auth/youtube/${ch.id}" style="font-size: 11px; color: var(--text-muted); text-decoration: underline;">Đăng nhập lại</a>
               </div>`
            : `<a href="/auth/youtube/${ch.id}" class="btn-auth">🔗 Đăng Nhập & Cấp Quyền YouTube</a>`;

        const avatarImg = ch.avatar_url 
            ? `<img src="${ch.avatar_url}" style="width: 42px; height: 42px; border-radius: 50%; object-fit: cover; border: 2px solid var(--accent-blue);">`
            : `<div style="width: 42px; height: 42px; border-radius: 50%; background: var(--gradient-brand); display: flex; align-items: center; justify-content: center; font-size: 20px;">📺</div>`;

        return `
            <div class="channel-card">
                <div class="channel-header">
                    <div style="display: flex; align-items: center; gap: 12px;">
                        ${avatarImg}
                        <div class="channel-title">
                            <h4>${escapeHtml(ch.name)}</h4>
                            <span>ID: <code>${ch.id}</code> | Ngách: <strong>${escapeHtml(ch.niche)}</strong></span>
                            ${ch.real_title ? `<div style="font-size: 11.5px; color: var(--accent-blue);">YouTube: ${escapeHtml(ch.real_title)} (${ch.subscribers} subs)</div>` : ''}
                        </div>
                    </div>
                    <div>${statusBadge}</div>
                </div>

                <div class="channel-metrics">
                    <div class="metric-item">
                        <span>Tài Khoản Ultra</span>
                        <strong>${(ch.google_account || 'account_1').toUpperCase()}</strong>
                    </div>
                    <div class="metric-item">
                        <span>Chế Độ Ảnh</span>
                        <strong>${ch.visual_mode === 'imagen_ai' ? 'Google Imagen 3 ✨' : 'Pexels Stock'}</strong>
                    </div>
                    <div class="metric-item">
                        <span>Subscribers Thật</span>
                        <strong style="color: var(--accent-green);">${ch.subscribers || 0}</strong>
                    </div>
                    <div class="metric-item">
                        <span>Video Đã Có</span>
                        <strong>${ch.video_count || 0}</strong>
                    </div>
                </div>

                <div style="display: flex; align-items: center; justify-content: space-between; border-top: 1px solid var(--border-color); padding-top: 12px;">
                    <div>${authAction}</div>
                    <button onclick="deleteChannel('${ch.id}')" class="btn-delete" title="Xóa kênh khỏi hệ thống">🗑️ Xóa</button>
                </div>
            </div>
        `;
    }).join('');
}

async function fetchVideos() {
    const res = await fetch('/api/videos');
    if (!res.ok) return;
    const videos = await res.json();
    const tbody = document.getElementById('videos-table-body');
    if (!tbody) return;

    if (videos.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted);">Chưa có video nào. Hãy dùng lệnh <code>/idea &lt;chủ đề&gt;</code> trên Telegram!</td></tr>`;
        return;
    }

    tbody.innerHTML = videos.map(v => {
        let statusBadge = '<span class="badge badge-blue">DRAFT</span>';
        if (v.status === 'SCRIPT_READY') statusBadge = '<span class="badge badge-purple">KỊCH BẢN XONG</span>';
        if (v.status === 'RENDERING') statusBadge = '<span class="badge badge-amber">ĐANG RENDER</span>';
        if (v.status === 'PUBLISHED') statusBadge = '<span class="badge badge-green">ĐÃ XUẤT BẢN</span>';

        return `
            <tr>
                <td><strong>#${v.id}</strong></td>
                <td>
                    <div style="font-weight: 600; color: var(--text-primary); max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                        ${escapeHtml(v.title || v.topic)}
                    </div>
                    <div style="font-size: 11.5px; color: var(--text-muted);">Chủ đề: ${escapeHtml(v.topic)}</div>
                </td>
                <td><code>${v.channel_id}</code></td>
                <td>@${escapeHtml(v.creator)}</td>
                <td>${statusBadge}</td>
                <td><span style="font-size: 12px; color: var(--text-muted);">${v.created_at}</span></td>
            </tr>
        `;
    }).join('');
}

// Modal Control
function openModal(id) {
    const modal = document.getElementById(id);
    if (modal) modal.classList.add('active');
}

function closeModal(id) {
    const modal = document.getElementById(id);
    if (modal) modal.classList.remove('active');
}

// Thêm kênh mới
async function submitAddChannel(e) {
    e.preventDefault();
    const payload = {
        id: document.getElementById('new-ch-id').value.trim(),
        name: document.getElementById('new-ch-name').value.trim(),
        niche: document.getElementById('new-ch-niche').value,
        google_account: document.getElementById('new-ch-account').value,
        visual_mode: document.getElementById('new-ch-visual').value,
        preferred_voice: document.getElementById('new-ch-voice').value,
        language: 'en'
    };

    try {
        const res = await fetch('/api/channels/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!res.ok) {
            alert('Lỗi: ' + (data.detail || 'Không thể tạo kênh'));
            return;
        }

        closeModal('modal-add-channel');
        document.getElementById('form-add-channel').reset();
        await fetchChannels();
        await fetchStats();
        alert(`✅ Đã thêm kênh '${payload.name}' thành công! Bây giờ bạn có thể bấm Kết nối Google OAuth.`);
    } catch (err) {
        alert('Lỗi khi gửi yêu cầu: ' + err);
    }
}

// Xóa kênh
async function deleteChannel(channelId) {
    if (!confirm(`Bạn có chắc chắn muốn xóa kênh '${channelId}' khỏi hệ thống không?`)) {
        return;
    }
    try {
        const res = await fetch(`/api/channels/${channelId}`, { method: 'DELETE' });
        if (res.ok) {
            await fetchChannels();
            await fetchStats();
        } else {
            alert('Không thể xóa kênh.');
        }
    } catch (err) {
        alert('Lỗi khi xóa: ' + err);
    }
}

// Upload client_secret.json
async function submitUploadSecret(e) {
    e.preventDefault();
    const fileInput = document.getElementById('input-client-secret');
    if (!fileInput.files.length) return;

    const formData = new FormData();
    formData.append('file', fileInput.files[0]);

    try {
        const res = await fetch('/api/credentials/upload_secret', {
            method: 'POST',
            body: formData
        });
        const data = await res.json();
        if (res.ok) {
            closeModal('modal-upload-secret');
            alert('✅ Đã lưu file client_secret.json thành công! Bạn đã có thể bấm Kết Nối Google OAuth cho các kênh.');
        } else {
            alert('Lỗi: ' + data.detail);
        }
    } catch (err) {
        alert('Lỗi khi tải file: ' + err);
    }
}

function updateLastSyncTime() {
    const el = document.getElementById('last-sync-time');
    if (el) {
        const now = new Date();
        el.textContent = `Đồng bộ: ${now.toLocaleTimeString()}`;
    }
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}
