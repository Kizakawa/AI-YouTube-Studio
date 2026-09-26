/**
 * AI YOUTUBE STUDIO v2.0 — INTERACTIVE DASHBOARD LOGIC
 */

/* ═══════════════════════════════════════════════════════
   STATE & GLOBALS
══════════════════════════════════════════════════════════ */
let allVideos = [];
let currentFilter = 'all';

const PAGE_META = {
    overview:      { title: 'Dashboard Tổng Quan',   sub: 'Theo dõi toàn bộ hệ thống AI YouTube Studio' },
    analytics:     { title: 'Số Liệu & Dự Đoán Tăng Trưởng', sub: 'Thống kê kênh thời gian thực & Mô hình dự đoán AI' },
    create:        { title: 'Tạo Kịch Bản AI',       sub: 'Nhập chủ đề — Gemini sinh kịch bản tự động' },
    manual_studio: { title: 'Studio Lồng Tiếng & Hiệu Ứng AI', sub: 'Tải 1 video đã ghép (Google Flow, CapCut...) + Lời thoại, AI sẽ lồng tiếng đọc, SFX và phụ đề Shorts cực đẹp' },
    pipeline:      { title: 'Pipeline Sản Xuất',      sub: 'Quản lý danh sách video và tiến độ render' },
    activity:      { title: 'Nhật Ký Hoạt Động',      sub: 'Lệnh Telegram thời gian thực — tự làm mới 10s' },
    channels:      { title: 'Kênh & Tài Khoản',       sub: 'Ma trận kênh YouTube và Google Ultra Pool' },
    members:       { title: 'Thành Viên Nhóm',         sub: 'Bảng xếp hạng đóng góp của các thành viên' },
};

/* ═══════════════════════════════════════════════════════
   INIT & SMART POLLING
══════════════════════════════════════════════════════════ */
let pollTimeout = null;

function scheduleNextFetch(delay = 10000) {
    if (pollTimeout) clearTimeout(pollTimeout);
    pollTimeout = setTimeout(async () => {
        await fetchAllData();
        const hasRendering = (allVideos || []).some(v => v.status === 'RENDERING');
        scheduleNextFetch(hasRendering ? 3000 : 10000);
    }, delay);
}

document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    fetchAllData().then(() => scheduleNextFetch(10000));

    document.getElementById('btn-refresh').addEventListener('click', () => {
        const btn = document.getElementById('btn-refresh');
        btn.disabled = true;
        fetchAllData().finally(() => { 
            btn.disabled = false;
            scheduleNextFetch(10000);
        });
    });
});

/* ═══════════════════════════════════════════════════════
   NAVIGATION
══════════════════════════════════════════════════════════ */
function initNavigation() {
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', () => switchTab(item.dataset.tab));
    });
}

function switchTab(tabId) {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));

    const navEl = document.getElementById(`nav-${tabId}`);
    const tabEl = document.getElementById(`tab-${tabId}`);
    if (navEl) navEl.classList.add('active');
    if (tabEl) tabEl.classList.add('active');

    const meta = PAGE_META[tabId] || {};
    document.getElementById('page-title').textContent = meta.title || tabId;
    document.getElementById('page-subtitle').textContent = meta.sub || '';

    if (tabId === 'channels') {
        fetchAccounts();
    } else if (tabId === 'analytics') {
        loadAnalytics();
    } else if (tabId === 'manual_studio') {
        initManualStudio();
    }
}

function navigateToVideos(statusFilter) {
    switchTab('pipeline');
    filterPipeline(statusFilter);
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

/* ═══════════════════════════════════════════════════════
   FETCH ALL
══════════════════════════════════════════════════════════ */
async function fetchAllData() {
    await Promise.allSettled([
        fetchStats(),
        fetchActivities(),
        fetchMembers(),
        fetchChannels(),
        fetchVideos(),
        fetchHealth(),
    ]);
    updateSyncTime();

    // Tải thông tin token ngầm sau khi UI đã hiển thị đầy đủ (không làm đơ/chậm trang web)
    if (!window._tokenHealthLoaded) {
        window._tokenHealthLoaded = true;
        setTimeout(() => checkTokenHealth(false), 1500);
    }
}

/* ═══════════════════════════════════════════════════════
   STATS
══════════════════════════════════════════════════════════ */
async function fetchStats() {
    const data = await apiGet('/api/stats');
    if (!data) return;
    setText('stat-total-videos',  data.total_videos  ?? 0);
    setText('stat-published',     data.published     ?? 0);
    setText('stat-failed',        data.failed        ?? 0);
    setText('stat-script-ready',  data.script_ready  ?? 0);
    setText('stat-rendering',     data.rendering     ?? 0);
    setText('stat-rendered',      data.rendered      ?? 0);
    setText('stat-channels',      data.total_channels ?? 0);
    setText('stat-gemini-keys',   `${data.gemini_keys_count ?? 0} keys`);
    setText('stat-members',       data.total_members  ?? 0);
    setText('stat-activities',    data.total_activities ?? 0);

    const authEl = document.getElementById('stat-auth-channels');
    if (authEl) {
        authEl.textContent = `${data.auth_channels || 0}/${data.total_channels || 0} OAuth`;
        authEl.style.color = (data.auth_channels > 0) ? 'var(--accent-green)' : 'var(--accent-amber)';
    }

    const driveEl = document.getElementById('stat-drive-storage');
    if (driveEl) {
        driveEl.textContent = data.storage_drive_total || 'Chưa kết nối';
    }
}

/* ═══════════════════════════════════════════════════════
   HEALTH
══════════════════════════════════════════════════════════ */
async function fetchHealth() {
    const data = await apiGet('/api/health');
    const badge = document.getElementById('system-health-badge');

    if (!data) {
        if (badge) { badge.classList.add('offline'); badge.querySelector('span').textContent = 'Server offline!'; }
        return;
    }

    if (badge) {
        badge.classList.remove('offline');
        if (data.web_only) {
            badge.querySelector('span').textContent = '⚠️ Chỉ Web — Bot Tele tắt';
            badge.style.background = 'rgba(251,191,36,0.08)';
            badge.style.borderColor = 'rgba(251,191,36,0.2)';
            badge.style.color = 'var(--accent-amber)';
            const dot = badge.querySelector('.status-dot');
            if (dot) { dot.style.background = 'var(--accent-amber)'; dot.style.boxShadow = '0 0 8px var(--accent-amber)'; }
        } else {
            badge.querySelector('span').textContent = 'Hệ thống trực tuyến';
        }
    }

    setHealth('health-ffmpeg',   data.ffmpeg !== 'not installed', data.ffmpeg !== 'not installed' ? data.ffmpeg : 'Chưa cài đặt');
    setHealth('health-gemini',   data.gemini_keys > 0, `${data.gemini_keys} API key(s) trong pool`);
    setHealth('health-telegram', data.telegram_token && !data.web_only, 
        data.web_only ? '⚠️ Web-Only mode — Bot polling tắt' : 
        (data.telegram_token ? 'Token đã cấu hình' : 'Chưa có BOT TOKEN trong .env'));
    setHealth('health-drive', data.drive_connected,
        data.drive_connected
            ? (data.drive_quota_label || '20 TB Google One (Đã kết nối ✓)')
            : 'Chưa xác thực — Chạy: py auth_google.py drive');
}

function setHealth(id, ok, msg) {
    const el = document.getElementById(id);
    if (!el) return;
    const dot = el.querySelector('.health-dot');
    const span = el.querySelector('span');
    dot.className = 'health-dot ' + (ok ? 'dot-ok' : 'dot-warn');
    span.textContent = msg;
    span.style.color = ok ? 'var(--accent-green)' : 'var(--accent-amber)';
}

/* ═══════════════════════════════════════════════════════
   TOKEN HEALTH MONITOR (Part 4)
══════════════════════════════════════════════════════════ */
async function checkTokenHealth(force = false) {
    const btn = document.getElementById('btn-check-tokens');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<span class="loading-spinner"></span> Đang kiểm tra...`;
    }

    if (force) {
        // Show loading state in key lists only on explicit click
        ['gemini-keys-list', 'pexels-keys-list'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.innerHTML = `<div class="token-checking"><span class="loading-spinner"></span> Đang kiểm tra keys...</div>`;
        });
    }

    try {
        const url = force ? '/api/token-health?force=true' : '/api/token-health';
        const data = await apiGet(url);
        if (data) {
            renderTokenHealth(data);
        }
    } catch (e) {
        showToast('Lỗi kiểm tra token: ' + e.message, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `<svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg> Kiểm Tra`;
        }
    }
}

function renderTokenHealth(data) {
    const CIRCUMFERENCE = 2 * Math.PI * 52; // ~326.73

    function updateGauge(gaugeId, pctId, percent, serviceName) {
        const gauge = document.getElementById(gaugeId);
        const pctEl = document.getElementById(pctId);
        if (!gauge || !pctEl) return;

        const offset = CIRCUMFERENCE - (percent / 100) * CIRCUMFERENCE;
        gauge.style.strokeDashoffset = offset;

        // Color coding
        gauge.classList.remove('gauge-warn', 'gauge-danger');
        if (percent <= 30) gauge.classList.add('gauge-danger');
        else if (percent <= 70) gauge.classList.add('gauge-warn');

        pctEl.textContent = percent > 0 ? `${percent}%` : '0%';
    }

    function renderKeysList(containerId, keys, type) {
        const container = document.getElementById(containerId);
        if (!container) return;

        if (!keys || keys.length === 0) {
            container.innerHTML = `<div style="color:var(--text-muted);font-size:12px;">Chưa cấu hình key nào trong .env</div>`;
            return;
        }

        container.innerHTML = keys.map(k => {
            const isOk = k.status === 'ok';
            const isWarn = k.status === 'quota_exceeded' || k.status === 'timeout';
            const statusClass = isOk ? 'key-ok' : (isWarn ? 'key-warn' : 'key-error');
            const icon = isOk ? '✅' : (isWarn ? '⚠️' : '❌');

            let infoText = '';
            let infoClass = '';
            if (isOk) {
                if (type === 'gemini') {
                    infoText = `${k.models_count || '?'} models`;
                } else {
                    infoText = k.remaining !== undefined ? `${k.remaining}/${k.limit} còn lại` : 'OK';
                }
                infoClass = 'key-info-ok';
            } else {
                infoText = k.error || k.status;
                infoClass = isWarn ? 'key-info-warn' : 'key-info-error';
            }

            return `
                <div class="key-status-item ${statusClass}">
                    <span class="key-status-icon">${icon}</span>
                    <span class="key-prefix">${esc(k.prefix)}</span>
                    <span class="key-info ${infoClass}">${esc(infoText)}</span>
                </div>`;
        }).join('');
    }

    // Gemini
    const gd = data.gemini || {};
    updateGauge('gauge-gemini', 'gauge-gemini-pct', gd.percent || 0, 'Gemini');
    setText('gemini-count-badge', `${gd.valid || 0}/${gd.total || 0} hoạt động`);
    renderKeysList('gemini-keys-list', gd.keys, 'gemini');

    // Pexels
    const pd = data.pexels || {};
    updateGauge('gauge-pexels', 'gauge-pexels-pct', pd.percent || 0, 'Pexels');
    setText('pexels-count-badge', `${pd.valid || 0}/${pd.total || 0} hoạt động`);
    renderKeysList('pexels-keys-list', pd.keys, 'pexels');
}

/* ═══════════════════════════════════════════════════════
   ACTIVITIES
══════════════════════════════════════════════════════════ */
async function fetchActivities() {
    const activities = await apiGet('/api/activities');
    const container = document.getElementById('activity-feed-container');
    if (!container) return;

    if (!activities || activities.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">⚡</div>
                <p>Chưa có hoạt động nào.<br>Hãy gõ <code>/start</code> hoặc <code>/idea &lt;chủ đề&gt;</code> trên Telegram!</p>
            </div>`;
        return;
    }

    const iconMap = {
        CMD_IDEA:       { icon: '💡', label: 'Ý TƯỞNG MỚI',    cls: 'badge-purple' },
        CHANGE_CHANNEL: { icon: '📺', label: 'ĐỔI KÊNH',        cls: 'badge-amber'  },
        SCRIPT_CREATED: { icon: '🎬', label: 'KỊCH BẢN XONG',   cls: 'badge-green'  },
        START_BOT:      { icon: '🚀', label: 'KHỞI ĐỘNG BOT',   cls: 'badge-blue'   },
        CANCEL_JOB:     { icon: '❌', label: 'HỦY JOB',          cls: 'badge-rose'   },
        REGENERATE:     { icon: '🔄', label: 'TẠO LẠI',          cls: 'badge-amber'  },
    };

    container.innerHTML = activities.map(item => {
        const m = iconMap[item.action_type] || { icon: '⚡', label: item.action_type, cls: 'badge-blue' };
        return `
        <div class="activity-item">
            <div class="activity-icon-badge">${m.icon}</div>
            <div class="activity-content">
                <div class="activity-header-line">
                    <span class="activity-user">@${esc(item.username)}</span>
                    <span class="activity-time">${item.created_at}</span>
                </div>
                <div class="activity-desc">${esc(item.details)}</div>
                <div style="margin-top:6px; display:flex; gap:6px; flex-wrap:wrap;">
                    <span class="badge ${m.cls}">${m.label}</span>
                    ${item.channel_id ? `<span class="badge" style="background:rgba(255,255,255,0.05);color:var(--text-muted);">Kênh: ${esc(item.channel_id)}</span>` : ''}
                </div>
            </div>
        </div>`;
    }).join('');
}

/* ═══════════════════════════════════════════════════════
   MEMBERS
══════════════════════════════════════════════════════════ */
async function fetchMembers() {
    const members = await apiGet('/api/members');
    const tbody = document.getElementById('members-table-body');
    if (!tbody) return;

    if (!members || members.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" class="empty-state">Chưa có thành viên nào tương tác với bot.</td></tr>`;
        return;
    }

    tbody.innerHTML = members.map((m, i) => {
        const roleBadge = m.role === 'admin'
            ? `<span class="badge badge-rose">Admin</span>`
            : `<span class="badge badge-blue">Creator</span>`;
        const init = (m.username || 'U')[0].toUpperCase();
        return `
        <tr>
            <td><strong>#${i + 1}</strong></td>
            <td>
                <div class="user-cell">
                    <div class="user-avatar">${init}</div>
                    <div>
                        <div style="font-weight:600;">@${esc(m.username)}</div>
                        <div style="font-size:11px;color:var(--text-muted);">ID: ${m.telegram_id}</div>
                    </div>
                </div>
            </td>
            <td>${roleBadge}</td>
            <td><code>${esc(m.active_channel_id)}</code></td>
            <td><strong style="color:var(--accent-green)">${m.total_jobs}</strong> video</td>
            <td><span style="color:var(--accent-purple)">${m.total_activities}</span> lệnh</td>
            <td style="font-size:12px;color:var(--text-muted);">${m.last_active}</td>
        </tr>`;
    }).join('');
}

/* ═══════════════════════════════════════════════════════
   PIPELINE / VIDEOS
══════════════════════════════════════════════════════════ */
async function fetchVideos() {
    const videos = await apiGet('/api/videos');
    if (videos) { allVideos = videos; }
    renderPipelineTable(allVideos.filter(v => currentFilter === 'all' || v.status === currentFilter));
}

function filterPipeline(status) {
    currentFilter = status;
    document.querySelectorAll('.filter-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.status === status);
    });
    renderPipelineTable(allVideos.filter(v => status === 'all' || v.status === status));
}

function renderPipelineTable(videos) {
    const tbody = document.getElementById('videos-table-body');
    if (!tbody) return;

    // Cập nhật nút Khôi phục Job Lỗi nếu có video FAILED
    const recoverBtn = document.getElementById('btn-recover-failed');
    const failedBadge = document.getElementById('failed-count-badge');
    const totalFailed = (allVideos || []).filter(v => v.status === 'FAILED').length;
    if (recoverBtn && failedBadge) {
        if (totalFailed > 0) {
            failedBadge.textContent = totalFailed;
            recoverBtn.style.display = 'inline-flex';
        } else {
            recoverBtn.style.display = 'none';
        }
    }

    if (!videos || videos.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8"><div class="empty-state"><div class="empty-icon">🎞️</div><p>Không có video nào. Hãy tạo kịch bản mới!</p></div></td></tr>`;
        return;
    }

    const statusMap = {
        DRAFT:        `<span class="badge" style="background:rgba(255,255,255,0.07);color:var(--text-muted)">DRAFT</span>`,
        SCRIPT_READY: `<span class="badge badge-purple">Kịch bản xong</span>`,
        AUDIO_READY:  `<span class="badge badge-blue">Audio xong</span>`,
        MEDIA_READY:  `<span class="badge badge-blue">Media xong</span>`,
        RENDERING:    `<span class="badge badge-amber">⏳ Đang render</span>`,
        RENDERED:     `<span class="badge badge-green">✅ Đã render</span>`,
        PUBLISHED:    `<span class="badge badge-green">🚀 Đã đăng</span>`,
        FAILED:       `<span class="badge badge-rose">❌ Lỗi</span>`,
    };

    tbody.innerHTML = videos.map(v => {
        const dur = v.audio_duration_ms > 0 ? `${(v.audio_duration_ms / 1000).toFixed(1)}s` : '—';
        const scenes = v.scene_count > 0 ? `${v.scene_count} cảnh` : '—';
        let statusBadge = statusMap[v.status] || `<span class="badge">${esc(v.status)}</span>`;

        if (v.status === 'RENDERING') {
            const pct = Math.max(0, Math.min(100, v.progress_percent || 0));
            statusBadge = `
                <div style="min-width:140px;">
                    <div style="display:flex;justify-content:space-between;font-size:11px;font-weight:700;margin-bottom:3px;color:var(--accent-amber);">
                        <span>🎬 Đang dựng video</span>
                        <span>${pct}%</span>
                    </div>
                    <div style="background:rgba(255,255,255,0.1);height:6px;border-radius:3px;overflow:hidden;">
                        <div style="background:linear-gradient(90deg,#f59e0b,#10b981);height:100%;width:${pct}%;transition:width 0.4s ease;"></div>
                    </div>
                    <div style="font-size:10.5px;color:var(--text-muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:160px;" title="${esc(v.progress_message || '')}">
                        ${esc(v.progress_message || 'Đang xử lý...')}
                    </div>
                </div>`;
        }

        // Source badge: Telegram vs Web
        const isWeb = v.source === 'web';
        const sourceBadge = isWeb 
            ? `<span class="badge badge-blue" style="font-size:11px;">🌐 Web</span>`
            : `<span class="badge badge-purple" style="font-size:11px;">📱 Telegram</span>`;
        const creatorLine = isWeb ? '' : `<div style="font-size:10.5px;color:var(--text-muted);margin-top:2px;">@${esc(v.creator)}</div>`;

        // Channel & Gmail resolution
        const chObj = (allChannels || []).find(c => c.id === v.channel_id);
        const chName = chObj ? chObj.name : v.channel_id;
        const chGmail = chObj ? (chObj.gmail || chObj.email_label || chObj.google_account) : '';

        // Action buttons
        let actionButtons = `<button class="btn btn-secondary btn-sm" onclick="openVideoDetail(${v.id})" title="Chi tiết">🔍</button>`;
        if (v.status === 'SCRIPT_READY') {
            actionButtons += `<button class="btn btn-secondary btn-sm" onclick="openInstantPreviewForJob(${v.id})" title="Xem trước video và phụ đề tức thì" style="font-size:11px; color:#f472b6; border-color:rgba(236,72,153,0.4);">👁️ Xem trước</button>`;
            actionButtons += `<button class="btn btn-secondary btn-sm" onclick="openScriptEditor(${v.id})" title="Chỉnh sửa kịch bản" style="font-size:11px;">✏️ Sửa</button>`;
            actionButtons += `<button class="btn btn-primary btn-sm" onclick="triggerWebRender(${v.id})" title="Xuất Video" style="font-size:11px;">🚀 Render</button>`;
        } else if (v.status === 'FAILED') {
            actionButtons += `<button class="btn btn-secondary btn-sm" onclick="openInstantPreviewForJob(${v.id})" title="Xem trước kịch bản" style="font-size:11px; color:#f472b6; border-color:rgba(236,72,153,0.4);">👁️ Xem trước</button>`;
            actionButtons += `<button class="btn btn-secondary btn-sm" onclick="openScriptEditor(${v.id})" title="Chỉnh sửa kịch bản" style="font-size:11px;">✏️ Sửa</button>`;
            actionButtons += `<button class="btn btn-primary btn-sm" onclick="triggerWebRender(${v.id})" title="Thử render lại" style="font-size:11px;background:var(--accent-amber);border-color:var(--accent-amber);">🔄 Thử lại</button>`;
        } else if (v.status === 'RENDERED') {
            actionButtons += `<button class="btn btn-primary btn-sm" onclick="openPublishModal(${v.id})" title="Chọn kênh & Đăng YouTube" style="font-size:11px;background:linear-gradient(135deg,#ef4444,#dc2626);border:none;font-weight:600;">🚀 Đăng YT</button>`;
            actionButtons += `<button class="btn btn-secondary btn-sm" onclick="openVideoDetail(${v.id})" title="Chi tiết video" style="font-size:11px;">🔍</button>`;
        }
        actionButtons += `<button class="btn btn-danger btn-sm" onclick="deleteJob(${v.id})" title="Xoá">🗑</button>`;

        return `
        <tr>
            <td><code>#${v.id}</code></td>
            <td>
                <div style="font-weight:600;color:var(--text-primary);max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(v.title)}">${esc(v.title)}</div>
                <div style="font-size:11.5px;color:var(--text-muted);">${esc(v.topic)}</div>
            </td>
            <td>
                <div class="channel-cell-name">📺 ${esc(chName)}</div>
                <div class="channel-cell-gmail">
                    <span>📧</span>
                    <span>${esc(chGmail || v.channel_id)}</span>
                </div>
            </td>
            <td>
                ${sourceBadge}
                ${creatorLine}
            </td>
            <td style="font-size:12px;color:var(--text-muted);">${scenes} / ${dur}</td>
            <td>${statusBadge}</td>
            <td style="font-size:12px;color:var(--text-muted);">${v.created_at}</td>
            <td>
                <div class="table-actions">
                    ${actionButtons}
                </div>
            </td>
        </tr>`;
    }).join('');
}

