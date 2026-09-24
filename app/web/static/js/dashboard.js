/**
 * AI YOUTUBE STUDIO v2.0 — INTERACTIVE DASHBOARD LOGIC
 */

/* ═══════════════════════════════════════════════════════
   STATE & GLOBALS
══════════════════════════════════════════════════════════ */
let allVideos = [];
let currentFilter = 'all';

const PAGE_META = {
    overview:  { title: 'Dashboard Tổng Quan',   sub: 'Theo dõi toàn bộ hệ thống AI YouTube Studio' },
    create:    { title: 'Tạo Kịch Bản AI',       sub: 'Nhập chủ đề — Gemini sinh kịch bản tự động' },
    pipeline:  { title: 'Pipeline Sản Xuất',      sub: 'Quản lý danh sách video và tiến độ render' },
    activity:  { title: 'Nhật Ký Hoạt Động',      sub: 'Lệnh Telegram thời gian thực — tự làm mới 10s' },
    channels:  { title: 'Kênh & Tài Khoản',       sub: 'Ma trận kênh YouTube và Google Ultra Pool' },
    members:   { title: 'Thành Viên Nhóm',         sub: 'Bảng xếp hạng đóng góp của các thành viên' },
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
    }
}

function navigateToVideos(statusFilter) {
    switchTab('pipeline');
    filterPipeline(statusFilter);
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

    // Auto-check token health on first load only
    if (!window._tokenHealthLoaded) {
        window._tokenHealthLoaded = true;
        checkTokenHealth();
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
async function checkTokenHealth() {
    const btn = document.getElementById('btn-check-tokens');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<span class="loading-spinner"></span> Đang kiểm tra...`;
    }

    // Show loading state in key lists
    ['gemini-keys-list', 'pexels-keys-list'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.innerHTML = `<div class="token-checking"><span class="loading-spinner"></span> Đang kiểm tra keys...</div>`;
    });

    try {
        const data = await apiGet('/api/token-health');
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
   CHANNELS
══════════════════════════════════════════════════════════ */
async function fetchChannels() {
    const channels = await apiGet('/api/channels');
    const container = document.getElementById('channels-container');
    if (!container) return;

    if (!channels || channels.length === 0) {
        container.innerHTML = `<div class="empty-state"><div class="empty-icon">📺</div><p>Chưa có kênh nào được cấu hình trong <code>config/channels.yaml</code></p></div>`;
        return;
    }

    container.innerHTML = channels.map(ch => `
        <div class="channel-card">
            <div class="channel-header">
                <div class="channel-title">
                    <h4>📺 ${esc(ch.name)}</h4>
                    <span>Ngách: <strong>${esc(ch.niche)}</strong> &nbsp;|&nbsp; ${ch.language.toUpperCase()}</span>
                </div>
                <span class="badge ${ch.is_authenticated ? 'badge-green' : 'badge-amber'}" style="${ch.is_authenticated ? '' : 'background: rgba(239, 68, 68, 0.15); color: #f87171;'}">${ch.token_status}</span>
            </div>
            <div class="channel-metrics">
                <div class="metric-item"><span>Google Account</span><strong>${esc(ch.google_account).toUpperCase()}</strong></div>
                <div class="metric-item"><span>Drive Quota</span><strong style="color:var(--accent-blue)">${ch.drive_quota}</strong></div>
                <div class="metric-item"><span>Tổng Video</span><strong>${ch.video_count}</strong></div>
                <div class="metric-item"><span>Đã Đăng</span><strong style="color:var(--accent-green)">${ch.published_count}</strong></div>
            </div>
            <div style="font-size:11.5px;color:var(--text-muted);">🎙 Voice: <code>${ch.preferred_voice}</code></div>
        </div>
    `).join('');
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

        // Action buttons
        let actionButtons = `<button class="btn btn-secondary btn-sm" onclick="openVideoDetail(${v.id})" title="Chi tiết">🔍</button>`;
        if (v.status === 'SCRIPT_READY') {
            actionButtons += `<button class="btn btn-primary btn-sm" onclick="triggerWebRender(${v.id})" title="Xuất Video" style="font-size:11px;">🚀 Render</button>`;
        } else if (v.status === 'FAILED') {
            actionButtons += `<button class="btn btn-primary btn-sm" onclick="triggerWebRender(${v.id})" title="Thử render lại" style="font-size:11px;background:var(--accent-amber);border-color:var(--accent-amber);">🔄 Thử lại</button>`;
        } else if (v.status === 'RENDERED') {
            actionButtons += `<button class="btn btn-primary btn-sm" onclick="openVideoDetail(${v.id})" title="Xem & Xuất bản" style="font-size:11px;">📤 Xuất</button>`;
        }
        actionButtons += `<button class="btn btn-danger btn-sm" onclick="deleteJob(${v.id})" title="Xoá">🗑</button>`;

        return `
        <tr>
            <td><code>#${v.id}</code></td>
            <td>
                <div style="font-weight:600;color:var(--text-primary);max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(v.title)}">${esc(v.title)}</div>
                <div style="font-size:11.5px;color:var(--text-muted);">${esc(v.topic)}</div>
            </td>
            <td><code>${esc(v.channel_id)}</code></td>
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
    const scenesHTML = (result.scenes || []).map(s => `
        <div class="scene-item">
            <div class="scene-num">${s.scene_id}</div>
            <div class="scene-body">
                <div class="scene-narration">${esc(s.narration)}</div>
                <div class="scene-keyword">🔍 ${esc(s.visual_keyword)}</div>
            </div>
            <div class="scene-dur">~${s.estimated_seconds}s</div>
        </div>`).join('');

    return `
        <div class="script-meta">
            <span class="badge badge-green">✅ Job #${result.job_id} đã lưu</span>
            <span class="badge badge-blue">${result.scene_count} phân cảnh</span>
            <span class="badge badge-amber">~${result.total_duration?.toFixed(0)}s tổng</span>
        </div>
        <div class="script-title">${esc(result.title)}</div>
        <div class="script-hook">💬 Hook: "${esc(result.hook)}"</div>
        <div>
            <div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--text-muted);margin-bottom:10px;">Phân Cảnh</div>
            <div class="scenes-list">${scenesHTML}</div>
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

    const scenesHTML = (data.scenes || []).map(s => `
        <div class="scene-item">
            <div class="scene-num">${s.scene_id}</div>
            <div class="scene-body">
                <div class="scene-narration">${esc(s.narration)}</div>
                <div class="scene-keyword">🔍 ${esc(s.visual_keyword)}</div>
                ${s.audio_path ? `<div class="scene-keyword">🎵 ${esc(s.audio_path)}</div>` : ''}
                ${s.media_path ? `<div class="scene-keyword">🖼 ${esc(s.media_path)}</div>` : ''}
            </div>
            <div class="scene-dur">~${s.estimated_seconds}s</div>
        </div>`).join('');

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
            ${(data.status === 'SCRIPT_READY' || data.status === 'FAILED') ? `<button class="btn btn-primary btn-sm" onclick="closeModal(); triggerWebRender(${data.id});">🚀 ${data.status === 'FAILED' ? 'Thử Render Lại' : 'Bắt Đầu Render'}</button>` : ''}
            ${data.status === 'RENDERED' && data.video_path ? `<a href="/api/videos/${data.id}/download" target="_blank" class="btn btn-primary btn-sm">📥 Tải Video</a>` : ''}
            ${(data.status === 'RENDERED' || data.status === 'PUBLISHED') ? `
            <button class="btn btn-secondary btn-sm" id="btn-upload-drive-${data.id}" onclick="uploadToDrive(${data.id})" style="background:linear-gradient(135deg,rgba(66,133,244,0.18),rgba(52,168,83,0.12));border-color:rgba(66,133,244,0.3);">
                ☁️ ${data.drive_link ? 'Cập nhật Drive' : 'Tải lên Drive 20TB'}
            </button>` : ''}
            ${data.youtube_id ? `<a href="https://youtube.com/shorts/${data.youtube_id}" target="_blank" class="btn btn-secondary btn-sm">▶ Xem trên YouTube</a>` : ''}
            ${data.drive_link ? `<a href="${data.drive_link}" target="_blank" class="btn btn-secondary btn-sm" style="color:var(--accent-green);">📂 Mở Link Drive</a>` : ''}
            <button class="btn btn-danger btn-sm" onclick="deleteJob(${data.id}); closeModal();">🗑 Xoá Job</button>
        </div>`;
}

function closeModal() {
    document.getElementById('modal-overlay').classList.remove('visible');
}

document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

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
        const data = await apiGet('/api/channels');
        if (data) {
            allChannels = Array.isArray(data) ? data : (data.channels || []);
            updateChannelDropdowns(allChannels);
        }
    } catch (e) {
        console.error('Lỗi fetch channels:', e);
    }
    // Also fetch accounts
    await fetchAccounts();
}

