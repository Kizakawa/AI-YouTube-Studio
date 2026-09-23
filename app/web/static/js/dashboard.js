/**
 * AI YOUTUBE MULTI-CHANNEL STUDIO - DASHBOARD LOGIC
 */

document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
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
    
    const authEl = document.getElementById('stat-auth-channels');
    if (authEl) {
        authEl.textContent = `${data.auth_channels || 0}/${data.total_channels || 0} kênh`;
        authEl.style.color = (data.auth_channels > 0) ? 'var(--accent-green)' : 'var(--accent-rose)';
    }

    document.getElementById('stat-drive-storage').textContent = data.storage_drive_total || 'Chưa kết nối';
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
        const roleBadge = m.role === 'admin' ? '<span class="badge badge-rose" style="background: rgba(251,113,133,0.15); color: var(--accent-rose);">Admin</span>' : '<span class="badge badge-blue">Creator</span>';
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

    container.innerHTML = channels.map(ch => {
        return `
            <div class="channel-card">
                <div class="channel-header">
                    <div class="channel-title">
                        <h4>📺 ${escapeHtml(ch.name)}</h4>
                        <span>Ngách: <strong>${escapeHtml(ch.niche)}</strong> | Ngôn ngữ: ${ch.language.toUpperCase()}</span>
                    </div>
                    <span class="badge ${ch.is_authenticated ? 'badge-green' : 'badge-amber'}" style="${ch.is_authenticated ? '' : 'background: rgba(239, 68, 68, 0.15); color: #f87171;'}">${ch.token_status}</span>
                </div>
                <div class="channel-metrics">
                    <div class="metric-item">
                        <span>Tài Khoản Google Ultra</span>
                        <strong>${ch.google_account.toUpperCase()}</strong>
                    </div>
                    <div class="metric-item">
                        <span>Kho Drive 2TB</span>
                        <strong style="color: var(--accent-blue);">${ch.drive_quota}</strong>
                    </div>
                    <div class="metric-item">
                        <span>Video Đã Sản Xuất</span>
                        <strong>${ch.video_count} video</strong>
                    </div>
                    <div class="metric-item">
                        <span>Giọng Đọc AI (TTS)</span>
                        <strong style="font-size: 12px;">${ch.preferred_voice.split('-')[2] || ch.preferred_voice}</strong>
                    </div>
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