/* ═══════════════════════════════════════════════════════
   CREATE SCRIPT
══════════════════════════════════════════════════════════ */
async function createScript() {
    const topic = document.getElementById('input-topic').value.trim();
    if (!topic) {
        showToast('Vui lòng nhập chủ đề video!', 'error');
        return;
    }

    const channel = document.getElementById('input-channel').value;
    const language = document.getElementById('input-language').value;

    const btn = document.getElementById('btn-create-script');
    btn.disabled = true;
    btn.innerHTML = `<span class="loading-spinner"></span> Đang gọi Gemini AI...`;

    try {
        const result = await apiPost('/api/scripts/create', { topic, channel_id: channel, language });

        if (!result) { return; }

        lastCreatedScriptResult = result;

        // Show result panel
        document.getElementById('result-job-badge').textContent = `Job #${result.job_id}`;
        document.getElementById('script-result-panel').style.display = 'flex';
        document.getElementById('script-result-content').innerHTML = buildScriptResultHTML(result);
        document.getElementById('script-result-panel').scrollIntoView({ behavior: 'smooth' });

        showToast(`✅ Kịch bản #${result.job_id} đã được tạo thành công!`, 'success');
        fetchVideos(); // refresh pipeline
    } catch (e) {
        showToast('Lỗi khi tạo kịch bản: ' + e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = `<svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg> Tạo Kịch Bản với Gemini AI`;
    }
}

function buildScriptResultHTML(result) {
    const scenesHTML = (result.scenes || []).map(s => {
        let stratBadge = '';
        if (s.production_method === 'IMAGE_TO_VIDEO' || s.media_type === 'i2v') {
            stratBadge = '<span class="badge" style="background:linear-gradient(135deg,#059669,#06b6d4);color:#fff;font-size:10px;padding:2px 6px;border:none;">🎨 Video từ Ảnh Chủ Đề (I2V)</span>';
        } else if (s.production_method === 'TEXT_TO_VIDEO' || s.visual_strategy === 'AI_VIDEO' || s.media_type === 'ai_video' || s.media_type === 't2v') {
            stratBadge = '<span class="badge" style="background:linear-gradient(135deg,#8b5cf6,#ec4899);color:#fff;font-size:10px;padding:2px 6px;border:none;">🔮 Video AI Trực Tiếp (T2V)</span>';
        } else if (s.production_method === 'REAL_ARCHIVE' || s.visual_strategy === 'REAL_DOCUMENTARY' || s.media_type === 'photo') {
            stratBadge = '<span class="badge badge-cyan" style="font-size:10px;padding:2px 6px;">📸 Tư Liệu Thật 100%</span>';
        } else if (s.production_method === 'STOCK_FOOTAGE' || s.visual_strategy === 'STOCK_VIDEO' || s.media_type === 'video') {
            stratBadge = '<span class="badge badge-amber" style="font-size:10px;padding:2px 6px;">🎥 Video B-Roll Thật</span>';
        } else if (s.visual_strategy === 'AI_IMAGE' || s.media_type === 'ai_image') {
            stratBadge = '<span class="badge badge-purple" style="font-size:10px;padding:2px 6px;">✨ Ảnh AI 8K</span>';
        }

        return `
        <div class="scene-item">
            <div class="scene-num">${s.scene_id}</div>
            <div class="scene-body">
                <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;flex-wrap:wrap;">
                    ${stratBadge}
                    ${s.tool ? `<span style="font-size:10px;color:var(--text-muted);background:rgba(255,255,255,0.06);padding:1px 5px;border-radius:4px;">🛠️ ${esc(s.tool)}</span>` : ''}
                </div>
                <div class="scene-narration">${esc(s.narration)}</div>
                <div class="scene-keyword">🔍 ${esc(s.visual_keyword)}</div>
                ${s.strategy_rationale ? `<div style="font-size:11px;color:#a78bfa;margin-top:2px;">🧠 Đạo diễn: ${esc(s.strategy_rationale)}</div>` : ''}
            </div>
            <div class="scene-dur">~${s.estimated_seconds}s</div>
        </div>`;
    }).join('');

    return `
        <div class="script-meta">
            <span class="badge badge-green">✅ Job #${result.job_id} đã lưu</span>
            <span class="badge badge-blue">${result.scene_count} phân cảnh</span>
            <span class="badge badge-amber">~${result.total_duration?.toFixed(0)}s tổng</span>
        </div>
        <div class="script-title">${esc(result.title)}</div>
        <div class="script-hook">💬 Hook: "${esc(result.hook)}"</div>

        <!-- Action Toolbar Right After Generation -->
        <div style="display:flex; gap:10px; flex-wrap:wrap; margin:16px 0; padding:12px 14px; background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.08); border-radius:10px; align-items:center;">
            <button class="btn btn-secondary" onclick="openInstantPreviewFromCreate()" style="background:linear-gradient(135deg, rgba(236,72,153,0.15), rgba(139,92,246,0.15)); border-color:rgba(236,72,153,0.45); color:#f472b6; font-weight:700; padding:9px 18px; box-shadow:0 2px 12px rgba(236,72,153,0.2);">
                👁️ Xem Trước Nhanh (Instant Preview)
            </button>
            <button class="btn btn-primary" onclick="triggerWebRender(${result.job_id})" style="background:linear-gradient(135deg,#3b82f6,#8b5cf6); border:none; font-weight:600; padding:9px 18px;">
                🚀 Bắt Đầu Tạo Video (Render)
            </button>
            <button class="btn btn-secondary" onclick="openScriptEditor(${result.job_id})" style="font-weight:600; padding:9px 16px;">
                ✏️ Chỉnh Sửa Kịch Bản (Thêm/Sửa/Xóa Cảnh)
            </button>
            <button class="btn btn-secondary" onclick="switchTab('pipeline')" style="font-size:12px; padding:9px 14px;">
                📋 Xem trong Pipeline →
            </button>
            <button class="btn btn-danger btn-sm" onclick="deleteJob(${result.job_id}); document.getElementById('script-result-panel').style.display='none';" style="margin-left:auto; padding:9px 14px;">
                🗑️ Xóa Kịch Bản
            </button>
        </div>

        <div>
            <div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--text-muted);margin-bottom:10px;">Phân Cảnh Chi Tiết</div>
            <div class="scenes-list">${scenesHTML}</div>
        </div>

        <!-- Bottom Action Bar -->
        <div style="display:flex; justify-content:space-between; align-items:center; margin-top:20px; padding-top:14px; border-top:1px solid rgba(255,255,255,0.08);">
            <button class="btn btn-secondary" onclick="openScriptEditor(${result.job_id})" style="font-weight:600;">
                ✏️ Chỉnh Sửa Kịch Bản Này
            </button>
            <button class="btn btn-primary btn-lg" onclick="triggerWebRender(${result.job_id})" style="background:linear-gradient(135deg,#3b82f6,#8b5cf6); border:none; font-weight:600;">
                🚀 Bắt Đầu Tạo Video Ngay →
            </button>
        </div>`;
}

/* ═══════════════════════════════════════════════════════
   VIDEO DETAIL MODAL
══════════════════════════════════════════════════════════ */
async function openVideoDetail(jobId) {
    const overlay = document.getElementById('modal-overlay');
    const body = document.getElementById('modal-body');
    document.getElementById('modal-title').textContent = `Chi Tiết Job #${jobId}`;
    overlay.classList.add('visible');
    body.innerHTML = `<div style="text-align:center;padding:32px;"><span class="loading-spinner"></span></div>`;

    const data = await apiGet(`/api/videos/${jobId}`);
    if (!data) {
        body.innerHTML = `<p style="color:var(--accent-rose)">Không tải được dữ liệu.</p>`;
        return;
    }

    const statusMap = {
        SCRIPT_READY: `<span class="badge badge-purple">Kịch bản xong</span>`,
        RENDERING:    `<span class="badge badge-amber">Đang render</span>`,
        RENDERED:     `<span class="badge badge-green">Đã render</span>`,
        PUBLISHED:    `<span class="badge badge-green">Đã đăng</span>`,
        FAILED:       `<span class="badge badge-rose">Lỗi</span>`,
    };

    const scenesHTML = (data.scenes || []).map(s => {
        const isVid = (s.media_type === 'video' || (s.media_path && s.media_path.endsWith('.mp4')));
        const isAi = (s.media_type === 'ai_image' || (s.media_path && s.media_path.includes('_ai')));

        let mediaBadge = '';
        if (s.production_method === 'IMAGE_TO_VIDEO' || (s.media_path && s.media_path.includes('_i2v'))) {
            mediaBadge = '<span class="badge" style="background:linear-gradient(135deg,#059669,#06b6d4);color:#fff;font-size:10px;padding:2px 6px;border:none;">🎨 Video từ Ảnh Chủ Đề (I2V)</span>';
        } else if (s.production_method === 'TEXT_TO_VIDEO' || (s.media_path && (s.media_path.includes('_t2v') || s.media_path.includes('ai_video')))) {
            mediaBadge = '<span class="badge" style="background:linear-gradient(135deg,#8b5cf6,#ec4899);color:#fff;font-size:10px;padding:2px 6px;border:none;">🔮 Video AI Trực Tiếp (T2V)</span>';
        } else if (s.production_method === 'REAL_ARCHIVE' || s.visual_strategy === 'REAL_DOCUMENTARY' || (s.media_path && (s.media_path.includes('_doc') || s.media_path.includes('_wiki')))) {
            mediaBadge = '<span class="badge badge-cyan" style="font-size:10px;padding:2px 6px;">📸 Tư Liệu Thật 100%</span>';
        } else if (s.production_method === 'STOCK_FOOTAGE' || isVid) {
            mediaBadge = '<span class="badge badge-amber" style="font-size:10px;padding:2px 6px;">🎥 Video B-Roll Thật</span>';
        } else if (s.visual_strategy === 'AI_IMAGE' || isAi) {
            mediaBadge = '<span class="badge badge-purple" style="font-size:10px;padding:2px 6px;">✨ Ảnh AI 8K</span>';
        } else {
            mediaBadge = '<span class="badge badge-cyan" style="font-size:10px;padding:2px 6px;">📸 Ảnh Thật</span>';
        }

        const fileName = s.media_path ? s.media_path.split(/[\\\\/]/).pop() : '';
        return `
        <div class="scene-item">
            <div class="scene-num">${s.scene_id}</div>
            <div class="scene-body">
                <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;flex-wrap:wrap;">
                    ${mediaBadge}
                    <span style="font-size:11px;color:var(--text-muted);">${s.actual_duration_seconds ? s.actual_duration_seconds.toFixed(1) + 's' : '~' + (s.estimated_seconds || 3) + 's'}</span>
                    ${s.camera_motion ? `<span style="font-size:10px;color:var(--text-muted);background:rgba(255,255,255,0.06);padding:1px 5px;border-radius:4px;">🎬 ${s.camera_motion}</span>` : ''}
                    ${s.tool ? `<span style="font-size:10px;color:var(--text-muted);background:rgba(255,255,255,0.06);padding:1px 5px;border-radius:4px;">🛠️ ${esc(s.tool)}</span>` : ''}
                </div>
                <div class="scene-narration">${esc(s.narration)}</div>
                <div class="scene-keyword">🔍 ${esc(s.visual_keyword || '')}</div>
                ${s.strategy_rationale ? `<div style="font-size:11px;color:#a78bfa;margin-top:2px;">🧠 Đạo diễn: ${esc(s.strategy_rationale)}</div>` : ''}
                ${fileName ? `<div class="scene-keyword" style="color:var(--text-muted);font-size:11px;margin-top:2px;">${isVid ? '🎬' : '🖼'} ${esc(fileName)}</div>` : ''}
            </div>
            <div class="scene-dur">~${s.estimated_seconds}s</div>
        </div>`;
    }).join('');

    body.innerHTML = `
        <div class="detail-grid">
            <div class="detail-row">
                <div class="detail-label">Chủ Đề</div>
                <div class="detail-value">${esc(data.topic)}</div>
            </div>
            <div class="detail-row">
                <div class="detail-label">Trạng Thái</div>
                <div class="detail-value">${statusMap[data.status] || `<span class="badge">${data.status}</span>`}</div>
            </div>
        </div>
        ${data.status === 'RENDERING' ? `
        <div class="detail-row" style="background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.25);border-radius:8px;padding:12px;margin-bottom:14px;">
            <div style="display:flex;justify-content:space-between;font-size:12px;font-weight:700;color:var(--accent-amber);margin-bottom:6px;">
                <span>🎬 Tiến Độ Dựng Video Trực Tiếp</span>
                <span>${data.progress_percent || 0}%</span>
            </div>
            <div style="background:rgba(255,255,255,0.1);height:8px;border-radius:4px;overflow:hidden;margin-bottom:6px;">
                <div style="background:linear-gradient(90deg,#f59e0b,#10b981);height:100%;width:${data.progress_percent || 0}%;transition:width 0.4s ease;"></div>
            </div>
            <div style="font-size:12px;color:var(--text-primary);">${esc(data.progress_message || 'Đang dựng video...')}</div>
        </div>` : ''}
        <div class="detail-row">
            <div class="detail-label">Tiêu Đề</div>
            <div class="detail-value" style="font-weight:600;font-size:16px;">${esc(data.title) || '—'}</div>
        </div>
        ${data.error_message && data.status === 'FAILED' ? `
        <div class="detail-row">
            <div class="detail-label" style="color:var(--accent-rose);">Lỗi</div>
            <div class="detail-value script-box" style="color:var(--accent-rose);">${esc(data.error_message)}</div>
        </div>` : ''}
        ${data.status === 'RENDERED' && data.video_path ? `
        <div class="detail-row" style="background:rgba(52,211,153,0.08);border:1px solid rgba(52,211,153,0.25);border-radius:8px;padding:14px;">
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">
                <span style="font-size:20px;">🎬</span>
                <div>
                    <div style="font-weight:700;color:var(--accent-green);font-size:13px;">Video Đã Sẵn Sàng!</div>
                    <div style="font-size:11px;color:var(--text-muted);margin-top:2px;">📂 ${esc(data.video_path)}</div>
                </div>
            </div>
            <a href="/api/videos/${data.id}/download" target="_blank" class="btn btn-primary btn-sm" style="width:100%;justify-content:center;">
                📥 Tải Video Về Máy
            </a>
        </div>` : ''}
        <div class="detail-row">
            <div class="detail-label">Nội Dung Lời Thoại</div>
            <div class="detail-value script-box">${esc(data.script) || '—'}</div>
        </div>
        ${data.scenes && data.scenes.length > 0 ? `
        <div class="detail-row">
            <div class="detail-label">Phân Cảnh (${data.scenes.length} cảnh)</div>
            <div class="scenes-list" style="margin-top:8px;">${scenesHTML}</div>
        </div>` : ''}
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:4px;">
            ${(data.status === 'SCRIPT_READY' || data.status === 'FAILED') ? `<button class="btn btn-secondary btn-sm" onclick="closeModal(); openInstantPreviewForJob(${data.id});" style="color:#f472b6; border-color:rgba(236,72,153,0.4); font-weight:600;">👁️ Xem Trước Nhanh</button>` : ''}
            ${(data.status === 'SCRIPT_READY' || data.status === 'FAILED') ? `<button class="btn btn-primary btn-sm" onclick="closeModal(); triggerWebRender(${data.id});">🚀 ${data.status === 'FAILED' ? 'Thử Render Lại' : 'Bắt Đầu Render'}</button>` : ''}
            ${(data.status === 'SCRIPT_READY' || data.status === 'FAILED') ? `<button class="btn btn-secondary btn-sm" onclick="closeModal(); openScriptEditor(${data.id});">✏️ Chỉnh Sửa Kịch Bản</button>` : ''}
            ${data.status === 'RENDERED' && data.video_path ? `<a href="/api/videos/${data.id}/download" target="_blank" class="btn btn-primary btn-sm">📥 Tải Video</a>` : ''}
            ${(data.status === 'RENDERED' && !data.youtube_id) ? `
            <button class="btn btn-primary btn-sm" id="btn-publish-yt-${data.id}" onclick="openPublishModal(${data.id})" style="background:linear-gradient(135deg,#ef4444,#dc2626);border:none;font-weight:600;">
                🚀 Chọn Kênh & Đăng YouTube
            </button>` : ''}
            ${(data.status === 'RENDERED' && data.youtube_id) ? `
            <button class="btn btn-secondary btn-sm" onclick="openPublishModal(${data.id})" style="font-size:11.5px; border-color:rgba(239,68,68,0.4);">
                🔄 Đăng Thêm Kênh Khác
            </button>` : ''}
            ${(data.status === 'RENDERED' || data.status === 'PUBLISHED') ? `
            <button class="btn btn-secondary btn-sm" id="btn-upload-drive-${data.id}" onclick="uploadToDrive(${data.id})" style="background:linear-gradient(135deg,rgba(66,133,244,0.18),rgba(52,168,83,0.12));border-color:rgba(66,133,244,0.3);">
                ☁️ ${data.drive_link ? 'Cập nhật Drive' : 'Tải lên Drive 20TB'}
            </button>` : ''}
            ${data.youtube_id ? `<a href="https://youtube.com/shorts/${data.youtube_id}" target="_blank" class="btn btn-secondary btn-sm" style="color:#ef4444;font-weight:600;">▶ Xem trên YouTube</a>` : ''}
            ${data.drive_link ? `<a href="${data.drive_link}" target="_blank" class="btn btn-secondary btn-sm" style="color:var(--accent-green);">📂 Mở Link Drive</a>` : ''}
            <button class="btn btn-danger btn-sm" onclick="deleteJob(${data.id}); closeModal();">🗑 Xoá Job</button>
        </div>`;
}

function closeModal() {
    document.getElementById('modal-overlay').classList.remove('visible');
}

document.addEventListener('keydown', e => { 
    if (e.key === 'Escape') {
        closeModal();
        closeScriptEditor();
    }
});

/* ===============================================================
   SCRIPT EDITOR MODAL (CRUD SCENES & METADATA)
================================================================ */
let activeEditorJob = null;

async function openScriptEditor(jobId) {
    const modal = document.getElementById('modal-script-editor');
    if (!modal) return;

    // Reset fields
    document.getElementById('editor-job-id').value = jobId;
    document.getElementById('editor-input-title').value = '';
    document.getElementById('editor-input-hook').value = '';
    document.getElementById('editor-input-tags').value = '';
    document.getElementById('editor-scenes-list').innerHTML = `<div style="text-align:center;padding:24px;"><span class="loading-spinner"></span> Đang tải kịch bản...</div>`;
    
    document.getElementById('script-editor-title').textContent = `Chỉnh Sửa Kịch Bản Video #${jobId}`;
    modal.classList.add('visible');

    try {
        const data = await apiGet(`/api/videos/${jobId}`);
        if (!data) {
            showToast('Không tải được dữ liệu kịch bản', 'error');
            closeScriptEditor();
            return;
        }

        activeEditorJob = data;
        document.getElementById('editor-input-title').value = data.title || data.topic || '';
        document.getElementById('editor-input-hook').value = (data.scenes && data.scenes[0]) ? data.scenes[0].narration : '';
        document.getElementById('editor-input-tags').value = data.tags || 'shorts, psychology, facts';

        renderEditorScenes(data.scenes || []);
    } catch (e) {
        showToast('Lỗi khi mở trình sửa kịch bản: ' + e.message, 'error');
        closeScriptEditor();
    }
}

function closeScriptEditor() {
    const modal = document.getElementById('modal-script-editor');
    if (modal) modal.classList.remove('visible');
    activeEditorJob = null;
}

function renderEditorScenes(scenes) {
    const container = document.getElementById('editor-scenes-list');
    container.innerHTML = '';

    if (!scenes || scenes.length === 0) {
        addEditorSceneRow({ scene_id: 1, narration: '', visual_keyword: '', estimated_seconds: 4.0 });
        return;
    }

    scenes.forEach((sc, idx) => {
        addEditorSceneRow(sc, idx + 1);
    });

    updateEditorScenesCount();
}

function addEditorSceneRow(sc = null, sceneNum = null) {
    const container = document.getElementById('editor-scenes-list');
    const currentRows = container.querySelectorAll('.editor-scene-card');
    const idx = sceneNum || (currentRows.length + 1);

    const narration = sc ? (sc.narration || '') : '';
    const kw = sc ? (sc.visual_keyword || '') : '';
    const vp = sc ? (sc.visual_prompt || '') : '';
    const sec = sc ? (sc.estimated_seconds || 4.0) : 4.0;
    const mediaType = sc ? (sc.media_type || 'auto') : 'auto';

    const card = document.createElement('div');
    card.className = 'editor-scene-card';
    card.style.cssText = `
        background: rgba(255,255,255,0.04);
        border: 1px solid rgba(255,255,255,0.1);
        border-radius: 10px;
        padding: 14px;
        position: relative;
        transition: all 0.2s ease;
    `;
    card.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
            <div style="display:flex; align-items:center; gap:8px;">
                <span class="badge badge-purple scene-index-badge" style="font-size:12px; font-weight:700; padding:4px 8px;">Cảnh #${idx}</span>
                <div style="display:flex; align-items:center; gap:4px; font-size:11.5px; color:var(--text-muted);">
                    <span>Thời lượng: ~</span>
                    <input type="number" class="scene-sec-input form-input" value="${sec}" min="1" max="25" step="0.5" style="width:58px; padding:2px 6px; font-size:12px; height:26px;" oninput="updateEditorScenesCount()">
                    <span>giây</span>
                </div>
            </div>
            <button type="button" class="btn btn-danger btn-sm" onclick="removeEditorSceneRow(this)" style="padding:3px 8px; font-size:11px;" title="Xóa cảnh này">
                🗑 Xóa Cảnh
            </button>
        </div>
        <div class="form-group" style="margin-bottom:8px;">
            <label class="form-label" style="font-size:11.5px; margin-bottom:4px;"><strong>Lời thoại phân cảnh (Narration / Voiceover):</strong></label>
            <textarea class="form-input scene-narration-input" rows="2" style="font-size:13px; line-height:1.4;" placeholder="Nhập lời thoại AI sẽ đọc cho phân cảnh này...">${esc(narration)}</textarea>
        </div>
        <div style="display:grid; grid-template-columns: 1.1fr 1fr; gap:10px; margin-bottom:8px;">
            <div class="form-group" style="margin-bottom:0;">
                <label class="form-label" style="font-size:11.5px; margin-bottom:4px;"><strong>Loại Media (Visual Source):</strong></label>
                <select class="form-input scene-mediatype-select" style="font-size:12px; height:34px; padding:4px 8px;">
                    <option value="auto" ${mediaType === 'auto' ? 'selected' : ''}>🧠 Tự Động (Video Strategist đề xuất cách làm tối ưu)</option>
                    <option value="i2v" ${mediaType === 'i2v' ? 'selected' : ''}>🎨 Video từ Ảnh Chủ Đề (Image-to-Video: chuẩn nét, không méo hình)</option>
                    <option value="ai_video" ${mediaType === 'ai_video' || mediaType === 't2v' ? 'selected' : ''}>🔮 Video AI Trực Tiếp (Text-to-Video: huyền bí, vũ trụ siêu thực)</option>
                    <option value="photo" ${mediaType === 'photo' ? 'selected' : ''}>📸 Tư Liệu Thật 100% (Nhân vật / Di tích / Sự kiện lịch sử)</option>
                    <option value="video" ${mediaType === 'video' ? 'selected' : ''}>🎥 Video Chuyển Động (Stock B-Roll Pexels 9:16)</option>
                    <option value="ai_image" ${mediaType === 'ai_image' ? 'selected' : ''}>✨ Ảnh Nghệ Thuật AI 8K (Cinematic Dramatic)</option>
                </select>
            </div>
            <div class="form-group" style="margin-bottom:0;">
                <label class="form-label" style="font-size:11.5px; margin-bottom:4px;"><strong>Từ khóa tìm kiếm (Visual Keyword):</strong></label>
                <input type="text" class="form-input scene-keyword-input" value="${esc(kw)}" placeholder="Ví dụ: dark silhouette rain, ticking clock..." style="font-size:12px; height:34px;">
            </div>
        </div>
        <div class="form-group" style="margin-bottom:0;">
            <label class="form-label" style="font-size:11.5px; margin-bottom:4px; display:flex; justify-content:space-between;">
                <strong>Prompt AI Chi Tiết (Visual Prompt - Tùy chọn):</strong>
                <span style="font-size:10.5px; color:var(--text-muted);">Tùy chỉnh góc máy, ánh sáng cho ảnh AI</span>
            </label>
            <input type="text" class="form-input scene-prompt-input" value="${esc(vp)}" placeholder="Mô tả bối cảnh điện ảnh chi tiết..." style="font-size:12px;">
        </div>
    `;

    container.appendChild(card);
    updateEditorScenesCount();
}

function removeEditorSceneRow(btn) {
    const card = btn.closest('.editor-scene-card');
    const container = document.getElementById('editor-scenes-list');
    if (container.querySelectorAll('.editor-scene-card').length <= 1) {
        showToast('Kịch bản phải có ít nhất 1 phân cảnh!', 'error');
        return;
    }
    if (card) {
        card.remove();
        reindexEditorScenes();
    }
}

function reindexEditorScenes() {
    const container = document.getElementById('editor-scenes-list');
    const cards = container.querySelectorAll('.editor-scene-card');
    cards.forEach((card, idx) => {
        const badge = card.querySelector('.scene-index-badge');
        if (badge) badge.textContent = `Cảnh #${idx + 1}`;
    });
    updateEditorScenesCount();
}

function updateEditorScenesCount() {
    const container = document.getElementById('editor-scenes-list');
    if (!container) return;
    const cards = container.querySelectorAll('.editor-scene-card');
    const countEl = document.getElementById('editor-scenes-count');
    if (countEl) countEl.textContent = cards.length;

    let totalSec = 0;
    cards.forEach(card => {
        const secInput = card.querySelector('.scene-sec-input');
        const val = parseFloat(secInput ? secInput.value : 0) || 0;
        totalSec += val;
    });

    const durEl = document.getElementById('editor-total-duration');
    if (durEl) {
        durEl.textContent = `Ước tính thời lượng: ~${totalSec.toFixed(0)}s (${cards.length} cảnh)`;
    }
}

async function submitScriptEdits(andRender = false) {
    const jobId = document.getElementById('editor-job-id').value;
    if (!jobId) return;

    const title = document.getElementById('editor-input-title').value.trim();
    if (!title) {
        showToast('Vui lòng nhập tiêu đề video!', 'error');
        return;
    }

    const hook = document.getElementById('editor-input-hook').value.trim();
    const tags = document.getElementById('editor-input-tags').value.trim();

    const container = document.getElementById('editor-scenes-list');
    const cards = container.querySelectorAll('.editor-scene-card');
    if (cards.length === 0) {
        showToast('Kịch bản phải có ít nhất 1 phân cảnh!', 'error');
        return;
    }

    const scenes = [];
    for (let i = 0; i < cards.length; i++) {
        const card = cards[i];
        const narration = card.querySelector('.scene-narration-input').value.trim();
        const kw = card.querySelector('.scene-keyword-input').value.trim();
        const vp = card.querySelector('.scene-prompt-input')?.value.trim() || kw;
        const mediaType = card.querySelector('.scene-mediatype-select')?.value || 'auto';
        const sec = parseFloat(card.querySelector('.scene-sec-input').value) || 3.5;

        if (!narration) {
            showToast(`Cảnh #${i + 1} chưa có lời thoại!`, 'error');
            card.scrollIntoView({ behavior: 'smooth' });
            return;
        }

        scenes.push({
            scene_id: i + 1,
            narration: narration,
            visual_keyword: kw || 'cinematic historical scene',
            visual_prompt: vp || kw || 'cinematic scene',
            media_type: mediaType,
            estimated_seconds: sec
        });
    }

    const saveBtn = andRender ? document.getElementById('btn-save-and-render') : document.getElementById('btn-save-script-only');
    const origHTML = saveBtn ? saveBtn.innerHTML : '';
    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.innerHTML = `<span class="loading-spinner"></span> Đang lưu...`;
    }

    try {
        const res = await fetch(`/api/videos/${jobId}/script`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title: title,
                hook: hook || scenes[0].narration,
                tags: tags,
                scenes: scenes
            })
        });

        const data = await res.json();
        if (res.ok && data.success) {
            showToast(`💾 Đã lưu kịch bản Video #${jobId} (${scenes.length} cảnh)!`, 'success');
            closeScriptEditor();
            await fetchVideos();

            // Cập nhật lại view preview nếu đang mở result panel của job này
            const resultBadge = document.getElementById('result-job-badge');
            if (resultBadge && resultBadge.textContent.includes(jobId)) {
                data.total_duration = scenes.reduce((sum, s) => sum + s.estimated_seconds, 0);
                data.hook = hook || scenes[0].narration;
                document.getElementById('script-result-content').innerHTML = buildScriptResultHTML(data);
            }

            if (andRender) {
                // Tự động kích hoạt render luôn
                await triggerWebRender(jobId);
            }
        } else {
            showToast(data.detail || data.message || 'Lỗi khi lưu kịch bản', 'error');
        }
    } catch (e) {
        showToast('Lỗi kết nối máy chủ: ' + e.message, 'error');
    } finally {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.innerHTML = origHTML;
        }
    }
}