function updateChannelDropdowns(channels) {
    const sel = document.getElementById('input-channel');
    if (!sel) return;
    const currentVal = sel.value;
    sel.innerHTML = '';
    channels.forEach(ch => {
        const opt = document.createElement('option');
        opt.value = ch.id;
        opt.textContent = `📺 ${ch.name} (${ch.niche || 'general'})`;
        sel.appendChild(opt);
    });
    if (currentVal && channels.some(c => c.id === currentVal)) {
        sel.value = currentVal;
    }
}

async function fetchAccounts() {
    const container = document.getElementById('accounts-container');
    if (!container) return;

    try {
        const data = await apiGet('/api/accounts');
        if (!data || !data.accounts) {
            container.innerHTML = `<div style="text-align:center; padding:30px; color:var(--text-muted);">Không thể tải danh sách tài khoản.</div>`;
            return;
        }

        allAccounts = data.accounts;
        renderAccounts(allAccounts);
    } catch (e) {
        container.innerHTML = `<div style="text-align:center; padding:30px; color:var(--accent-rose);">Lỗi kết nối API: ${esc(e.message)}</div>`;
    }
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
        return `
            <div class="account-card" id="card-${esc(acc.id)}">
                <div class="account-header">
                    <div class="account-identity">
                        <div class="account-icon">🌐</div>
                        <div class="account-title-wrap">
                            <h4>
                                ${esc(acc.id)}
                                <span class="badge badge-blue">${brandChannels.length} Kênh</span>
                            </h4>
                            <div class="account-email">${esc(acc.email_label)}</div>
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
                        <h5>📺 Danh Sách Kênh Thương Hiệu (${brandChannels.length})</h5>
                        <button class="btn btn-secondary btn-sm" onclick="openAddChannelModal('${esc(acc.id)}')">
                            + Thêm Kênh Mới
                        </button>
                    </div>

                    <div class="channels-items-list">
                        ${brandChannels.length === 0 ? `
                            <div style="text-align:center; padding:18px; color:var(--text-muted); font-size:12.5px;">
                                Chưa có kênh nào thuộc tài khoản này. Bấm <strong>"+ Thêm Kênh Mới"</strong> để gán kênh.
                            </div>
                        ` : brandChannels.map(ch => `
                            <div class="channel-row-item">
                                <div class="channel-row-main">
                                    <div class="channel-row-title">
                                        <span>${esc(ch.name)}</span>
                                        <span class="badge badge-blue" style="font-size:10px;">ID: ${esc(ch.id)}</span>
                                    </div>
                                    <div class="channel-row-meta">
                                        <span>🎯 <strong>Chủ đề:</strong> ${esc(ch.niche || 'general')}</span>
                                        <span>🌐 <strong>Ngôn ngữ:</strong> ${esc(ch.language || 'en').toUpperCase()}</span>
                                        <span>🎙️ <strong>Giọng:</strong> ${esc(ch.preferred_voice || 'Default')}</span>
                                        <span>🖼️ <strong>Visual:</strong> ${ch.visual_mode === 'imagen_ai' ? '✨ Imagen 3' : '🎞️ Pexels'}</span>
                                        <span>⏰ <strong>Đăng:</strong> ${ch.schedule?.publish_hour_utc ?? 12}:00 UTC</span>
                                    </div>
                                </div>
                                <div class="channel-row-actions">
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
                        `).join('')}
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
    document.getElementById('acc-input-id').value = '';
    document.getElementById('acc-input-email').value = '';
    document.getElementById('acc-input-drive').value = '2 TB Google One';
    document.getElementById('acc-input-quota').value = '10000';
    document.getElementById('modal-account').classList.add('visible');
}

function closeAccountModal() {
    document.getElementById('modal-account').classList.remove('visible');
}

async function submitAccountForm() {
    const id = document.getElementById('acc-input-id').value.trim();
    const email = document.getElementById('acc-input-email').value.trim();
    const drive = document.getElementById('acc-input-drive').value.trim();
    const quota = parseInt(document.getElementById('acc-input-quota').value.trim()) || 10000;

    if (!id) {
        showToast('Vui lòng nhập Mã tài khoản', 'error');
        return;
    }
    if (!email) {
        showToast('Vui lòng nhập Email / Ghi chú', 'error');
        return;
    }

    try {
        const res = await apiPost('/api/accounts', {
            id: id,
            email_label: email,
            drive_storage: drive,
            youtube_quota_daily: quota
        });
        if (res && res.success) {
            showToast(res.message || 'Đã tạo tài khoản Google thành công!', 'success');
            closeAccountModal();
            await fetchAccounts();
            await fetchStats();
        } else {
            showToast(res?.detail || 'Không thể tạo tài khoản', 'error');
        }
    } catch (e) {
        showToast(`Lỗi tạo tài khoản: ${e.message}`, 'error');
    }
}

async function deleteAccount(accountId) {
    if (!confirm(`⚠️ Bạn có chắc muốn xóa tài khoản '${accountId}'?\nTất cả các kênh liên kết cũng sẽ bị xóa khỏi cấu hình.`)) return;

    try {
        const res = await fetch(`/api/accounts/${accountId}`, { method: 'DELETE' });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast(data.message || `Đã xóa tài khoản ${accountId}`, 'success');
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
    allAccounts.forEach(acc => {
        const opt = document.createElement('option');
        opt.value = acc.id;
        opt.textContent = `${acc.id} (${acc.email_label})`;
        if (acc.id === selectedAccId) opt.selected = true;
        sel.appendChild(opt);
    });
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
    document.getElementById('channel-input-visual').value = 'imagen_ai';
    document.getElementById('channel-input-hour').value = '12';
    document.getElementById('modal-channel').classList.add('visible');
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
    document.getElementById('channel-input-visual').value = ch.visual_mode || 'imagen_ai';
    document.getElementById('channel-input-hour').value = ch.schedule?.publish_hour_utc ?? 12;
    document.getElementById('modal-channel').classList.add('visible');
}

function closeChannelModal() {
    document.getElementById('modal-channel').classList.remove('visible');
}

async function submitChannelForm() {
    const mode = document.getElementById('channel-mode').value;
    const accountId = document.getElementById('channel-parent-acc').value;
    const id = document.getElementById('channel-input-id').value.trim();
    const name = document.getElementById('channel-input-name').value.trim();
    const niche = document.getElementById('channel-input-niche').value.trim() || 'general';
    const lang = document.getElementById('channel-input-lang').value;
    const voice = document.getElementById('channel-input-voice').value;
    const visual = document.getElementById('channel-input-visual').value;
    const hour = parseInt(document.getElementById('channel-input-hour').value) || 12;

    if (!id) {
        showToast('Vui lòng nhập Mã Kênh', 'error');
        return;
    }
    if (!name) {
        showToast('Vui lòng nhập Tên Kênh', 'error');
        return;
    }

    const payload = {
        id: id,
        name: name,
        niche: niche,
        language: lang,
        preferred_voice: voice,
        visual_mode: visual,
        publish_hour_utc: hour,
        days: ["monday", "wednesday", "friday", "sunday"]
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
            showToast(res.message || 'Thành công!', 'success');
            closeChannelModal();
            await fetchChannels();
            await fetchAccounts();
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

function useChannelForScript(channelId) {
    switchTab('create');
    const sel = document.getElementById('input-channel');
    if (sel) {
        sel.value = channelId;
    }
}

/* ═══════════════════════════════════════════════════════
   GOOGLE OAUTH AUTHENTICATION
══════════════════════════════════════════════════════════ */
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

        modal.classList.add('visible');
        heading.textContent = `Đang mở trình duyệt xác thực ${service.toUpperCase()}...`;
        desc.textContent = `Hệ thống đang mở trình duyệt đăng nhập Google cho tài khoản '${accountId}'. Hãy bấm 'Cho Phép' trên trang Google.`;
        resultBox.style.display = 'none';

        if (oauthPollTimer) clearInterval(oauthPollTimer);

        // Start polling status every 2 seconds
        oauthPollTimer = setInterval(async () => {
            try {
                const statusData = await apiGet(`/api/accounts/${accountId}/auth-status?service=${service}`);
                if (statusData) {
                    if (statusData.status === 'success') {
                        clearInterval(oauthPollTimer);
                        heading.textContent = '🎉 Xác Thực Thành Công!';
                        desc.textContent = statusData.message || 'Đã lưu token đăng nhập thành công.';
                        resultBox.style.display = 'block';
                        resultBox.style.background = 'rgba(52,211,153,0.15)';
                        resultBox.style.color = 'var(--accent-green)';
                        resultBox.textContent = '✓ Token đã được đồng bộ vào hệ thống.';
                        showToast(`Đã kết nối ${service.toUpperCase()} thành công!`, 'success');
                        setTimeout(() => {
                            closeOAuthModal();
                            fetchAccounts();
                            fetchStats();
                        }, 1800);
                    } else if (statusData.status === 'error') {
                        clearInterval(oauthPollTimer);
                        heading.textContent = '❌ Lỗi Xác Thực';
                        desc.textContent = statusData.error || 'Quá trình xác thực bị hủy hoặc xảy ra lỗi.';
                        resultBox.style.display = 'block';
                        resultBox.style.background = 'rgba(251,113,133,0.15)';
                        resultBox.style.color = 'var(--accent-rose)';
                        resultBox.textContent = statusData.error || 'Lỗi không xác định.';
                    }
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
    document.getElementById('modal-oauth').classList.remove('visible');
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