/* ═══════════════════════════════════════════════════════
   DELETE JOB
══════════════════════════════════════════════════════════ */
async function deleteJob(id) {
    if (!confirm(`Bạn chắc chắn muốn xoá job #${id}?`)) return;
    try {
        const res = await fetch(`/api/videos/${id}`, { method: 'DELETE' });
        if (res.ok) {
            showToast(`🗑 Đã xoá job #${id}`, 'success');
            fetchVideos();
        } else {
            showToast('Không thể xoá job này.', 'error');
        }
    } catch (e) {
        showToast('Lỗi kết nối khi xoá.', 'error');
    }
}

/* ===============================================================
   PUBLISH TO YOUTUBE MODAL & ACTIONS
================================================================ */
let activePublishJob = null;

async function openPublishModal(jobId) {
    activePublishJob = null;
    let job = (allVideos || []).find(v => v.id === jobId);
    if (!job) {
        try {
            job = await apiGet(`/api/videos/${jobId}`);
        } catch (e) {}
    }
    if (!job) {
        showToast(`Không tìm thấy video #${jobId}`, 'error');
        return;
    }
    activePublishJob = job;

    document.getElementById('publish-job-id').value = jobId;
    document.getElementById('publish-modal-title').textContent = `🚀 Đăng Video #${jobId} Lên YouTube`;
    
    // Set title and counter
    const titleInput = document.getElementById('publish-title-input');
    titleInput.value = job.title || job.topic || '';
    updateTitleCounter();

    // Set description
    const descInput = document.getElementById('publish-desc-input');
    descInput.value = job.description || (job.tags ? job.tags.split(',').map(t => `#${t.trim()}`).join(' ') : '#shorts #viral');

    // Make sure channels are loaded
    if (!allChannels || allChannels.length === 0) {
        await fetchChannels();
    }

    // Populate channel dropdown
    const sel = document.getElementById('publish-channel-select');
    sel.innerHTML = '';

    if (!allChannels || allChannels.length === 0) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = '⚠️ Chưa có kênh YouTube nào được cấu hình';
        sel.appendChild(opt);
    } else {
        allChannels.forEach(ch => {
            const opt = document.createElement('option');
            opt.value = ch.id;
            const isAuth = Boolean(ch.is_authenticated || ch.youtube_connected);
            const statusIcon = isAuth ? '🟢' : '🟡';
            const gmail = ch.gmail || ch.email_label || ch.google_account;
            opt.textContent = `${statusIcon} ${ch.name} ── [${gmail}] ${isAuth ? '(Đã kết nối)' : '(Chưa cấp quyền)'}`;
            sel.appendChild(opt);
        });

        // Set default selection
        if (job.channel_id && allChannels.some(c => c.id === job.channel_id)) {
            sel.value = job.channel_id;
        } else {
            // Pick first authenticated channel, or first channel
            const firstAuth = allChannels.find(c => c.is_authenticated || c.youtube_connected);
            if (firstAuth) {
                sel.value = firstAuth.id;
            } else {
                sel.value = allChannels[0].id;
            }
        }
    }

    onPublishChannelChange();

    const confirmBtn = document.getElementById('btn-confirm-publish');
    if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.innerHTML = `🚀 Xác Nhận Đăng Lên YouTube`;
    }

    document.getElementById('modal-publish-yt').classList.add('visible');
}

function closePublishModal() {
    document.getElementById('modal-publish-yt').classList.remove('visible');
}

function updateTitleCounter() {
    const input = document.getElementById('publish-title-input');
    const counter = document.getElementById('publish-title-counter');
    if (input && counter) {
        counter.textContent = `${input.value.length}/100`;
        if (input.value.length > 85) {
            counter.style.color = 'var(--accent-amber)';
        } else {
            counter.style.color = 'var(--text-muted)';
        }
    }
}

function onPublishChannelChange() {
    const sel = document.getElementById('publish-channel-select');
    const selectedId = sel ? sel.value : null;
    const infoText = document.getElementById('publish-channel-info-text');
    const actionBtn = document.getElementById('publish-channel-action-btn');
    const infoBox = document.getElementById('publish-channel-info-box');

    if (!selectedId) {
        if (infoText) infoText.innerHTML = `<span style="color:var(--text-muted)">Vui lòng tạo kênh trước trong tab 'Kênh & Tài Khoản'</span>`;
        if (actionBtn) actionBtn.innerHTML = '';
        return;
    }

    const ch = (allChannels || []).find(c => c.id === selectedId);
    if (!ch) return;

    const isAuth = Boolean(ch.is_authenticated || ch.youtube_connected);
    const gmail = ch.gmail || ch.email_label || ch.google_account;
    const confirmBtn = document.getElementById('btn-confirm-publish');

    if (isAuth) {
        if (infoBox) {
            infoBox.style.background = 'rgba(16, 185, 129, 0.12)';
            infoBox.style.borderColor = 'rgba(16, 185, 129, 0.3)';
        }
        if (infoText) {
            infoText.innerHTML = `🟢 <strong>${esc(ch.name)}</strong>: Đã cấp quyền YouTube riêng biệt. Sẵn sàng xuất bản chính xác!`;
            infoText.style.color = 'var(--accent-green)';
        }
        if (actionBtn) actionBtn.innerHTML = '';
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.title = '';
        }
    } else {
        if (infoBox) {
            infoBox.style.background = 'rgba(245, 158, 11, 0.15)';
            infoBox.style.borderColor = 'rgba(245, 158, 11, 0.35)';
        }
        if (infoText) {
            infoText.innerHTML = `⚠️ <strong>${esc(ch.name)}</strong> chưa cấp quyền YouTube riêng! Không thể xuất bản để tránh đăng nhầm sang kênh cũ.`;
            infoText.style.color = 'var(--accent-amber)';
        }
        if (actionBtn) {
            actionBtn.innerHTML = `
                <button class="btn btn-primary btn-sm" onclick="startChannelAuth('${esc(ch.id)}', '${esc(ch.name)}')" style="font-size:11.5px; background:var(--accent-amber); border-color:var(--accent-amber); font-weight:600;">
                    🔑 Cấp Quyền YouTube (Kênh Này)
                </button>
            `;
        }
        if (confirmBtn) {
            confirmBtn.disabled = true;
            confirmBtn.title = 'Vui lòng cấp quyền YouTube cho kênh này trước khi xuất bản';
        }
    }

    // Tự động tải thông tin khe giờ vàng tiếp theo cho kênh đã chọn
    fetchGoldenSlotPreview(selectedId);
}

async function fetchGoldenSlotPreview(channelId) {
    const el = document.getElementById('golden-slot-preview-text');
    if (!el || !channelId) return;
    try {
        const res = await fetch(`/api/channels/${channelId}/next-golden-slot`);
        const data = await res.json();
        if (data.success && data.display_vn) {
            el.innerHTML = `🎯 <strong>Khe tiếp theo:</strong> ${data.display_vn}<br><span style="color:#93c5fd">(${data.display_us})</span>`;
        } else {
            el.textContent = 'Tự động tính 20:00 hoặc 00:30 VN chưa có video';
        }
    } catch (e) {
        el.textContent = 'Tự động tính 20:00 hoặc 00:30 VN chưa có video';
    }
}

async function submitPublishYouTube() {
    const jobId = document.getElementById('publish-job-id').value;
    const channelId = document.getElementById('publish-channel-select').value;
    const title = document.getElementById('publish-title-input').value.trim();
    const description = document.getElementById('publish-desc-input').value.trim();
    const rawPrivacy = document.querySelector('input[name="publish_privacy"]:checked')?.value || 'auto_golden_slot';
    const isAutoSchedule = (rawPrivacy === 'auto_golden_slot');
    const privacy = isAutoSchedule ? 'private' : rawPrivacy;

    if (!jobId) {
        showToast('Không xác định được video job', 'error');
        return;
    }
    if (!channelId) {
        showToast('Vui lòng chọn kênh YouTube để đăng video', 'error');
        return;
    }
    if (!title) {
        showToast('Vui lòng nhập tiêu đề video trước khi đăng', 'error');
        return;
    }

    const ch = (allChannels || []).find(c => c.id === channelId);
    const isAuth = ch ? Boolean(ch.is_authenticated || ch.youtube_connected) : false;
    if (!isAuth) {
        showToast(`⚠️ Kênh '${ch?.name || channelId}' chưa được cấp quyền YouTube riêng. Hãy bấm 'Cấp Quyền YouTube (Kênh Này)' trước khi đăng!`, 'error');
        return;
    }

    const btn = document.getElementById('btn-confirm-publish');
    const originalHTML = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = isAutoSchedule
            ? `<span class="loading-spinner"></span> Đang xếp vào khe giờ vàng cho kênh '${ch?.name || channelId}'...`
            : `<span class="loading-spinner"></span> Đang xuất bản lên kênh '${ch?.name || channelId}'...`;
    }

    showToast(
        isAutoSchedule
            ? `⏰ Đang xếp Video #${jobId} vào khe giờ vàng tối ưu...`
            : `🚀 Đang xuất bản Video #${jobId} lên kênh '${ch?.name || channelId}'...`,
        'info'
    );

    try {
        const res = await fetch(`/api/videos/${jobId}/publish-youtube`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                channel_id: channelId,
                title: title,
                description: description,
                privacy: privacy,
                auto_schedule: isAutoSchedule,
                schedule_time_iso: isAutoSchedule ? 'auto' : null
            })
        });

        const data = await res.json();
        if (res.ok && data.success) {
            showToast(`🎉 ${data.message || 'Đăng video lên YouTube thành công!'}`, 'success');
            closePublishModal();
            await fetchStats();
            await fetchVideos();
            await fetchChannels();
            const detailModal = document.getElementById('modal-overlay');
            if (detailModal && detailModal.classList.contains('visible')) {
                openVideoDetail(jobId);
            }
        } else {
            const msg = data.detail || data.message || 'Đăng YouTube thất bại';
            showToast('❌ ' + msg, 'error');
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = originalHTML;
            }
        }
    } catch (e) {
        showToast('Lỗi kết nối máy chủ: ' + e.message, 'error');
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalHTML;
        }
    }
}

/* ===============================================================
   UPLOAD TO GOOGLE DRIVE 20TB
================================================================ */
async function uploadToDrive(jobId) {
    const btn = document.getElementById(`btn-upload-drive-${jobId}`);
    const originalHTML = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<span class="loading-spinner"></span> Đang tải lên Drive...`;
    }
    try {
        const res = await fetch(`/api/videos/${jobId}/upload-drive`, { method: 'POST' });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast(`☁️ ${data.message}`, 'success');
            if (data.drive_link && btn) {
                const linkEl = document.createElement('a');
                linkEl.href = data.drive_link;
                linkEl.target = '_blank';
                linkEl.className = 'btn btn-secondary btn-sm';
                linkEl.style.color = 'var(--accent-green)';
                linkEl.innerHTML = '📂 Mở Link Drive';
                btn.replaceWith(linkEl);
            }
            fetchVideos();
        } else {
            const msg = data.detail || data.message || 'Upload Drive thất bại';
            showToast('❌ ' + msg, 'error');
            if (btn) { btn.disabled = false; btn.innerHTML = originalHTML; }
        }
    } catch (e) {
        showToast('Lỗi kết nối: ' + e.message, 'error');
        if (btn) { btn.disabled = false; btn.innerHTML = originalHTML; }
    }
}

async function triggerWebRender(jobId) {
    if (!confirm(`🚀 Bạn muốn bắt đầu render Video #${jobId} từ Web?\n\nVideo sẽ được đưa vào hàng đợi render.`)) return;
    try {
        const res = await apiPost(`/api/videos/${jobId}/render`, {});
        if (res && res.success) {
            showToast(`🚀 Video #${jobId} đã được đưa vào hàng đợi render!`, 'success');
            fetchVideos();
        } else {
            showToast(res?.message || `Không thể render Video #${jobId}`, 'error');
        }
    } catch (e) {
        showToast(`Lỗi khi gửi yêu cầu render: ${e.message}`, 'error');
    }
}

/* ═══════════════════════════════════════════════════════
   TOAST
══════════════════════════════════════════════════════════ */
function showToast(msg, type = 'info') {
    const iconMap = { success: '✅', error: '❌', info: 'ℹ️' };
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `<span class="toast-icon">${iconMap[type] || 'ℹ️'}</span><span class="toast-text">${esc(msg)}</span>`;
    container.appendChild(toast);
    setTimeout(() => { toast.style.opacity = '0'; toast.style.transform = 'translateY(8px)'; toast.style.transition = '0.3s'; setTimeout(() => toast.remove(), 300); }, 3500);
}

async function recoverAllFailedJobs() {
    if (!confirm('🔄 Bạn có chắc muốn khôi phục tất cả các video bị lỗi về hàng đợi để render lại?')) return;
    try {
        const res = await apiPost('/api/videos/recover-failed', {});
        if (res?.success) {
            showToast(res.message || 'Đã khôi phục các job lỗi về trạng thái sẵn sàng!', 'success');
            await fetchVideos();
        } else {
            showToast(res?.detail || 'Không thể khôi phục các job lỗi', 'error');
        }
    } catch (e) {
        showToast(`Lỗi khôi phục: ${e.message}`, 'error');
    }
}

/* ═══════════════════════════════════════════════════════
   HELPERS
══════════════════════════════════════════════════════════ */
async function apiGet(url) {
    try {
        const res = await fetch(url);
        if (!res.ok) { showToast(`API Error: ${url} (${res.status})`, 'error'); return null; }
        return await res.json();
    } catch (e) {
        return null;
    }
}

async function apiPost(url, body) {
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Unknown error' }));
        throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
}

function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
}

function updateSyncTime() {
    const el = document.getElementById('last-sync-time');
    if (el) el.textContent = `Đồng bộ: ${new Date().toLocaleTimeString('vi-VN')}`;
}

function esc(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

/* ═══════════════════════════════════════════════════════
   ACCOUNTS & CHANNELS MANAGEMENT
══════════════════════════════════════════════════════════ */
let allAccounts = [];
let allChannels = [];
let oauthPollTimer = null;

async function fetchChannels() {
    try {
        const [chData, accData] = await Promise.all([
            apiGet('/api/channels'),
            apiGet('/api/accounts')
        ]);

        if (chData) {
            allChannels = Array.isArray(chData) ? chData : (chData.channels || []);
            updateChannelDropdowns(allChannels);
        }

        if (accData && accData.accounts) {
            allAccounts = accData.accounts;
        }

        renderChannelsHub(allChannels, allAccounts);
        renderAccounts(allAccounts);
    } catch (e) {
        console.error('Lỗi fetch channels & accounts:', e);
    }
}

function updateChannelDropdowns(channels) {
    const sel = document.getElementById('input-channel');
    if (!sel) return;
    const currentVal = sel.value;
    sel.innerHTML = '';

    if (!channels || channels.length === 0) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = '⚠️ Chưa có kênh nào được cấu hình';
        sel.appendChild(opt);
        return;
    }

    channels.forEach(ch => {
        const opt = document.createElement('option');
        opt.value = ch.id;
        const gmail = ch.gmail || ch.email_label || ch.google_account;
        opt.textContent = `📺 ${ch.name} ── 📧 [${gmail}]`;
        sel.appendChild(opt);
    });

    if (currentVal && channels.some(c => c.id === currentVal)) {
        sel.value = currentVal;
    }
}

async function fetchAccounts() {
    try {
        const data = await apiGet('/api/accounts');
        if (data && data.accounts) {
            allAccounts = data.accounts;
            renderAccounts(allAccounts);
            // Refresh channel hub as well to ensure synced email labels
            if (allChannels && allChannels.length > 0) {
                renderChannelsHub(allChannels, allAccounts);
            }
        }
    } catch (e) {
        console.error('Lỗi fetch accounts:', e);
    }
}

function renderChannelsHub(channels, accounts) {
    const container = document.getElementById('channels-hub-container');
    if (!container) return;

    // Update KPI Bar
    const kpiChannels = document.getElementById('kpi-total-channels');
    if (kpiChannels) kpiChannels.textContent = channels.length;

    const uniqueGmails = new Set();
    (accounts || []).forEach(a => { if (a.email_label && a.email_label !== 'không có') uniqueGmails.add(a.email_label); });
    const kpiGmails = document.getElementById('kpi-total-gmails');
    if (kpiGmails) kpiGmails.textContent = uniqueGmails.size || (accounts || []).length;

    const kpiAuth = document.getElementById('kpi-auth-channels');
    if (kpiAuth) {
        const authCount = channels.filter(c => c.is_authenticated || c.youtube_connected).length;
        kpiAuth.textContent = `${authCount}/${channels.length}`;
    }

    const kpiVideos = document.getElementById('kpi-total-videos');
    if (kpiVideos) {
        const totalV = channels.reduce((sum, c) => sum + (c.video_count || 0), 0);
        kpiVideos.textContent = totalV;
    }

    if (!channels || channels.length === 0) {
        container.innerHTML = `
            <div style="grid-column: 1 / -1; text-align:center; padding:40px; color:var(--text-muted); background:rgba(255,255,255,0.02); border-radius:12px; border:1px dashed rgba(255,255,255,0.1);">
                <div style="font-size:36px; margin-bottom:12px;">📺</div>
                <h4 style="color:var(--text-primary); margin-bottom:6px;">Chưa có kênh YouTube nào</h4>
                <p style="font-size:13px; margin-bottom:16px;">Tạo kênh thương hiệu đầu tiên và gắn vào tài khoản Gmail để bắt đầu.</p>
                <button class="btn btn-primary" onclick="openAddChannelModal()">+ Thêm Kênh Đầu Tiên</button>
            </div>
        `;
        return;
    }

    container.innerHTML = channels.map(ch => {
        const parentAcc = (accounts || []).find(a => a.id === ch.google_account) || {};
        const gmail = ch.gmail || parentAcc.email_label || 'Chưa đặt Gmail';
        const isAuth = Boolean(ch.is_authenticated || ch.youtube_connected);
        const authBadge = isAuth 
            ? `<span class="service-status-pill connected">🟢 Đã cấp quyền YouTube</span>`
            : `<span class="service-status-pill disconnected">🟡 Chưa kết nối YouTube</span>`;

        const visualLabel = ch.visual_mode === 'auto' ? '🤖 AI Auto' : (ch.visual_mode === 'imagen_ai' ? '✨ Imagen 3' : '🎞️ Pexels HD');
        const pubHour = ch.schedule?.publish_hour_utc ?? 12;

        return `
            <div class="channel-hub-card" id="hub-ch-${esc(ch.id)}">
                <div class="channel-hub-header">
                    <div class="channel-hub-title-wrap">
                        <div class="channel-hub-avatar">📺</div>
                        <div class="channel-hub-names">
                            <h4>${esc(ch.name)}</h4>
                            <div style="display:flex; align-items:center; gap:6px; margin-top:2px;">
                                <span class="channel-hub-id-badge">ID: ${esc(ch.id)}</span>
                                ${ch.language ? `<span class="badge badge-purple" style="font-size:10px;">${esc(ch.language.toUpperCase())}</span>` : ''}
                            </div>
                        </div>
                    </div>
                    ${authBadge}
                </div>

                <!-- Highlight: Managing Gmail -->
                <div class="channel-hub-gmail-box">
                    <div class="channel-hub-gmail-info">
                        <span class="gmail-icon">📧</span>
                        <div style="min-width:0;">
                            <div style="font-size:10px; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.5px; font-weight:600;">Gmail Điều Hành Kênh</div>
                            <div class="channel-hub-gmail-text" title="${esc(gmail)}">${esc(gmail)}</div>
                        </div>
                    </div>
                    <div style="display:flex; align-items:center; gap:6px; flex-shrink:0;">
                        <span class="channel-hub-gmail-account-id">${esc(ch.google_account)}</span>
                        <button class="btn btn-secondary btn-sm" style="padding:3px 7px; font-size:11px;" onclick="openEditAccountModal('${esc(ch.google_account)}')" title="Đổi địa chỉ Gmail này">
                            ✏️ Đổi
                        </button>
                    </div>
                </div>

                <!-- Meta chips -->
                <div class="channel-hub-meta-chips">
                    <span class="hub-chip">🎯 <strong>Niche:</strong> ${esc(ch.niche || 'general')}</span>
                    <span class="hub-chip">🎙️ <strong>Giọng:</strong> ${esc(ch.preferred_voice || 'Default')}</span>
                    <span class="hub-chip">🖼️ <strong>Visual:</strong> ${visualLabel}</span>
                    <span class="hub-chip">⏰ <strong>Đăng:</strong> ${pubHour}:00 UTC</span>
                </div>

                <!-- Stats row -->
                <div class="channel-hub-stats-row">
                    <div>
                        <span>📊 Tổng video: <strong>${ch.video_count || 0}</strong></span>
                        &nbsp;·&nbsp;
                        <span style="color:var(--accent-green)">🚀 Đã đăng: <strong>${ch.published_count || 0}</strong></span>
                    </div>
                    <div style="font-size:11.5px; color:var(--text-muted);">
                        ☁️ ${esc(ch.drive_quota || '2 TB Google One')}
                    </div>
                </div>

                <!-- Actions -->
                <div class="channel-hub-actions">
                    <button class="btn btn-primary btn-sm" style="flex:1; justify-content:center;" onclick="useChannelForScript('${esc(ch.id)}')" title="Tạo kịch bản mới cho kênh này">
                        🚀 Tạo Video Cho Kênh
                    </button>
                    <button class="btn btn-secondary btn-sm" onclick="openEditChannelModal('${esc(ch.google_account)}', '${esc(ch.id)}')" title="Chỉnh sửa thông tin kênh">
                        ✏️ Sửa Kênh
                    </button>
                    ${!isAuth ? `
                        <button class="btn btn-primary btn-sm" style="background:var(--accent-amber); border-color:var(--accent-amber);" onclick="startGoogleAuth('${esc(ch.google_account)}', 'youtube')" title="Đăng nhập Google cấp quyền YouTube">
                            🔑 Cấp Quyền
                        </button>
                    ` : ''}
                    <button class="btn btn-danger btn-sm" onclick="deleteChannel('${esc(ch.google_account)}', '${esc(ch.id)}')" title="Xóa kênh">
                        🗑️
                    </button>
                </div>
            </div>
        `;
    }).join('');
}

function useChannelForScript(channelId) {
    switchTab('create');
    const sel = document.getElementById('input-channel');
    if (sel) {
        sel.value = channelId;
    }
    const topicInput = document.getElementById('input-topic');
    if (topicInput) {
        topicInput.focus();
    }
    const ch = (allChannels || []).find(c => c.id === channelId);
    showToast(`Đã chọn kênh: ${ch?.name || channelId}! Hãy nhập chủ đề video.`, 'info');
}

function renderAccounts(accounts) {
    const container = document.getElementById('accounts-container');
    if (!container) return;

    if (!accounts || accounts.length === 0) {
        container.innerHTML = `
            <div style="text-align:center; padding:40px; color:var(--text-muted); background:rgba(255,255,255,0.02); border-radius:12px; border:1px dashed rgba(255,255,255,0.1);">
                <div style="font-size:36px; margin-bottom:12px;">📂</div>
                <h4 style="color:var(--text-primary); margin-bottom:6px;">Chưa có tài khoản Google nào</h4>
                <p style="font-size:13px; margin-bottom:16px;">Thêm tài khoản Google mẹ để bắt đầu quản lý kênh YouTube và bộ nhớ Google Drive.</p>
                <button class="btn btn-primary" onclick="openAddAccountModal()">+ Thêm Tài Khoản Đầu Tiên</button>
            </div>
        `;
        return;
    }

    container.innerHTML = accounts.map(acc => {
        const brandChannels = acc.brand_channels || [];
        const email = acc.email_label || 'Chưa đặt Gmail';

        return `
            <div class="account-card" id="card-${esc(acc.id)}">
                <div class="account-header">
                    <div class="account-identity">
                        <div class="account-icon">📧</div>
                        <div class="account-title-wrap">
                            <h4 style="font-size:15.5px;">
                                ${esc(email)}
                                <span class="badge badge-blue">${brandChannels.length} Kênh Trực Thuộc</span>
                            </h4>
                            <div class="account-email" style="display:flex; align-items:center; gap:8px;">
                                <span>Mã tài khoản: <code>${esc(acc.id)}</code></span>
                                <button class="btn btn-secondary btn-sm" style="padding:2px 7px; font-size:11px;" onclick="openEditAccountModal('${esc(acc.id)}')">
                                    ✏️ Đổi Gmail / Sửa
                                </button>
                            </div>
                        </div>
                    </div>
                    <div class="account-badges-group">
                        <span class="badge badge-purple">☁️ ${esc(acc.drive_storage)}</span>
                        <span class="badge badge-green">⚡ ${Number(acc.youtube_quota_daily).toLocaleString()} Quota/ngày</span>
                        <button class="btn btn-secondary btn-sm" onclick="openSecretModal('${esc(acc.id)}')" title="Cài đặt Client Secret">
                            📁 Secret
                        </button>
                        <button class="btn btn-danger btn-sm" onclick="deleteAccount('${esc(acc.id)}')" title="Xóa tài khoản này">
                            🗑️
                        </button>
                    </div>
                </div>

                <!-- Services Grid (YouTube & Drive OAuth) -->
                <div class="services-grid">
                    <!-- YouTube Service -->
                    <div class="service-card">
                        <div class="service-card-top">
                            <div class="service-title">
                                <span style="font-size:16px;">🎬</span>
                                <span>YouTube Studio API</span>
                            </div>
                            <span class="service-status-pill ${acc.youtube_connected ? 'connected' : 'disconnected'}">
                                ${acc.youtube_connected ? '✓ Đã kết nối' : '⚠️ Chưa kết nối'}
                            </span>
                        </div>
                        <div class="service-actions">
                            ${acc.youtube_connected ? `
                                <button class="btn btn-secondary btn-sm" style="color:var(--accent-rose); width:100%; justify-content:center;" onclick="disconnectService('${esc(acc.id)}', 'youtube')">
                                    Ngắt Kết Nối
                                </button>
                            ` : `
                                <button class="btn btn-primary btn-sm" style="width:100%; justify-content:center;" onclick="startGoogleAuth('${esc(acc.id)}', 'youtube')">
                                    🔑 Đăng Nhập Google (YouTube)
                                </button>
                            `}
                        </div>
                    </div>

                    <!-- Drive Service -->
                    <div class="service-card">
                        <div class="service-card-top">
                            <div class="service-title">
                                <span style="font-size:16px;">☁️</span>
                                <span>Google Drive Storage</span>
                            </div>
                            <span class="service-status-pill ${acc.drive_connected ? 'connected' : 'disconnected'}">
                                ${acc.drive_connected ? '✓ Đã kết nối' : '⚠️ Chưa kết nối'}
                            </span>
                        </div>
                        <div class="service-actions">
                            ${acc.drive_connected ? `
                                <button class="btn btn-secondary btn-sm" style="color:var(--accent-rose); width:100%; justify-content:center;" onclick="disconnectService('${esc(acc.id)}', 'drive')">
                                    Ngắt Kết Nối
                                </button>
                            ` : `
                                <button class="btn btn-primary btn-sm" style="width:100%; justify-content:center; background:linear-gradient(135deg,rgba(66,133,244,0.3),rgba(52,168,83,0.3)); border-color:rgba(66,133,244,0.4);" onclick="startGoogleAuth('${esc(acc.id)}', 'drive')">
                                    ☁️ Kết Nối Google Drive
                                </button>
                            `}
                        </div>
                    </div>
                </div>

                <!-- Channels List under this Account -->
                <div class="account-channels-wrap">
                    <div class="channels-header">
                        <h5>📺 Kênh YouTube Thuộc Gmail Này (${brandChannels.length})</h5>
                        <button class="btn btn-secondary btn-sm" onclick="openAddChannelModal('${esc(acc.id)}')">
                            + Thêm Kênh Mới
                        </button>
                    </div>

                    <div class="channels-items-list">
                        ${brandChannels.length === 0 ? `
                            <div style="text-align:center; padding:18px; color:var(--text-muted); font-size:12.5px;">
                                Chưa có kênh nào gán với Gmail này. Bấm <strong>"+ Thêm Kênh Mới"</strong> để gán kênh.
                            </div>
                        ` : brandChannels.map(ch => {
                            const isAuth = Boolean(ch.has_own_token || ch.youtube_connected);
                            return `
                            <div class="channel-row-item" style="display:flex; flex-direction:column; gap:8px; padding:12px 14px; background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:10px; margin-bottom:10px;">
                                <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:8px;">
                                    <div class="channel-row-main" style="flex:1; min-width:250px;">
                                        <div class="channel-row-title" style="display:flex; align-items:center; gap:8px; margin-bottom:5px; flex-wrap:wrap;">
                                            <span style="font-weight:700; font-size:14px; color:var(--text-primary);">📺 ${esc(ch.name)}</span>
                                            <span class="badge badge-blue" style="font-size:10px;">ID: ${esc(ch.id)}</span>
                                            ${isAuth ? `
                                                <span class="badge badge-green" style="font-size:11px; background:rgba(16,185,129,0.15); color:var(--accent-green); border:1px solid rgba(16,185,129,0.3); display:inline-flex; align-items:center; gap:4px;">
                                                    ✓ Đã cấp quyền YouTube riêng ${ch.token_channel_title ? `[${esc(ch.token_channel_title)}]` : ''}
                                                </span>
                                            ` : `
                                                <span class="badge badge-amber" style="font-size:11px; background:rgba(245,158,11,0.15); color:var(--accent-amber); border:1px solid rgba(245,158,11,0.35); display:inline-flex; align-items:center; gap:4px;">
                                                    ⚠️ Chưa cấp quyền YouTube
                                                </span>
                                            `}
                                        </div>
                                        <div class="channel-row-meta" style="font-size:12px; color:var(--text-secondary); display:flex; gap:12px; flex-wrap:wrap;">
                                            <span>🎯 <strong>Chủ đề:</strong> ${esc(ch.niche || 'general')}</span>
                                            <span>🌐 <strong>Ngôn ngữ:</strong> ${esc(ch.language || 'en').toUpperCase()}</span>
                                            <span>🎙️ <strong>Giọng:</strong> ${esc(ch.preferred_voice || 'Default')}</span>
                                            <span>🖼️ <strong>Visual:</strong> ${ch.visual_mode === 'auto' ? '🤖 AI Tự chọn' : (ch.visual_mode === 'imagen_ai' ? '✨ Imagen 3' : '🎞️ Pexels')}</span>
                                            <span>⏰ <strong>Đăng:</strong> ${ch.schedule?.publish_hour_utc ?? 12}:00 UTC</span>
                                        </div>
                                    </div>
                                    <div class="channel-row-actions" style="display:flex; gap:6px; align-items:center; flex-wrap:wrap;">
                                        ${!isAuth ? `
                                            <button class="btn btn-primary btn-sm" onclick="startChannelAuth('${esc(ch.id)}', '${esc(ch.name)}')" title="Cấp quyền đăng video cho kênh này" style="background:linear-gradient(135deg,rgba(239,68,68,0.9),rgba(220,38,38,0.9)); border-color:#dc2626; font-size:11.5px; font-weight:600;">
                                                🔑 Cấp Quyền YouTube (Kênh Này)
                                            </button>
                                        ` : `
                                            <button class="btn btn-secondary btn-sm" onclick="startChannelAuth('${esc(ch.id)}', '${esc(ch.name)}')" title="Ủy quyền lại kênh này nếu cần" style="font-size:11px;">
                                                🔄 Cấp Lại Quyền
                                            </button>
                                            <button class="btn btn-secondary btn-sm" onclick="disconnectChannelAuth('${esc(ch.id)}')" title="Hủy quyền YouTube" style="color:var(--accent-rose); font-size:11px;">
                                                Hủy quyền
                                            </button>
                                        `}
                                        <button class="btn btn-secondary btn-sm" onclick="useChannelForScript('${esc(ch.id)}')" title="Tạo kịch bản cho kênh này">
                                            🚀 Tạo Video
                                        </button>
                                        <button class="btn btn-secondary btn-sm" onclick="openEditChannelModal('${esc(acc.id)}', '${esc(ch.id)}')" title="Sửa thông tin kênh">
                                            ✏️ Sửa
                                        </button>
                                        <button class="btn btn-danger btn-sm" onclick="deleteChannel('${esc(acc.id)}', '${esc(ch.id)}')" title="Xóa kênh">
                                            🗑️
                                        </button>
                                    </div>
                                </div>
                            </div>
                            `;
                        }).join('')}
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

/* ═══════════════════════════════════════════════════════
   ACCOUNT MODALS & CRUD
══════════════════════════════════════════════════════════ */
function openAddAccountModal() {
    const modeEl = document.getElementById('acc-mode');
    if (modeEl) modeEl.value = 'add';

    const titleEl = document.getElementById('modal-account-title');
    if (titleEl) titleEl.textContent = 'Thêm Tài Khoản Google Mới';

    const idInput = document.getElementById('acc-input-id');
    if (idInput) {
        idInput.value = '';
        idInput.disabled = false;
    }

    document.getElementById('acc-input-email').value = '';
    document.getElementById('acc-input-drive').value = '2 TB Google One';
    document.getElementById('acc-input-quota').value = '10000';
    document.getElementById('modal-account').classList.add('visible');
}

function openEditAccountModal(accountId) {
    const acc = (allAccounts || []).find(a => a.id === accountId);
    if (!acc) {
        showToast('Không tìm thấy tài khoản ' + accountId, 'error');
        return;
    }

    const modeEl = document.getElementById('acc-mode');
    if (modeEl) modeEl.value = 'edit';

    const titleEl = document.getElementById('modal-account-title');
    if (titleEl) titleEl.textContent = `Chỉnh Sửa Tài Khoản: ${acc.id}`;

    const idInput = document.getElementById('acc-input-id');
    if (idInput) {
        idInput.value = acc.id;
        idInput.disabled = true;
    }

    const emailInput = document.getElementById('acc-input-email');
    if (emailInput) emailInput.value = acc.email_label || '';

    const driveInput = document.getElementById('acc-input-drive');
    if (driveInput) driveInput.value = acc.drive_storage || '2 TB Google One';

    const quotaInput = document.getElementById('acc-input-quota');
    if (quotaInput) quotaInput.value = acc.youtube_quota_daily || 10000;

    document.getElementById('modal-account').classList.add('visible');
}

function closeAccountModal() {
    document.getElementById('modal-account').classList.remove('visible');
}

async function submitAccountForm() {
    const mode = document.getElementById('acc-mode')?.value || 'add';
    const id = document.getElementById('acc-input-id').value.trim();
    const email = document.getElementById('acc-input-email').value.trim();
    const drive = document.getElementById('acc-input-drive').value.trim();
    const quota = parseInt(document.getElementById('acc-input-quota').value.trim()) || 10000;

    if (!id) {
        showToast('Vui lòng nhập Mã tài khoản', 'error');
        return;
    }
    if (!email) {
        showToast('Vui lòng nhập Địa chỉ Gmail quản lý', 'error');
        return;
    }

    try {
        let res;
        if (mode === 'edit') {
            const resp = await fetch(`/api/accounts/${id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    email_label: email,
                    drive_storage: drive,
                    youtube_quota_daily: quota
                })
            });
            res = await resp.json();
        } else {
            res = await apiPost('/api/accounts', {
                id: id,
                email_label: email,
                drive_storage: drive,
                youtube_quota_daily: quota
            });
        }

        if (res && res.success) {
            showToast(res.message || 'Thành công!', 'success');
            closeAccountModal();
            await fetchChannels();
            await fetchAccounts();
            await fetchStats();
        } else {
            showToast(res?.detail || res?.message || 'Không thể lưu tài khoản', 'error');
        }
    } catch (e) {
        showToast(`Lỗi: ${e.message}`, 'error');
    }
}

async function deleteAccount(accountId) {
    if (!confirm(`⚠️ Bạn có chắc muốn xóa tài khoản '${accountId}'?\nTất cả các kênh liên kết cũng sẽ bị xóa khỏi cấu hình.`)) return;

    try {
        const res = await fetch(`/api/accounts/${accountId}`, { method: 'DELETE' });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast(data.message || `Đã xóa tài khoản ${accountId}`, 'success');
            await fetchChannels();
            await fetchAccounts();
            await fetchStats();
        } else {
            showToast(data.detail || 'Không thể xóa tài khoản', 'error');
        }
    } catch (e) {
        showToast(`Lỗi xóa tài khoản: ${e.message}`, 'error');
    }
}

/* ═══════════════════════════════════════════════════════
   CHANNEL MODALS & CRUD
══════════════════════════════════════════════════════════ */
function populateParentAccountSelect(selectedAccId) {
    const sel = document.getElementById('channel-parent-acc');
    if (!sel) return;
    sel.innerHTML = '';
    const accList = allAccounts || [];
    const activeAccId = selectedAccId || (accList[0]?.id || 'account_1');

    accList.forEach(acc => {
        const opt = document.createElement('option');
        opt.value = acc.id;
        const email = acc.email_label || 'Chưa đặt Gmail';
        opt.textContent = `📧 ${email} (Mã: ${acc.id})`;
        if (acc.id === activeAccId) opt.selected = true;
        sel.appendChild(opt);
    });
}

function onChannelNameInput(val) {
    const mode = document.getElementById('channel-mode')?.value;
    const idInput = document.getElementById('channel-input-id');
    if (mode === 'add' && idInput) {
        const clean = (val || '').toLowerCase()
            .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
            .replace(/[^a-z0-9]+/g, '_')
            .replace(/^_+|_+$/g, '');
        idInput.value = clean ? `ch_${clean}` : '';
    }
}

function openAddChannelModal(accountId) {
    populateParentAccountSelect(accountId);
    document.getElementById('channel-mode').value = 'add';
    document.getElementById('modal-channel-title').textContent = 'Thêm Kênh YouTube Mới';
    document.getElementById('channel-input-id').value = '';
    document.getElementById('channel-input-id').disabled = false;
    document.getElementById('channel-input-name').value = '';
    document.getElementById('channel-input-niche').value = '';
    document.getElementById('channel-input-lang').value = 'en';
    document.getElementById('channel-input-voice').value = 'en-US-ChristopherNeural';
    document.getElementById('channel-input-visual').value = 'auto';
    document.getElementById('channel-input-hour').value = '12';
    document.getElementById('modal-channel').classList.add('visible');
    setTimeout(() => {
        document.getElementById('channel-input-name')?.focus();
    }, 150);
}

function openEditChannelModal(accountId, channelId) {
    populateParentAccountSelect(accountId);
    const acc = allAccounts.find(a => a.id === accountId);
    const ch = acc?.brand_channels?.find(c => c.id === channelId);
    if (!ch) {
        showToast('Không tìm thấy thông tin kênh', 'error');
        return;
    }

    document.getElementById('channel-mode').value = 'edit';
    document.getElementById('modal-channel-title').textContent = `Chỉnh Sửa Kênh: ${ch.name}`;
    document.getElementById('channel-input-id').value = ch.id;
    document.getElementById('channel-input-id').disabled = true; // Không cho đổi ID khi sửa
    document.getElementById('channel-input-name').value = ch.name || '';
    document.getElementById('channel-input-niche').value = ch.niche || '';
    document.getElementById('channel-input-lang').value = ch.language || 'en';
    document.getElementById('channel-input-voice').value = ch.preferred_voice || 'en-US-ChristopherNeural';
    document.getElementById('channel-input-visual').value = ch.visual_mode || 'auto';
    document.getElementById('channel-input-hour').value = ch.schedule?.publish_hour_utc ?? 12;
    document.getElementById('modal-channel').classList.add('visible');
}

function closeChannelModal() {
    document.getElementById('modal-channel').classList.remove('visible');
}

async function submitChannelForm() {
    const mode = document.getElementById('channel-mode').value;
    const accountId = document.getElementById('channel-parent-acc').value;
    let id = document.getElementById('channel-input-id').value.trim();
    const name = document.getElementById('channel-input-name').value.trim();
    const niche = document.getElementById('channel-input-niche').value.trim() || 'general';
    const lang = document.getElementById('channel-input-lang').value;
    const voice = document.getElementById('channel-input-voice').value;
    const visual = document.getElementById('channel-input-visual').value;
    const hour = parseInt(document.getElementById('channel-input-hour').value) || 12;

    if (!name) {
        showToast('Vui lòng nhập Tên Kênh YouTube', 'error');
        document.getElementById('channel-input-name')?.focus();
        return;
    }

    // Tự động sinh ID nếu người dùng không nhập
    if (!id) {
        const clean = name.toLowerCase()
            .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
            .replace(/[^a-z0-9]+/g, '_')
            .replace(/^_+|_+$/g, '');
        id = clean ? `ch_${clean}` : `ch_${Date.now()}`;
    }

    const payload = {
        id: id,
        name: name,
        niche: niche,
        language: lang,
        preferred_voice: voice,
        visual_mode: visual,
        publish_hour_utc: hour,
        days: ["monday", "wednesday", "friday", "sunday"],
        google_account: accountId
    };

    try {
        let res;
        if (mode === 'add') {
            res = await apiPost(`/api/accounts/${accountId}/channels`, payload);
        } else {
            const response = await fetch(`/api/accounts/${accountId}/channels/${id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            res = await response.json();
        }

        if (res && res.success) {
            showToast(res.message || 'Lưu kênh thành công!', 'success');
            closeChannelModal();
            await fetchChannels();
            await fetchAccounts();

            if (mode === 'add') {
                setTimeout(() => {
                    if (confirm(`🎉 Kênh "${name}" đã được tạo thành công!\n\n⚡ BƯỚC TIẾP THEO:\nBạn có muốn CẤP QUYỀN YOUTUBE cho kênh này ngay bây giờ không?\n(Hệ thống sẽ mở Google OAuth để bạn chọn đúng kênh '${name}')`)) {
                        startChannelAuth(payload.id, name);
                    }
                }, 300);
            }
        } else {
            showToast(res?.detail || res?.message || 'Có lỗi xảy ra', 'error');
        }
    } catch (e) {
        showToast(`Lỗi: ${e.message}`, 'error');
    }
}

async function deleteChannel(accountId, channelId) {
    if (!confirm(`⚠️ Bạn có chắc muốn xóa kênh '${channelId}'?`)) return;

    try {
        const res = await fetch(`/api/accounts/${accountId}/channels/${channelId}`, { method: 'DELETE' });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast(data.message || `Đã xóa kênh ${channelId}`, 'success');
            await fetchChannels();
            await fetchAccounts();
        } else {
            showToast(data.detail || 'Không thể xóa kênh', 'error');
        }
    } catch (e) {
        showToast(`Lỗi xóa kênh: ${e.message}`, 'error');
    }
}

/* ═══════════════════════════════════════════════════════
   CHANNEL-SPECIFIC YOUTUBE OAUTH
══════════════════════════════════════════════════════════ */
async function startChannelAuth(channelId, channelName) {
    try {
        const modal = document.getElementById('modal-oauth');
        const heading = document.getElementById('oauth-status-heading');
        const desc = document.getElementById('oauth-status-desc');
        const resultBox = document.getElementById('oauth-result-box');
        const linkBox = document.getElementById('oauth-link-box');
        const authLink = document.getElementById('oauth-auth-link');
        const timerText = document.getElementById('oauth-timer-text');
        const spinner = document.getElementById('oauth-spinner');

        // Reset modal state
        modal.classList.add('visible');
        heading.innerHTML = `🔑 Cấp Quyền YouTube Riêng: <span style="color:#60a5fa;">${esc(channelName || channelId)}</span>`;
        desc.innerHTML = `
            <div style="background:rgba(245,158,11,0.12); border:1px solid rgba(245,158,11,0.3); border-radius:8px; padding:12px; margin-bottom:12px; text-align:left; font-size:12.5px; color:#fde68a; line-height:1.6;">
                <strong>⚠️ LƯU Ý RẤT QUAN TRỌNG KHI ĐĂNG NHẬP GOOGLE:</strong><br>
                1. Trình duyệt sẽ mở trang Google, hãy chọn Gmail của bạn.<br>
                2. Một danh sách các <strong>Kênh YouTube / Brand Account</strong> sẽ hiện ra:<br>
                👉 <strong>HÃY BẤM CHỌN ĐÚNG KÊNH "${esc(channelName || channelId)}"!</strong><br>
                <em>(Tuyệt đối không chọn kênh cũ để tránh đăng nhầm video).</em><br>
                3. Bấm <strong>Cho phép / Allow</strong> để hoàn tất.
            </div>
            Đang khởi động phiên đăng nhập Google...
        `;
        resultBox.style.display = 'none';
        linkBox.style.display = 'none';
        spinner.style.display = '';

        if (oauthPollTimer) clearInterval(oauthPollTimer);
        if (oauthCountdownTimer) clearInterval(oauthCountdownTimer);

        let secondsLeft = 180;
        oauthCountdownTimer = setInterval(() => {
            secondsLeft--;
            if (secondsLeft <= 0) {
                clearInterval(oauthCountdownTimer);
                oauthCountdownTimer = null;
            }
            if (timerText && secondsLeft > 0) {
                const m = Math.floor(secondsLeft / 60);
                const s = secondsLeft % 60;
                timerText.textContent = `⏳ Thời gian còn lại: ${m}:${String(s).padStart(2, '0')}`;
            }
        }, 1000);

        // Khởi động OAuth cho kênh này
        const res = await apiPost(`/api/channels/${channelId}/start-auth?service=youtube`, {});
        if (!res || !res.success) {
            showToast(res?.detail || 'Không thể khởi động xác thực cho kênh này', 'error');
            closeOAuthModal();
            return;
        }

        // Polling trạng thái mỗi 2 giây
        oauthPollTimer = setInterval(async () => {
            try {
                const statusData = await apiGet(`/api/channels/${channelId}/auth-status`);
                if (!statusData) return;

                if (statusData.auth_url && linkBox.style.display === 'none') {
                    authLink.href = statusData.auth_url;
                    linkBox.style.display = 'block';
                    desc.innerHTML = `
                        <div style="background:rgba(245,158,11,0.12); border:1px solid rgba(245,158,11,0.3); border-radius:8px; padding:12px; margin-bottom:12px; text-align:left; font-size:12.5px; color:#fde68a; line-height:1.6;">
                            <strong>⚠️ LƯU Ý RẤT QUAN TRỌNG KHI ĐĂNG NHẬP GOOGLE:</strong><br>
                            1. Chọn Gmail của bạn.<br>
                            👉 <strong>2. CHỌN ĐÚNG KÊNH "${esc(channelName || channelId)}"</strong> trong danh sách Kênh / Brand Account.<br>
                            3. Bấm <strong>Cho phép (Allow)</strong> để hoàn tất!
                        </div>
                        Trang đăng nhập Google đã sẵn sàng. Hãy bấm nút bên dưới nếu trình duyệt không tự mở:
                    `;
                    try {
                        window.open(statusData.auth_url, '_blank');
                    } catch (e) {
                        console.log('Popup blocked:', e);
                    }
                }

                if (statusData.status === 'success') {
                    clearInterval(oauthPollTimer);
                    if (oauthCountdownTimer) { clearInterval(oauthCountdownTimer); oauthCountdownTimer = null; }
                    spinner.style.display = 'none';
                    linkBox.style.display = 'none';
                    heading.textContent = '🎉 Cấp Quyền Kênh Thành Công!';
                    desc.innerHTML = `
                        Đã kết nối thành công kênh <strong>${esc(statusData.channel_title || channelName)}</strong>.<br>
                        Từ bây giờ, tất cả video của kênh này sẽ đăng chính xác 100%!
                    `;
                    resultBox.style.display = 'block';
                    resultBox.style.background = 'rgba(52,211,153,0.15)';
                    resultBox.style.color = 'var(--accent-green)';
                    resultBox.textContent = `✓ Đã lưu token riêng cho ${statusData.channel_title || channelName}.`;
                    showToast(`Đã cấp quyền thành công cho kênh ${statusData.channel_title || channelName}!`, 'success');
                    setTimeout(() => {
                        closeOAuthModal();
                        fetchChannels();
                        fetchAccounts();
                        fetchStats();
                    }, 2200);
                } else if (statusData.status === 'error') {
                    clearInterval(oauthPollTimer);
                    if (oauthCountdownTimer) { clearInterval(oauthCountdownTimer); oauthCountdownTimer = null; }
                    spinner.style.display = 'none';
                    linkBox.style.display = 'none';
                    heading.textContent = '❌ Lỗi Cấp Quyền Kênh';
                    desc.textContent = statusData.error || 'Quá trình xác thực bị hủy hoặc xảy ra lỗi.';
                    resultBox.style.display = 'block';
                    resultBox.style.background = 'rgba(251,113,133,0.15)';
                    resultBox.style.color = 'var(--accent-rose)';
                    resultBox.textContent = statusData.error || 'Lỗi không xác định.';
                }
            } catch (err) {
                console.warn('Polling channel OAuth error:', err);
            }
        }, 2000);

    } catch (e) {
        showToast(`Lỗi cấp quyền kênh: ${e.message}`, 'error');
    }
}

async function disconnectChannelAuth(channelId) {
    if (!confirm(`Bạn có chắc muốn hủy cấp quyền YouTube riêng cho kênh này không?`)) return;
    try {
        const res = await apiPost(`/api/channels/${channelId}/disconnect`, {});
        if (res && res.success) {
            showToast('Đã hủy quyền YouTube cho kênh thành công', 'success');
            await fetchChannels();
            await fetchAccounts();
        } else {
            showToast(res?.detail || 'Không thể hủy quyền', 'error');
        }
    } catch (e) {
        showToast(`Lỗi: ${e.message}`, 'error');
    }
}

/* ═══════════════════════════════════════════════════════
   GOOGLE OAUTH AUTHENTICATION
══════════════════════════════════════════════════════════ */
let oauthCountdownTimer = null;

async function startGoogleAuth(accountId, service) {
    try {
        const res = await apiPost(`/api/accounts/${accountId}/auth-start`, { service: service });
        if (!res || !res.success) {
            showToast(res?.detail || 'Không thể khởi chạy phiên đăng nhập Google', 'error');
            return;
        }

        const modal = document.getElementById('modal-oauth');
        const heading = document.getElementById('oauth-status-heading');
        const desc = document.getElementById('oauth-status-desc');
        const resultBox = document.getElementById('oauth-result-box');
        const linkBox = document.getElementById('oauth-link-box');
        const authLink = document.getElementById('oauth-auth-link');
        const timerText = document.getElementById('oauth-timer-text');
        const spinner = document.getElementById('oauth-spinner');

        // Reset trạng thái modal
        modal.classList.add('visible');
        heading.textContent = `Đang mở trình duyệt xác thực ${service.toUpperCase()}...`;
        desc.textContent = `Hệ thống đang mở trình duyệt đăng nhập Google cho tài khoản '${accountId}'. Hãy bấm 'Cho Phép' trên trang Google.`;
        resultBox.style.display = 'none';
        linkBox.style.display = 'none';
        spinner.style.display = '';

        if (oauthPollTimer) clearInterval(oauthPollTimer);
        if (oauthCountdownTimer) clearInterval(oauthCountdownTimer);

        // Đếm ngược 3 phút (đồng bộ với timeout 180s trên server)
        let secondsLeft = 180;
        oauthCountdownTimer = setInterval(() => {
            secondsLeft--;
            if (secondsLeft <= 0) {
                clearInterval(oauthCountdownTimer);
                oauthCountdownTimer = null;
            }
            if (timerText && secondsLeft > 0) {
                const m = Math.floor(secondsLeft / 60);
                const s = secondsLeft % 60;
                timerText.textContent = `⏳ Thời gian còn lại: ${m}:${String(s).padStart(2, '0')}`;
            }
        }, 1000);

        // Polling trạng thái mỗi 2 giây
        oauthPollTimer = setInterval(async () => {
            try {
                const statusData = await apiGet(`/api/accounts/${accountId}/auth-status?service=${service}`);
                if (!statusData) return;

                // Hiển thị link xác thực ngay khi server cung cấp auth_url
                if (statusData.auth_url && linkBox.style.display === 'none') {
                    authLink.href = statusData.auth_url;
                    linkBox.style.display = 'block';
                    heading.textContent = `Đăng nhập Google ${service.toUpperCase()}`;
                    desc.innerHTML = `Vui lòng đăng nhập Google và bấm <strong>"Cho phép / Allow"</strong> để hoàn tất cấp quyền.`;
                    try {
                        window.open(statusData.auth_url, '_blank');
                    } catch (e) {
                        console.log('Browser blocked auto popup:', e);
                    }
                }

                if (statusData.status === 'success') {
                    clearInterval(oauthPollTimer);
                    if (oauthCountdownTimer) { clearInterval(oauthCountdownTimer); oauthCountdownTimer = null; }
                    spinner.style.display = 'none';
                    linkBox.style.display = 'none';
                    heading.textContent = '🎉 Xác Thực Thành Công!';
                    desc.textContent = statusData.message || 'Đã lưu token đăng nhập thành công.';
                    resultBox.style.display = 'block';
                    resultBox.style.background = 'rgba(52,211,153,0.15)';
                    resultBox.style.color = 'var(--accent-green)';
                    resultBox.textContent = '✓ Token đã được đồng bộ vào hệ thống.';
                    showToast(`Đã kết nối ${service.toUpperCase()} thành công!`, 'success');
                    setTimeout(() => {
                        closeOAuthModal();
                        fetchChannels();
                        fetchAccounts();
                        fetchStats();
                    }, 1800);
                } else if (statusData.status === 'error') {
                    clearInterval(oauthPollTimer);
                    if (oauthCountdownTimer) { clearInterval(oauthCountdownTimer); oauthCountdownTimer = null; }
                    spinner.style.display = 'none';
                    linkBox.style.display = 'none';
                    heading.textContent = '❌ Lỗi Xác Thực';
                    desc.textContent = statusData.error || 'Quá trình xác thực bị hủy hoặc xảy ra lỗi.';
                    resultBox.style.display = 'block';
                    resultBox.style.background = 'rgba(251,113,133,0.15)';
                    resultBox.style.color = 'var(--accent-rose)';
                    resultBox.textContent = statusData.error || 'Lỗi không xác định.';
                }
            } catch (err) {
                console.warn('Polling OAuth error:', err);
            }
        }, 2000);

    } catch (e) {
        showToast(`Lỗi khởi động OAuth: ${e.message}`, 'error');
    }
}

function closeOAuthModal() {
    if (oauthPollTimer) {
        clearInterval(oauthPollTimer);
        oauthPollTimer = null;
    }
    if (oauthCountdownTimer) {
        clearInterval(oauthCountdownTimer);
        oauthCountdownTimer = null;
    }
    const modal = document.getElementById('modal-oauth');
    const linkBox = document.getElementById('oauth-link-box');
    const spinner = document.getElementById('oauth-spinner');
    if (modal) modal.classList.remove('visible');
    if (linkBox) linkBox.style.display = 'none';
    if (spinner) spinner.style.display = '';
}

async function disconnectService(accountId, service) {
    if (!confirm(`Bạn có chắc muốn ngắt kết nối ${service.toUpperCase()} của tài khoản '${accountId}'?`)) return;

    try {
        const res = await apiPost(`/api/accounts/${accountId}/disconnect`, { service: service });
        if (res && res.success) {
            showToast(res.message || `Đã ngắt kết nối ${service}`, 'success');
            await fetchAccounts();
            await fetchStats();
        } else {
            showToast(res?.detail || 'Không thể ngắt kết nối', 'error');
        }
    } catch (e) {
        showToast(`Lỗi: ${e.message}`, 'error');
    }
}

/* ═══════════════════════════════════════════════════════
   CLIENT SECRET UPLOAD
══════════════════════════════════════════════════════════ */
function openSecretModal(accountId) {
    document.getElementById('secret-target-account').value = accountId;
    document.getElementById('modal-secret-title').textContent = `Nạp client_secret.json (${accountId})`;
    document.getElementById('secret-json-textarea').value = '';
    document.getElementById('secret-file-input').value = '';
    document.getElementById('modal-secret').classList.add('visible');
}

function closeSecretModal() {
    document.getElementById('modal-secret').classList.remove('visible');
}

function handleSecretFileSelect(evt) {
    const file = evt.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = function(e) {
        document.getElementById('secret-json-textarea').value = e.target.result;
    };
    reader.readAsText(file);
}

async function submitSecretUpload() {
    const accountId = document.getElementById('secret-target-account').value;
    const jsonContent = document.getElementById('secret-json-textarea').value.trim();

    if (!accountId) {
        showToast('Thiếu mã tài khoản', 'error');
        return;
    }
    if (!jsonContent) {
        showToast('Vui lòng chọn file hoặc dán nội dung JSON', 'error');
        return;
    }

    try {
        JSON.parse(jsonContent); // Quick validate syntax
    } catch (err) {
        showToast('Nội dung không phải JSON hợp lệ', 'error');
        return;
    }

    try {
        const res = await apiPost(`/api/accounts/${accountId}/upload-secret`, { content: jsonContent });
        if (res && res.success) {
            showToast(res.message || 'Đã nạp file client_secret thành công!', 'success');
            closeSecretModal();
            await fetchAccounts();
        } else {
            showToast(res?.detail || 'Không thể lưu client_secret.json', 'error');
        }
    } catch (e) {
        showToast(`Lỗi nạp secret: ${e.message}`, 'error');
    }
}

/* ═══════════════════════════════════════════════════════
   YOUTUBE LIVE ANALYTICS & AI FORECAST
══════════════════════════════════════════════════════════ */
let currentAnalyticsChannelId = null;
let currentAnalyticsData = null;
let chartGrowthForecast = null;
let chartRevenueDonut = null;
let chartVideosPerformance = null;
let activeRevenueStageIdx = 1;

async function loadAnalytics() {
    try {
        const res = await apiGet('/api/analytics/channels');
        const channels = res?.channels || [];
        const select = document.getElementById('analytics-channel-select');
        if (!select) return;

        select.innerHTML = '';
        channels.forEach(ch => {
            const opt = document.createElement('option');
            opt.value = ch.id;
            opt.textContent = `📺 ${ch.name} (${ch.handle || '@channel'})`;
            select.appendChild(opt);
        });

        if (channels.length > 0) {
            currentAnalyticsChannelId = channels[0].id;
            await fetchChannelStats(currentAnalyticsChannelId);
        }
    } catch (e) {
        console.error('Lỗi nạp kênh analytics:', e);
    }
}

async function onAnalyticsChannelChange() {
    const select = document.getElementById('analytics-channel-select');
    currentAnalyticsChannelId = select.value;
    await fetchChannelStats(currentAnalyticsChannelId);
}

async function refreshAnalytics(isManual = false) {
    if (!currentAnalyticsChannelId) {
        const select = document.getElementById('analytics-channel-select');
        currentAnalyticsChannelId = select ? select.value : 'channel_maya';
    }
    const btn = document.getElementById('btn-sync-analytics');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner" style="width:12px;height:12px;display:inline-block;"></span> Đang quét...';
    }
    try {
        await fetchChannelStats(currentAnalyticsChannelId, isManual);
        if (isManual) showToast('Đã quét xong số liệu thời gian thực từ YouTube!', 'success');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `
                <svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
                Quét Số Liệu Live
            `;
        }
    }
}

async function fetchChannelStats(channelId, isManual = false) {
    try {
        const data = await apiGet(`/api/analytics/channel/${channelId}`);
        if (!data || !data.success) {
            showToast(data?.error || 'Không thể lấy số liệu kênh', 'error');
            return;
        }
        currentAnalyticsData = data;
        renderAnalyticsUI(data);
    } catch (e) {
        console.error('Lỗi fetchChannelStats:', e);
        showToast(`Lỗi lấy dữ liệu: ${e.message}`, 'error');
    }
}

function renderAnalyticsUI(data) {
    // 1. Channel Header
    document.getElementById('analytics-channel-title').textContent = data.channel_title || 'YouTube Channel';
    document.getElementById('analytics-handle').textContent = data.channel_handle || '@channel';
    document.getElementById('analytics-sub-meta').textContent = data.subscribers_text || '0 người đăng ký';
    document.getElementById('analytics-vid-meta').textContent = data.videos_count_text || `${data.videos?.length || 0} video`;

    const extLink = document.getElementById('analytics-external-link');
    if (extLink && data.channel_url) {
        extLink.href = data.channel_url;
    }

    const avatar = document.getElementById('analytics-avatar');
    if (avatar) {
        if (data.avatar_url) {
            avatar.innerHTML = `<img src="${data.avatar_url}" style="width:100%;height:100%;object-fit:cover;" alt="Avatar" />`;
        } else {
            avatar.innerHTML = '🧠';
        }
    }

    // 2. Big 4 KPIs
    document.getElementById('kpi-total-views').textContent = (data.total_views || 0).toLocaleString();
    document.getElementById('kpi-total-subs').textContent = (data.subscribers_num || 1).toLocaleString();
    document.getElementById('kpi-total-videos').textContent = `${data.videos?.length || 0} Shorts`;
    document.getElementById('kpi-avg-views').textContent = (data.avg_views || 0).toLocaleString();

    // 3. Videos Table
    const tbody = document.getElementById('analytics-videos-tbody');
    if (tbody) {
        tbody.innerHTML = '';
        const vids = data.videos || [];
        if (vids.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:24px;">Chưa tìm thấy video nào trên kênh</td></tr>';
        } else {
            vids.forEach((v, idx) => {
                const tr = document.createElement('tr');
                const rankBadge = idx === 0
                    ? '<span class="rank-badge-gold">#1 TOP</span>'
                    : (idx === 1 ? '<span class="rank-badge-silver">#2</span>' : '<span class="rank-badge-bronze">#3</span>');

                const aiBadge = idx === 0
                    ? '<span class="badge badge-green">🔥 Winning Angle: Tâm lý + Tài phiệt</span><div style="font-size:11.5px;color:var(--text-muted);margin-top:4px;">Chủ đề khơi gợi tò mò cực đại. Khuyên nhân bản thêm 3 góc nhìn khác.</div>'
                    : (idx === 1
                        ? '<span class="badge badge-blue">⭐ Chủ đề Lãnh đạo</span><div style="font-size:11.5px;color:var(--text-muted);margin-top:4px;">Tỷ lệ xem đều đặn, phù hợp khán giả trung niên.</div>'
                        : '<span class="badge badge-purple">💡 Thủ thuật Tâm lý</span><div style="font-size:11.5px;color:var(--text-muted);margin-top:4px;">Hook mạnh về nói dối, cần tăng tốc độ nhịp ở 3 giây đầu.</div>'
                    );

                tr.innerHTML = `
                    <td style="text-align:center;">${rankBadge}</td>
                    <td>
                        <img src="${v.thumbnail}" style="width:50px;height:70px;border-radius:6px;object-fit:cover;border:1px solid rgba(255,255,255,0.1);" alt="Thumb" onerror="this.src='/static/img/placeholder.png'" />
                    </td>
                    <td>
                        <div style="font-weight:600;font-size:13.5px;color:#f8fafc;margin-bottom:4px;">${escapeHtml(v.title)}</div>
                        <div style="font-size:11.5px;color:var(--text-muted);">Mã: <code>${v.video_id || 'N/A'}</code></div>
                    </td>
                    <td>
                        <div style="font-size:15px;font-weight:700;color:#10b981;">${v.views_text}</div>
                    </td>
                    <td>${aiBadge}</td>
                    <td>
                        <a href="${v.url}" target="_blank" class="btn btn-secondary btn-sm" style="display:inline-flex;align-items:center;gap:4px;padding:4px 8px;font-size:12px;">
                            Xem Shorts ↗
                        </a>
                    </td>
                `;
                tbody.appendChild(tr);
            });
        }
    }

    // 4. Forecast Cards
    const forecast = data.forecast;
    if (forecast) {
        renderForecastCards(forecast.stages || []);
        renderRecommendedTopics(forecast.recommended_topics || []);
    }

    // 5. Interactive Charts
    initOrUpdateCharts(data);
}

function renderForecastCards(stages) {
    const container = document.getElementById('forecast-cards-container');
    if (!container) return;
    container.innerHTML = '';

    stages.forEach((stg, i) => {
        const card = document.createElement('div');
        const isHighlight = i === 1 || i === 2;
        card.className = `forecast-card ${isHighlight ? 'highlight' : ''}`;

        card.innerHTML = `
            <div>
                <div class="forecast-stage-label">${stg.label}</div>
                <div class="forecast-views-val">${stg.projected_views.toLocaleString()} <span style="font-size:13px;font-weight:500;color:var(--text-muted);">views</span></div>
                <div style="font-size:11.5px;color:#10b981;margin-bottom:12px;">🎯 ${stg.breakout_chance}</div>
            </div>
            <div>
                <div class="forecast-metric-row">
                    <span>Tổng video:</span>
                    <strong style="color:#fff;">~${stg.total_videos} video</strong>
                </div>
                <div class="forecast-metric-row">
                    <span>Người đăng ký:</span>
                    <strong style="color:#38bdf8;">~${stg.projected_subs.toLocaleString()} subs</strong>
                </div>
                <div class="forecast-metric-row">
                    <span>AdSense Shorts:</span>
                    <strong style="color:#f59e0b;">~$${stg.adsense_usd}</strong>
                </div>
                <div class="forecast-metric-row">
                    <span>Affiliate Ngách:</span>
                    <strong style="color:#10b981;">~$${stg.affiliate_usd}</strong>
                </div>
                <div class="forecast-revenue-pill">
                    Tiềm năng: ~${stg.total_revenue_vnd_formatted}
                </div>
            </div>
        `;
        container.appendChild(card);
    });
}

function renderRecommendedTopics(topics) {
    const container = document.getElementById('recommended-topics-container');
    if (!container) return;
    container.innerHTML = '';

    topics.forEach(t => {
        const card = document.createElement('div');
        card.className = 'topic-rec-card';
        card.innerHTML = `
            <div>
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                    <span class="badge badge-blue">🎯 Điểm tiềm năng: ${t.potential_score}/10</span>
                    <span style="font-size:11.5px;color:var(--text-muted);">Kỳ vọng: <strong>${t.target_views}</strong></span>
                </div>
                <div class="topic-hook-text">"${escapeHtml(t.hook)}"</div>
                <div style="font-size:12px;color:var(--text-secondary);margin-top:6px;">👉 ${escapeHtml(t.topic)}</div>
            </div>
            <button class="btn btn-primary btn-sm" style="width:100%;margin-top:8px;" onclick="applyTopicToCreate('${escapeHtml(t.topic)}', '${escapeHtml(t.hook)}')">
                🎬 Tạo Kịch Bản Với Chủ Đề Này
            </button>
        `;
        container.appendChild(card);
    });
}

async function onForecastRateChange() {
    const rateSelect = document.getElementById('forecast-daily-rate');
    const rate = parseFloat(rateSelect.value) || 1.0;
    try {
        const res = await apiPost('/api/analytics/forecast', {
            channel_id: currentAnalyticsChannelId || 'channel_maya',
            daily_uploads: rate
        });
        if (res && res.forecast) {
            if (currentAnalyticsData) {
                currentAnalyticsData.forecast = res.forecast;
            }
            renderForecastCards(res.forecast.stages || []);
            if (window.Chart && currentAnalyticsData) {
                renderGrowthForecastChart(currentAnalyticsData);
                renderRevenueDonutChart(currentAnalyticsData, activeRevenueStageIdx);
            }
            showToast(`Đã tính toán lại mô hình với tần suất ${rate} video/ngày`, 'info');
        }
    } catch (e) {
        console.error('Lỗi tính forecast:', e);
    }
}

function applyTopicToCreate(topic, hook) {
    switchTab('create');
    const topicInput = document.getElementById('prompt-topic');
    if (topicInput) {
        topicInput.value = `${topic} (Hook: ${hook})`;
        topicInput.focus();
        showToast('Đã nạp chủ đề được AI đề xuất vào ô tạo kịch bản!', 'success');
    }
}

/* ═══════════════════════════════════════════════════════
   CHART.JS VISUALIZATION IMPLEMENTATION
══════════════════════════════════════════════════════════ */
function initOrUpdateCharts(data) {
    if (!window.Chart) {
        console.warn('[Analytics Charts] Chart.js chưa sẵn sàng.');
        return;
    }
    try {
        renderGrowthForecastChart(data);
        renderRevenueDonutChart(data, activeRevenueStageIdx);
        renderVideosPerformanceChart(data);
    } catch (err) {
        console.error('Lỗi khởi tạo biểu đồ:', err);
    }
}

function renderGrowthForecastChart(data) {
    const canvas = document.getElementById('chart-growth-forecast');
    if (!canvas) return;

    if (chartGrowthForecast) {
        chartGrowthForecast.destroy();
        chartGrowthForecast = null;
    }

    const stages = data?.forecast?.stages || [];
    const labels = ['Hiện tại', ...stages.map(s => {
        const parts = s.label.split('(')[0].trim();
        return parts;
    })];
    const viewsData = [data.total_views || 0, ...stages.map(s => s.projected_views)];
    const subsData = [data.subscribers_num || 1, ...stages.map(s => s.projected_subs)];

    const ctx = canvas.getContext('2d');
    const viewsGradient = ctx.createLinearGradient(0, 0, 0, 260);
    viewsGradient.addColorStop(0, 'rgba(56, 189, 248, 0.40)');
    viewsGradient.addColorStop(1, 'rgba(56, 189, 248, 0.01)');

    const subsGradient = ctx.createLinearGradient(0, 0, 0, 260);
    subsGradient.addColorStop(0, 'rgba(16, 185, 129, 0.35)');
    subsGradient.addColorStop(1, 'rgba(16, 185, 129, 0.01)');

    chartGrowthForecast = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: '👁️ Lượt xem tích lũy (Views)',
                    data: viewsData,
                    borderColor: '#38bdf8',
                    backgroundColor: viewsGradient,
                    borderWidth: 3,
                    fill: true,
                    tension: 0.38,
                    pointBackgroundColor: '#38bdf8',
                    pointBorderColor: '#0f172a',
                    pointBorderWidth: 2,
                    pointRadius: 5,
                    pointHoverRadius: 8,
                    yAxisID: 'y'
                },
                {
                    label: '👥 Người đăng ký (Subs)',
                    data: subsData,
                    borderColor: '#10b981',
                    backgroundColor: subsGradient,
                    borderWidth: 2.5,
                    fill: true,
                    tension: 0.38,
                    pointBackgroundColor: '#10b981',
                    pointBorderColor: '#0f172a',
                    pointBorderWidth: 2,
                    pointRadius: 5,
                    pointHoverRadius: 8,
                    yAxisID: 'y1'
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                mode: 'index',
                intersect: false
            },
            plugins: {
                legend: {
                    position: 'top',
                    align: 'end',
                    labels: {
                        color: '#94a3b8',
                        font: { size: 12, family: 'Inter, sans-serif', weight: 600 },
                        usePointStyle: true,
                        pointStyle: 'circle',
                        padding: 14
                    }
                },
                tooltip: {
                    backgroundColor: 'rgba(15, 23, 42, 0.95)',
                    titleColor: '#f8fafc',
                    bodyColor: '#cbd5e1',
                    borderColor: 'rgba(255, 255, 255, 0.12)',
                    borderWidth: 1,
                    padding: 12,
                    cornerRadius: 8,
                    callbacks: {
                        label: function(context) {
                            const val = context.parsed.y;
                            if (context.datasetIndex === 0) {
                                return ` 👁️ Lượt xem: ${val.toLocaleString()} views`;
                            } else {
                                return ` 👥 Người đăng ký: ${val.toLocaleString()} subs`;
                            }
                        }
                    }
                }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: { color: '#94a3b8', font: { size: 12 } }
                },
                y: {
                    type: 'linear',
                    display: true,
                    position: 'left',
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: {
                        color: '#38bdf8',
                        font: { size: 11 },
                        callback: function(val) {
                            if (val >= 1000000) return (val / 1000000).toFixed(1) + 'M';
                            if (val >= 1000) return (val / 1000).toFixed(0) + 'K';
                            return val.toLocaleString();
                        }
                    }
                },
                y1: {
                    type: 'linear',
                    display: true,
                    position: 'right',
                    grid: { drawOnChartArea: false },
                    ticks: {
                        color: '#10b981',
                        font: { size: 11 },
                        callback: function(val) {
                            if (val >= 1000) return (val / 1000).toFixed(1) + 'K';
                            return val.toLocaleString();
                        }
                    }
                }
            }
        }
    });
}

function renderRevenueDonutChart(data, stageIdx = 1) {
    const canvas = document.getElementById('chart-revenue-donut');
    if (!canvas) return;

    if (chartRevenueDonut) {
        chartRevenueDonut.destroy();
        chartRevenueDonut = null;
    }

    const stages = data?.forecast?.stages || [];
    const stage = stages[stageIdx] || stages[1] || stages[0];
    if (!stage) return;

    const adsense = Number(stage.adsense_usd) || 0;
    const affiliate = Number(stage.affiliate_usd) || 0;
    const brand = stage.projected_views > 100000 ? Math.round(stage.total_revenue_usd * 0.25 * 10) / 10 : 0;

    const revText = document.getElementById('donut-center-revenue');
    if (revText) {
        revText.textContent = stage.total_revenue_vnd_formatted || '0 ₫';
    }

    const legContainer = document.getElementById('donut-legend-summary');
    if (legContainer) {
        legContainer.innerHTML = `
            <div class="donut-legend-item">
                <div class="donut-legend-title"><span style="color:#f59e0b;">●</span> AdSense</div>
                <div class="donut-legend-value" style="color:#f59e0b;">~$${adsense}</div>
            </div>
            <div class="donut-legend-item">
                <div class="donut-legend-title"><span style="color:#10b981;">●</span> Affiliate</div>
                <div class="donut-legend-value" style="color:#10b981;">~$${affiliate}</div>
            </div>
            <div class="donut-legend-item">
                <div class="donut-legend-title"><span style="color:#8b5cf6;">●</span> Brand Deal</div>
                <div class="donut-legend-value" style="color:#8b5cf6;">~$${brand}</div>
            </div>
        `;
    }

    const ctx = canvas.getContext('2d');
    chartRevenueDonut = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['🪙 AdSense Shorts', '🛒 Affiliate Ngách', '🤝 Brand Sponsorship'],
            datasets: [{
                data: [adsense, affiliate, brand > 0 ? brand : 0.01],
                backgroundColor: [
                    '#f59e0b',
                    '#10b981',
                    '#8b5cf6'
                ],
                borderWidth: 2,
                borderColor: '#0f172a',
                hoverOffset: 6
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '72%',
            plugins: {
                legend: {
                    display: false
                },
                tooltip: {
                    backgroundColor: 'rgba(15, 23, 42, 0.95)',
                    titleColor: '#f8fafc',
                    bodyColor: '#cbd5e1',
                    borderColor: 'rgba(255, 255, 255, 0.12)',
                    borderWidth: 1,
                    padding: 10,
                    cornerRadius: 8,
                    callbacks: {
                        label: function(context) {
                            return ` ${context.label}: ~$${context.parsed} USD`;
                        }
                    }
                }
            }
        }
    });
}

function onRevenueDonutStageChange() {
    const sel = document.getElementById('chart-donut-stage-select');
    if (!sel || !currentAnalyticsData) return;
    activeRevenueStageIdx = parseInt(sel.value, 10) || 0;
    renderRevenueDonutChart(currentAnalyticsData, activeRevenueStageIdx);
}

function renderVideosPerformanceChart(data) {
    const canvas = document.getElementById('chart-videos-performance');
    if (!canvas) return;

    if (chartVideosPerformance) {
        chartVideosPerformance.destroy();
        chartVideosPerformance = null;
    }

    const vids = data.videos || [];
    if (vids.length === 0) return;

    const labels = vids.map((v, i) => {
        const cleanTitle = (v.title || `Video #${i + 1}`).trim();
        return cleanTitle.length > 28 ? cleanTitle.slice(0, 25) + '...' : cleanTitle;
    });
    const views = vids.map(v => v.views_num || 0);
    const avgVal = data.avg_views || 0;
    const avgLineData = vids.map(() => avgVal);

    const maxView = Math.max(...views);
    const bgColors = views.map(v => {
        if (v === maxView && v > 0) return '#f59e0b'; // Gold for #1 top
        return '#8b5cf6'; // Indigo purple
    });

    const ctx = canvas.getContext('2d');
    chartVideosPerformance = new Chart(ctx, {
        data: {
            labels: labels,
            datasets: [
                {
                    type: 'bar',
                    label: '👁️ Lượt xem thực tế',
                    data: views,
                    backgroundColor: bgColors,
                    borderRadius: 6,
                    maxBarThickness: 45,
                    order: 2
                },
                {
                    type: 'line',
                    label: `⭐ Mức TB kênh (${avgVal.toLocaleString()} views)`,
                    data: avgLineData,
                    borderColor: '#fbbf24',
                    borderWidth: 2,
                    borderDash: [6, 6],
                    pointRadius: 0,
                    fill: false,
                    order: 1
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'top',
                    align: 'end',
                    labels: {
                        color: '#94a3b8',
                        font: { size: 12, family: 'Inter, sans-serif' },
                        usePointStyle: true,
                        padding: 12
                    }
                },
                tooltip: {
                    backgroundColor: 'rgba(15, 23, 42, 0.95)',
                    titleColor: '#f8fafc',
                    bodyColor: '#cbd5e1',
                    borderColor: 'rgba(255, 255, 255, 0.12)',
                    borderWidth: 1,
                    padding: 12,
                    cornerRadius: 8,
                    callbacks: {
                        title: function(items) {
                            if (!items.length) return '';
                            const idx = items[0].dataIndex;
                            return vids[idx]?.title || items[0].label;
                        },
                        label: function(context) {
                            if (context.dataset.type === 'line') {
                                return ` ⭐ Mức trung bình: ${avgVal.toLocaleString()} lượt xem`;
                            }
                            const val = context.parsed.y;
                            const diff = val - avgVal;
                            const diffText = diff >= 0
                                ? `(+${diff.toLocaleString()} so với TB - 🔥 Vượt trội)`
                                : `(${diff.toLocaleString()} so với TB)`;
                            return ` 👁️ Lượt xem: ${val.toLocaleString()} ${diffText}`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: {
                        color: '#94a3b8',
                        font: { size: 11.5 },
                        maxRotation: 20
                    }
                },
                y: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: {
                        color: '#94a3b8',
                        font: { size: 11 },
                        callback: function(val) {
                            return val.toLocaleString();
                        }
                    }
                }
            }
        }
    });
}

/* ═════════════════════════════════════════════════════════════════════════════
   MANUAL STUDIO (LỒNG TIẾNG, SFX & PHỤ ĐỀ AI)
   ═════════════════════════════════════════════════════════════════════════════ */
let manualStudioInitialized = false;
let activeStudioMode = 'single_video';
let singleVideoFile = null;
let manualScenesList = [];
let manualSfxOptions = [];
let manualVoicesList = [];

function setStudioMode(mode) {
    activeStudioMode = mode;
    const singleSection = document.getElementById('studio-single-video-section');
    const multiSection = document.getElementById('studio-multi-scene-section');
    const btnSingle = document.getElementById('btn-mode-single');
    const btnMulti = document.getElementById('btn-mode-multi');

    if (mode === 'single_video') {
        if (singleSection) singleSection.style.display = 'block';
        if (multiSection) multiSection.style.display = 'none';
        if (btnSingle) btnSingle.classList.add('active');
        if (btnMulti) btnMulti.classList.remove('active');
    } else {
        if (singleSection) singleSection.style.display = 'none';
        if (multiSection) multiSection.style.display = 'block';
        if (btnSingle) btnSingle.classList.remove('active');
        if (btnMulti) btnMulti.classList.add('active');
    }
}

function triggerSingleVideoSelect() {
    const fileInput = document.getElementById('single-video-file-input');
    if (fileInput) fileInput.click();
}

function handleSingleVideoSelect(input) {
    if (!input.files || !input.files[0]) return;
    loadSingleVideoFile(input.files[0]);
}

function loadSingleVideoFile(file) {
    if (!file.type.startsWith('video/')) {
        showToast('⚠️ Vui lòng chọn tệp video (MP4, WebM, MOV...)', 'warning');
        return;
    }
    singleVideoFile = file;

    const player = document.getElementById('single-video-player');
    const emptyBox = document.getElementById('single-dropzone-empty');
    const previewWrapper = document.getElementById('single-video-preview-wrapper');
    const nameEl = document.getElementById('single-video-meta-name');
    const sizeEl = document.getElementById('single-video-meta-size');
    const durEl = document.getElementById('single-video-meta-duration');

    if (nameEl) nameEl.textContent = file.name;
    if (sizeEl) sizeEl.textContent = (file.size / (1024 * 1024)).toFixed(1) + ' MB';

    const objectUrl = URL.createObjectURL(file);
    if (player) {
        player.src = objectUrl;
        player.onloadedmetadata = () => {
            const sec = Math.round(player.duration * 10) / 10;
            if (durEl) durEl.textContent = sec + 's';
        };
    }

    if (emptyBox) emptyBox.style.display = 'none';
    if (previewWrapper) previewWrapper.style.display = 'block';

    // Tự điền tiêu đề gợi ý nếu ô tiêu đề đang rỗng
    const titleInput = document.getElementById('single-title');
    if (titleInput && !titleInput.value.trim()) {
        const rawName = file.name.replace(/\.[^/.]+$/, '').replace(/[-_]/g, ' ');
        titleInput.value = rawName.charAt(0).toUpperCase() + rawName.slice(1);
    }

    showToast(`🎬 Đã nạp video: ${file.name}`, 'success');
}

function initSingleVideoDropzone() {
    const dropzone = document.getElementById('single-video-dropzone');
    if (!dropzone || dropzone._dragInitialized) return;
    dropzone._dragInitialized = true;

    ['dragenter', 'dragover'].forEach(eventName => {
        dropzone.addEventListener(eventName, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropzone.classList.add('dragover');
        }, false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropzone.addEventListener(eventName, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropzone.classList.remove('dragover');
        }, false);
    });

    dropzone.addEventListener('drop', (e) => {
        const dt = e.dataTransfer;
        if (dt && dt.files && dt.files[0]) {
            loadSingleVideoFile(dt.files[0]);
        }
    }, false);
}

let singleTimingMode = 'auto';

function setScriptTimingMode(mode) {
    singleTimingMode = mode;
    const btnAuto = document.getElementById('timing-mode-btn-auto');
    const btnTimestamps = document.getElementById('timing-mode-btn-timestamps');
    const toolbar = document.getElementById('timing-helper-toolbar');
    const badge = document.getElementById('timing-badge-indicator');
    const input = document.getElementById('single-script-input');

    if (mode === 'timestamps') {
        if (btnAuto) btnAuto.classList.remove('active');
        if (btnTimestamps) btnTimestamps.classList.add('active');
        if (toolbar) toolbar.style.display = 'flex';
        if (badge) {
            badge.textContent = '⏱️ AI căn khớp chuẩn xác từng khung thời gian';
            badge.className = 'pill-indicator pill-indicator-pink';
        }
        if (input && !input.value.trim()) {
            input.placeholder = `Nhập kịch bản kèm mốc thời gian theo định dạng:
00-10: Bạn có tin dưới đáy đại dương vẫn còn những bí ẩn...
10:25: Nơi áp lực nước có thể nghiền nát cả một chiếc tàu ngầm!
25-45: Hôm nay chúng ta sẽ khám phá sự thật chấn động này...

💡 Mẹo: Hỗ trợ 00-10:, 10:25:, 00:00 - 00:10:, hoặc [00-10]. AI sẽ tự động tăng tốc đọc hoặc đệm nhịp để khớp chuẩn xác từng giây!`;
        }
    } else {
        if (btnAuto) btnAuto.classList.add('active');
        if (btnTimestamps) btnTimestamps.classList.remove('active');
        if (toolbar) toolbar.style.display = 'none';
        if (badge) {
            badge.textContent = '✨ Giữ trọn 100% thời lượng video';
            badge.className = 'pill-indicator pill-indicator-green';
        }
        if (input && !input.value.trim()) {
            input.placeholder = `Dán toàn bộ lời thoại kịch bản vào đây...

Ví dụ:
Bạn có tin rằng dưới đáy đại dương sâu thẳm vẫn còn những quái vật bí ẩn chưa từng được phát hiện?
Nơi mà áp lực nước có thể nghiền nát một chiếc tàu ngầm trong nháy mắt!
Hôm nay, chúng ta sẽ khám phá 3 sự thật chấn động nhất...

💡 Mẹo: Hệ thống sẽ tự động đo thời lượng đọc, giữ trọn vẹn 100% video tải lên và chèn SFX chuyển ý cực kỳ mượt mà!`;
        }
    }
    updateScriptStats();
}

function insertTimestampSample() {
    const input = document.getElementById('single-script-input');
    if (!input) return;
    const sample = `00-10: Bạn có tin rằng dưới đáy đại dương sâu thẳm vẫn còn những sinh vật bí ẩn chưa từng được phát hiện?\n10:25: Nơi mà áp lực nước có thể nghiền nát một chiếc tàu ngầm titan trong nháy mắt!\n25-45: Hôm nay chúng ta sẽ cùng khám phá 3 sự thật gây chấn động nhất thế giới...`;
    input.value = sample;
    updateScriptStats();
    showToast('📋 Đã điền kịch bản mẫu mốc thời gian (00-10, 10:25, 25-45)!', 'success');
    input.focus();
}

function autoGenerateTimestampsFromVideo() {
    const player = document.getElementById('single-video-player');
    const totalSec = (player && player.duration && !isNaN(player.duration)) ? Math.round(player.duration) : 49;
    
    // Tự động phân chia 3 mốc thời gian tỷ lệ vàng: Mở đầu (25%), Cao trào (40%), Kết thúc (35%)
    const m1 = 0;
    const m2 = Math.max(8, Math.min(15, Math.round(totalSec * 0.25)));
    const m3 = Math.max(m2 + 8, Math.round(totalSec * 0.65));
    const m4 = totalSec;

    const pad = (n) => String(n).padStart(2, '0');
    const template = `${pad(m1)}-${pad(m2)}: [Đoạn 1 - Mở đầu giật gân, cuốn hút người xem]\n${pad(m2)}:${pad(m3)}: [Đoạn 2 - Nội dung chính, dẫn dắt và cao trào]\n${pad(m3)}-${pad(m4)}: [Đoạn 3 - Bí ẩn kết luận, kêu gọi đăng ký kênh]`;

    const input = document.getElementById('single-script-input');
    if (input) {
        input.value = template;
        updateScriptStats();
        showToast(`🪄 Đã tự động tạo 3 khung thời gian chuẩn theo video ${totalSec}s!`, 'success');
        input.focus();
    }
}

function updateScriptStats() {
    const input = document.getElementById('single-script-input');
    if (!input) return;
    const text = input.value.trim();
    const words = text ? text.split(/\s+/).length : 0;
    const chars = text.length;

    // Tốc độ đọc trung bình tiếng Việt/Anh ~3.2 từ/giây
    const estSeconds = words > 0 ? Math.max(1, Math.round(words / 3.2)) : 0;

    const wordEl = document.getElementById('single-word-count');
    const charEl = document.getElementById('single-char-count');
    const estEl = document.getElementById('single-est-duration');
    const slotsEl = document.getElementById('timing-stats-slots');
    const slotCountEl = document.getElementById('single-slot-count');

    if (wordEl) wordEl.textContent = words;
    if (charEl) charEl.textContent = chars;
    if (estEl) estEl.textContent = estSeconds;

    if (singleTimingMode === 'timestamps') {
        const tsRegex = /^\s*\[?\s*(\d{1,2}(?::\d{1,2})?)\s*(?:-|đến|to|->|:)\s*(\d{1,2}(?::\d{1,2})?)\s*\]?\s*[:\-]?\s*(.*)$/gm;
        const matches = text.match(tsRegex);
        const count = matches ? matches.length : 0;
        if (slotCountEl) slotCountEl.textContent = count;
        if (slotsEl) slotsEl.style.display = 'inline';
    } else {
        if (slotsEl) slotsEl.style.display = 'none';
    }
}

/* ═══════════════════════════════════════════════════════
   PREVIEW VOICE (NGHE THỬ GIỌNG ĐỌC AI PHÁT RA LOA)
══════════════════════════════════════════════════════ */
let currentVoiceAudio = null;
let isVoicePreviewLoading = false;

function resetPreviewVoiceButton() {
    isVoicePreviewLoading = false;
    const icon = document.getElementById('preview-voice-icon');
    const text = document.getElementById('preview-voice-text');
    const btn = document.getElementById('btn-preview-voice');
    if (icon) icon.textContent = '🔊';
    if (text) text.textContent = 'Nghe Thử Giọng';
    if (btn) {
        btn.disabled = false;
        btn.style.color = '#f472b6';
        btn.style.borderColor = 'rgba(236,72,153,0.35)';
        btn.style.background = 'rgba(236,72,153,0.08)';
    }
}

function onSingleVoiceChange(voiceId) {
    // Tự động phát thử giọng đọc khi người dùng chọn giọng mới
    previewSelectedVoice(true);
}

async function previewSelectedVoice(isAutoTrigger = false) {
    // Nếu đang phát, bấm lại sẽ dừng
    if (currentVoiceAudio && !currentVoiceAudio.paused) {
        currentVoiceAudio.pause();
        currentVoiceAudio = null;
        resetPreviewVoiceButton();
        if (!isAutoTrigger) {
            showToast('⏹️ Đã dừng phát giọng thử', 'info');
            return;
        }
    }

    if (isVoicePreviewLoading) return;

    const voiceSelect = document.getElementById('single-voice');
    const rateSelect = document.getElementById('single-voice-rate');
    const scriptInput = document.getElementById('single-script-input');
    const btn = document.getElementById('btn-preview-voice');
    const icon = document.getElementById('preview-voice-icon');
    const text = document.getElementById('preview-voice-text');

    const voice = voiceSelect ? voiceSelect.value : 'vi-VN-HoaiMyNeural';
    const rate = rateSelect ? rateSelect.value : '+10%';
    const scriptText = scriptInput ? scriptInput.value.trim() : '';

    const selectedOption = voiceSelect ? voiceSelect.options[voiceSelect.selectedIndex] : null;
    const voiceName = selectedOption ? selectedOption.text : voice;

    // Trích câu thoại thử: lấy câu đầu tiên của kịch bản người dùng nhập
    let sampleSnippet = '';
    if (scriptText) {
        const firstLine = scriptText.split('\n')[0].trim();
        const sentences = firstLine.split(/[.?!]/).map(s => s.trim()).filter(Boolean);
        sampleSnippet = sentences.length > 0 ? sentences[0] : firstLine;
    }

    isVoicePreviewLoading = true;
    if (icon) icon.innerHTML = '<span class="loading-spinner" style="display:inline-block;width:12px;height:12px;border:2px solid #f472b6;border-top-color:transparent;border-radius:50%;animation:spin 0.8s linear infinite;"></span>';
    if (text) text.textContent = 'Đang tải giọng...';
    if (btn) btn.disabled = true;

    try {
        const resp = await fetch('/api/manual-studio/preview-voice', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                voice: voice,
                rate: rate,
                text: sampleSnippet
            })
        });

        if (!resp.ok) {
            const errData = await resp.json().catch(() => ({}));
            throw new Error(errData.detail || `Lỗi tải giọng đọc (${resp.status})`);
        }

        const audioBlob = await resp.blob();
        const audioUrl = URL.createObjectURL(audioBlob);

        currentVoiceAudio = new Audio(audioUrl);
        currentVoiceAudio.onended = () => {
            resetPreviewVoiceButton();
            currentVoiceAudio = null;
        };
        currentVoiceAudio.onerror = () => {
            resetPreviewVoiceButton();
            currentVoiceAudio = null;
        };

        await currentVoiceAudio.play();

        // Cập nhật nút sang trạng thái đang phát
        isVoicePreviewLoading = false;
        if (btn) {
            btn.disabled = false;
            btn.style.color = '#34d399';
            btn.style.borderColor = 'rgba(52,211,153,0.4)';
            btn.style.background = 'rgba(52,211,153,0.12)';
        }
        if (icon) icon.textContent = '⏹️';
        if (text) text.textContent = 'Dừng Nghe';

        showToast(`🎙️ Đang phát thử: ${voiceName}`, 'info');

    } catch (err) {
        console.error('Lỗi preview voice:', err);
        showToast('❌ ' + err.message, 'error');
        resetPreviewVoiceButton();
    }
}

async function initManualStudio() {
    // Populate channels cho cả 2 chế độ
    const channelSelects = [
        document.getElementById('manual-channel'),
        document.getElementById('single-channel')
    ];
    if (allChannels && allChannels.length > 0) {
        const optionsHtml = allChannels.map(c => 
            `<option value="${escapeHtml(c.id)}">📺 ${escapeHtml(c.name)} (${escapeHtml(c.id)})</option>`
        ).join('');
        channelSelects.forEach(sel => {
            if (sel) sel.innerHTML = optionsHtml;
        });
    }

    initSingleVideoDropzone();
    updateScriptStats();

    if (manualStudioInitialized) return;

    try {
        const [voicesRes, sfxRes, bgmRes] = await Promise.all([
            apiGet('/api/manual-studio/voices'),
            apiGet('/api/manual-studio/sfx'),
            apiGet('/api/manual-studio/bgm')
        ]);

        // 1. Voices
        const voiceSelects = [
            document.getElementById('manual-voice'),
            document.getElementById('single-voice')
        ];
        if (voicesRes && voicesRes.voices) {
            manualVoicesList = voicesRes.voices;
            const voiceHtml = manualVoicesList.map(v => 
                `<option value="${escapeHtml(v.id)}">${escapeHtml(v.name)}</option>`
            ).join('');
            voiceSelects.forEach(sel => {
                if (sel) sel.innerHTML = voiceHtml;
            });
        }

        // 2. SFX options
        if (sfxRes && sfxRes.sfx) {
            manualSfxOptions = sfxRes.sfx;
        }

        // 3. BGM
        const bgmSelects = [
            document.getElementById('manual-bgm'),
            document.getElementById('single-bgm')
        ];
        if (bgmRes && bgmRes.bgm) {
            const bgmHtml = '<option value="">-- Không dùng nhạc nền --</option>' + 
                bgmRes.bgm.map(b => `<option value="${escapeHtml(b.path)}">🎵 ${escapeHtml(b.name)}</option>`).join('');
            bgmSelects.forEach(sel => {
                if (sel) {
                    const currentVal = sel.value;
                    sel.innerHTML = bgmHtml;
                    if (currentVal) sel.value = currentVal;
                }
            });
        }

        // Initialize with 2 default scene slots
        if (manualScenesList.length === 0) {
            addManualScene({ cameraMotion: "zoom_in", sfxCue: "auto" });
            addManualScene({ cameraMotion: "zoom_out", sfxCue: "auto" });
        }

        const manualScriptEl = document.getElementById('manual-script-input');
        if (manualScriptEl && !manualScriptEl.value.trim()) {
            manualScriptEl.value = `Bạn có tin rằng dưới đáy đại dương sâu thẳm vẫn còn những quái vật bí ẩn chưa từng được phát hiện?\nNơi mà áp lực nước có thể nghiền nát một chiếc tàu ngầm trong nháy mắt!`;
            updateManualScriptStats();
        }

        manualStudioInitialized = true;
    } catch (e) {
        console.error('Lỗi khởi tạo Manual Studio:', e);
    }
}

// ═════════════════════════════════════════════════════════════════════════════
// MULTI-SCENE STUDIO & SINGLE SCRIPT LOGIC
// ═════════════════════════════════════════════════════════════════════════════
let manualTimingMode = 'auto';

function setManualScriptTimingMode(mode) {
    manualTimingMode = mode;
    const btnAuto = document.getElementById('manual-timing-mode-btn-auto');
    const btnTimestamps = document.getElementById('manual-timing-mode-btn-timestamps');
    const toolbar = document.getElementById('manual-timing-helper-toolbar');
    const badge = document.getElementById('manual-timing-badge-indicator');

    if (mode === 'timestamps') {
        if (btnAuto) btnAuto.classList.remove('active');
        if (btnTimestamps) btnTimestamps.classList.add('active');
        if (toolbar) toolbar.style.display = 'flex';
        if (badge) {
            badge.textContent = '⏱️ AI căn khớp theo mốc thời gian hoặc nhãn cảnh';
            badge.className = 'pill-indicator pill-indicator-pink';
        }
    } else {
        if (btnAuto) btnAuto.classList.add('active');
        if (btnTimestamps) btnTimestamps.classList.remove('active');
        if (toolbar) toolbar.style.display = 'none';
        if (badge) {
            badge.textContent = '✨ AI tự động phân bổ đều cho các phân cảnh';
            badge.className = 'pill-indicator pill-indicator-green';
        }
    }
    updateManualScriptStats();
}

function parseMultiSceneScript(scriptText, numScenes, timingMode = 'auto') {
    const raw = (scriptText || '').trim();
    if (!raw || numScenes <= 0) {
        return Array.from({ length: Math.max(1, numScenes) }, () => ({ narration: '', duration: null }));
    }

    const lines = raw.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
    const tsRegex = /^\s*\[?\s*(\d{1,2}(?::\d{1,2})?)\s*(?:-|đến|to|->|:)\s*(\d{1,2}(?::\d{1,2})?)\s*\]?\s*[:\-]?\s*(.*)$/i;
    const sceneLabelRegex = /^\s*(?:cảnh|phân cảnh|scene|đoạn|bước|part)?\s*#?(\d+)[:.\-]\s*(.*)$/i;

    function toSec(v) {
        const parts = v.trim().split(':');
        if (parts.length === 2) return parseFloat(parts[0]) * 60 + parseFloat(parts[1]);
        if (parts.length === 3) return parseFloat(parts[0]) * 3600 + parseFloat(parts[1]) * 60 + parseFloat(parts[2]);
        return parseFloat(v);
    }

    // 1. Phân tích theo mốc thời gian 00-10:
    const tsMatches = [];
    let currentTs = null;
    for (const line of lines) {
        const m = line.match(tsRegex);
        if (m) {
            const s1 = toSec(m[1]);
            const s2 = toSec(m[2]);
            const dur = (s2 > s1) ? Math.max(2.0, s2 - s1) : null;
            currentTs = { text: m[3].trim(), duration: dur };
            tsMatches.push(currentTs);
        } else if (currentTs) {
            currentTs.text += ' ' + line;
        }
    }

    if (tsMatches.length > 0) {
        const results = [];
        for (let i = 0; i < numScenes; i++) {
            if (i < tsMatches.length) {
                results.push({ narration: tsMatches[i].text.trim(), duration: tsMatches[i].duration });
            } else {
                results.push({ narration: '', duration: null });
            }
        }
        if (tsMatches.length > numScenes) {
            const extra = tsMatches.slice(numScenes).map(m => m.text.trim()).join(' ');
            results[results.length - 1].narration = (results[results.length - 1].narration + ' ' + extra).trim();
        }
        return results;
    }

    // 2. Phân tích theo nhãn Cảnh 1:, Cảnh 2:...
    const sceneMatches = [];
    let currentSc = null;
    for (const line of lines) {
        const m = line.match(sceneLabelRegex);
        if (m) {
            currentSc = { idx: parseInt(m[1]), text: m[2].trim() };
            sceneMatches.push(currentSc);
        } else if (currentSc) {
            currentSc.text += ' ' + line;
        }
    }

    if (sceneMatches.length >= 2) {
        const results = [];
        for (let i = 0; i < numScenes; i++) {
            if (i < sceneMatches.length) {
                results.push({ narration: sceneMatches[i].text.trim(), duration: null });
            } else {
                results.push({ narration: '', duration: null });
            }
        }
        if (sceneMatches.length > numScenes) {
            const extra = sceneMatches.slice(numScenes).map(s => s.text.trim()).join(' ');
            results[results.length - 1].narration = (results[results.length - 1].narration + ' ' + extra).trim();
        }
        return results;
    }

    // 3. Phân tích theo đoạn văn (Paragraphs)
    const paragraphs = raw.split(/\n\s*\n/).map(p => p.trim()).filter(Boolean);
    if (paragraphs.length === numScenes) {
        return paragraphs.map(p => ({ narration: p, duration: null }));
    }

    // 4. Chia đều theo câu văn (Sentences)
    const fullText = lines.join(' ');
    const rawSentences = fullText.split(/(?<=[.!?…])\s+/).map(s => s.trim()).filter(Boolean);
    const sentences = rawSentences.length > 0 ? rawSentences : [fullText];

    const results = [];
    if (sentences.length >= numScenes) {
        const k = Math.floor(sentences.length / numScenes);
        const m = sentences.length % numScenes;
        let start = 0;
        for (let i = 0; i < numScenes; i++) {
            const chunkSize = k + (i < m ? 1 : 0);
            const chunk = sentences.slice(start, start + chunkSize).join(' ');
            results.push({ narration: chunk, duration: null });
            start += chunkSize;
        }
    } else {
        for (let i = 0; i < numScenes; i++) {
            results.push({ narration: sentences[i] || '', duration: null });
        }
    }
    return results;
}

function updateManualScriptStats() {
    const input = document.getElementById('manual-script-input');
    if (!input) return;
    const text = input.value.trim();
    const words = text ? text.split(/\s+/).length : 0;
    const chars = text.length;
    const estSeconds = words > 0 ? Math.max(1, Math.round(words / 3.2)) : 0;

    const wordEl = document.getElementById('manual-word-count');
    const charEl = document.getElementById('manual-char-count');
    const estEl = document.getElementById('manual-est-duration');
    const slotsEl = document.getElementById('manual-timing-stats-slots');
    const slotCountEl = document.getElementById('manual-slot-count');

    if (wordEl) wordEl.textContent = words;
    if (charEl) charEl.textContent = chars;
    if (estEl) estEl.textContent = estSeconds;

    const numScenes = manualScenesList.length || 1;
    const parsed = parseMultiSceneScript(text, numScenes, manualTimingMode);

    if (manualTimingMode === 'timestamps') {
        if (slotsEl) slotsEl.style.display = 'inline';
        if (slotCountEl) slotCountEl.textContent = parsed.filter(p => p.narration).length;
    } else {
        if (slotsEl) slotsEl.style.display = 'none';
    }

    // Render Live Preview Inspector
    const previewContainer = document.getElementById('manual-script-scenes-preview');
    const mappingList = document.getElementById('manual-mapping-list');
    if (previewContainer && mappingList) {
        if (words > 0) {
            previewContainer.style.display = 'block';
            mappingList.innerHTML = parsed.map((item, idx) => {
                const durInfo = item.duration ? ` [${item.duration}s]` : '';
                const snippet = item.narration ? escapeHtml(item.narration) : '<em style="color:#64748b;">(Chưa có lời thoại)</em>';
                return `
                    <div class="scene-mapping-item">
                        <span class="scene-mapping-tag">🎬 Cảnh #${idx + 1}${durInfo}:</span>
                        <span class="scene-mapping-text">${snippet}</span>
                    </div>
                `;
            }).join('');
        } else {
            previewContainer.style.display = 'none';
        }
    }
}

function insertManualTimestampSample() {
    const input = document.getElementById('manual-script-input');
    if (!input) return;
    const numScenes = Math.max(2, manualScenesList.length);
    let sample = '';
    let cur = 0;
    for (let i = 1; i <= numScenes; i++) {
        const next = cur + (i === 1 ? 10 : (i === 2 ? 15 : 12));
        const pad = (n) => String(n).padStart(2, '0');
        if (i === 1) {
            sample += `${pad(cur)}-${pad(next)}: Bạn có tin rằng dưới đáy đại dương vẫn còn những bí ẩn kinh hoàng?\n`;
        } else if (i === 2) {
            sample += `${pad(cur)}:${pad(next)}: Nơi áp lực nước có thể nghiền nát một chiếc tàu ngầm trong chớp mắt!\n`;
        } else {
            sample += `${pad(cur)}-${pad(next)}: Cùng khám phá sự thật chấn động này ngay bây giờ!\n`;
        }
        cur = next;
    }
    input.value = sample.trim();
    updateManualScriptStats();
    showToast('📋 Đã chèn mẫu mốc thời gian khớp theo số phân cảnh!', 'success');
    input.focus();
}

function autoFormatManualScriptByScenes() {
    const input = document.getElementById('manual-script-input');
    if (!input) return;
    const numScenes = manualScenesList.length || 2;
    const text = input.value.trim();
    const parsed = parseMultiSceneScript(text, numScenes, 'auto');

    let cur = 0;
    const formatted = parsed.map((item, idx) => {
        const next = cur + 10;
        const pad = (n) => String(n).padStart(2, '0');
        const content = item.narration || `[Nội dung lời thoại cho phân cảnh #${idx + 1}]`;
        const line = `${pad(cur)}-${pad(next)}: ${content}`;
        cur = next;
        return line;
    }).join('\n\n');

    input.value = formatted;
    setManualScriptTimingMode('timestamps');
    updateManualScriptStats();
    showToast(`🪄 Đã tự động chia kịch bản thành ${numScenes} mốc tương ứng với ${numScenes} phân cảnh!`, 'success');
    input.focus();
}

function triggerBatchScenesUpload() {
    const input = document.getElementById('batch-scenes-file-input');
    if (input) input.click();
}

function handleBatchScenesSelect(input) {
    if (!input.files || input.files.length === 0) return;
    const files = Array.from(input.files);
    
    files.forEach((file, idx) => {
        const isVideo = file.type.startsWith('video');
        const previewUrl = URL.createObjectURL(file);
        const sizeStr = (file.size / (1024 * 1024)).toFixed(1) + ' MB';
        
        // Nếu cảnh đầu tiên đang để trống chưa có file thì điền vào
        const emptyScene = manualScenesList.find(s => !s.file);
        if (emptyScene && idx === 0) {
            emptyScene.file = file;
            emptyScene.isVideo = isVideo;
            emptyScene.fileName = file.name;
            emptyScene.fileSize = sizeStr;
            emptyScene.previewUrl = previewUrl;
        } else {
            addManualScene({
                file: file,
                fileName: file.name,
                fileSize: sizeStr,
                isVideo: isVideo,
                previewUrl: previewUrl,
                cameraMotion: 'zoom_in',
                sfxCue: 'auto'
            });
        }
    });

    renderManualScenes();
    updateManualScriptStats();
    showToast(`📁 Đã nạp ${files.length} tệp phân cảnh theo đúng thứ tự!`, 'success');
    input.value = '';
}

function addManualScene(data = null) {
    const sceneId = 'sc_' + Date.now() + '_' + Math.random().toString(36).substr(2, 4);
    manualScenesList.push({
        id: sceneId,
        file: data ? data.file : null,
        fileName: data ? (data.fileName || '') : '',
        fileSize: data ? (data.fileSize || '') : '',
        previewUrl: data ? (data.previewUrl || '') : '',
        isVideo: data ? (data.isVideo || false) : false,
        cameraMotion: data ? (data.cameraMotion || 'zoom_in') : 'zoom_in',
        sfxCue: data ? (data.sfxCue || 'auto') : 'auto'
    });
    renderManualScenes();
    updateManualScriptStats();
}

function removeManualScene(sceneId) {
    if (manualScenesList.length <= 1) {
        showToast('Cần giữ lại ít nhất 1 phân cảnh!', 'warning');
        return;
    }
    manualScenesList = manualScenesList.filter(s => s.id !== sceneId);
    renderManualScenes();
    updateManualScriptStats();
}

function moveManualScene(sceneId, direction) {
    const index = manualScenesList.findIndex(s => s.id === sceneId);
    if (index === -1) return;
    const targetIndex = index + direction;
    if (targetIndex < 0 || targetIndex >= manualScenesList.length) return;

    const temp = manualScenesList[index];
    manualScenesList[index] = manualScenesList[targetIndex];
    manualScenesList[targetIndex] = temp;

    renderManualScenes();
    updateManualScriptStats();
    showToast(`Đã đổi sang Cảnh #${targetIndex + 1}`, 'info');
}

function updateSceneMotion(sceneId, val) {
    const sc = manualScenesList.find(s => s.id === sceneId);
    if (sc) sc.cameraMotion = val;
}

function updateSceneSfx(sceneId, val) {
    const sc = manualScenesList.find(s => s.id === sceneId);
    if (sc) sc.sfxCue = val;
}

function handleSceneFileSelect(sceneId, input) {
    if (!input.files || !input.files[0]) return;
    const file = input.files[0];
    const sc = manualScenesList.find(s => s.id === sceneId);
    if (sc) {
        sc.file = file;
        sc.fileName = file.name;
        sc.fileSize = (file.size / (1024 * 1024)).toFixed(1) + ' MB';
        sc.isVideo = file.type.startsWith('video');
        sc.previewUrl = URL.createObjectURL(file);
        renderManualScenes();
        updateManualScriptStats();
        showToast(`Đã nạp Cảnh: ${file.name}`, 'success');
    }
}

function renderManualScenes() {
    const container = document.getElementById('manual-scenes-container');
    const countEl = document.getElementById('manual-scene-count');
    if (countEl) countEl.textContent = manualScenesList.length;
    if (!container) return;

    container.innerHTML = manualScenesList.map((sc, index) => {
        const isFirst = index === 0;
        const isLast = index === manualScenesList.length - 1;
        const sfxHtml = manualSfxOptions.map(opt => {
            const isSelected = (sc.sfxCue === opt.id) ? 'selected' : '';
            return `<option value="${escapeHtml(opt.id)}" ${isSelected}>${escapeHtml(opt.name)}</option>`;
        }).join('');

        let previewContent = '';
        if (sc.previewUrl) {
            if (sc.isVideo) {
                previewContent = `<video src="${sc.previewUrl}" class="manual-compact-preview-media" muted autoplay loop playsinline></video>`;
            } else {
                previewContent = `<img src="${sc.previewUrl}" class="manual-compact-preview-media" alt="Preview">`;
            }
            previewContent += `<div style="position:absolute; bottom:4px; right:4px; background:rgba(0,0,0,0.8); font-size:9.5px; color:#fff; padding:1px 6px; border-radius:3px; pointer-events:none;">Đổi ↗</div>`;
        } else {
            previewContent = `
                <div style="font-size:24px; line-height:1; margin-bottom:4px;">📁</div>
                <div style="font-size:11px; font-weight:600; color:var(--text-main);">Chọn Tệp</div>
                <div style="font-size:9.5px; color:var(--text-muted);">Video hoặc Ảnh</div>
            `;
        }

        const fileMetaHtml = sc.fileName 
            ? `<span style="font-size:11px; color:#93c5fd; max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${escapeHtml(sc.fileName)}">📎 ${escapeHtml(sc.fileName)} (${sc.fileSize})</span>`
            : `<span style="font-size:11px; color:var(--text-muted); font-style:italic;">Chưa chọn tệp</span>`;

        return `
            <div class="manual-scene-compact-card" id="card-${sc.id}">
                <div class="manual-scene-compact-header">
                    <div style="display:flex; align-items:center; gap:8px;">
                        <span class="manual-scene-badge" style="font-size:11px; padding:2px 8px;">🎬 CẢNH #${index + 1}</span>
                        ${isFirst ? '<span class="badge badge-purple" style="font-size:9.5px; padding:1px 6px;">HOOK</span>' : ''}
                        ${fileMetaHtml}
                    </div>
                    <div style="display:flex; align-items:center; gap:4px;">
                        <button type="button" class="scene-btn-reorder" onclick="moveManualScene('${sc.id}', -1)" ${isFirst ? 'disabled' : ''} title="Đưa cảnh lên trước">⬆️</button>
                        <button type="button" class="scene-btn-reorder" onclick="moveManualScene('${sc.id}', 1)" ${isLast ? 'disabled' : ''} title="Đưa cảnh xuống sau">⬇️</button>
                        <button type="button" class="btn-remove-scene" onclick="removeManualScene('${sc.id}')" title="Xóa cảnh này" style="padding:3px 6px; font-size:11px; margin-left:4px;">
                            <svg width="11" height="11" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path></svg>
                        </button>
                    </div>
                </div>

                <div class="manual-scene-compact-body">
                    <!-- Khung tải media nhỏ gọn -->
                    <div class="manual-compact-dropzone" onclick="document.getElementById('input-file-${sc.id}').click()" title="Bấm để chọn ảnh hoặc video">
                        ${previewContent}
                        <input type="file" id="input-file-${sc.id}" style="display:none;" accept="image/*,video/*" onchange="handleSceneFileSelect('${sc.id}', this)">
                    </div>

                    <!-- Điều khiển Chuyển động & SFX -->
                    <div class="manual-scene-controls-compact">
                        <div>
                            <label class="form-label" style="font-size:11px; margin-bottom:3px;">
                                🎥 Chuyển động (Nếu là ảnh)
                            </label>
                            <select class="form-input" style="padding:4px 8px; font-size:12px;" onchange="updateSceneMotion('${sc.id}', this.value)">
                                <option value="zoom_in" ${sc.cameraMotion === 'zoom_in' ? 'selected' : ''}>🔍 Zoom In (Vào tâm)</option>
                                <option value="zoom_out" ${sc.cameraMotion === 'zoom_out' ? 'selected' : ''}>🔎 Zoom Out (Thu nhỏ)</option>
                                <option value="pan_left" ${sc.cameraMotion === 'pan_left' ? 'selected' : ''}>⬅️ Pan Trái</option>
                                <option value="pan_right" ${sc.cameraMotion === 'pan_right' ? 'selected' : ''}>➡️ Pan Phải</option>
                                <option value="pan_up" ${sc.cameraMotion === 'pan_up' ? 'selected' : ''}>⬆️ Pan Lên</option>
                                <option value="pan_down" ${sc.cameraMotion === 'pan_down' ? 'selected' : ''}>⬇️ Pan Xuống</option>
                            </select>
                        </div>

                        <div>
                            <label class="form-label" style="font-size:11px; margin-bottom:3px;">
                                🎵 SFX Chuyển Cảnh
                            </label>
                            <select class="form-input" style="padding:4px 8px; font-size:12px;" onchange="updateSceneSfx('${sc.id}', this.value)">
                                ${sfxHtml}
                            </select>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

function onManualVoiceChange(voiceId) {
    previewManualSelectedVoice(true);
}

async function previewManualSelectedVoice(isAutoTrigger = false) {
    if (currentVoiceAudio && !currentVoiceAudio.paused) {
        currentVoiceAudio.pause();
        currentVoiceAudio = null;
        resetPreviewVoiceButton();
        if (!isAutoTrigger) {
            showToast('⏹️ Đã dừng phát giọng thử', 'info');
            return;
        }
    }

    if (isVoicePreviewLoading) return;

    const voiceSelect = document.getElementById('manual-voice');
    const rateSelect = document.getElementById('manual-voice-rate');
    const scriptInput = document.getElementById('manual-script-input');
    const btn = document.getElementById('btn-preview-voice-manual');
    const icon = document.getElementById('preview-voice-icon-manual');
    const text = document.getElementById('preview-voice-text-manual');

    const voice = voiceSelect ? voiceSelect.value : 'vi-VN-HoaiMyNeural';
    const rate = rateSelect ? rateSelect.value : '+10%';
    const scriptText = scriptInput ? scriptInput.value.trim() : '';

    let sampleText = scriptText;
    if (!sampleText) {
        sampleText = 'Chào bạn, đây là bản xem trước giọng đọc AI tự động cho các phân cảnh của bạn.';
    } else {
        const sentences = sampleText.split(/[.!?\n]/).map(s => s.trim()).filter(Boolean);
        sampleText = sentences.slice(0, 2).join('. ') + '.';
    }

    isVoicePreviewLoading = true;
    if (icon) icon.textContent = '⏳';
    if (text) text.textContent = 'Đang tải âm...';
    if (btn) btn.disabled = true;

    try {
        const resp = await fetch('/api/manual-studio/preview-voice', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ voice, rate, text: sampleText })
        });

        const data = await resp.json();
        if (!resp.ok || !data.success) {
            throw new Error(data.detail || 'Không thể tạo âm thanh thử');
        }

        const audio = new Audio(data.audio_url);
        currentVoiceAudio = audio;

        if (icon) icon.textContent = '🔊';
        if (text) text.textContent = 'Đang phát thử...';
        if (btn) {
            btn.disabled = false;
            btn.style.borderColor = '#10b981';
            btn.style.color = '#34d399';
            btn.style.background = 'rgba(16, 185, 129, 0.12)';
        }

        audio.onended = () => {
            currentVoiceAudio = null;
            if (icon) icon.textContent = '🔊';
            if (text) text.textContent = 'Nghe Thử Giọng';
            if (btn) {
                btn.style.borderColor = 'rgba(236,72,153,0.35)';
                btn.style.color = '#f472b6';
                btn.style.background = 'rgba(236,72,153,0.08)';
            }
        };

        audio.onerror = (e) => {
            console.error('Lỗi playback audio:', e);
            currentVoiceAudio = null;
            if (icon) icon.textContent = '🔊';
            if (text) text.textContent = 'Nghe Thử Giọng';
            if (btn) {
                btn.style.borderColor = 'rgba(236,72,153,0.35)';
                btn.style.color = '#f472b6';
                btn.style.background = 'rgba(236,72,153,0.08)';
            }
        };

        await audio.play();
        showToast('🔊 Đang phát giọng đọc thử...', 'info');

    } catch (err) {
        console.error('Lỗi nghe thử giọng đọc:', err);
        showToast(`❌ Không thể phát thử: ${err.message}`, 'error');
        if (icon) icon.textContent = '🔊';
        if (text) text.textContent = 'Nghe Thử Giọng';
        if (btn) btn.disabled = false;
    } finally {
        isVoicePreviewLoading = false;
    }
}

async function submitManualStudio() {
    const titleInput = document.getElementById('manual-title');
    const channelSelect = document.getElementById('manual-channel');
    const voiceSelect = document.getElementById('manual-voice');
    const rateSelect = document.getElementById('manual-voice-rate');
    const bgmSelect = document.getElementById('manual-bgm');
    const transitionSelect = document.getElementById('manual-transition');
    const subCheckbox = document.getElementById('manual-subtitles-enabled');
    const duckCheckbox = document.getElementById('manual-ducking-enabled');
    const sfxCheckbox = document.getElementById('manual-sfx-enabled');
    const submitBtn = document.getElementById('btn-submit-manual-studio');
    const scriptInput = document.getElementById('manual-script-input');

    const title = (titleInput ? titleInput.value.trim() : '') || 'Shorts Tùy Chỉnh';
    const scriptText = scriptInput ? scriptInput.value.trim() : '';

    if (!scriptText) {
        showToast('⚠️ Vui lòng nhập lời thoại kịch bản cho video!', 'warning');
        if (scriptInput) scriptInput.focus();
        return;
    }

    if (manualScenesList.length === 0) {
        showToast('⚠️ Vui lòng thêm ít nhất 1 phân cảnh!', 'warning');
        return;
    }

    const emptyFiles = manualScenesList.filter(s => !s.file);
    if (emptyFiles.length > 0) {
        showToast(`⚠️ Còn ${emptyFiles.length} phân cảnh chưa chọn tệp media. Vui lòng bấm vào khung tải để chọn video/ảnh!`, 'warning');
        return;
    }

    const originalBtnHTML = submitBtn ? submitBtn.innerHTML : '🚀 Bắt Đầu Ghép & Tạo Video';
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerHTML = `
            <svg class="spinner" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"><circle cx="12" cy="12" r="10" stroke-width="3" stroke-dasharray="32" stroke-linecap="round"></circle></svg>
            Đang tải media & bắt đầu render...
        `;
    }

    try {
        const formData = new FormData();
        const parsedSegments = parseMultiSceneScript(scriptText, manualScenesList.length, manualTimingMode);

        const scenesSpec = manualScenesList.map((sc, idx) => {
            const seg = parsedSegments[idx] || { narration: '', duration: null };
            return {
                scene_id: idx + 1,
                file_index: idx,
                narration: seg.narration.trim(),
                camera_motion: sc.cameraMotion,
                transition: transitionSelect ? transitionSelect.value : 'fade',
                sfx_cue: sc.sfxCue,
                manual_duration_seconds: seg.duration
            };
        });

        const payloadObj = {
            title: title,
            target_channel_id: channelSelect ? channelSelect.value : 'channel_1',
            voice: voiceSelect ? voiceSelect.value : 'vi-VN-HoaiMyNeural',
            voice_rate: rateSelect ? rateSelect.value : '+10%',
            bgm_path: bgmSelect ? bgmSelect.value : null,
            use_ducking: duckCheckbox ? duckCheckbox.checked : true,
            subtitle_enabled: subCheckbox ? subCheckbox.checked : true,
            use_sfx: sfxCheckbox ? sfxCheckbox.checked : true,
            transition_type: transitionSelect ? transitionSelect.value : 'fade',
            timing_mode: manualTimingMode,
            mode: 'multi_scene',
            script: scriptText,
            scenes: scenesSpec
        };

        formData.append('payload', JSON.stringify(payloadObj));

        // Append files in order
        manualScenesList.forEach(sc => {
            formData.append('files', sc.file);
        });

        const resp = await fetch('/api/manual-studio/create', {
            method: 'POST',
            body: formData
        });

        const resData = await resp.json();

        if (resp.ok && resData.success) {
            showToast(`🚀 ${resData.message}`, 'success');
            setTimeout(() => {
                switchTab('pipeline');
                fetchVideos();
            }, 1200);
        } else {
            showToast(`❌ Lỗi: ${resData.detail || 'Không thể tạo video'}`, 'error');
        }
    } catch (err) {
        console.error('Lỗi gửi submit manual studio:', err);
        showToast(`❌ Lỗi kết nối: ${err.message}`, 'error');
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalBtnHTML;
        }
    }
}

async function submitSingleVideoStudio() {
    if (!singleVideoFile) {
        showToast('⚠️ Vui lòng tải lên 1 video hoàn chỉnh trước khi render!', 'warning');
        return;
    }

    const scriptInput = document.getElementById('single-script-input');
    const scriptText = scriptInput ? scriptInput.value.trim() : '';
    if (!scriptText) {
        showToast('⚠️ Vui lòng nhập lời thoại kịch bản cho video!', 'warning');
        if (scriptInput) scriptInput.focus();
        return;
    }

    const titleInput = document.getElementById('single-title');
    const channelSelect = document.getElementById('single-channel');
    const voiceSelect = document.getElementById('single-voice');
    const rateSelect = document.getElementById('single-voice-rate');
    const bgmSelect = document.getElementById('single-bgm');
    const subCheckbox = document.getElementById('single-subtitles-enabled');
    const duckCheckbox = document.getElementById('single-ducking-enabled');
    const sfxCheckbox = document.getElementById('single-sfx-enabled');
    const submitBtn = document.getElementById('btn-submit-single-studio');

    const title = (titleInput ? titleInput.value.trim() : '') || 'Shorts Lồng Tiếng';
    const originalBtnHTML = submitBtn ? submitBtn.innerHTML : '🚀 Lồng Giọng Đọc, SFX & Gắn Phụ Đề';

    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerHTML = `
            <svg class="spinner" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"><circle cx="12" cy="12" r="10" stroke-width="3" stroke-dasharray="32" stroke-linecap="round"></circle></svg>
            Đang tải video & bắt đầu render...
        `;
    }

    try {
        const formData = new FormData();
        const payloadObj = {
            mode: 'single_video',
            timing_mode: singleTimingMode,
            title: title,
            target_channel_id: channelSelect ? channelSelect.value : 'channel_1',
            voice: voiceSelect ? voiceSelect.value : 'vi-VN-HoaiMyNeural',
            voice_rate: rateSelect ? rateSelect.value : '+10%',
            bgm_path: bgmSelect ? bgmSelect.value : null,
            use_ducking: duckCheckbox ? duckCheckbox.checked : true,
            subtitle_enabled: subCheckbox ? subCheckbox.checked : true,
            use_sfx: sfxCheckbox ? sfxCheckbox.checked : true,
            script: scriptText
        };

        formData.append('payload', JSON.stringify(payloadObj));
        formData.append('files', singleVideoFile);

        const resp = await fetch('/api/manual-studio/create', {
            method: 'POST',
            body: formData
        });

        const resData = await resp.json();

        if (resp.ok && resData.success) {
            showToast(`🎉 ${resData.message}`, 'success');
            // Chuyển sang pipeline để theo dõi tiến độ
            setTimeout(() => {
                switchTab('pipeline');
                fetchVideos();
            }, 1200);
        } else {
            showToast(`❌ Lỗi: ${resData.detail || 'Không thể tạo video'}`, 'error');
        }
    } catch (err) {
        console.error('Lỗi gửi single video studio:', err);
        showToast(`❌ Lỗi kết nối: ${err.message}`, 'error');
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalBtnHTML;
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════════════
// WI-FI BROADCAST & MOBILE ACCESS
// ═══════════════════════════════════════════════════════════════════════════════
let currentWifiUrl = window.location.href;

async function openWifiModal() {
    const modal = document.getElementById('modal-wifi-share');
    if (!modal) return;
    modal.style.display = 'flex';

    try {
        const resp = await fetch('/api/network-info');
        if (resp.ok) {
            const data = await resp.json();
            currentWifiUrl = data.wifi_url || window.location.href;
            const urlText = document.getElementById('wifi-share-url-text');
            if (urlText) urlText.innerText = currentWifiUrl;

            const qrImg = document.getElementById('wifi-qr-img');
            if (qrImg) {
                const qrEndpoint = `https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(currentWifiUrl)}`;
                qrImg.src = qrEndpoint;
            }
        }
    } catch (e) {
        console.error('Lỗi lấy network info:', e);
        const urlText = document.getElementById('wifi-share-url-text');
        if (urlText) urlText.innerText = window.location.href;
    }
}

function closeWifiModal() {
    const modal = document.getElementById('modal-wifi-share');
    if (modal) modal.style.display = 'none';
}

function copyWifiUrl() {
    if (navigator.clipboard && currentWifiUrl) {
        navigator.clipboard.writeText(currentWifiUrl).then(() => {
            showToast('✅ Đã sao chép địa chỉ Wi-Fi vào bộ nhớ tạm!', 'success');
        }).catch(() => {
            prompt('Sao chép địa chỉ dưới đây:', currentWifiUrl);
        });
    } else {
        prompt('Sao chép địa chỉ dưới đây:', currentWifiUrl);
    }
}

/* ═════════════════════════════════════════════════════════════════════════════
   INSTANT PREVIEW PLAYER (REMOTION-STYLE SHORTS SIMULATOR)
   ═════════════════════════════════════════════════════════════════════════════ */
let lastCreatedScriptResult = null;

let instantPlayerState = {
    isOpen: false,
    source: '', // 'create', 'single_studio', 'multi_studio', 'job'
    jobId: null,
    title: '',
    hook: '',
    channelName: '@channel',
    voice: 'vi-VN-HoaiMyNeural',
    voiceRate: '+10%',
    scenes: [],
    currentSceneIndex: 0,
    isPlaying: false,
    isMuted: false,
    sceneElapsed: 0,
    totalDuration: 0,
    intervalId: null,
    currentAudio: null,
    audioBlobCache: {},
    isAudioLoading: false
};

function formatPreviewTime(sec) {
    const s = Math.max(0, Math.floor(sec || 0));
    const m = Math.floor(s / 60);
    const remS = s % 60;
    return `${m.toString().padStart(2, '0')}:${remS.toString().padStart(2, '0')}`;
}

function openInstantPreviewModal(cfg) {
    instantPlayerState.isOpen = true;
    instantPlayerState.source = cfg.source || 'create';
    instantPlayerState.jobId = cfg.jobId || null;
    instantPlayerState.title = cfg.title || 'Video Shorts';
    instantPlayerState.hook = cfg.hook || '';
    instantPlayerState.channelName = cfg.channelName || '@your_channel';
    instantPlayerState.voice = cfg.voice || 'vi-VN-HoaiMyNeural';
    instantPlayerState.voiceRate = cfg.voiceRate || '+10%';
    instantPlayerState.scenes = cfg.scenes || [];
    instantPlayerState.currentSceneIndex = 0;
    instantPlayerState.isPlaying = true;
    instantPlayerState.isMuted = false;
    instantPlayerState.sceneElapsed = 0;

    // Calculate total duration
    instantPlayerState.totalDuration = instantPlayerState.scenes.reduce((acc, sc) => acc + (parseFloat(sc.duration) || 4), 0);
    if (instantPlayerState.totalDuration <= 0) instantPlayerState.totalDuration = 30;

    // Update Header & Meta
    const titleEl = document.getElementById('preview-director-title');
    if (titleEl) titleEl.textContent = instantPlayerState.title;
    const hookEl = document.getElementById('preview-director-hook');
    if (hookEl) hookEl.textContent = instantPlayerState.hook ? `💬 Hook: "${instantPlayerState.hook}"` : '💬 Shorts Viral Hook';
    const chEl = document.getElementById('preview-stage-channel-name');
    if (chEl) chEl.textContent = instantPlayerState.channelName;
    const voiceEl = document.getElementById('preview-current-voice');
    if (voiceEl) voiceEl.textContent = `${instantPlayerState.voice} (${instantPlayerState.voiceRate})`;
    const badgeEl = document.getElementById('preview-total-scenes-badge');
    if (badgeEl) badgeEl.textContent = `${instantPlayerState.scenes.length} phân cảnh · ~${Math.round(instantPlayerState.totalDuration)}s`;

    // Render Scene Timeline in Right Column
    renderPreviewTimelineList();

    // Show Modal
    const modal = document.getElementById('modal-instant-preview');
    if (modal) modal.classList.add('visible');

    // Update Play Button Icon
    updatePlayPauseButtonUI(true);

    // Load Scene 0 and Play
    loadInstantPreviewScene(0, true);
}

function closeInstantPreview() {
    instantPlayerState.isOpen = false;
    instantPlayerState.isPlaying = false;
    if (instantPlayerState.intervalId) {
        clearInterval(instantPlayerState.intervalId);
        instantPlayerState.intervalId = null;
    }
    if (instantPlayerState.currentAudio) {
        instantPlayerState.currentAudio.pause();
        instantPlayerState.currentAudio = null;
    }
    const videoEl = document.getElementById('preview-video-element');
    if (videoEl) {
        videoEl.pause();
        videoEl.src = '';
    }
    const modal = document.getElementById('modal-instant-preview');
    if (modal) modal.classList.remove('visible');
}

function renderPreviewTimelineList() {
    const list = document.getElementById('preview-scenes-list');
    if (!list) return;
    list.innerHTML = '';

    instantPlayerState.scenes.forEach((sc, idx) => {
        const item = document.createElement('div');
        item.className = `preview-scene-item ${idx === instantPlayerState.currentSceneIndex ? 'active' : ''}`;
        item.id = `preview-scene-item-${idx}`;
        item.onclick = () => jumpToPreviewScene(idx);

        const typeIcon = sc.mediaType === 'video' ? '🎥' : (sc.mediaType === 'image' ? '📸' : '🔮');
        const dur = Math.round(sc.duration || 4);

        item.innerHTML = `
            <div style="font-weight:700; font-size:12px; color:#ec4899; min-width:54px;">Cảnh ${sc.sceneId || (idx + 1)}</div>
            <div style="flex-grow:1; min-width:0;">
                <div style="font-size:12px; color:#f8fafc; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
                    ${typeIcon} ${escapeHtml(sc.narration || sc.prompt || `Phân cảnh #${idx + 1}`)}
                </div>
                <div style="font-size:11px; color:var(--text-muted); margin-top:2px;">
                    ${sc.prompt ? escapeHtml(sc.prompt.slice(0, 36) + '...') : `${dur} giây`}
                </div>
            </div>
            <div style="font-size:11px; font-weight:700; color:var(--text-secondary);">${dur}s</div>
        `;
        list.appendChild(item);
    });
}

function loadInstantPreviewScene(idx, autoPlay = true) {
    if (idx < 0 || idx >= instantPlayerState.scenes.length) return;

    instantPlayerState.currentSceneIndex = idx;
    instantPlayerState.sceneElapsed = 0;

    const scene = instantPlayerState.scenes[idx];
    const totalScenes = instantPlayerState.scenes.length;

    // Update Scene Indicator Pill
    const pill = document.getElementById('preview-scene-pill');
    if (pill) pill.textContent = `Cảnh ${idx + 1}/${totalScenes}`;

    // Highlight timeline item
    document.querySelectorAll('.preview-scene-item').forEach(el => el.classList.remove('active'));
    const curItem = document.getElementById(`preview-scene-item-${idx}`);
    if (curItem) {
        curItem.classList.add('active');
        curItem.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    // Media Switcher: Video vs Image vs AI Canvas
    const videoEl = document.getElementById('preview-video-element');
    const imageEl = document.getElementById('preview-image-element');
    const canvasEl = document.getElementById('preview-canvas-backdrop');
    const watermark = document.getElementById('preview-watermark-scene');
    const promptHint = document.getElementById('preview-prompt-hint');

    if (scene.mediaType === 'video' && scene.mediaUrl) {
        if (imageEl) imageEl.style.display = 'none';
        if (canvasEl) canvasEl.style.display = 'none';
        if (videoEl) {
            videoEl.style.display = 'block';
            videoEl.src = scene.mediaUrl;
            videoEl.currentTime = 0;
            videoEl.muted = instantPlayerState.isMuted;
            if (instantPlayerState.isPlaying) videoEl.play().catch(() => {});
        }
    } else if (scene.mediaType === 'image' && scene.mediaUrl) {
        if (videoEl) { videoEl.pause(); videoEl.style.display = 'none'; }
        if (canvasEl) canvasEl.style.display = 'none';
        if (imageEl) {
            imageEl.style.display = 'block';
            imageEl.style.backgroundImage = `url("${scene.mediaUrl}")`;
        }
    } else {
        if (videoEl) { videoEl.pause(); videoEl.style.display = 'none'; }
        if (imageEl) imageEl.style.display = 'none';
        if (canvasEl) {
            canvasEl.style.display = 'flex';
            if (watermark) watermark.textContent = `PHÂN CẢNH ${scene.sceneId || (idx + 1)}`;
            if (promptHint) promptHint.textContent = scene.prompt || scene.narration || 'Mô phỏng hình ảnh phân cảnh AI';
        }
    }

    // Kinetic Subtitles Word Setup
    const subBox = document.getElementById('preview-subtitles-box');
    const words = (scene.narration || '').trim().split(/\s+/).filter(Boolean);
    if (subBox) {
        if (words.length === 0) {
            subBox.innerHTML = '<span style="color:rgba(255,255,255,0.4);font-size:13px;">[Âm nhạc chuyển cảnh]</span>';
        } else {
            subBox.innerHTML = words.map((w, wIdx) => `<span class="preview-k-word future" id="pk-word-${wIdx}">${escapeHtml(w)}</span>`).join(' ');
        }
    }

    // Play Voice Audio
    playSceneVoiceover(scene);

    // Start Tick Timer
    if (instantPlayerState.intervalId) clearInterval(instantPlayerState.intervalId);
    instantPlayerState.intervalId = setInterval(updateInstantPlayerTick, 60);
}

async function playSceneVoiceover(scene) {
    if (instantPlayerState.currentAudio) {
        instantPlayerState.currentAudio.pause();
        instantPlayerState.currentAudio = null;
    }

    if (!scene.narration || scene.narration.trim().length === 0) return;

    // 1. If pre-rendered audio exists
    if (scene.audioUrl) {
        instantPlayerState.currentAudio = new Audio(scene.audioUrl);
        instantPlayerState.currentAudio.muted = instantPlayerState.isMuted;
        instantPlayerState.currentAudio.onended = () => onAudioEnded();
        if (instantPlayerState.isPlaying) instantPlayerState.currentAudio.play().catch(() => {});
        return;
    }

    // 2. If cached in audioBlobCache
    const cacheKey = `${instantPlayerState.voice}_${instantPlayerState.voiceRate}_${scene.narration}`;
    if (instantPlayerState.audioBlobCache[cacheKey]) {
        instantPlayerState.currentAudio = new Audio(instantPlayerState.audioBlobCache[cacheKey]);
        instantPlayerState.currentAudio.muted = instantPlayerState.isMuted;
        instantPlayerState.currentAudio.onended = () => onAudioEnded();
        if (instantPlayerState.isPlaying) instantPlayerState.currentAudio.play().catch(() => {});
        return;
    }

    // 3. Fetch from /api/manual-studio/preview-voice
    try {
        const resp = await fetch('/api/manual-studio/preview-voice', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                voice: instantPlayerState.voice,
                rate: instantPlayerState.voiceRate,
                text: scene.narration
            })
        });
        if (resp.ok) {
            const blob = await resp.blob();
            const blobUrl = URL.createObjectURL(blob);
            instantPlayerState.audioBlobCache[cacheKey] = blobUrl;

            // Only play if player is still on this scene
            if (instantPlayerState.isOpen && instantPlayerState.scenes[instantPlayerState.currentSceneIndex] === scene) {
                instantPlayerState.currentAudio = new Audio(blobUrl);
                instantPlayerState.currentAudio.muted = instantPlayerState.isMuted;
                instantPlayerState.currentAudio.onended = () => onAudioEnded();
                if (instantPlayerState.isPlaying) instantPlayerState.currentAudio.play().catch(() => {});
            }
        }
    } catch (e) {
        console.warn('Preview voice fetch error:', e);
    }
}

function onAudioEnded() {
    if (!instantPlayerState.isPlaying) return;
    nextPreviewScene();
}

function updateInstantPlayerTick() {
    if (!instantPlayerState.isOpen || !instantPlayerState.isPlaying) return;

    const curScene = instantPlayerState.scenes[instantPlayerState.currentSceneIndex];
    if (!curScene) return;

    const sceneDuration = parseFloat(curScene.duration) || 4;

    // Time advancement
    if (instantPlayerState.currentAudio && !instantPlayerState.currentAudio.paused) {
        instantPlayerState.sceneElapsed = instantPlayerState.currentAudio.currentTime;
    } else {
        const videoEl = document.getElementById('preview-video-element');
        if (curScene.mediaType === 'video' && videoEl && !videoEl.paused) {
            instantPlayerState.sceneElapsed = videoEl.currentTime;
        } else {
            instantPlayerState.sceneElapsed += 0.06;
        }
    }

    // Calculate global time
    let previousScenesTime = 0;
    for (let i = 0; i < instantPlayerState.currentSceneIndex; i++) {
        previousScenesTime += (parseFloat(instantPlayerState.scenes[i].duration) || 4);
    }
    const currentGlobalTime = previousScenesTime + Math.min(sceneDuration, instantPlayerState.sceneElapsed);

    // Update Time Badge
    const timeBadge = document.getElementById('preview-time-badge');
    if (timeBadge) {
        timeBadge.textContent = `${formatPreviewTime(currentGlobalTime)} / ${formatPreviewTime(instantPlayerState.totalDuration)}`;
    }

    // Update Scrubber Fill
    const fill = document.getElementById('preview-scrubber-fill');
    if (fill) {
        const pct = Math.min(100, Math.max(0, (currentGlobalTime / instantPlayerState.totalDuration) * 100));
        fill.style.width = `${pct}%`;
    }

    // Update Kinetic Word-by-Word Highlight
    const words = (curScene.narration || '').trim().split(/\s+/).filter(Boolean);
    if (words.length > 0) {
        const ratio = Math.min(1, Math.max(0, instantPlayerState.sceneElapsed / sceneDuration));
        const activeWordIdx = Math.min(words.length - 1, Math.floor(ratio * words.length));

        for (let w = 0; w < words.length; w++) {
            const wordEl = document.getElementById(`pk-word-${w}`);
            if (wordEl) {
                if (w < activeWordIdx) {
                    wordEl.className = 'preview-k-word passed';
                } else if (w === activeWordIdx) {
                    wordEl.className = 'preview-k-word active';
                } else {
                    wordEl.className = 'preview-k-word future';
                }
            }
        }
    }

    // Scene transition check
    if (instantPlayerState.sceneElapsed >= sceneDuration) {
        nextPreviewScene();
    }
}

function toggleInstantPlayPause() {
    if (instantPlayerState.isPlaying) {
        pauseInstantPreview();
    } else {
        playInstantPreview();
    }
}

function playInstantPreview() {
    instantPlayerState.isPlaying = true;
    updatePlayPauseButtonUI(true);

    if (instantPlayerState.currentAudio) {
        instantPlayerState.currentAudio.play().catch(() => {});
    }
    const videoEl = document.getElementById('preview-video-element');
    const curScene = instantPlayerState.scenes[instantPlayerState.currentSceneIndex];
    if (curScene && curScene.mediaType === 'video' && videoEl) {
        videoEl.play().catch(() => {});
    }
}

function pauseInstantPreview() {
    instantPlayerState.isPlaying = false;
    updatePlayPauseButtonUI(false);

    if (instantPlayerState.currentAudio) {
        instantPlayerState.currentAudio.pause();
    }
    const videoEl = document.getElementById('preview-video-element');
    if (videoEl) {
        videoEl.pause();
    }
}

function updatePlayPauseButtonUI(isPlaying) {
    const bottomIcon = document.getElementById('preview-btn-play-icon');
    if (bottomIcon) bottomIcon.textContent = isPlaying ? '⏸' : '▶';

    const centerOverlay = document.getElementById('preview-center-play-overlay');
    const centerIcon = document.getElementById('preview-center-play-icon');
    if (centerOverlay && centerIcon) {
        centerIcon.textContent = isPlaying ? '⏸' : '▶';
        centerOverlay.style.opacity = isPlaying ? '0' : '1';
        centerOverlay.style.pointerEvents = isPlaying ? 'none' : 'auto';
    }

    const eqIndicator = document.getElementById('preview-audio-indicator');
    if (eqIndicator) {
        eqIndicator.style.opacity = isPlaying ? '1' : '0.3';
    }
}

function nextPreviewScene() {
    if (instantPlayerState.currentSceneIndex < instantPlayerState.scenes.length - 1) {
        loadInstantPreviewScene(instantPlayerState.currentSceneIndex + 1, true);
    } else {
        // Finished all scenes
        pauseInstantPreview();
        loadInstantPreviewScene(0, false);
        showToast('🎬 Đã xem hết video Shorts! Bấm Play để xem lại hoặc Render để xuất video.', 'info');
    }
}

function prevPreviewScene() {
    if (instantPlayerState.currentSceneIndex > 0) {
        loadInstantPreviewScene(instantPlayerState.currentSceneIndex - 1, true);
    } else {
        loadInstantPreviewScene(0, true);
    }
}

function jumpToPreviewScene(index) {
    loadInstantPreviewScene(index, true);
}

function onScrubberClick(e) {
    const track = document.getElementById('preview-scrubber-track');
    if (!track) return;
    const rect = track.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const pct = Math.max(0, Math.min(1, clickX / rect.width));
    const targetGlobalTime = pct * instantPlayerState.totalDuration;

    // Find which scene contains this target global time
    let accumulatedTime = 0;
    for (let i = 0; i < instantPlayerState.scenes.length; i++) {
        const scDur = parseFloat(instantPlayerState.scenes[i].duration) || 4;
        if (targetGlobalTime <= accumulatedTime + scDur || i === instantPlayerState.scenes.length - 1) {
            loadInstantPreviewScene(i, true);
            instantPlayerState.sceneElapsed = Math.max(0, targetGlobalTime - accumulatedTime);
            if (instantPlayerState.currentAudio) {
                instantPlayerState.currentAudio.currentTime = instantPlayerState.sceneElapsed;
            }
            break;
        }
        accumulatedTime += scDur;
    }
}

function togglePreviewMute() {
    instantPlayerState.isMuted = !instantPlayerState.isMuted;
    if (instantPlayerState.currentAudio) {
        instantPlayerState.currentAudio.muted = instantPlayerState.isMuted;
    }
    const videoEl = document.getElementById('preview-video-element');
    if (videoEl) {
        videoEl.muted = instantPlayerState.isMuted;
    }
    const muteBtn = document.getElementById('preview-btn-mute');
    if (muteBtn) {
        muteBtn.textContent = instantPlayerState.isMuted ? '🔇' : '🔊';
        muteBtn.style.color = instantPlayerState.isMuted ? '#ef4444' : '#fff';
    }
}

function proceedRenderFromPreview() {
    const src = instantPlayerState.source;
    const jobId = instantPlayerState.jobId;
    closeInstantPreview();

    if (src === 'create' || src === 'job') {
        if (jobId) {
            triggerWebRender(jobId);
        } else {
            showToast('Không tìm thấy mã kịch bản để render!', 'error');
        }
    } else if (src === 'single_studio') {
        submitSingleVideoStudio();
    } else if (src === 'multi_studio') {
        submitManualStudio();
    }
}

function editScriptFromPreview() {
    const src = instantPlayerState.source;
    const jobId = instantPlayerState.jobId;
    closeInstantPreview();

    if ((src === 'create' || src === 'job') && jobId) {
        openScriptEditor(jobId);
    } else if (src === 'single_studio') {
        const scriptInput = document.getElementById('single-script-input');
        if (scriptInput) scriptInput.focus();
    } else if (src === 'multi_studio') {
        const scriptInput = document.getElementById('manual-script-input');
        if (scriptInput) scriptInput.focus();
    }
}

/* ─── Entry points for Instant Preview ─── */
function openInstantPreviewFromCreate() {
    if (!lastCreatedScriptResult) {
        // Fallback: Check if there's a job ID in result-job-badge
        const badge = document.getElementById('result-job-badge');
        const match = badge?.textContent?.match(/#(\d+)/);
        if (match) {
            openInstantPreviewForJob(parseInt(match[1], 10));
            return;
        }
        showToast('Vui lòng tạo kịch bản AI trước khi xem trước!', 'warning');
        return;
    }

    const res = lastCreatedScriptResult;
    const chSel = document.getElementById('input-channel');
    const chName = chSel?.options[chSel.selectedIndex]?.text || '@youtube_shorts';

    const scenes = (res.scenes || []).map((s, idx) => ({
        sceneId: s.scene_id || (idx + 1),
        narration: s.narration || '',
        duration: s.estimated_seconds || 4,
        prompt: s.visual_keyword || s.strategy_rationale || '',
        mediaUrl: s.media_path || null,
        mediaType: s.media_type || (s.media_path ? 'image' : 'prompt'),
        audioUrl: s.audio_path || null
    }));

    openInstantPreviewModal({
        source: 'create',
        jobId: res.job_id,
        title: res.title || 'Video Shorts AI',
        hook: res.hook || '',
        channelName: chName,
        voice: 'vi-VN-HoaiMyNeural',
        voiceRate: '+10%',
        scenes: scenes
    });
}

function openInstantPreviewFromSingleStudio() {
    const title = document.getElementById('single-title')?.value.trim() || 'Video Shorts Hoàn Chỉnh';
    const script = document.getElementById('single-script-input')?.value.trim() || '';
    const voice = document.getElementById('single-voice')?.value || 'vi-VN-HoaiMyNeural';
    const voiceRate = document.getElementById('single-voice-rate')?.value || '+10%';

    let videoUrl = null;
    let videoDuration = 15;
    if (singleVideoFile) {
        videoUrl = URL.createObjectURL(singleVideoFile);
        const player = document.getElementById('single-video-player');
        if (player && player.duration > 0) videoDuration = player.duration;
    }

    if (!script && !videoUrl) {
        showToast('Vui lòng tải lên video hoặc nhập kịch bản để xem trước!', 'warning');
        return;
    }

    // Parse script into scenes
    const lines = script.split('\n').map(l => l.trim()).filter(Boolean);
    const sceneCount = Math.max(1, lines.length);
    const durPerScene = videoDuration / sceneCount;

    const scenes = (lines.length > 0 ? lines : ['Video chưa có lời thoại']).map((line, idx) => {
        // Strip timestamps like 00-10:
        const cleanText = line.replace(/^\d+[\-:]\d+[:\s]*/, '');
        return {
            sceneId: idx + 1,
            narration: cleanText,
            duration: Math.round(durPerScene * 10) / 10,
            mediaUrl: videoUrl,
            mediaType: videoUrl ? 'video' : 'prompt',
            prompt: `Video hoàn chỉnh (${Math.round(videoDuration)}s)`
        };
    });

    openInstantPreviewModal({
        source: 'single_studio',
        title: title,
        hook: lines[0] || '',
        channelName: '@shorts_studio',
        voice: voice,
        voiceRate: voiceRate,
        scenes: scenes
    });
}

function openInstantPreviewFromMultiStudio() {
    const title = document.getElementById('manual-title')?.value.trim() || 'Video Shorts Ghép Cảnh';
    const voice = document.getElementById('manual-voice')?.value || 'vi-VN-HoaiMyNeural';
    const voiceRate = document.getElementById('manual-voice-rate')?.value || '+10%';

    if (!manualScenes || manualScenes.length === 0) {
        showToast('Vui lòng thêm ít nhất 1 phân cảnh để xem trước!', 'warning');
        return;
    }

    // Refresh mapped narration from script input
    autoDistributeScriptToScenes();

    const scenes = manualScenes.map((sc, idx) => ({
        sceneId: sc.id || (idx + 1),
        narration: sc.assignedNarration || '',
        duration: sc.duration || 4,
        mediaUrl: sc.mediaUrl || null,
        mediaType: sc.file?.type?.startsWith('video') ? 'video' : (sc.file?.type?.startsWith('image') ? 'image' : (sc.mediaUrl ? 'image' : 'prompt')),
        prompt: sc.file ? sc.file.name : `Phân cảnh #${idx + 1}`
    }));

    openInstantPreviewModal({
        source: 'multi_studio',
        title: title,
        hook: scenes[0]?.narration || '',
        channelName: '@multi_scene_studio',
        voice: voice,
        voiceRate: voiceRate,
        scenes: scenes
    });
}

async function openInstantPreviewForJob(jobId) {
    try {
        const data = await apiGet(`/api/videos/${jobId}`);
        if (!data) return;

        const scenes = (data.scenes || []).map((s, idx) => ({
            sceneId: s.scene_id || (idx + 1),
            narration: s.narration || '',
            duration: s.estimated_seconds || (s.audio_duration_ms ? s.audio_duration_ms / 1000 : 4),
            prompt: s.visual_keyword || s.cinematic_prompt || s.topic || '',
            mediaUrl: s.media_path || null,
            mediaType: s.media_type || (s.media_path ? 'image' : 'prompt'),
            audioUrl: s.audio_path || null
        }));

        if (scenes.length === 0 && data.script) {
            const lines = data.script.split('\n').map(l => l.trim()).filter(Boolean);
            lines.forEach((l, idx) => {
                scenes.push({
                    sceneId: idx + 1,
                    narration: l.replace(/^\d+[\-:]\d+[:\s]*/, ''),
                    duration: 4,
                    mediaUrl: null,
                    mediaType: 'prompt',
                    prompt: `Kịch bản #${data.id}`
                });
            });
        }

        openInstantPreviewModal({
            source: 'job',
            jobId: data.id,
            title: data.title || `Job #${data.id}`,
            hook: data.topic || '',
            channelName: `@channel_${data.channel_id || 'shorts'}`,
            voice: 'vi-VN-HoaiMyNeural',
            voiceRate: '+10%',
            scenes: scenes
        });
    } catch (e) {
        showToast('Lỗi tải dữ liệu job: ' + e.message, 'error');
    }
}

