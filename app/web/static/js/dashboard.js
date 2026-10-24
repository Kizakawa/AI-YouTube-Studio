/**
 * AI YOUTUBE STUDIO v2.0 — INTERACTIVE DASHBOARD LOGIC
 */

/* ═══════════════════════════════════════════════════════
   STATE & GLOBALS
══════════════════════════════════════════════════════════ */
let allVideos = [];
let allChannels = [];
let allNiches = [];
let allAccounts = [];
let currentFilter = 'all';

const PAGE_META = {
    overview:      { title: 'Dashboard Tổng Quan',   sub: 'Theo dõi toàn bộ hệ thống AI YouTube Studio' },
    analytics:     { title: 'Số Liệu & Dự Đoán Tăng Trưởng', sub: 'Thống kê kênh thời gian thực & Mô hình dự đoán AI' },
    trends:        { title: 'Google Trends & Bắt Trend AI', sub: 'Nhu cầu tìm kiếm thực tế — Bắc cầu sang ngách kênh & Thumbnail 1 giây' },
    create:        { title: 'Tạo Kịch Bản AI',       sub: 'Nhập chủ đề — Gemini sinh kịch bản tự động' },
    manual_studio: { title: 'Studio Lồng Tiếng & Hiệu Ứng AI', sub: 'Tải 1 video đã ghép (Google Flow, CapCut...) + Lời thoại, AI sẽ lồng tiếng đọc, SFX và phụ đề Shorts cực đẹp' },
    pipeline:      { title: 'Pipeline Sản Xuất',      sub: 'Quản lý danh sách video và tiến độ render' },
    repurposer:    { title: 'Tái Chế Shorts AI Viral', sub: 'Trích xuất các khoảnh khắc vàng từ video dài thành YouTube Shorts 9:16 chuẩn bản quyền & tối ưu thuật toán' },
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

/* ═══════════════════════════════════════════════════════
   DEVICE TOKEN PERSISTENCE (Lưu trữ 7 ngày qua LocalStorage & Cookie)
══════════════════════════════════════════════════════════ */
function saveDeviceToken(token) {
    if (!token) return;
    try {
        localStorage.setItem('youtube_device_token', token);
        document.cookie = `device_token=${encodeURIComponent(token)}; path=/; max-age=604800; SameSite=Lax`;
    } catch (e) {
        console.warn('Không thể lưu token thiết bị:', e);
    }
}

function getStoredDeviceToken() {
    let token = '';
    try {
        token = localStorage.getItem('youtube_device_token');
    } catch (e) {}
    if (!token) {
        const match = document.cookie.match(/(?:^|;\s*)device_token=([^;]+)/);
        if (match) {
            token = decodeURIComponent(match[1]);
            try { localStorage.setItem('youtube_device_token', token); } catch (e) {}
        }
    }
    return token || '';
}

/* ═══════════════════════════════════════════════════════
   FETCH INTERCEPTOR (Tự động đính kèm X-Device-Token)
══════════════════════════════════════════════════════════ */
const _originalFetch = window.fetch;
window.fetch = function(url, options = {}) {
    options = options || {};
    options.headers = options.headers || {};
    const token = getStoredDeviceToken();
    if (token) {
        if (options.headers instanceof Headers) {
            options.headers.set('X-Device-Token', token);
        } else if (typeof options.headers === 'object') {
            options.headers['X-Device-Token'] = token;
        }
    }
    return _originalFetch(url, options);
};

/* ═══════════════════════════════════════════════════════
   TELEGRAM 2FA DEVICE GATEKEEPER
══════════════════════════════════════════════════════════ */
let _gkPollingInterval = null;
let _currentRequestId = null;

async function checkDeviceAuthorization() {
    try {
        const token = getStoredDeviceToken();
        const res = await fetch('/api/auth/check', {
            headers: { 'X-Device-Token': token }
        });
        const data = await res.json();

        const overlay = document.getElementById('device-gatekeeper-overlay');
        if (!overlay) return true;

        if (data.authenticated) {
            if (data.token) {
                saveDeviceToken(data.token);
            }
            document.documentElement.classList.remove('device-unauthorized');
            overlay.style.display = 'none';
            return true;
        }

        // Thiết bị từ xa chưa được duyệt -> Kích hoạt giao diện Cyber Gatekeeper 2FA
        document.documentElement.classList.add('device-unauthorized');
        overlay.style.display = 'flex';
        const devNameEl = document.getElementById('gk-device-name');
        const ipEl = document.getElementById('gk-client-ip');
        if (devNameEl) devNameEl.textContent = data.device_name || 'Thiết bị khách';
        if (ipEl) ipEl.textContent = data.client_ip || '---';

        // Tự động kích hoạt gửi yêu cầu phê duyệt về Telegram
        await initiateDeviceAccessRequest();
        return false;
    } catch (e) {
        console.warn('Lỗi kiểm tra quyền thiết bị:', e);
        return false;
    }
}

async function initiateDeviceAccessRequest() {
    try {
        const statusBadge = document.getElementById('gk-status-badge');
        const reqIdEl = document.getElementById('gk-request-id');
        const reqTimeEl = document.getElementById('gk-request-time');

        if (statusBadge) {
            statusBadge.innerHTML = `<span class="gk-pulse-amber"></span> Đang tạo mã phiên & gửi Telegram...`;
        }
        if (reqIdEl) reqIdEl.textContent = 'Đang khởi tạo...';

        const res = await fetch('/api/auth/request-access', { method: 'POST' });
        const data = await res.json();
        _currentRequestId = data.request_id;

        if (reqIdEl) {
            reqIdEl.textContent = data.request_id || 'Chưa xác định';
        }
        if (reqTimeEl) {
            reqTimeEl.textContent = new Date().toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        }
        if (statusBadge) {
            statusBadge.innerHTML = `<span class="gk-pulse-amber"></span> Chờ duyệt trên Telegram...`;
        }

        if (_gkPollingInterval) clearInterval(_gkPollingInterval);
        _gkPollingInterval = setInterval(pollDeviceStatus, 2000);
    } catch (e) {
        console.error('Không thể gửi yêu cầu xác thực thiết bị:', e);
        const statusBadge = document.getElementById('gk-status-badge');
        if (statusBadge) {
            statusBadge.innerHTML = `<span style="color:#ef4444; font-weight:700;">⚠️ Lỗi kết nối máy chủ</span>`;
        }
    }
}

async function pollDeviceStatus() {
    if (!_currentRequestId) return;
    try {
        const res = await fetch(`/api/auth/status/${_currentRequestId}`);
        const data = await res.json();

        const overlay = document.getElementById('device-gatekeeper-overlay');
        const iconCont = document.getElementById('gk-icon-container');
        const statusBadge = document.getElementById('gk-status-badge');
        const hintBox = document.getElementById('gk-hint-box');
        const portalTitle = document.getElementById('gk-portal-title');
        const subtitle = document.getElementById('gk-subtitle');

        if (data.status === 'approved') {
            clearInterval(_gkPollingInterval);
            _gkPollingInterval = null;
            if (data.token) {
                saveDeviceToken(data.token);
            }
            if (portalTitle) {
                portalTitle.innerHTML = '🎉 XÁC THỰC THÀNH CÔNG!';
                portalTitle.style.color = '#34d399';
            }
            if (subtitle) {
                subtitle.textContent = 'Quản trị viên đã phê duyệt thiết bị (hạn 7 ngày). Đang chuyển hướng vào Studio...';
            }
            if (iconCont) {
                iconCont.innerHTML = '✅';
                iconCont.style.background = 'linear-gradient(135deg, rgba(16,185,129,0.25), rgba(5,150,105,0.2))';
                iconCont.style.borderColor = 'rgba(16,185,129,0.6)';
                iconCont.style.boxShadow = '0 0 35px rgba(16,185,129,0.5)';
            }
            if (statusBadge) {
                statusBadge.innerHTML = `<span style="color:#10b981; font-weight:700; display:flex; align-items:center; gap:6px;"><span>🟢</span> Đã Phê Duyệt (Hạn 7 ngày)</span>`;
            }
            if (hintBox) {
                hintBox.innerHTML = `🎉 <strong>Quyền truy cập đã sẵn sàng!</strong> Hệ thống đang mở giao diện làm việc...`;
                hintBox.style.borderColor = 'rgba(16,185,129,0.4)';
                hintBox.style.background = 'rgba(16,185,129,0.1)';
            }

            setTimeout(() => {
                document.documentElement.classList.remove('device-unauthorized');
                if (overlay) {
                    overlay.style.transition = 'opacity 0.6s ease, transform 0.6s ease';
                    overlay.style.opacity = '0';
                    overlay.style.transform = 'scale(1.02)';
                    setTimeout(() => {
                        overlay.style.display = 'none';
                        overlay.style.opacity = '1';
                        overlay.style.transform = 'none';
                        fetchAllData().then(() => scheduleNextFetch(10000));
                    }, 600);
                } else {
                    fetchAllData().then(() => scheduleNextFetch(10000));
                }
            }, 900);
        } else if (data.status === 'rejected') {
            clearInterval(_gkPollingInterval);
            _gkPollingInterval = null;
            if (portalTitle) {
                portalTitle.innerHTML = '⛔ TỪ CHỐI TRUY CẬP';
                portalTitle.style.color = '#ef4444';
            }
            if (iconCont) {
                iconCont.innerHTML = '❌';
                iconCont.style.background = 'linear-gradient(135deg, rgba(239,68,68,0.25), rgba(220,38,38,0.15))';
                iconCont.style.borderColor = 'rgba(239,68,68,0.6)';
                iconCont.style.boxShadow = '0 0 35px rgba(239,68,68,0.4)';
            }
            if (statusBadge) {
                statusBadge.innerHTML = `<span style="color:#ef4444; font-weight:700; display:flex; align-items:center; gap:6px;"><span>🔴</span> Quản Trị Viên Từ Chối</span>`;
            }
            if (hintBox) {
                hintBox.innerHTML = `⚠️ <strong>Yêu cầu truy cập của bạn đã bị từ chối</strong> bởi Quản trị viên trên Telegram.<br>Nếu đây là nhầm lẫn, vui lòng liên hệ Admin hoặc bấm nút <strong>"Gửi Lại Yêu Cầu"</strong>.`;
                hintBox.style.borderColor = 'rgba(239,68,68,0.4)';
                hintBox.style.background = 'rgba(239,68,68,0.08)';
            }
        }
    } catch (e) {
        console.warn('Lỗi poll trạng thái thiết bị:', e);
    }
}

function copyGkRequestId() {
    const reqIdEl = document.getElementById('gk-request-id');
    const text = reqIdEl ? (reqIdEl.textContent || '').trim() : '';
    if (!text || text.includes('Đang') || text === '---') {
        return;
    }

    navigator.clipboard.writeText(text).then(() => {
        const copyBtns = document.querySelectorAll('.gk-btn-copy-sm');
        copyBtns.forEach(btn => {
            const old = btn.textContent;
            btn.textContent = '✅ Đã Copy!';
            btn.style.color = '#34d399';
            setTimeout(() => {
                btn.textContent = old;
                btn.style.color = '';
            }, 2000);
        });
    }).catch(err => {
        console.warn('Không thể sao chép Request ID:', err);
    });
}

async function checkDeviceStatusManual() {
    const btn = document.getElementById('gk-check-now-btn');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<span style="display:inline-block; animation:gk-spin 1s infinite linear;">⚙️</span> Đang kiểm tra...`;
    }

    try {
        if (_currentRequestId) {
            await pollDeviceStatus();
        } else {
            const isAuth = await checkDeviceAuthorization();
            if (isAuth) {
                fetchAllData().then(() => scheduleNextFetch(10000));
            }
        }
    } catch (e) {
        console.error('Lỗi khi kiểm tra thủ công:', e);
    } finally {
        setTimeout(() => {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = `⚡ Kiểm Tra Trạng Thái`;
            }
        }, 800);
    }
}

async function requestDeviceAccessAgain() {
    const retryBtn = document.getElementById('gk-retry-btn');
    if (retryBtn) {
        retryBtn.disabled = true;
        retryBtn.innerHTML = `⏳ Đang gửi...`;
    }
    const hintBox = document.getElementById('gk-hint-box');
    const iconCont = document.getElementById('gk-icon-container');
    const portalTitle = document.getElementById('gk-portal-title');
    const subtitle = document.getElementById('gk-subtitle');

    if (portalTitle) {
        portalTitle.innerHTML = 'AI YOUTUBE STUDIO HUB';
        portalTitle.style.color = '#fff';
    }
    if (subtitle) {
        subtitle.textContent = 'Thiết bị của bạn đang chờ phê duyệt. Để bảo mật ma trận kênh YouTube và hạn ngạch AI, yêu cầu truy cập đã được gửi tự động tới nhóm Telegram của Quản trị viên.';
    }
    if (iconCont) {
        iconCont.innerHTML = '🛡️';
        iconCont.style.background = 'linear-gradient(135deg, rgba(56,189,248,0.2), rgba(129,140,248,0.12))';
        iconCont.style.borderColor = 'rgba(56,189,248,0.45)';
        iconCont.style.boxShadow = '0 0 30px rgba(56,189,248,0.28)';
    }
    if (hintBox) {
        hintBox.innerHTML = `<div style="font-weight:700; color:#38bdf8; margin-bottom:4px; display:flex; align-items:center; gap:6px;"><span>💬</span> Cách mở khóa:</div>1. Mở nhóm Telegram của bot &rarr; Tìm tin nhắn <strong>"🛡️ YÊU CẦU TRUY CẬP THIẾT BỊ MỚI"</strong>.<br>2. Quản trị viên chỉ cần bấm nút <strong>"✅ Đồng Ý Cấp Quyền (7 ngày)"</strong>.<br>3. Màn hình này sẽ <strong>tự động nhận diện và mở khóa Web Studio sau 1 giây</strong> mà bạn không cần phải làm gì thêm!`;
        hintBox.style.borderColor = 'rgba(56, 189, 248, 0.28)';
        hintBox.style.background = 'rgba(56, 189, 248, 0.06)';
    }

    await initiateDeviceAccessRequest();
    if (retryBtn) {
        setTimeout(() => {
            retryBtn.disabled = false;
            retryBtn.innerHTML = `🔄 Gửi Lại Yêu Cầu`;
        }, 1500);
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    initNavigation();
    initDeviceIdentity();
    const isAuth = await checkDeviceAuthorization();
    if (isAuth) {
        fetchAllData().then(() => scheduleNextFetch(10000));
    }

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
        fetchNiches();
    } else if (tabId === 'analytics') {
        loadAnalytics();
    } else if (tabId === 'manual_studio') {
        initManualStudio();
    } else if (tabId === 'repurposer') {
        initRepurposerTab();
    } else if (tabId === 'trends') {
        initTrendsTab();
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

function linkifyText(str) {
    if (!str) return '';
    const safe = escapeHtml(str);
    return safe
        .replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer" style="color:#38bdf8; text-decoration:underline; word-break:break-all;">$1</a>')
        .replace(/\n/g, '<br>');
}

/* ═══════════════════════════════════════════════════════
   FETCH ALL
══════════════════════════════════════════════════════════ */
let consecutiveNetworkErrors = 0;

async function fetchAllData() {
    const results = await Promise.allSettled([
        fetchStats(),
        fetchActivities(),
        fetchMembers(),
        fetchChannels(),
        fetchNiches(),
        fetchVideos(),
        fetchHealth(),
        fetchAutoPilotStatus(),
    ]);

    // Tự động phát hiện khi máy chủ bị tắt và tự tìm máy chủ mới trong mạng Wi-Fi
    const allFailed = results.every(r => r.status === 'rejected');
    if (allFailed) {
        consecutiveNetworkErrors++;
        if (consecutiveNetworkErrors >= 2 && !isScanningForServer && window.location.hostname !== '127.0.0.1' && window.location.hostname !== 'localhost') {
            scanForActiveServer();
        }
    } else {
        consecutiveNetworkErrors = 0;
    }

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
            : 'Chưa kết nối — Bấm [Kết Nối Google Drive] bên dưới');

    if (data.render) {
        setHealth('health-render', true, data.render.engine || 'FFmpeg GPU Ready');
    }
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
        let actionButtons = '';
        if (v.status === 'SCRIPT_READY' || v.status === 'MEDIA_READY' || v.status === 'AUDIO_READY') {
            actionButtons += `<button class="btn btn-secondary btn-sm" onclick="openVideoDetail(${v.id})" title="Chi tiết kịch bản">🔍</button>`;
            actionButtons += `<button class="btn btn-secondary btn-sm" onclick="openStoryboardModal(${v.id})" title="Kiểm duyệt Storyboard & Đổi Media Pexels/AI" style="font-size:11px; color:#38bdf8; border-color:rgba(56,189,248,0.4);">🎞️ Storyboard</button>`;
            actionButtons += `<button class="btn btn-secondary btn-sm" onclick="openInstantPreviewForJob(${v.id})" title="Xem trước video và phụ đề tức thì" style="font-size:11px; color:#f472b6; border-color:rgba(236,72,153,0.4);">👁️ Xem trước</button>`;
            actionButtons += `<button class="btn btn-secondary btn-sm" onclick="openScriptEditor(${v.id})" title="Chỉnh sửa kịch bản" style="font-size:11px;">✏️ Sửa</button>`;
            actionButtons += `<button class="btn btn-primary btn-sm" onclick="triggerWebRender(${v.id})" title="Xuất Video" style="font-size:11px;">🚀 Render</button>`;
        } else if (v.status === 'FAILED') {
            actionButtons += `<button class="btn btn-secondary btn-sm" onclick="openVideoDetail(${v.id})" title="Xem chi tiết lỗi">🔍</button>`;
            actionButtons += `<button class="btn btn-secondary btn-sm" onclick="openStoryboardModal(${v.id})" title="Kiểm duyệt Storyboard & Đổi Media Pexels/AI" style="font-size:11px; color:#38bdf8; border-color:rgba(56,189,248,0.4);">🎞️ Storyboard</button>`;
            actionButtons += `<button class="btn btn-secondary btn-sm" onclick="openInstantPreviewForJob(${v.id})" title="Xem trước kịch bản" style="font-size:11px; color:#f472b6; border-color:rgba(236,72,153,0.4);">👁️ Xem trước</button>`;
            actionButtons += `<button class="btn btn-secondary btn-sm" onclick="openScriptEditor(${v.id})" title="Chỉnh sửa kịch bản" style="font-size:11px;">✏️ Sửa</button>`;
            actionButtons += `<button class="btn btn-primary btn-sm" onclick="triggerWebRender(${v.id})" title="Thử render lại" style="font-size:11px;background:var(--accent-amber);border-color:var(--accent-amber);">🔄 Thử lại</button>`;
        } else if (v.status === 'RENDERED') {
            actionButtons += `<button class="btn btn-success btn-sm" onclick="playRenderedVideo(${v.id})" title="Bấm vào để xem video thành phẩm" style="font-size:11.5px;background:linear-gradient(135deg,#10b981,#059669);border:none;font-weight:700;color:#fff;box-shadow:0 2px 8px rgba(16,185,129,0.35);">▶️ Xem Video</button>`;
            actionButtons += `<button class="btn btn-primary btn-sm" onclick="openPublishModal(${v.id})" title="Chọn kênh & Đăng YouTube" style="font-size:11px;background:linear-gradient(135deg,#ef4444,#dc2626);border:none;font-weight:600;">🚀 Đăng YT</button>`;
            actionButtons += `<button class="btn btn-secondary btn-sm" onclick="openVideoDetail(${v.id})" title="Chi tiết video & phân cảnh" style="font-size:11px;">🔍</button>`;
        } else if (v.status === 'PUBLISHED') {
            actionButtons += `<button class="btn btn-success btn-sm" onclick="playRenderedVideo(${v.id})" title="Xem video đã đăng" style="font-size:11.5px;background:linear-gradient(135deg,#10b981,#059669);border:none;font-weight:700;color:#fff;box-shadow:0 2px 8px rgba(16,185,129,0.35);">▶️ Xem Video</button>`;
            actionButtons += `<button class="btn btn-secondary btn-sm" onclick="openPublishModal(${v.id})" title="Đăng lại / Đăng kênh khác" style="font-size:11px;border-color:rgba(239,68,68,0.4);color:#ef4444;font-weight:600;">🔄 Đăng Lại</button>`;
            actionButtons += `<button class="btn btn-secondary btn-sm" onclick="openVideoDetail(${v.id})" title="Chi tiết video & phân cảnh" style="font-size:11px;">🔍</button>`;
        } else {
            actionButtons += `<button class="btn btn-secondary btn-sm" onclick="openVideoDetail(${v.id})" title="Chi tiết">🔍</button>`;
        }
        let scoreBadge = '';
        let qScore = v.quality_scores;
        if (typeof qScore === 'string') {
            try { qScore = JSON.parse(qScore); } catch (e) { qScore = null; }
        }
        if (qScore && (qScore.overall !== undefined || qScore.overall_score !== undefined)) {
            const overall = qScore.overall !== undefined ? qScore.overall : qScore.overall_score;
            scoreBadge = `<span class="badge ${overall >= 8.0 ? 'badge-green' : 'badge-amber'}" style="font-size:10px; padding:1px 6px; margin-left:6px; font-weight:700; white-space:nowrap;" title="Điểm Script Critic">⭐ ${overall.toFixed(1)}/10</span>`;
        }

        actionButtons += `<button class="btn btn-danger btn-sm" onclick="deleteJob(${v.id})" title="Xoá">🗑</button>`;

        return `
        <tr>
            <td><code>#${v.id}</code></td>
            <td>
                <div style="font-weight:600;color:var(--text-primary);max-width:250px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:flex;align-items:center;" title="${esc(v.title)}">
                    <span style="overflow:hidden;text-overflow:ellipsis;">${esc(v.title)}</span>${scoreBadge}
                </div>
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
   CREATE SCRIPT & SMART AI IDEA STUDIO
══════════════════════════════════════════════════════════ */
let _lastSmartChannelIdeas = [];

function fillQuickTopic(topic, directorNotes = '') {
    const input = document.getElementById('input-topic');
    if (!input) return;
    input.value = topic;
    input.focus();
    input.style.borderColor = 'var(--accent-cyan)';
    setTimeout(() => { input.style.borderColor = ''; }, 1200);

    if (directorNotes) {
        const notesEl = document.getElementById('input-director-notes');
        if (notesEl) {
            notesEl.value = directorNotes;
            notesEl.style.borderColor = '#38bdf8';
            setTimeout(() => { notesEl.style.borderColor = ''; }, 1200);
        }
    }
}

async function fetchSmartChannelIdeas() {
    const channelSel = document.getElementById('input-channel');
    const channelId = channelSel ? channelSel.value : '';
    if (!channelId) {
        showToast('⚠️ Vui lòng chọn kênh mục tiêu trước khi gợi ý ý tưởng!', 'warning');
        return;
    }

    const btn = document.getElementById('btn-suggest-channel-ideas');
    const loadingEl = document.getElementById('channel-ideas-loading');
    const gridEl = document.getElementById('channel-ideas-grid');
    const dnaBar = document.getElementById('channel-dna-summary-bar');

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<span class="loading-spinner"></span> Đang phân tích DNA kênh...`;
    }
    if (loadingEl) loadingEl.style.display = 'block';
    if (gridEl) gridEl.style.display = 'none';

    try {
        const data = await apiGet(`/api/channels/${encodeURIComponent(channelId)}/topics/suggest?count=5`);
        if (loadingEl) loadingEl.style.display = 'none';
        if (!data || !Array.isArray(data.ideas) || data.ideas.length === 0) {
            showToast('⚠️ Chưa lấy được gợi ý ý tưởng cho kênh này.', 'warning');
            return;
        }
        _lastSmartChannelIdeas = data.ideas;
        renderSmartChannelIdeas(data);
        showToast(`✅ Đã sinh ${data.ideas.length} ý tưởng độc quyền chuẩn ngôn ngữ (${data.language || 'en'})!`, 'success');
    } catch (e) {
        if (loadingEl) loadingEl.style.display = 'none';
        showToast('Lỗi khi phân tích ý tưởng kênh: ' + e.message, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `✨ Gợi Ý 5 Ý Tưởng Triệu View Cho Kênh Này`;
        }
    }
}

function renderSmartChannelIdeas(data) {
    const dnaBar = document.getElementById('channel-dna-summary-bar');
    const gridEl = document.getElementById('channel-ideas-grid');
    if (!gridEl) return;

    if (dnaBar) {
        const topV = data.top_performer;
        const flopV = data.flop_performer;
        const satList = Array.isArray(data.saturated_concepts) ? data.saturated_concepts : [];
        const langBadge = (data.language || 'en').toUpperCase();
        dnaBar.style.display = 'block';
        dnaBar.innerHTML = `
            <div style="display:flex; flex-wrap:wrap; gap:12px; align-items:center; justify-content:space-between;">
                <div>
                    <span class="badge badge-blue" style="margin-right:6px;">🌐 Ngôn ngữ: ${escapeHtml(langBadge)}</span>
                    <span class="badge badge-purple" style="margin-right:6px;">🎯 Ngách: ${escapeHtml(data.niche || 'general')}</span>
                    <span style="color:#94a3b8;">Đã xuất bản: <strong style="color:#fff;">${data.published_count || 0} video</strong></span>
                </div>
                ${topV ? `<div style="color:#34d399; font-size:11.5px;">🏆 <strong>Top 1 DNA:</strong> "${escapeHtml(topV.title)}" (<strong>${(topV.views || 0).toLocaleString()} views</strong>)</div>` : ''}
            </div>
            ${satList.length > 0 ? `
                <div style="margin-top:6px; padding-top:6px; border-top:1px dashed rgba(255,255,255,0.1); color:#fca5a5; font-size:11.5px;">
                    🚫 <strong>Khái niệm đã bão hòa (AI tự động né tránh):</strong> ${satList.map(k => `<code style="background:rgba(239,68,68,0.15); color:#fecaca; padding:1px 6px; border-radius:4px; margin-right:4px;">${escapeHtml(k)}</code>`).join('')}
                </div>
            ` : ''}
        `;
    }

    const ideas = data.ideas || [];
    gridEl.style.display = 'grid';
    gridEl.innerHTML = ideas.map((item, idx) => {
        const titleTxt = item.viral_title || item.topic || `Ý tưởng #${idx + 1}`;
        const hookTxt = item.hook_3s || item.hook || '';
        const thumbTxt = item.thumbnail_text || 'WATCH TILL END';
        const retTxt = item.predicted_retention || '85% - 92%';
        const whyTxt = item.why_viral || '';
        const fixTxt = item.fixes_flaw || '';
        const formulaTxt = item.viral_formula || 'Winning DNA';

        return `
            <div style="background:rgba(15,23,42,0.75); border:1px solid rgba(59,130,246,0.28); border-radius:10px; padding:13px; display:flex; flex-direction:column; justify-content:space-between; gap:10px;">
                <div>
                    <div style="display:flex; justify-content:space-between; align-items:center; gap:6px; margin-bottom:6px; flex-wrap:wrap;">
                        <span class="badge badge-purple" style="font-size:10.5px;">#${idx + 1} · ${escapeHtml(formulaTxt)}</span>
                        <span class="badge badge-green" style="font-size:10.5px;">📈 Retention: ${escapeHtml(retTxt)}</span>
                    </div>
                    <div style="font-weight:700; font-size:13.5px; color:#f8fafc; line-height:1.35; margin-bottom:6px;">
                        ${escapeHtml(titleTxt)}
                    </div>
                    ${hookTxt ? `
                        <div style="font-size:11.5px; color:#93c5fd; background:rgba(59,130,246,0.08); border-left:3px solid #3b82f6; padding:6px 8px; border-radius:4px; margin-bottom:6px; line-height:1.4;">
                            <strong>⚡ Hook 0-3s:</strong> "${escapeHtml(hookTxt)}"
                        </div>
                    ` : ''}
                    <div style="display:flex; align-items:center; gap:6px; margin-bottom:6px; font-size:11px;">
                        <span style="color:#fde047; font-weight:700;">🖼️ Thumb:</span>
                        <code style="background:rgba(234,179,8,0.18); color:#fef08a; font-weight:800; padding:2px 6px; border-radius:4px;">${escapeHtml(thumbTxt)}</code>
                    </div>
                    ${whyTxt ? `<div style="font-size:11.5px; color:#cbd5e1; line-height:1.35; margin-bottom:4px;">💡 <strong>Vì sao thắng:</strong> ${escapeHtml(whyTxt)}</div>` : ''}
                    ${fixTxt ? `<div style="font-size:11px; color:#6ee7b7; line-height:1.35;">🛠️ <strong>Khắc phục flop:</strong> ${escapeHtml(fixTxt)}</div>` : ''}
                </div>
                <div style="display:flex; gap:6px; margin-top:4px;">
                    <button type="button" class="btn btn-secondary btn-sm" style="flex:1; font-size:11.5px; padding:6px 8px;" onclick="applySmartIdeaToCreator(${idx}, false)">
                        🎯 Nạp Vào Ô Chủ Đề
                    </button>
                    <button type="button" class="btn btn-primary btn-sm" style="flex:1.2; font-size:11.5px; padding:6px 8px; background:linear-gradient(135deg,#3b82f6,#8b5cf6); border:none; font-weight:700;" onclick="applySmartIdeaToCreator(${idx}, true)">
                        🚀 Tạo Kịch Bản Ngay
                    </button>
                </div>
            </div>
        `;
    }).join('');
}

function applySmartIdeaToCreator(idx, autoGenerate = false) {
    const idea = _lastSmartChannelIdeas[idx];
    if (!idea) return;

    const topicInput = document.getElementById('input-topic');
    const notesInput = document.getElementById('input-director-notes');

    const bestTopic = idea.viral_title || idea.topic || '';
    const notesParts = [];
    if (idea.topic && idea.viral_title && idea.topic !== idea.viral_title) {
        notesParts.push(`Triển khai cốt truyện: ${idea.topic}`);
    }
    if (idea.hook_3s || idea.hook) {
        notesParts.push(`Bắt buộc dùng câu Hook 0-3s: "${idea.hook_3s || idea.hook}"`);
    }
    if (idea.thumbnail_text) {
        notesParts.push(`Chữ Thumbnail 1 giây: "${idea.thumbnail_text}"`);
    }
    if (idea.pinned_comment) {
        notesParts.push(`Câu hỏi ghim bình luận: "${idea.pinned_comment}"`);
    }
    if (idea.fixes_flaw) {
        notesParts.push(`Lưu ý chống flop: ${idea.fixes_flaw}`);
    }

    if (topicInput) {
        topicInput.value = bestTopic;
        topicInput.style.borderColor = '#10b981';
        setTimeout(() => { topicInput.style.borderColor = ''; }, 1500);
    }
    if (notesInput) {
        notesInput.value = notesParts.join(' | ');
        notesInput.style.borderColor = '#38bdf8';
        setTimeout(() => { notesInput.style.borderColor = ''; }, 1500);
    }

    if (typeof checkTopicDuplication === 'function') {
        checkTopicDuplication(bestTopic);
    }

    if (autoGenerate) {
        createScript();
    } else {
        showToast('🎯 Đã nạp Tiêu đề + Hook 3s + Chỉ đạo đạo diễn vào khung soạn kịch bản!', 'success');
    }
}

async function createScript() {
    const inputTopic = document.getElementById('input-topic');
    const topic = inputTopic ? inputTopic.value.trim() : '';
    if (!topic) {
        if (inputTopic) {
            inputTopic.focus();
            inputTopic.style.borderColor = '#ef4444';
            inputTopic.style.boxShadow = '0 0 0 3px rgba(239, 68, 68, 0.35)';
            setTimeout(() => {
                inputTopic.style.borderColor = '';
                inputTopic.style.boxShadow = '';
            }, 2000);
        }
        showToast('⚠️ Vui lòng nhập chủ đề video (hoặc bấm "✨ Gợi Ý 5 Ý Tưởng Triệu View" ở trên)!', 'warning');
        return;
    }

    const channel = document.getElementById('input-channel').value;
    const language = document.getElementById('input-language').value;
    const directorNotesEl = document.getElementById('input-director-notes');
    const director_notes = directorNotesEl ? directorNotesEl.value.trim() : '';

    const btn = document.getElementById('btn-create-script');
    btn.disabled = true;
    btn.innerHTML = `<span class="loading-spinner"></span> Đang phân tích & tạo kịch bản...`;

    try {
        const result = await apiPost('/api/scripts/create', {
            topic,
            channel_id: channel,
            language,
            director_notes: director_notes || undefined
        });

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

async function createManualScript() {
    const inputTopic = document.getElementById('input-topic');
    let title = inputTopic ? inputTopic.value.trim() : '';
    if (!title) {
        title = prompt('Nhập tiêu đề hoặc chủ đề cho kịch bản thủ công:', 'Kịch bản Shorts mới') || '';
        title = title.trim();
        if (!title) return;
        if (inputTopic) inputTopic.value = title;
    }

    const channel = document.getElementById('input-channel').value;
    const language = document.getElementById('input-language').value;

    const btn = document.getElementById('btn-create-manual-script');
    const origHTML = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<span class="loading-spinner"></span> Đang tạo...`;
    }

    try {
        const result = await apiPost('/api/scripts/create-manual', {
            title: title,
            topic: title,
            channel_id: channel,
            language: language
        });

        if (!result) return;

        lastCreatedScriptResult = result;

        // Hiển thị panel kết quả kịch bản
        document.getElementById('result-job-badge').textContent = `Job #${result.job_id} (Soạn thủ công)`;
        document.getElementById('script-result-panel').style.display = 'flex';
        document.getElementById('script-result-content').innerHTML = buildScriptResultHTML(result);
        document.getElementById('script-result-panel').scrollIntoView({ behavior: 'smooth' });

        showToast(`✍️ Đã khởi tạo kịch bản #${result.job_id}! Bạn có thể chỉnh sửa lời thoại ngay.`, 'success');
        fetchVideos(); // refresh pipeline

        // Tự động mở ngay Modal chỉnh sửa để người dùng nhập lời thoại và tùy chỉnh
        setTimeout(() => {
            openScriptEditor(result.job_id);
        }, 300);
    } catch (e) {
        showToast('Lỗi khi tạo kịch bản thủ công: ' + e.message, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = origHTML;
        }
    }
}

function buildScriptResultHTML(result) {
    let evalData = result.eval || result.quality_scores;
    if (typeof evalData === 'string') {
        try { evalData = JSON.parse(evalData); } catch (e) { evalData = null; }
    }
    let evalBannerHTML = '';
    if (evalData && (evalData.overall_score !== undefined || evalData.overall !== undefined)) {
        const overall = evalData.overall_score !== undefined ? evalData.overall_score : evalData.overall;
        const hook = evalData.hook_strength !== undefined ? evalData.hook_strength : (evalData.hook || 0);
        const pacing = evalData.story_pacing !== undefined ? evalData.story_pacing : (evalData.pacing || 0);
        const visual = evalData.visual_direction !== undefined ? evalData.visual_direction : (evalData.visual || 0);
        const passed = evalData.passed !== undefined ? evalData.passed : (overall >= 8.0);
        const critiques = evalData.actionable_critiques || [];

        evalBannerHTML = `
        <div style="background: linear-gradient(135deg, rgba(16, 185, 129, 0.12), rgba(59, 130, 246, 0.1)); border: 1px solid rgba(16, 185, 129, 0.35); border-radius: 12px; padding: 14px 18px; margin: 14px 0;">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom: 8px;">
                <div style="display:flex; align-items:center; gap:8px;">
                    <span style="font-size:20px;">🎯</span>
                    <div>
                        <div style="font-weight:700; color:#34d399; font-size:13.5px; display:flex; align-items:center; gap:8px;">
                            <span>Script Critic Đã Duyệt Đạt Chuẩn: ${overall.toFixed(1)}/10</span>
                            <span class="badge ${passed ? 'badge-green' : 'badge-amber'}">${passed ? 'ĐẠT CHUẨN VIRAL' : 'CẦN CẢI THIỆN'}</span>
                        </div>
                        <div style="font-size:11px; color:var(--text-muted); margin-top:2px;">Kịch bản đã vượt qua bài kiểm tra chất lượng tự động trước khi xuất ra màn hình</div>
                    </div>
                </div>
                <div style="display:flex; gap:12px; font-size:12px; background:rgba(0,0,0,0.25); padding:6px 12px; border-radius:8px;">
                    <span>⚡ Hook 3s: <strong style="color:#60a5fa;">${hook.toFixed(1)}/10</strong></span>
                    <span>⏱️ Nhịp độ: <strong style="color:#a78bfa;">${pacing.toFixed(1)}/10</strong></span>
                    <span>👁️ Thị giác: <strong style="color:#f472b6;">${visual.toFixed(1)}/10</strong></span>
                </div>
            </div>
            ${critiques.length > 0 ? `
                <div style="font-size:11.5px; color:#cbd5e1; background:rgba(0,0,0,0.2); padding:8px 12px; border-radius:8px; line-height:1.4; margin-top:6px;">
                    <strong>💡 Gợi ý từ Critic:</strong> ${critiques.map(c => esc(c)).join(' • ')}
                </div>
            ` : ''}
        </div>`;
    }

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

        ${evalBannerHTML}

        <!-- Quick AI Feedback box right on the result card -->
        <div style="background: linear-gradient(135deg, rgba(139, 92, 246, 0.12), rgba(236, 72, 153, 0.08)); border: 1px solid rgba(139, 92, 246, 0.3); border-radius: 12px; padding: 14px 18px; margin: 14px 0;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 8px;">
                <div style="font-weight: 700; color: #c084fc; font-size: 13px; display:flex; align-items:center; gap:6px;">
                    <span>🤖 Góp Ý Cho AI Viết Lại Kịch Bản & Tự Động Chấm Điểm</span>
                </div>
                <span style="font-size:11px; color:var(--text-muted);">AI sẽ tiếp thu góp ý, tái cấu trúc kịch bản và chấm điểm lại</span>
            </div>
            <div style="display:flex; gap:8px;">
                <input type="text" id="result-quick-feedback-input" class="form-input" placeholder="Ví dụ: Đổi câu hook giật gân hơn, thêm tình tiết bí ẩn ở cảnh 3, nhịp độ dồn dập hơn..." style="font-size:12.5px; height:38px;" onkeydown="if(event.key==='Enter') submitQuickFeedbackFromCard(${result.job_id})">
                <button type="button" class="btn btn-primary" id="btn-quick-feedback-${result.job_id}" onclick="submitQuickFeedbackFromCard(${result.job_id})" style="background:linear-gradient(135deg,#8b5cf6,#ec4899); border:none; white-space:nowrap; font-weight:600; font-size:12px; padding:0 18px;">
                    ✨ Viết Lại & Chấm Điểm
                </button>
            </div>
        </div>

        <!-- Action Toolbar Right After Generation -->
        <div style="display:flex; gap:10px; flex-wrap:wrap; margin:16px 0; padding:12px 14px; background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.08); border-radius:10px; align-items:center;">
            <button class="btn btn-secondary" onclick="openScriptEditor(${result.job_id})" style="font-weight:700; padding:9px 18px; background:linear-gradient(135deg, rgba(234,179,8,0.15), rgba(249,115,22,0.15)); border-color:rgba(234,179,8,0.5); color:#facc15; box-shadow:0 2px 10px rgba(234,179,8,0.15);">
                ✏️ Bước 1: Chỉnh Sửa Kịch Bản (Thêm / Sửa / Xóa Cảnh)
            </button>
            <button class="btn btn-secondary" onclick="openInstantPreviewFromCreate()" style="background:linear-gradient(135deg, rgba(236,72,153,0.15), rgba(139,92,246,0.15)); border-color:rgba(236,72,153,0.45); color:#f472b6; font-weight:700; padding:9px 18px;">
                👁️ Xem Trước Nhịp Đọc & B-Roll
            </button>
            <button class="btn btn-primary" onclick="triggerWebRender(${result.job_id})" style="background:linear-gradient(135deg,#3b82f6,#8b5cf6); border:none; font-weight:700; padding:9px 20px; box-shadow:0 3px 14px rgba(59,130,246,0.35);">
                🚀 Bước 2: Duyệt & Tạo Video (Render)
            </button>
            <button class="btn btn-secondary" onclick="switchTab('pipeline')" style="font-size:12px; padding:9px 14px; margin-left:auto;">
                📋 Xem trong Pipeline →
            </button>
            <button class="btn btn-danger btn-sm" onclick="deleteJob(${result.job_id}); document.getElementById('script-result-panel').style.display='none';" style="padding:9px 14px;">
                🗑️ Xóa Kịch Bản
            </button>
        </div>

        <div>
            <div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--text-muted);margin-bottom:10px;">Phân Cảnh Chi Tiết</div>
            <div class="scenes-list">${scenesHTML}</div>
        </div>

        <!-- Bottom Action Bar -->
        <div style="display:flex; justify-content:space-between; align-items:center; margin-top:20px; padding-top:14px; border-top:1px solid rgba(255,255,255,0.08); flex-wrap:wrap; gap:12px;">
            <button class="btn btn-secondary btn-lg" onclick="openScriptEditor(${result.job_id})" style="font-weight:700; border-color:rgba(234,179,8,0.5); color:#facc15;">
                ✏️ Chỉnh Sửa Kịch Bản Này (Thêm / Sửa Cảnh)
            </button>
            <button class="btn btn-primary btn-lg" onclick="triggerWebRender(${result.job_id})" style="background:linear-gradient(135deg,#3b82f6,#8b5cf6); border:none; font-weight:700; box-shadow:0 4px 18px rgba(59,130,246,0.35);">
                🚀 Duyệt Kịch Bản & Bắt Đầu Tạo Video Ngay →
            </button>
        </div>`;
}

async function submitQuickFeedbackFromCard(jobId) {
    const input = document.getElementById('result-quick-feedback-input');
    const feedback = input ? input.value.trim() : '';
    if (!feedback) {
        showToast('Vui lòng nhập góp ý để AI chỉnh sửa kịch bản!', 'error');
        if (input) input.focus();
        return;
    }

    const btn = document.getElementById(`btn-quick-feedback-${jobId}`);
    const origHTML = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<span class="loading-spinner"></span> Đang viết lại & chấm điểm...`;
    }

    try {
        const res = await fetch(`/api/videos/${jobId}/ai-refine`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ feedback: feedback })
        });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.detail || data.message || 'Lỗi khi tinh chỉnh kịch bản');
        }

        showToast(`✨ Kịch bản #${jobId} đã được AI viết lại theo góp ý và đạt chuẩn!`, 'success');
        data.total_duration = (data.scenes || []).reduce((sum, s) => sum + (s.estimated_seconds || 3.5), 0);
        data.scene_count = (data.scenes || []).length;
        document.getElementById('script-result-content').innerHTML = buildScriptResultHTML(data);
        await fetchVideos();
    } catch (e) {
        showToast('Lỗi AI tinh chỉnh: ' + e.message, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = origHTML;
        }
    }
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

    let evalDetailHTML = '';
    let qScore = data.quality_scores;
    if (typeof qScore === 'string') {
        try { qScore = JSON.parse(qScore); } catch (e) { qScore = null; }
    }
    if (qScore && (qScore.overall !== undefined || qScore.overall_score !== undefined)) {
        const overall = qScore.overall !== undefined ? qScore.overall : qScore.overall_score;
        const hook = qScore.hook !== undefined ? qScore.hook : (qScore.hook_strength || 0);
        const pacing = qScore.pacing !== undefined ? qScore.pacing : (qScore.story_pacing || 0);
        const visual = qScore.visual !== undefined ? qScore.visual : (qScore.visual_direction || 0);
        const passed = qScore.passed !== undefined ? qScore.passed : (overall >= 8.0);
        const critiques = qScore.actionable_critiques || [];
        evalDetailHTML = `
        <div class="detail-row" style="background: linear-gradient(135deg, rgba(16, 185, 129, 0.08), rgba(59, 130, 246, 0.08)); border: 1px solid rgba(16, 185, 129, 0.25); border-radius: 8px; padding: 12px 14px;">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px; margin-bottom:6px;">
                <div style="font-weight:700; color:#34d399; font-size:13px; display:flex; align-items:center; gap:6px;">
                    <span>🎯 Điểm Script Critic: ${overall.toFixed(1)}/10</span>
                    <span class="badge ${passed ? 'badge-green' : 'badge-amber'}">${passed ? 'ĐẠT CHUẨN VIRAL' : 'CẦN CẢI THIỆN'}</span>
                </div>
                <div style="display:flex; gap:10px; font-size:11.5px; color:var(--text-secondary);">
                    <span>⚡ Hook: <strong style="color:#60a5fa;">${hook.toFixed(1)}</strong></span>
                    <span>⏱️ Nhịp độ: <strong style="color:#a78bfa;">${pacing.toFixed(1)}</strong></span>
                    <span>👁️ Thị giác: <strong style="color:#f472b6;">${visual.toFixed(1)}</strong></span>
                </div>
            </div>
            ${critiques.length > 0 ? `<div style="font-size:11px; color:#cbd5e1; margin-top:4px;"><strong>💡 Gợi ý:</strong> ${critiques.map(c => esc(c)).join(' • ')}</div>` : ''}
        </div>`;
    }

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
            <div class="detail-value" id="detail-video-title-${data.id}" style="font-weight:600;font-size:16px;">${esc(data.title) || '—'}</div>
        </div>
        <div class="detail-row" style="background:rgba(59,130,246,0.07);border:1px solid rgba(59,130,246,0.25);border-radius:10px;padding:12px 14px;margin-bottom:10px;">
            <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:8px;">
                <div style="font-weight:700;color:#60a5fa;font-size:12.5px;display:flex;align-items:center;gap:6px;">
                    <span>🏷️ Thẻ Tags & Hashtags Video</span>
                    ${data.youtube_id ? `<span class="badge badge-green" style="font-size:10px;">Đồng bộ trực tiếp YouTube (${esc(data.youtube_id)})</span>` : `<span class="badge badge-purple" style="font-size:10px;">Tự động gắn khi Upload</span>`}
                </div>
                <button type="button" class="btn btn-secondary btn-sm" onclick="autoSuggestAndApplyVideoTags(${data.id})" style="font-size:11px;padding:3px 9px;border-color:rgba(59,130,246,0.4);color:#60a5fa;font-weight:600;">
                    ✨ Tự Động Gắn Tag Chuẩn Ngách
                </button>
            </div>
            <div id="detail-tags-chips-${data.id}" style="display:flex;gap:5px;flex-wrap:wrap;margin-bottom:8px;">
                ${(data.tags ? data.tags.split(',').map(t => t.trim()).filter(Boolean) : []).map(t => `<span style="font-size:11px;padding:2px 8px;border-radius:12px;background:rgba(59,130,246,0.18);color:#93c5fd;border:1px solid rgba(59,130,246,0.3);">#${esc(t.replace(/^#+/, ''))}</span>`).join('') || '<span style="font-size:11px;color:var(--text-muted);">Chưa có tag — bấm Gợi Ý hoặc nhập thêm bên dưới</span>'}
            </div>
            <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
                <input type="text" id="detail-add-tags-input-${data.id}" class="form-input" style="flex:1;min-width:200px;font-size:12px;padding:6px 10px;" placeholder="Nhập thêm tag (VD: wildlife, honey badger, predator...)" onkeydown="if(event.key==='Enter'){event.preventDefault();addCustomTagsToVideo(${data.id});}">
                <button type="button" class="btn btn-primary btn-sm" onclick="addCustomTagsToVideo(${data.id})" style="font-size:11.5px;padding:6px 12px;font-weight:600;">
                    ➕ Gắn Thêm Tag
                </button>
            </div>
        </div>
        ${evalDetailHTML}
        ${data.error_message && data.status === 'FAILED' ? `
        <div class="detail-row">
            <div class="detail-label" style="color:var(--accent-rose);">Lỗi</div>
            <div class="detail-value script-box" style="color:var(--accent-rose);">${esc(data.error_message)}</div>
        </div>` : ''}
        ${(data.status === 'RENDERED' || data.status === 'PUBLISHED') && data.video_path ? `
        <div class="detail-row" style="background:rgba(16,185,129,0.08);border:1px solid rgba(16,185,129,0.3);border-radius:12px;padding:14px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px;">
                <div style="font-weight:700;color:var(--accent-green);font-size:13px;display:flex;align-items:center;gap:6px;">
                    <span>🎬 Video Thành Phẩm (Shorts 9:16)</span>
                </div>
                <div style="display:flex;gap:6px;">
                    <button class="btn btn-sm btn-success" onclick="closeModal(); playRenderedVideo(${data.id});" style="font-size:11px;font-weight:700;padding:3px 10px;background:linear-gradient(135deg,#10b981,#059669);border:none;color:#fff;">
                        ▶️ Xem Toàn Màn Hình
                    </button>
                    <a href="/api/videos/${data.id}/download" target="_blank" class="btn btn-primary btn-sm" style="font-size:11px;padding:3px 10px;">
                        📥 Tải Video MP4
                    </a>
                </div>
            </div>
            <div style="display:flex;justify-content:center;background:#000;border-radius:10px;padding:8px;margin-bottom:10px;">
                <video controls playsinline style="max-height:280px;max-width:100%;aspect-ratio:9/16;border-radius:8px;background:#000;" src="/api/videos/${data.id}/stream" onerror="this.parentElement.innerHTML='<div style=\'color:#f87171;font-size:12px;padding:12px;text-align:center;\'>⚠️ Video được render trên máy đồng đội hoặc file không khả dụng trên máy local này.</div>'"></video>
            </div>

            <!-- Thumbnail High-CTR Preview & Download -->
            <div style="background:rgba(0,0,0,0.3); border:1px solid rgba(255,255,255,0.1); border-radius:8px; padding:12px; margin-top:8px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; flex-wrap:wrap; gap:8px;">
                    <span style="font-size:12.5px; font-weight:700; color:#facc15; display:flex; align-items:center; gap:6px;">
                        <span>🖼️ Thumbnail High-CTR ("Thị Trường 1 Giây")</span>
                    </span>
                    <button class="btn btn-secondary btn-sm" onclick="promptRegenerateThumbnail(${data.id})" style="font-size:11px; padding:4px 9px;">
                        ⚡ Tạo Lại Thumbnail Mới
                    </button>
                </div>
                <div style="display:flex; gap:16px; align-items:flex-start; flex-wrap:wrap;">
                    <div style="flex:1.4; min-width:200px; text-align:center;">
                        <div style="font-size:11px; color:var(--text-muted); margin-bottom:6px; font-weight:600;">Chuẩn Ngang 16:9 (YouTube Standard)</div>
                        <img src="/api/videos/${data.id}/thumbnail?aspect_ratio=16x9&t=${Date.now()}" onerror="this.style.display='none'" style="width:100%; border-radius:6px; border:1px solid rgba(255,255,255,0.15); box-shadow:0 4px 12px rgba(0,0,0,0.4);" alt="Thumbnail 16:9">
                        <div style="margin-top:8px;">
                            <a href="/api/videos/${data.id}/thumbnail?aspect_ratio=16x9" target="_blank" class="btn btn-secondary btn-sm" style="font-size:11px; padding:4px 10px;">
                                📥 Tải Ảnh 16:9 HD
                            </a>
                        </div>
                    </div>
                    <div style="width:130px; text-align:center;">
                        <div style="font-size:11px; color:var(--text-muted); margin-bottom:6px; font-weight:600;">Dọc 9:16 (Shorts)</div>
                        <img src="/api/videos/${data.id}/thumbnail?aspect_ratio=9x16&t=${Date.now()}" onerror="this.style.display='none'" style="max-height:160px; width:auto; border-radius:6px; border:1px solid rgba(255,255,255,0.15); box-shadow:0 4px 12px rgba(0,0,0,0.4);" alt="Thumbnail 9:16">
                        <div style="margin-top:8px;">
                            <a href="/api/videos/${data.id}/thumbnail?aspect_ratio=9x16" target="_blank" class="btn btn-secondary btn-sm" style="font-size:11px; padding:4px 10px;">
                                📥 Tải Ảnh 9:16
                            </a>
                        </div>
                    </div>
                </div>
            </div>
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
            ${(data.status === 'SCRIPT_READY' || data.status === 'FAILED') ? `<button class="btn btn-secondary btn-sm" onclick="closeModal(); openStoryboardModal(${data.id});" style="color:#38bdf8; border-color:rgba(56,189,248,0.4); font-weight:600;">🎞️ Mở Storyboard Swapper</button>` : ''}
            ${(data.status === 'SCRIPT_READY' || data.status === 'FAILED') ? `<button class="btn btn-secondary btn-sm" onclick="closeModal(); openInstantPreviewForJob(${data.id});" style="color:#f472b6; border-color:rgba(236,72,153,0.4); font-weight:600;">👁️ Xem Trước Nhanh</button>` : ''}
            ${(data.status === 'SCRIPT_READY' || data.status === 'FAILED') ? `<button class="btn btn-primary btn-sm" onclick="closeModal(); triggerWebRender(${data.id});">🚀 ${data.status === 'FAILED' ? 'Thử Render Lại' : 'Bắt Đầu Render'}</button>` : ''}
            ${(data.status === 'SCRIPT_READY' || data.status === 'FAILED') ? `<button class="btn btn-secondary btn-sm" onclick="closeModal(); openScriptEditor(${data.id});">✏️ Chỉnh Sửa Kịch Bản</button>` : ''}
            ${(data.status === 'RENDERED' || data.status === 'PUBLISHED') && data.video_path ? `<a href="/api/videos/${data.id}/download" target="_blank" class="btn btn-primary btn-sm">📥 Tải Video</a>` : ''}
            ${((data.status === 'RENDERED' || data.status === 'PUBLISHED') && !data.youtube_id) ? `
            <button class="btn btn-primary btn-sm" id="btn-publish-yt-${data.id}" onclick="openPublishModal(${data.id})" style="background:linear-gradient(135deg,#ef4444,#dc2626);border:none;font-weight:600;">
                🚀 Chọn Kênh & Đăng YouTube
            </button>` : ''}
            ${((data.status === 'RENDERED' || data.status === 'PUBLISHED') && data.youtube_id) ? `
            <button class="btn btn-secondary btn-sm" onclick="openPublishModal(${data.id})" style="font-size:11.5px; border-color:rgba(239,68,68,0.4); color:#ef4444; font-weight:600;">
                🔄 Đăng Lại / Đăng Kênh Khác
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

/* ===============================================================
   RENDERED SHORTS VIDEO PLAYER MODAL
================================================================ */
async function playRenderedVideo(jobId) {
    const modal = document.getElementById('modal-rendered-player');
    const vidEl = document.getElementById('rendered-video-element');
    const titleEl = document.getElementById('rendered-player-title');
    const durEl = document.getElementById('rendered-player-duration');
    const fallbackBox = document.getElementById('rendered-player-fallback');
    const fallbackTitle = document.getElementById('rendered-player-fallback-title');
    const fallbackMsg = document.getElementById('rendered-player-fallback-msg');
    const downloadBtn = document.getElementById('rendered-player-download-btn');
    const detailBtn = document.getElementById('rendered-player-detail-btn');
    const publishBtn = document.getElementById('rendered-player-publish-btn');

    if (!modal || !vidEl) return;

    // Reset video state
    vidEl.pause();
    vidEl.removeAttribute('src');
    vidEl.load();
    vidEl.style.display = 'block';
    if (fallbackBox) fallbackBox.style.display = 'none';

    titleEl.textContent = `Video #${jobId} — Đang tải...`;
    durEl.textContent = '';
    downloadBtn.href = `/api/videos/${jobId}/download`;
    detailBtn.onclick = () => { closeRenderedPlayerModal(); openVideoDetail(jobId); };
    publishBtn.onclick = () => { closeRenderedPlayerModal(); openPublishModal(jobId); };

    modal.style.display = 'flex';
    modal.classList.add('visible');

    // Lấy thông tin chi tiết video
    try {
        const data = await apiGet(`/api/videos/${jobId}`);
        if (data) {
            titleEl.textContent = data.title || `Video #${jobId}`;
            if (data.audio_duration_ms) {
                durEl.textContent = `⏱️ ${(data.audio_duration_ms / 1000).toFixed(1)}s`;
            } else if (data.scenes && data.scenes.length) {
                durEl.textContent = `🎬 ${data.scenes.length} cảnh`;
            }
        }
    } catch (e) {
        console.warn('Lỗi lấy thông tin video:', e);
    }

    // Set streaming URL
    const streamUrl = `/api/videos/${jobId}/stream?t=${Date.now()}`;
    vidEl.src = streamUrl;

    vidEl.onerror = () => {
        vidEl.style.display = 'none';
        if (fallbackBox) {
            fallbackBox.style.display = 'block';
            fallbackTitle.textContent = 'Không Thể Phát Video Trực Tiếp';
            fallbackMsg.innerHTML = `File video có thể được dựng trên máy đồng đội hoặc đường dẫn không khả dụng trên máy local này.<br><br>
                <a href="/api/videos/${jobId}/download" class="btn btn-secondary btn-sm" style="display:inline-flex;margin-top:6px;">📥 Thử Tải Trực Tiếp</a>`;
        }
    };

    vidEl.play().catch(err => {
        console.log('Video autoplay caught:', err);
    });
}

function closeRenderedPlayerModal() {
    const modal = document.getElementById('modal-rendered-player');
    const vidEl = document.getElementById('rendered-video-element');
    if (vidEl) {
        vidEl.pause();
        vidEl.removeAttribute('src');
        vidEl.load();
    }
    if (modal) {
        modal.style.display = 'none';
        modal.classList.remove('visible');
    }
}

document.addEventListener('keydown', e => { 
    if (e.key === 'Escape') {
        closeModal();
        closeScriptEditor();
        closeRenderedPlayerModal();
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
    const fbInput = document.getElementById('editor-ai-feedback-input');
    if (fbInput) fbInput.value = '';
    const badge = document.getElementById('editor-ai-score-badge');
    if (badge) badge.style.display = 'none';
    const critiquesEl = document.getElementById('editor-ai-critiques');
    if (critiquesEl) critiquesEl.style.display = 'none';

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

        let qScore = data.quality_scores;
        if (typeof qScore === 'string') {
            try { qScore = JSON.parse(qScore); } catch (e) { qScore = null; }
        }
        if (qScore && (qScore.overall !== undefined || qScore.overall_score !== undefined)) {
            const overall = qScore.overall !== undefined ? qScore.overall : qScore.overall_score;
            const hook = qScore.hook !== undefined ? qScore.hook : (qScore.hook_strength || 0);
            const pacing = qScore.pacing !== undefined ? qScore.pacing : (qScore.story_pacing || 0);
            const visual = qScore.visual !== undefined ? qScore.visual : (qScore.visual_direction || 0);
            const passed = qScore.passed !== undefined ? qScore.passed : (overall >= 8.0);
            if (badge) {
                badge.style.display = 'inline-block';
                badge.className = `badge ${passed ? 'badge-green' : 'badge-amber'}`;
                badge.textContent = `🎯 Script Critic: ${overall.toFixed(1)}/10 (Hook: ${hook.toFixed(1)} | Pacing: ${pacing.toFixed(1)} | Visual: ${visual.toFixed(1)})`;
            }
            if (critiquesEl) {
                const critList = qScore.actionable_critiques || [];
                if (critList.length > 0) {
                    critiquesEl.style.display = 'block';
                    critiquesEl.innerHTML = `<strong>💡 Đánh giá từ Critic:</strong> ` + critList.map(c => esc(c)).join(' • ');
                } else {
                    critiquesEl.style.display = 'none';
                }
            }
        }

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

async function submitAiScriptRefinement() {
    const jobId = document.getElementById('editor-job-id').value;
    if (!jobId) return;

    const feedbackInput = document.getElementById('editor-ai-feedback-input');
    const feedback = feedbackInput ? feedbackInput.value.trim() : '';
    if (!feedback) {
        showToast('Vui lòng nhập nội dung góp ý chỉnh sửa kịch bản!', 'error');
        if (feedbackInput) feedbackInput.focus();
        return;
    }

    const btn = document.getElementById('btn-editor-ai-refine');
    const origHTML = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<span class="loading-spinner"></span> Đang tinh chỉnh & chấm điểm lại...`;
    }

    try {
        const res = await fetch(`/api/videos/${jobId}/ai-refine`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ feedback: feedback })
        });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.detail || data.message || 'Lỗi khi tinh chỉnh kịch bản');
        }

        showToast(`✨ Kịch bản #${jobId} đã được AI viết lại và chấm điểm đạt chuẩn!`, 'success');
        if (feedbackInput) feedbackInput.value = '';

        if (data.title) document.getElementById('editor-input-title').value = data.title;
        if (data.hook) document.getElementById('editor-input-hook').value = data.hook;
        if (data.tags) document.getElementById('editor-input-tags').value = data.tags;

        if (data.scenes && data.scenes.length > 0) {
            renderEditorScenes(data.scenes);
        }

        const badge = document.getElementById('editor-ai-score-badge');
        const critiquesEl = document.getElementById('editor-ai-critiques');
        let qScore = data.quality_scores;
        if (typeof qScore === 'string') {
            try { qScore = JSON.parse(qScore); } catch (e) { qScore = null; }
        }
        if (qScore && (qScore.overall !== undefined || qScore.overall_score !== undefined)) {
            const overall = qScore.overall !== undefined ? qScore.overall : qScore.overall_score;
            const hook = qScore.hook !== undefined ? qScore.hook : (qScore.hook_strength || 0);
            const pacing = qScore.pacing !== undefined ? qScore.pacing : (qScore.story_pacing || 0);
            const visual = qScore.visual !== undefined ? qScore.visual : (qScore.visual_direction || 0);
            const passed = qScore.passed !== undefined ? qScore.passed : (overall >= 8.0);
            if (badge) {
                badge.style.display = 'inline-block';
                badge.className = `badge ${passed ? 'badge-green' : 'badge-amber'}`;
                badge.textContent = `🎯 Script Critic: ${overall.toFixed(1)}/10 (Hook: ${hook.toFixed(1)} | Pacing: ${pacing.toFixed(1)} | Visual: ${visual.toFixed(1)})`;
            }
            if (critiquesEl) {
                const critList = qScore.actionable_critiques || [];
                if (critList.length > 0) {
                    critiquesEl.style.display = 'block';
                    critiquesEl.innerHTML = `<strong>💡 Đánh giá từ Critic:</strong> ` + critList.map(c => esc(c)).join(' • ');
                } else {
                    critiquesEl.style.display = 'none';
                }
            }
        }

        // Cập nhật result panel nếu đang mở cho job này
        const resultBadge = document.getElementById('result-job-badge');
        if (resultBadge && resultBadge.textContent.includes(jobId)) {
            data.total_duration = (data.scenes || []).reduce((sum, s) => sum + (s.estimated_seconds || 3.5), 0);
            data.scene_count = (data.scenes || []).length;
            document.getElementById('script-result-content').innerHTML = buildScriptResultHTML(data);
        }

        await fetchVideos();
    } catch (e) {
        showToast('Lỗi AI tinh chỉnh: ' + e.message, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = origHTML;
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

    // Set description & tags
    const descInput = document.getElementById('publish-desc-input');
    descInput.value = job.description || (job.tags ? job.tags.split(',').map(t => `#${t.trim()}`).join(' ') : '#shorts #viral');
    const tagsInput = document.getElementById('publish-tags-input');
    if (tagsInput) {
        tagsInput.value = job.tags || '';
        if (!tagsInput.value) {
            suggestTagsForPublishModal(true);
        }
    }

    // Reset pinned comment
    const commentInput = document.getElementById('publish-pinned-comment-input');
    if (commentInput) {
        commentInput.value = '';
    }

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
            const isExpired = Boolean(ch.token_expired);
            const statusIcon = isExpired ? '🔴' : (isAuth ? '🟢' : '🟡');
            const statusText = isExpired ? '(Token đã hết hạn - Cần cấp lại)' : (isAuth ? '(Đã kết nối)' : '(Chưa cấp quyền)');
            const gmail = ch.gmail || ch.email_label || ch.google_account;
            opt.textContent = `${statusIcon} ${ch.name} ── [${gmail}] ${statusText}`;
            sel.appendChild(opt);
        });

        // Set default selection: Ưu tiên kênh đang kết nối tốt và chưa hết hạn
        const isCurrentAuth = job.channel_id && allChannels.some(c => c.id === job.channel_id && (c.is_authenticated || c.youtube_connected) && !c.token_expired);
        if (isCurrentAuth) {
            sel.value = job.channel_id;
        } else {
            const firstValidAuth = allChannels.find(c => (c.is_authenticated || c.youtube_connected) && !c.token_expired);
            if (firstValidAuth) {
                sel.value = firstValidAuth.id;
            } else if (job.channel_id && allChannels.some(c => c.id === job.channel_id)) {
                sel.value = job.channel_id;
            } else {
                sel.value = allChannels[0].id;
            }
        }
    }

    const confirmBtn = document.getElementById('btn-confirm-publish');
    if (confirmBtn) {
        confirmBtn.disabled = false;
    }

    onPublishChannelChange();

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
            confirmBtn.title = 'Bấm để xuất bản video lên kênh này';
            confirmBtn.innerHTML = `🚀 Xác Nhận Đăng Lên YouTube`;
            confirmBtn.style.background = 'linear-gradient(135deg, #ef4444, #dc2626)';
        }
    } else {
        const isExp = Boolean(ch.token_expired);
        if (infoBox) {
            infoBox.style.background = 'rgba(245, 158, 11, 0.15)';
            infoBox.style.borderColor = 'rgba(245, 158, 11, 0.35)';
        }
        if (infoText) {
            infoText.innerHTML = isExp
                ? `🔴 <strong>${esc(ch.name)}</strong>: Token Google đã hết hạn/thu hồi. Bấm nút bên phải hoặc bấm 'Cấp Quyền' bên dưới để cấp lại trong 10s!`
                : `⚠️ <strong>${esc(ch.name)}</strong>: Chưa cấp quyền YouTube riêng. Bấm nút bên phải để kết nối nhanh!`;
            infoText.style.color = 'var(--accent-amber)';
        }
        if (actionBtn) {
            actionBtn.innerHTML = `
                <button type="button" class="btn btn-primary btn-sm" onclick="startChannelAuth(decodeURIComponent('${encodeURIComponent(ch.id)}'), decodeURIComponent('${encodeURIComponent(ch.name)}'))" style="font-size:11.5px; background:var(--accent-amber); border-color:var(--accent-amber); font-weight:600;">
                    🔑 Cấp Quyền YouTube (1 Chạm)
                </button>
            `;
        }
        if (confirmBtn) {
            confirmBtn.disabled = false; // Luôn cho phép bấm, không bao giờ disable làm người dùng tưởng đơ web
            confirmBtn.title = 'Kênh này cần cấp quyền YouTube trước khi đăng. Bấm để kết nối Google 1-chạm.';
            confirmBtn.innerHTML = `🔑 Cấp Quyền YouTube & Đăng Video`;
            confirmBtn.style.background = 'linear-gradient(135deg, #f59e0b, #d97706)';
        }
    }

    // Tự động tải thông tin khe giờ vàng tiếp theo cho kênh đã chọn
    fetchGoldenSlotPreview(selectedId);
    if (activePublishJob && activePublishJob.id) {
        fetchMonetizationPreview(activePublishJob.id, selectedId);
    }
}

let currentMonetizationBundle = null;

async function fetchMonetizationPreview(jobId, channelId) {
    const nameEl = document.getElementById('publish-affiliate-name');
    const ctaEl = document.getElementById('publish-affiliate-cta');
    const linkEl = document.getElementById('publish-affiliate-link');
    const tagEl = document.getElementById('publish-affiliate-tag');
    const pinnedCommentInput = document.getElementById('publish-pinned-comment-input');
    const descInput = document.getElementById('publish-desc-input');

    if (!jobId) return;
    if (nameEl) nameEl.textContent = 'Đang phân tích kịch bản & khớp sản phẩm Amazon...';

    try {
        const url = `/api/videos/${jobId}/monetization-preview` + (channelId ? `?channel_id=${encodeURIComponent(channelId)}` : '');
        const res = await fetch(url);
        const data = await res.json();
        if (data.success && data.product) {
            currentMonetizationBundle = data;
            if (nameEl) nameEl.textContent = data.product.name;
            if (ctaEl) ctaEl.textContent = `🎯 ${data.product.cta}`;
            if (linkEl) {
                linkEl.href = data.product.affiliate_url;
                linkEl.textContent = data.product.affiliate_url;
            }
            if (tagEl) tagEl.textContent = `Tag: ${data.product.tracking_tag || 'kizakawa-20'}`;

            // Điền bình luận mồi nếu ô bình luận đang trống
            if (pinnedCommentInput && (!pinnedCommentInput.value || pinnedCommentInput.value.trim() === '')) {
                pinnedCommentInput.value = data.enriched_pinned_comment || '';
            }

            // Nếu mô tả chưa có link amazon thì tự động chèn vào
            if (descInput && !descInput.value.includes('amazon.com') && !descInput.value.includes(data.product.asin)) {
                if (data.enriched_description) {
                    descInput.value = data.enriched_description;
                }
            }
        }
    } catch (e) {
        console.warn('Lỗi lấy preview monetization:', e);
    }
}

function applyAffiliateToDescription() {
    if (!currentMonetizationBundle || !currentMonetizationBundle.enriched_description) {
        showToast('Chưa có thông tin sản phẩm affiliate để áp dụng', 'info');
        return;
    }
    const descInput = document.getElementById('publish-desc-input');
    if (descInput) {
        descInput.value = currentMonetizationBundle.enriched_description;
        showToast('✅ Đã cập nhật link Affiliate Amazon vào mô tả!', 'success');
    }
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
            el.textContent = 'Tự động tính 1 trong 4 khung giờ vàng chuẩn US (06:00, 10:00, 20:00, 00:30 VN)';
        }
    } catch (e) {
        el.textContent = 'Tự động tính 1 trong 4 khung giờ vàng chuẩn US (06:00, 10:00, 20:00, 00:30 VN)';
    }
}

async function submitPublishYouTube() {
    const jobId = document.getElementById('publish-job-id').value;
    const channelId = document.getElementById('publish-channel-select').value;
    const title = document.getElementById('publish-title-input').value.trim();
    const description = document.getElementById('publish-desc-input').value.trim();
    const pinnedComment = document.getElementById('publish-pinned-comment-input')?.value?.trim() || null;
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
        showToast(`🔑 Kênh '${ch?.name || channelId}' chưa cấp quyền hoặc Token đã hết hạn. Đang mở trang kết nối Google OAuth...`, 'warning');
        startChannelAuth(channelId, ch?.name || channelId);
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
        const tagsVal = document.getElementById('publish-tags-input')?.value?.trim() || '';
        const res = await fetch(`/api/videos/${jobId}/publish-youtube`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                channel_id: channelId,
                title: title,
                description: description,
                tags: tagsVal,
                pinned_comment: pinnedComment,
                privacy: privacy,
                auto_schedule: isAutoSchedule,
                schedule_time_iso: isAutoSchedule ? 'auto' : null
            })
        });

        const data = await res.json();
        if (res.ok && data.success) {
            let msg = `🎉 ${data.message || 'Đăng video lên YouTube thành công!'}`;
            if (data.product_info && data.product_info.name) {
                msg += ` ── 🛒 Gắn link Amazon: ${data.product_info.name} (Tag: ${data.product_info.tracking_tag || 'kizakawa-20'})`;
            }
            showToast(msg, 'success');
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
// allAccounts and allChannels hoisted to top
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
    if (typeof updateTrendsChannelDropdown === 'function') {
        updateTrendsChannelDropdown(channels);
    }
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
    } else if (channels.length > 0) {
        sel.value = channels[0].id;
    }

    const syncChannelLanguage = (channelId) => {
        const selectedCh = channels.find(c => c.id === channelId);
        if (selectedCh && selectedCh.language) {
            const langSel = document.getElementById('input-language');
            if (langSel) {
                const rawLang = selectedCh.language.toLowerCase();
                if (rawLang.startsWith('vi')) {
                    langSel.value = 'vi';
                } else if (rawLang.startsWith('pt')) {
                    langSel.value = 'pt-BR';
                } else {
                    langSel.value = 'en';
                }
            }
        }
    };

    sel.onchange = () => {
        syncChannelLanguage(sel.value);
        const gridEl = document.getElementById('channel-ideas-grid');
        const dnaBar = document.getElementById('channel-dna-summary-bar');
        if (gridEl) gridEl.style.display = 'none';
        if (dnaBar) dnaBar.style.display = 'none';
    };

    if (sel.value) {
        syncChannelLanguage(sel.value);
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
                    ${(() => {
                        const nObj = (allNiches || []).find(n => n.id === ch.niche);
                        const nText = nObj ? `${esc(nObj.icon || '🎯')} ${esc(nObj.badge || nObj.name)}` : esc(ch.niche || 'general');
                        return `<span class="hub-chip" style="border-color:rgba(245,158,11,0.35); background:rgba(245,158,11,0.1); color:#fbbf24; cursor:pointer;" onclick="openEditNicheModal('${esc(ch.niche || 'general')}')" title="Bấm để xem & tinh chỉnh khuôn mẫu ngách này"><strong>Ngách:</strong> ${nText}</span>`;
                    })()}
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
                        <button class="btn btn-primary btn-sm" style="background:var(--accent-amber); border-color:var(--accent-amber);" onclick="startChannelAuth('${esc(ch.id)}', '${esc(ch.name)}', '${esc(ch.google_account)}')" title="Đăng nhập Google cấp quyền YouTube cho kênh này">
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

let _batchPollTimer = null;

async function triggerBatchProduce6Channels(countPerChannel = 2) {
    const box = document.getElementById('batch-produce-status-box');
    const btn = document.getElementById('btn-batch-produce-6');
    if (box) {
        box.style.display = 'block';
        box.innerHTML = `<div style="margin-top:12px; padding:12px; background:rgba(16,185,129,0.1); border:1px solid rgba(16,185,129,0.3); border-radius:8px; color:#6ee7b7; font-size:12.5px;">🚀 Đang khởi động tiến trình sản xuất tự động ${countPerChannel * 6} video cho 6 kênh chủ lực (loại trừ Paws & Hearts)...</div>`;
    }
    try {
        const res = await apiPost('/api/channels/batch-auto-produce', { channels: 'all_6', count_per_channel: countPerChannel });
        showToast(res?.message || 'Đã khởi chạy sản xuất hàng loạt 6 kênh!', 'success');
        if (_batchPollTimer) clearInterval(_batchPollTimer);
        _batchPollTimer = setInterval(pollBatchProduceStatus, 5000);
        await pollBatchProduceStatus();
    } catch (e) {
        if (box) box.innerHTML = `<div style="margin-top:12px; padding:12px; color:var(--accent-rose);">❌ Lỗi khởi chạy: ${esc(e.message || String(e))}</div>`;
    }
}

async function pollBatchProduceStatus() {
    const box = document.getElementById('batch-produce-status-box');
    if (!box) return;
    try {
        const res = await apiGet('/api/channels/batch-auto-produce/status');
        const st = res?.state;
        if (!st) return;
        box.style.display = 'block';
        const itemsHtml = (st.results || []).map(r => r.error
            ? `<div style="color:#f87171; font-size:12px;">❌ <strong>${esc(r.channel)}:</strong> ${esc(r.error)}</div>`
            : `<div style="color:#34d399; font-size:12px;">✅ <strong>[Job #${r.job_id}] ${esc(r.channel)}:</strong> ${esc(r.title)} — <a href="${esc(r.youtube_url)}" target="_blank" style="color:#60a5fa;">${esc(r.youtube_url)}</a> (${esc(r.scheduled_time)})</div>`
        ).join('');
        box.innerHTML = `
            <div style="margin-top:12px; padding:12px 14px; background:rgba(15,23,42,0.8); border:1px solid rgba(16,185,129,0.3); border-radius:10px;">
                <div style="font-weight:700; font-size:13px; color:#e2e8f0; margin-bottom:6px;">
                    ${st.running ? `⏳ Đang sản xuất tự động (${st.completed_count}/${st.total_target}) — Đang xử lý: <span style="color:#fbbf24;">${esc(st.current_channel)}</span>` : `🎉 Hoàn tất sản xuất tự động (${st.completed_count}/${st.total_target} video)`}
                </div>
                <div style="display:flex; flex-direction:column; gap:4px;">${itemsHtml}</div>
            </div>
        `;
        if (!st.running && _batchPollTimer) {
            clearInterval(_batchPollTimer);
            _batchPollTimer = null;
        }
    } catch (e) {}
}

async function runLiveOAuthMatrixCheck() {
    const box = document.getElementById('live-oauth-matrix-box');
    const btn = document.getElementById('btn-live-oauth-matrix');
    if (!box) return;
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '⏳ Đang kiểm tra trực tiếp 7 kênh với Google OAuth...';
    }
    box.style.display = 'block';
    box.innerHTML = `<div style="padding:16px; text-align:center; color:var(--text-secondary);">🔄 Đang kết nối trực tiếp tới máy chủ Google OAuth để kiểm tra sức khỏe Refresh Token và Ma trận 3 Project Quota...</div>`;
    try {
        const res = await apiGet('/api/channels/oauth-live-check');
        if (!res || !res.success) {
            box.innerHTML = `<div style="padding:14px; color:var(--accent-rose);">❌ Không thể kiểm tra trạng thái OAuth Live.</div>`;
            return;
        }
        const projs = res.accounts || ['account_1', 'acount_2', 'accout3'];
        const rows = (res.channels || []).map((ch, idx) => {
            const projCells = projs.map(p => {
                const linked = ch.project_matrix && ch.project_matrix[p];
                const encId = encodeURIComponent(ch.id);
                const encName = encodeURIComponent(ch.name);
                const encProj = encodeURIComponent(p);
                return `<td style="text-align:center; padding:8px;">
                    <button type="button" class="btn btn-sm ${linked ? 'btn-secondary' : 'btn-primary'}"
                        style="font-size:11px; padding:4px 8px; ${linked ? 'border-color:rgba(16,185,129,0.4); color:#34d399;' : 'background:rgba(245,158,11,0.2); border-color:rgba(245,158,11,0.5); color:#fbbf24;'}"
                        onclick="startChannelAuth(decodeURIComponent('${encId}'), decodeURIComponent('${encName}'), decodeURIComponent('${encProj}'))"
                        title="Bấm để cấp quyền / làm mới OAuth kênh '${esc(ch.name)}' với Project '${esc(p)}'">
                        ${linked ? '🟢 Đã liên kết' : '➕ Cấp quyền'}
                    </button>
                </td>`;
            }).join('');
            const encIdMain = encodeURIComponent(ch.id);
            const encNameMain = encodeURIComponent(ch.name);
            const encAccMain = encodeURIComponent(ch.primary_account || 'account_1');
            return `<tr style="border-bottom:1px solid rgba(255,255,255,0.06);">
                <td style="padding:8px 10px; font-weight:600;">#${idx + 1} · 📺 ${esc(ch.name)}</td>
                <td style="padding:8px 10px;"><code>${esc(ch.primary_account)}</code></td>
                <td style="padding:8px 10px; color:${ch.is_valid ? '#34d399' : '#f87171'}; font-weight:600;">${esc(ch.status_text)}</td>
                ${projCells}
                <td style="padding:8px 10px; text-align:right;">
                    <button type="button" class="btn btn-primary btn-sm" style="font-size:11px;"
                        onclick="startChannelAuth(decodeURIComponent('${encIdMain}'), decodeURIComponent('${encNameMain}'), decodeURIComponent('${encAccMain}'))">
                        🔑 Cấp Quyền Ngay
                    </button>
                </td>
            </tr>`;
        }).join('');

        box.innerHTML = `
            <div style="margin-top:12px; background:rgba(15,23,42,0.75); border:1px solid rgba(96,165,250,0.25); border-radius:10px; padding:14px; overflow-x:auto;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; flex-wrap:wrap; gap:8px;">
                    <div style="font-weight:700; color:#e2e8f0; font-size:13.5px;">
                        🩺 Kết Quả Kiểm Định Live OAuth: <span style="color:#34d399;">${res.valid_count}/${res.total_count} Kênh Hoạt Động Tốt</span>
                    </div>
                    <span style="font-size:11.5px; color:var(--text-muted);">💡 Bấm vào từng ô Project để liên kết xoay vòng Quota (30.000 Quota/ngày/kênh)</span>
                </div>
                <table style="width:100%; border-collapse:collapse; font-size:12.5px;">
                    <thead>
                        <tr style="border-bottom:1px solid rgba(255,255,255,0.12); color:var(--text-secondary); text-align:left;">
                            <th style="padding:8px 10px;">Kênh YouTube</th>
                            <th style="padding:8px 10px;">Project Chính</th>
                            <th style="padding:8px 10px;">Trạng Thái Token Live</th>
                            ${projs.map(p => `<th style="padding:8px; text-align:center;">Pool: ${esc(p)}</th>`).join('')}
                            <th style="padding:8px 10px; text-align:right;">Thao Tác</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        `;
    } catch (e) {
        box.innerHTML = `<div style="padding:14px; color:var(--accent-rose);">❌ Lỗi kiểm tra OAuth Live: ${esc(e.message || String(e))}</div>`;
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '🩺 Kiểm Tra Sức Khỏe Token Live & Ma Trận Quota (7 Kênh)';
        }
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

    const wizardBanner = `
        <div class="account-card" style="margin-bottom:18px; background:linear-gradient(135deg, rgba(30,41,59,0.9), rgba(15,23,42,0.95)); border:1px solid rgba(59,130,246,0.3);">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
                <div>
                    <h4 style="font-size:15px; color:#60a5fa; margin-bottom:4px;">🔐 Trung Tâm Xác Thực OAuth Tự Động & Ma Trận Quota Đa Dự Án (Web Wizard)</h4>
                    <p style="font-size:12.5px; color:var(--text-secondary); margin:0;">
                        Tích hợp toàn bộ tính năng kiểm tra Token Live, tự động định tuyến chống chọn nhầm kênh và liên kết Ma trận 7 Kênh × 3 Google Cloud Project ngay trên Web.
                    </p>
                </div>
                <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
                    <button id="btn-batch-produce-6" type="button" class="btn btn-primary btn-sm" onclick="triggerBatchProduce6Channels(2)" style="font-weight:600; padding:8px 14px; background:linear-gradient(135deg,#10b981,#059669); border-color:#059669;">
                        🚀 Sản Xuất Tự Động 6 Kênh (2 Video/Kênh)
                    </button>
                    <button id="btn-live-oauth-matrix" type="button" class="btn btn-primary btn-sm" onclick="runLiveOAuthMatrixCheck()" style="font-weight:600; padding:8px 14px;">
                        🩺 Kiểm Tra Sức Khỏe Token Live & Ma Trận Quota (7 Kênh)
                    </button>
                </div>
            </div>
            <div id="batch-produce-status-box" style="display:none;"></div>
            <div id="live-oauth-matrix-box" style="display:none;"></div>
        </div>
    `;

    container.innerHTML = wizardBanner + accounts.map(acc => {
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
                                            <span>🎯 <strong>Chủ đề:</strong> ${ch.niche ? esc(ch.niche) : '<em style="color:var(--text-muted)">✨ Tự do (Đa chủ đề)</em>'}</span>
                                            <span>🌐 <strong>Ngôn ngữ:</strong> ${esc(ch.language || 'en').toUpperCase()}</span>
                                            <span>🎙️ <strong>Giọng:</strong> ${esc(ch.preferred_voice || 'Default')}</span>
                                            <span>🖼️ <strong>Visual:</strong> ${ch.visual_mode === 'auto' ? '🤖 AI Tự chọn' : (ch.visual_mode === 'imagen_ai' ? '✨ Imagen 3' : '🎞️ Pexels')}</span>
                                            <span>⏰ <strong>Đăng:</strong> ${ch.schedule?.publish_hour_utc ?? 12}:00 UTC</span>
                                        </div>
                                    </div>
                                    <div class="channel-row-actions" style="display:flex; gap:6px; align-items:center; flex-wrap:wrap;">
                                        ${!isAuth ? `
                                            <button class="btn btn-primary btn-sm" onclick="startChannelAuth(decodeURIComponent('${encodeURIComponent(ch.id)}'), decodeURIComponent('${encodeURIComponent(ch.name)}'))" title="Cấp quyền đăng video cho kênh này" style="background:linear-gradient(135deg,rgba(239,68,68,0.9),rgba(220,38,38,0.9)); border-color:#dc2626; font-size:11.5px; font-weight:600;">
                                                🔑 Cấp Quyền YouTube (Kênh Này)
                                            </button>
                                        ` : `
                                            <button class="btn btn-secondary btn-sm" onclick="startChannelAuth(decodeURIComponent('${encodeURIComponent(ch.id)}'), decodeURIComponent('${encodeURIComponent(ch.name)}'))" title="Ủy quyền lại kênh này nếu cần" style="font-size:11px;">
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
    updateChannelNicheSelectOptions(allNiches);
    document.getElementById('channel-mode').value = 'add';
    document.getElementById('modal-channel-title').textContent = 'Thêm Kênh YouTube Mới';
    document.getElementById('channel-input-id').value = '';
    document.getElementById('channel-input-id').disabled = false;
    document.getElementById('channel-input-name').value = '';
    
    // Mặc định: Không bắt buộc chọn hồ sơ ngách (cho phép rỗng)
    const nicheSel = document.getElementById('channel-input-niche');
    if (nicheSel) {
        nicheSel.value = '';
    }

    document.getElementById('channel-input-lang').value = 'en';
    document.getElementById('channel-input-visual').value = 'auto';
    document.getElementById('channel-input-hour').value = '12';
    document.getElementById('modal-channel').classList.add('visible');
    setTimeout(() => {
        document.getElementById('channel-input-name')?.focus();
    }, 150);
}

function openEditChannelModal(accountId, channelId) {
    populateParentAccountSelect(accountId);
    updateChannelNicheSelectOptions(allNiches);
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
    
    const nicheSel = document.getElementById('channel-input-niche');
    if (nicheSel) {
        nicheSel.value = ch.niche || '';
    }

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
    const niche = document.getElementById('channel-input-niche').value.trim(); // Có thể rỗng nếu không chọn
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
        niche: niche || '',
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
let currentOAuthSessionKey = '';

async function startChannelAuth(channelId, channelName, accountId = '') {
    let authWin = null;
    try {
        // Mở trước tab mới trực tiếp trong sự kiện click của người dùng để tránh bị trình duyệt Edge/Chrome chặn popup
        authWin = window.open('about:blank', '_blank');
        if (authWin) {
            authWin.document.title = "Đang kết nối Google...";
            authWin.document.body.innerHTML = `
                <div style="font-family:system-ui,-apple-system,sans-serif; text-align:center; padding:80px 20px; color:#1e293b; background:#f8fafc; min-height:100vh;">
                    <div style="font-size:48px; margin-bottom:16px;">⏳</div>
                    <h2 style="font-size:22px; margin-bottom:8px; color:#0f172a;">Đang mở trang đăng nhập Google...</h2>
                    <p style="color:#64748b; font-size:14px;">Trình duyệt đang kết nối máy chủ Google OAuth an toàn, vui lòng đợi 1 giây...</p>
                </div>
            `;
        }
    } catch (e) {
        console.warn("Popup pre-open:", e);
    }

    try {
        currentOAuthSessionKey = 'channel:' + channelId + ':youtube';
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
                1. Trình duyệt đã mở trang Google trên tab mới, hãy chọn Gmail của bạn.<br>
                2. Một danh sách các <strong>Kênh YouTube / Brand Account</strong> sẽ hiện ra:<br>
                👉 <strong>HÃY BẤM CHỌN ĐÚNG KÊNH "${esc(channelName || channelId)}"!</strong><br>
                <em>(Tuyệt đối không chọn kênh cũ để tránh đăng nhầm video).</em><br>
                3. Bấm <strong>Cho phép / Allow</strong> để hoàn tất.
            </div>
            Đang tải trang đăng nhập Google...
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

        // Khởi động OAuth cho kênh này (hỗ trợ chỉ định Project account_id để mở rộng Quota Pool)
        const accQuery = accountId ? `&account_id=${encodeURIComponent(accountId)}` : '';
        const res = await apiPost(`/api/channels/${encodeURIComponent(channelId)}/start-auth?service=youtube${accQuery}`, {});
        if (!res || !res.success) {
            if (authWin && !authWin.closed) authWin.close();
            showToast(res?.detail || 'Không thể khởi động xác thực cho kênh này', 'error');
            closeOAuthModal();
            return;
        }

        // Nếu có auth_url trả về ngay lập tức, chuyển hướng tab popup ngay
        if (res.auth_url) {
            authLink.href = res.auth_url;
            linkBox.style.display = 'block';
            spinner.style.display = 'none';
            let openedTab = false;
            if (authWin && !authWin.closed) {
                try {
                    authWin.location.href = res.auth_url;
                    openedTab = true;
                } catch (e) {}
            }
            if (!openedTab) {
                try {
                    const w = window.open(res.auth_url, '_blank');
                    if (w) openedTab = true;
                } catch (e) {}
            }

            if (openedTab) {
                heading.innerHTML = `🌐 Đang Xác Thực Kênh: <span style="color:#60a5fa;">${esc(channelName || channelId)}</span>`;
                desc.innerHTML = `
                    <div style="background:rgba(245,158,11,0.12); border:1px solid rgba(245,158,11,0.3); border-radius:8px; padding:12px; margin-bottom:12px; text-align:left; font-size:12.5px; color:#fde68a; line-height:1.6;">
                        <strong>⚠️ LƯU Ý RẤT QUAN TRỌNG KHI ĐĂNG NHẬP GOOGLE:</strong><br>
                        1. Trình duyệt đã mở trang Google ở tab mới, hãy chọn Gmail của bạn.<br>
                        👉 <strong>2. HÃY BẤM CHỌN ĐÚNG KÊNH "${esc(channelName || channelId)}"!</strong><br>
                        3. Bấm <strong>Cho phép / Allow</strong> để hoàn tất.
                    </div>
                    <em>(Nếu tab đăng nhập không tự mở, hãy bấm nút to màu xanh bên dưới)</em>
                `;
            } else {
                heading.innerHTML = `⚠️ Trình Duyệt Đang Chặn Tab Tự Động`;
                desc.innerHTML = `
                    <div style="background:rgba(239,68,68,0.12); border:1px solid rgba(239,68,68,0.3); border-radius:8px; padding:12px; margin-bottom:12px; text-align:left; font-size:12.5px; color:#fca5a5; line-height:1.6;">
                        Trình duyệt của bạn đã chặn cửa sổ bật lên tự động.<br>
                        👉 <strong>VUI LÒNG BẤM VÀO NÚT BÊN DƯỚI ĐỂ MỞ TRANG ĐĂNG NHẬP:</strong>
                    </div>
                `;
            }
        }

        // Polling trạng thái mỗi 1.5 giây
        oauthPollTimer = setInterval(async () => {
            try {
                const statusData = await apiGet(`/api/channels/${encodeURIComponent(channelId)}/auth-status`);
                if (!statusData) return;

                if (statusData.auth_url && linkBox.style.display === 'none') {
                    authLink.href = statusData.auth_url;
                    linkBox.style.display = 'block';
                    spinner.style.display = 'none';
                    if (authWin && !authWin.closed && authWin.location.href.includes('about:blank')) {
                        authWin.location.href = statusData.auth_url;
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
                    spinner.style.display = 'none';
                    linkBox.style.display = 'block';
                    heading.textContent = '❌ Lỗi Cấp Quyền Kênh';
                    desc.innerHTML = 'Hệ thống chưa nhận được mã hợp lệ. Bạn có thể mở lại trang đăng nhập hoặc dán lại đường link vào ô bên dưới:';
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
        if (authWin && !authWin.closed) {
            try {
                authWin.document.body.innerHTML = `
                    <div style="font-family:system-ui,-apple-system,sans-serif; text-align:center; padding:60px 20px; color:#ef4444; background:#f8fafc; min-height:100vh;">
                        <div style="font-size:48px; margin-bottom:16px;">⚠️</div>
                        <h2 style="font-size:20px; margin-bottom:8px; color:#dc2626;">Không Thể Kết Nối Máy Chủ</h2>
                        <p style="color:#64748b; font-size:14px; max-width:450px; margin:0 auto 20px;">Lỗi: ${esc(e.message)}</p>
                        <p style="color:#94a3b8; font-size:13px;">Vui lòng quay lại tab Studio, tải lại trang và thử lại.</p>
                    </div>
                `;
            } catch (err) {
                try { authWin.close(); } catch (cl) {}
            }
        }
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
    let authWin = null;
    try {
        // Mở trước tab mới trực tiếp trong sự kiện click của người dùng để tránh bị trình duyệt Edge/Chrome chặn popup
        authWin = window.open('about:blank', '_blank');
        if (authWin) {
            authWin.document.title = "Đang kết nối Google...";
            authWin.document.body.innerHTML = `
                <div style="font-family:system-ui,-apple-system,sans-serif; text-align:center; padding:80px 20px; color:#1e293b; background:#f8fafc; min-height:100vh;">
                    <div style="font-size:48px; margin-bottom:16px;">⏳</div>
                    <h2 style="font-size:22px; margin-bottom:8px; color:#0f172a;">Đang mở trang đăng nhập Google...</h2>
                    <p style="color:#64748b; font-size:14px;">Trình duyệt đang kết nối máy chủ Google OAuth an toàn, vui lòng đợi 1 giây...</p>
                </div>
            `;
        }
    } catch (e) {
        console.warn("Popup pre-open:", e);
    }

    try {
        currentOAuthSessionKey = accountId + ':' + service;
        const res = await apiPost(`/api/accounts/${accountId}/auth-start`, { service: service });
        if (!res || !res.success) {
            if (authWin && !authWin.closed) authWin.close();
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
        heading.textContent = `Đăng nhập Google ${service.toUpperCase()}: ${accountId}`;
        desc.textContent = `Hệ thống đã mở trang đăng nhập Google trên tab mới. Hãy đăng nhập Gmail và bấm 'Cho Phép'.`;
        resultBox.style.display = 'none';
        spinner.style.display = '';

        if (res.auth_url) {
            authLink.href = res.auth_url;
            linkBox.style.display = 'block';
            spinner.style.display = 'none';
            let openedTab = false;
            if (authWin && !authWin.closed) {
                try {
                    authWin.location.href = res.auth_url;
                    openedTab = true;
                } catch (e) {}
            }
            if (!openedTab) {
                try {
                    const w = window.open(res.auth_url, '_blank');
                    if (w) openedTab = true;
                } catch (e) {}
            }

            if (openedTab) {
                heading.innerHTML = `🌐 Đang Xác Thực: <span style="color:#60a5fa;">${esc(accountId)} (${service.toUpperCase()})</span>`;
                desc.innerHTML = `
                    Đã mở tab mới để đăng nhập Google.<br>
                    Vui lòng chọn Gmail của bạn và bấm <strong>Cho phép (Allow)</strong>.<br>
                    <em style="color:#94a3b8; font-size:12px;">(Nếu tab không tự mở, hãy bấm nút xanh bên dưới).</em>
                `;
            } else {
                heading.innerHTML = `⚠️ Trình Duyệt Đang Chặn Tab Tự Động`;
                desc.innerHTML = `
                    <div style="background:rgba(239,68,68,0.12); border:1px solid rgba(239,68,68,0.3); border-radius:8px; padding:12px; margin-bottom:12px; text-align:left; font-size:12.5px; color:#fca5a5; line-height:1.6;">
                        Trình duyệt của bạn đã chặn cửa sổ bật lên tự động.<br>
                        👉 <strong>VUI LÒNG BẤM VÀO NÚT BÊN DƯỚI ĐỂ MỞ TRANG ĐĂNG NHẬP:</strong>
                    </div>
                `;
            }
        }

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

        // Polling trạng thái mỗi 1.5 giây
        let _oauthSuccessHandled = false;
        oauthPollTimer = setInterval(async () => {
            try {
                const statusData = await apiGet(`/api/accounts/${accountId}/auth-status?service=${service}`);
                if (!statusData || _oauthSuccessHandled) return;

                // Hiển thị link xác thực ngay khi server cung cấp auth_url
                if (statusData.auth_url && linkBox.style.display === 'none') {
                    authLink.href = statusData.auth_url;
                    linkBox.style.display = 'block';
                    spinner.style.display = 'none';
                    if (authWin && !authWin.closed && authWin.location.href.includes('about:blank')) {
                        authWin.location.href = statusData.auth_url;
                    }
                }

                if (statusData.status === 'success') {
                    _oauthSuccessHandled = true;
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
                    spinner.style.display = 'none';
                    linkBox.style.display = 'block';
                    heading.textContent = '❌ Lỗi Xác Thực';
                    desc.innerHTML = 'Hệ thống chưa nhận được mã hợp lệ. Bạn có thể mở lại trang đăng nhập Google hoặc dán lại mã code bên dưới:';
                    resultBox.style.display = 'block';
                    resultBox.style.background = 'rgba(251,113,133,0.15)';
                    resultBox.style.color = 'var(--accent-rose)';
                    resultBox.textContent = statusData.error || 'Lỗi không xác định.';
                }
            } catch (err) {
                console.warn('Polling OAuth error:', err);
            }
        }, 1500);

    } catch (e) {
        if (authWin && !authWin.closed) {
            try {
                authWin.document.body.innerHTML = `
                    <div style="font-family:system-ui,-apple-system,sans-serif; text-align:center; padding:60px 20px; color:#ef4444; background:#f8fafc; min-height:100vh;">
                        <div style="font-size:48px; margin-bottom:16px;">⚠️</div>
                        <h2 style="font-size:20px; margin-bottom:8px; color:#dc2626;">Không Thể Kết Nối Máy Chủ</h2>
                        <p style="color:#64748b; font-size:14px; max-width:450px; margin:0 auto 20px;">Lỗi: ${esc(e.message)}</p>
                        <p style="color:#94a3b8; font-size:13px;">Vui lòng quay lại tab Studio, tải lại trang và thử lại.</p>
                    </div>
                `;
            } catch (err) {
                try { authWin.close(); } catch (cl) {}
            }
        }
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
    const manualInput = document.getElementById('oauth-manual-input');
    if (manualInput) manualInput.value = '';
    currentOAuthSessionKey = '';
    if (modal) modal.classList.remove('visible');
    if (linkBox) linkBox.style.display = 'none';
    if (spinner) spinner.style.display = '';
}

// Lắng nghe sự kiện callback tự động 1-Click từ cửa sổ popup Google OAuth (Web application)
window.addEventListener('message', function(event) {
    if (event.data && (event.data.type === 'oauth_complete' || event.data.type === 'oauth_success')) {
        showToast('🎉 Đã cấp quyền Google thành công!', 'success');
        closeOAuthModal();
        fetchChannels();
        fetchAccounts();
        fetchStats();
    }
});

async function submitManualOAuthCode() {
    const input = document.getElementById('oauth-manual-input');
    let val = input ? input.value.trim() : '';
    if (!val) {
        showToast('Vui lòng dán link chuyển hướng hoặc mã code từ Google', 'error');
        return;
    }

    // Gỡ dấu ngoặc kép nếu user lỡ copy cả dấu ngoặc
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
        val = val.slice(1, -1).trim();
    }

    // Kiểm tra xem có phải link đăng nhập (login/consent) ban đầu không
    const isLoginLink = !val.includes('code=') && 
                        !val.includes('localhost') && 
                        !val.includes('127.0.0.1') && 
                        (val.startsWith('https://accounts.google.com') || val.includes('accounts.google.com/o/oauth2'));
    if (isLoginLink) {
        showToast('⚠️ Bạn đang dán link đăng nhập Google! Hãy mở link đó trên trình duyệt để lấy link localhost.', 'error');
        alert('⚠️ BẠN ĐANG DÁN LINK ĐĂNG NHẬP GOOGLE!\n\nCách lấy link chính xác:\n1. Bấm vào nút "🔗 Mở trang đăng nhập Google" ở trên.\n2. Chọn tài khoản Gmail của bạn và bấm [Tiếp tục] / [Cho Phép].\n3. Trình duyệt sẽ chuyển hướng đến một trang báo "Không thể truy cập trang web này (localhost)" - ĐÂY LÀ ĐÚNG!\n4. Bạn chỉ cần nhấn tổ hợp phím Ctrl + L (hoặc click thanh địa chỉ), nhấn Ctrl + A rồi Ctrl + C để COPY TOÀN BỘ ĐƯỜNG LINK (bắt đầu bằng http://localhost...), dán vào ô này rồi bấm Xác Nhận!');
        return;
    }

    // Kiểm tra xem link có bị cắt ngắn (...) do trình duyệt Edge/Chrome thu gọn không
    if (val.includes('...') || val.endsWith('…')) {
        alert('⚠️ ĐƯỜNG LINK BỊ CẮT THIẾU!\n\nBạn đã copy đường link bị thiếu ở cuối (có dấu "..." do trình duyệt Edge thu gọn).\n\n👉 Cách khắc phục:\nTại tab báo "Không thể truy cập localhost", hãy nhấn tổ hợp phím Ctrl + L (hoặc click chuột vào thanh địa chỉ), nhấn Ctrl + A (chọn tất cả), rồi Ctrl + C (sao chép) để lấy toàn bộ đường link!');
        return;
    }

    // Cảnh báo nếu dán link localhost nhưng thiếu tham số code=
    if ((val.startsWith('http://') || val.startsWith('https://') || val.startsWith('localhost')) && !val.includes('code=')) {
        alert('⚠️ ĐƯỜNG LINK THIẾU MÃ CODE!\n\nBạn chỉ mới copy phần đầu địa chỉ (http://localhost...). Đường link kết quả của Google bắt buộc phải có chứa "?code=4/...".\n\n👉 Cách lấy đúng:\n1. Mở lại tab báo "localhost refused to connect".\n2. Nhấn phím Ctrl + L (hoặc click vào thanh địa chỉ), nhấn Ctrl + A rồi Ctrl + C để sao chép toàn bộ.\n3. Dán lại vào đây rồi bấm Xác Nhận!');
        return;
    }

    // Trích xuất và kiểm tra định dạng mã Google trước khi gửi
    let extractedCode = val;
    const m = val.match(/code=([^&?\s#]+)/);
    if (m) {
        extractedCode = decodeURIComponent(m[1]);
    } else {
        extractedCode = decodeURIComponent(val.split('&')[0].split('?')[0].trim());
    }
    if (extractedCode.startsWith('code=')) extractedCode = extractedCode.slice(5).trim();
    if (extractedCode.startsWith('0A') && !extractedCode.startsWith('4/')) extractedCode = '4/' + extractedCode;

    if (!extractedCode.startsWith('4/')) {
        alert('⚠️ MÃ XÁC THỰC KHÔNG HỢP LỆ!\n\nMã xác thực Google OAuth bắt buộc phải bắt đầu bằng "4/".\nChuỗi bạn vừa dán không chứa mã Google hợp lệ.\n\nVui lòng kiểm tra lại đường link trên thanh địa chỉ của tab localhost!');
        return;
    }
    if (extractedCode.length < 35) {
        alert('⚠️ MÃ XÁC THỰC BỊ THIẾU KÝ TỰ!\n\nMã xác thực quá ngắn (' + extractedCode.length + ' ký tự). Mã Google hợp lệ dài từ 50 đến 100 ký tự.\n\n👉 Bạn hãy mở lại tab localhost, nhấn phím Ctrl + L rồi Ctrl + C để sao chép 100% đường link!');
        return;
    }

    if (!currentOAuthSessionKey) {
        showToast('Không tìm thấy phiên xác thực đang chạy. Vui lòng bấm Kết Nối lại.', 'error');
        return;
    }
    const btn = document.getElementById('btn-oauth-submit-manual');
    if (btn) btn.textContent = '⏳ Đang xác thực...';

    try {
        const res = await apiPost('/api/oauth/submit-callback', {
            session_key: currentOAuthSessionKey,
            callback_url_or_code: val
        });
        if (res && res.success) {
            showToast('✓ Đã gửi mã xác thực! Hệ thống đang lưu token...', 'success');
            const resultBox = document.getElementById('oauth-result-box');
            if (resultBox) resultBox.style.display = 'none';
            const heading = document.getElementById('oauth-status-heading');
            if (heading) heading.textContent = '⏳ Đang kiểm tra mã và lưu token Google...';
            const spinner = document.getElementById('oauth-spinner');
            if (spinner) spinner.style.display = '';
        } else {
            showToast(res?.detail || 'Không thể xử lý mã xác thực', 'error');
        }
    } catch (e) {
        showToast('Lỗi gửi mã xác thực: ' + e.message, 'error');
    } finally {
        if (btn) btn.textContent = 'Xác Nhận';
    }
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
    const avgViewsVal = data.avg_views || 300;
    const chNiche = data.channel_niche || 'general';
    if (tbody) {
        tbody.innerHTML = '';
        const vids = data.videos || [];
        if (vids.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:24px;">Chưa tìm thấy video nào trên kênh</td></tr>';
        } else {
            const subEl = document.getElementById('analytics-winning-subtitle');
            if (subEl && vids[0]) {
                subEl.innerHTML = `Các chủ đề và câu Hook 3s có xác suất giữ chân &gt;85% được học từ DNA video Top #1 <strong>"${escapeHtml(vids[0].title)}" (${vids[0].views_text})</strong> và lọc bỏ khái niệm bão hòa`;
            }
            vids.forEach((v, idx) => {
                const tr = document.createElement('tr');
                const rankBadge = idx === 0
                    ? '<span class="rank-badge-gold">#1 TOP</span>'
                    : (idx === 1 ? '<span class="rank-badge-silver">#2</span>' : (idx === 2 ? '<span class="rank-badge-bronze">#3</span>' : `<span class="badge" style="background:rgba(255,255,255,0.06);">#${idx + 1}</span>`));

                const vNum = v.views_num || 0;
                const hasNumber = /\d/.test(v.title || '');
                let aiBadge = '';
                if (idx === 0) {
                    aiBadge = `<span class="badge badge-green">🔥 Winning DNA (+${Math.max(10, Math.round(((vNum - avgViewsVal) / Math.max(avgViewsVal, 1)) * 100))}% vs TB)</span><div style="font-size:11.5px;color:var(--text-muted);margin-top:4px;">${hasNumber ? 'Có con số mỏ neo cụ thể & tương phản mạnh.' : 'Hook chạm đúng tò mò bản năng của tệp khán giả.'} Khuyên nhân bản cấu trúc nhịp điệu này sang chủ đề mới.</div>`;
                } else if (vNum >= avgViewsVal) {
                    aiBadge = `<span class="badge badge-blue">⭐ Giữ Chân Ổn Định (Trên TB Kênh)</span><div style="font-size:11.5px;color:var(--text-muted);margin-top:4px;">Tỷ lệ vượt ngưỡng Swipe-away tốt trong ngách <code>${escapeHtml(chNiche)}</code>.</div>`;
                } else if (vNum < avgViewsVal * 0.45) {
                    aiBadge = `<span class="badge" style="background:rgba(239,68,68,0.18);color:#fca5a5;">⚠️ Cảnh Báo Bão Hòa / Hook Chậm</span><div style="font-size:11.5px;color:var(--text-muted);margin-top:4px;">Views thấp hơn kỳ vọng do lặp lại từ khóa cũ hoặc thiếu cú sốc thị giác/thính giác ở 2 giây đầu.</div>`;
                } else {
                    aiBadge = `<span class="badge badge-purple">💡 Tiềm Năng Tối Ưu CTR</span><div style="font-size:11.5px;color:var(--text-muted);margin-top:4px;">Chủ đề tốt, cần đẩy nhanh tốc độ lời thoại (+6%) và thêm con số cụ thể vào tiêu đề.</div>`;
                }

                const cloneTitleSafe = (v.title || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
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
                        <div style="display:flex;flex-direction:column;gap:5px;">
                            <a href="${v.url}" target="_blank" class="btn btn-secondary btn-sm" style="display:inline-flex;align-items:center;justify-content:center;gap:4px;padding:4px 8px;font-size:11.5px;">
                                Xem Shorts ↗
                            </a>
                            ${idx === 0 ? `<button type="button" class="btn btn-primary btn-sm" style="padding:4px 8px;font-size:11px;background:linear-gradient(135deg,#8b5cf6,#3b82f6);border:none;" onclick="applyTopicToCreate('Nhân bản DNA từ video Top 1: ${cloneTitleSafe}', 'Giữ cấu trúc nhịp điệu của video #1 nhưng triển khai trên thực thể/bối cảnh mới 100%')">🧬 Nhân Bản DNA</button>` : ''}
                        </div>
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

let _lastAnalyticsRecommendedTopics = [];

function renderRecommendedTopics(topics) {
    const container = document.getElementById('recommended-topics-container');
    if (!container) return;
    container.innerHTML = '';
    _lastAnalyticsRecommendedTopics = Array.isArray(topics) ? topics : [];

    _lastAnalyticsRecommendedTopics.forEach((t, idx) => {
        const card = document.createElement('div');
        card.className = 'topic-rec-card';
        const titleTxt = t.viral_title || t.topic || `Chủ đề đề xuất #${idx + 1}`;
        const hookTxt = t.hook_3s || t.hook || '';
        const topicDetail = t.topic || '';
        const thumbTxt = t.thumbnail_text || '';
        const retTxt = t.predicted_retention || '86% - 93%';
        const whyTxt = t.why_viral || '';
        const fixTxt = t.fixes_flaw || '';
        const scoreVal = t.potential_score || 9.6;
        const targetViews = t.target_views || '40K - 180K';

        card.innerHTML = `
            <div>
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;flex-wrap:wrap;gap:6px;">
                    <span class="badge badge-blue">🎯 Điểm tiềm năng: ${scoreVal}/10</span>
                    <span class="badge badge-green" style="font-size:10.5px;">📈 Retention: ${escapeHtml(retTxt)}</span>
                    <span style="font-size:11.5px;color:var(--text-muted);">Kỳ vọng: <strong>${escapeHtml(targetViews)}</strong></span>
                </div>
                <div style="font-weight:700;font-size:14px;color:#f8fafc;margin-bottom:6px;line-height:1.35;">${escapeHtml(titleTxt)}</div>
                <div class="topic-hook-text" style="font-size:12px;color:#93c5fd;margin-bottom:6px;">⚡ <strong>Hook 0-3s:</strong> "${escapeHtml(hookTxt)}"</div>
                ${thumbTxt ? `<div style="font-size:11.5px;color:#fde047;margin-bottom:6px;">🖼️ <strong>Thumb 1s:</strong> <code style="background:rgba(234,179,8,0.18);color:#fef08a;padding:1px 6px;border-radius:4px;font-weight:700;">${escapeHtml(thumbTxt)}</code></div>` : ''}
                <div style="font-size:12px;color:var(--text-secondary);margin-top:4px;">👉 <strong>Cốt truyện:</strong> ${escapeHtml(topicDetail)}</div>
                ${whyTxt ? `<div style="font-size:11.5px;color:#a7f3d0;margin-top:5px;">💡 <strong>Vì sao thắng:</strong> ${escapeHtml(whyTxt)}</div>` : ''}
                ${fixTxt ? `<div style="font-size:11px;color:#fca5a5;margin-top:3px;">🛡️ <strong>Chống Flop:</strong> ${escapeHtml(fixTxt)}</div>` : ''}
            </div>
            <button class="btn btn-primary btn-sm" style="width:100%;margin-top:10px;" onclick="applyAnalyticsTopicByIdx(${idx})">
                🎬 Nạp Chủ Đề & Hook Này Vào Xưởng Kịch Bản
            </button>
        `;
        container.appendChild(card);
    });
}

function applyAnalyticsTopicByIdx(idx) {
    const t = _lastAnalyticsRecommendedTopics[idx];
    if (!t) return;
    const titleOrTopic = t.viral_title || t.topic || '';
    const hookTxt = t.hook_3s || t.hook || '';
    const notesParts = [];
    if (t.topic && t.viral_title && t.topic !== t.viral_title) {
        notesParts.push(`Triển khai cốt truyện: ${t.topic}`);
    }
    if (hookTxt) notesParts.push(`Bắt buộc dùng câu Hook 0-3s: "${hookTxt}"`);
    if (t.thumbnail_text) notesParts.push(`Chữ Thumbnail 1 giây: "${t.thumbnail_text}"`);
    if (t.fixes_flaw) notesParts.push(`Lưu ý chống flop: ${t.fixes_flaw}`);
    applyTopicToCreate(titleOrTopic, notesParts.join(' | '));
}

async function generateFreshAnalyticsTopics() {
    const chId = currentAnalyticsChannelId || document.getElementById('analytics-channel-select')?.value || '';
    if (!chId) {
        showToast('⚠️ Vui lòng chọn kênh phân tích trước!', 'warning');
        return;
    }
    const btn = document.getElementById('btn-analytics-live-ideas');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<span class="loading-spinner"></span> AI đang phân tích DNA kênh...`;
    }
    try {
        const res = await apiGet(`/api/channels/${encodeURIComponent(chId)}/topics/suggest?count=5`);
        if (res && Array.isArray(res.ideas) && res.ideas.length > 0) {
            const mapped = res.ideas.map(item => ({
                viral_title: item.viral_title || item.topic,
                topic: item.topic || item.viral_title,
                hook: item.hook_3s || item.hook || '',
                hook_3s: item.hook_3s || item.hook || '',
                thumbnail_text: item.thumbnail_text || '',
                predicted_retention: item.predicted_retention || '87% - 94%',
                why_viral: item.why_viral || '',
                fixes_flaw: item.fixes_flaw || '',
                potential_score: 9.8,
                target_views: '50K - 220K'
            }));
            renderRecommendedTopics(mapped);
            showToast(`✅ Đã sinh 5 ý tưởng mới chuẩn ngôn ngữ (${res.language || 'en'}) từ Gemini AI!`, 'success');
        }
    } catch (e) {
        showToast('Lỗi sinh ý tưởng AI: ' + e.message, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `✨ AI Sinh Thêm 5 Ý Tưởng Độc Quyền (Live Gemini)`;
        }
    }
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

function applyTopicToCreate(topic, hookOrNotes = '') {
    switchTab('create');
    if (typeof currentAnalyticsChannelId !== 'undefined' && currentAnalyticsChannelId) {
        const chSel = document.getElementById('input-channel');
        if (chSel && chSel.querySelector(`option[value="${currentAnalyticsChannelId}"]`)) {
            chSel.value = currentAnalyticsChannelId;
            if (typeof chSel.onchange === 'function') chSel.onchange();
        }
    }
    const topicInput = document.getElementById('input-topic') || document.getElementById('prompt-topic');
    const notesInput = document.getElementById('input-director-notes');
    if (topicInput) {
        topicInput.value = topic;
        topicInput.focus();
        topicInput.style.borderColor = '#10b981';
        setTimeout(() => { topicInput.style.borderColor = ''; }, 1500);
    }
    if (notesInput && hookOrNotes) {
        notesInput.value = hookOrNotes.includes('Hook') || hookOrNotes.includes('Triển khai')
            ? hookOrNotes
            : `Bắt buộc dùng câu Hook 0-3s: "${hookOrNotes}"`;
        notesInput.style.borderColor = '#38bdf8';
        setTimeout(() => { notesInput.style.borderColor = ''; }, 1500);
    }
    showToast('🎯 Đã nạp chủ đề & chỉ đạo đạo diễn vào Xưởng Kịch Bản!', 'success');
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

    const voice = voiceSelect ? voiceSelect.value : 'en-US-AndrewNeural';
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
                if (sel) {
                    sel.innerHTML = voiceHtml;
                    sel.value = 'en-US-AndrewNeural';
                }
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

    const voice = voiceSelect ? voiceSelect.value : 'en-US-AndrewNeural';
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
            voice: voiceSelect ? voiceSelect.value : 'en-US-AndrewNeural',
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
            voice: voiceSelect ? voiceSelect.value : 'en-US-AndrewNeural',
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
// ═══════════════════════════════════════════════════════════════════════════════
// WI-FI BROADCAST & MOBILE ACCESS (WITH FIXED MDNS & AUTO-FAILOVER)
// ═══════════════════════════════════════════════════════════════════════════════
let currentWifiUrl = window.location.href;
let currentMdnsUrl = 'http://aiyoutube.local:8000';
let isScanningForServer = false;

async function openWifiModal() {
    const modal = document.getElementById('modal-wifi-share');
    if (!modal) return;
    modal.style.display = 'flex';

    try {
        const resp = await fetch('/api/network-info');
        if (resp.ok) {
            const data = await resp.json();
            currentWifiUrl = data.wifi_url || window.location.href;
            currentMdnsUrl = data.mdns_url || 'http://aiyoutube.local:8000';

            const urlText = document.getElementById('wifi-share-url-text');
            if (urlText) urlText.innerText = currentWifiUrl;

            const mdnsText = document.getElementById('wifi-share-mdns-text');
            if (mdnsText) mdnsText.innerText = currentMdnsUrl;

            const qrImg = document.getElementById('wifi-qr-img');
            if (qrImg) {
                // Tạo QR code trỏ tới URL mDNS cố định để mọi người quét 1 lần là dùng vĩnh viễn!
                const qrEndpoint = `https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(currentMdnsUrl)}`;
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
            showToast('✅ Đã sao chép địa chỉ IP trực tiếp!', 'success');
        }).catch(() => {
            prompt('Sao chép địa chỉ dưới đây:', currentWifiUrl);
        });
    } else {
        prompt('Sao chép địa chỉ dưới đây:', currentWifiUrl);
    }
}

function copyMdnsUrl() {
    const url = currentMdnsUrl || 'http://aiyoutube.local:8000';
    if (navigator.clipboard) {
        navigator.clipboard.writeText(url).then(() => {
            showToast('✅ Đã sao chép địa chỉ cố định aiyoutube.local!', 'success');
        }).catch(() => {
            prompt('Sao chép địa chỉ cố định:', url);
        });
    } else {
        prompt('Sao chép địa chỉ cố định:', url);
    }
}

async function scanForActiveServer() {
    if (isScanningForServer) return;
    isScanningForServer = true;
    showToast('🔍 Đang tự động quét tìm máy chủ mới trong mạng Wi-Fi...', 'warning', 10000);

    // 1. Thử tên miền mDNS cố định trước
    try {
        const testRes = await fetch('http://aiyoutube.local:8000/api/system/client-info', {
            mode: 'cors',
            signal: AbortSignal.timeout(1500)
        });
        if (testRes.ok) {
            showToast('🎉 Đã kết nối tới máy chủ qua aiyoutube.local! Đang chuyển hướng...', 'success');
            setTimeout(() => { window.location.href = 'http://aiyoutube.local:8000'; }, 800);
            return;
        }
    } catch (_) {}

    // 2. Quét nhanh dải subnet LAN
    const curHost = window.location.hostname;
    const parts = curHost.split('.');
    if (parts.length === 4 && curHost !== '127.0.0.1' && curHost !== 'localhost') {
        const prefix = `${parts[0]}.${parts[1]}.${parts[2]}`;
        const myLast = parseInt(parts[3], 10);
        
        const candidates = [];
        for (let i = 2; i <= 50; i++) {
            if (i !== myLast) candidates.push(`${prefix}.${i}`);
        }
        for (let i = 51; i <= 150; i++) {
            if (i !== myLast) candidates.push(`${prefix}.${i}`);
        }

        const batchSize = 12;
        for (let b = 0; b < candidates.length; b += batchSize) {
            const batch = candidates.slice(b, b + batchSize);
            const promises = batch.map(async ip => {
                try {
                    const r = await fetch(`http://${ip}:8000/api/system/client-info`, {
                        mode: 'cors',
                        signal: AbortSignal.timeout(800)
                    });
                    if (r.ok) return ip;
                } catch (_) {}
                return null;
            });
            const results = await Promise.all(promises);
            const found = results.find(ip => ip !== null);
            if (found) {
                showToast(`🎉 Đã tìm thấy máy chủ mới tại ${found}:8000! Đang chuyển hướng...`, 'success', 4000);
                setTimeout(() => { window.location.href = `http://${found}:8000`; }, 1000);
                return;
            }
        }
    }

    isScanningForServer = false;
    showToast('⚠️ Không tìm thấy máy chủ nào đang bật cổng 8000. Hãy kiểm tra lệnh py run.py!', 'error', 6000);
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
    voice: 'en-US-AndrewNeural',
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
    instantPlayerState.voice = cfg.voice || 'en-US-AndrewNeural';
    instantPlayerState.voiceRate = cfg.voiceRate || '+10%';
    instantPlayerState.scenes = cfg.scenes || [];
    instantPlayerState.currentSceneIndex = 0;
    instantPlayerState.isPlaying = true;
    instantPlayerState.isMuted = false;
    instantPlayerState.sceneElapsed = 0;

    // Calculate total duration
    instantPlayerState.totalDuration = instantPlayerState.scenes.reduce((acc, sc) => acc + (parseFloat(sc.duration) || 4), 0);
    if (instantPlayerState.totalDuration <= 0) instantPlayerState.totalDuration = 30;

    // Nạp màu highlight phụ đề từ Niche Profile tương ứng
    let highlightColor = '#FFE600';
    const targetNicheId = cfg.nicheId || cfg.niche;
    if (targetNicheId) {
        const n = (allNiches || []).find(x => x.id === targetNicheId);
        if (n?.subtitles?.highlight_color) highlightColor = n.subtitles.highlight_color;
    } else if (cfg.channelId) {
        const ch = (allChannels || []).find(c => c.id === cfg.channelId);
        const n = (allNiches || []).find(x => x.id === (ch?.niche || 'general'));
        if (n?.subtitles?.highlight_color) highlightColor = n.subtitles.highlight_color;
    }
    instantPlayerState.highlightColor = highlightColor;

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
                    wordEl.style.color = '';
                    wordEl.style.textShadow = '';
                } else if (w === activeWordIdx) {
                    wordEl.className = 'preview-k-word active';
                    if (instantPlayerState.highlightColor) {
                        wordEl.style.color = instantPlayerState.highlightColor;
                        wordEl.style.textShadow = `0 0 14px ${instantPlayerState.highlightColor}, 2px 2px 0 #000, -2px -2px 0 #000`;
                    }
                } else {
                    wordEl.className = 'preview-k-word future';
                    wordEl.style.color = '';
                    wordEl.style.textShadow = '';
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
        voice: 'en-US-AndrewNeural',
        voiceRate: '+10%',
        scenes: scenes
    });
}

function openInstantPreviewFromSingleStudio() {
    const title = document.getElementById('single-title')?.value.trim() || 'Video Shorts Hoàn Chỉnh';
    const script = document.getElementById('single-script-input')?.value.trim() || '';
    const voice = document.getElementById('single-voice')?.value || 'en-US-AndrewNeural';
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
    const voice = document.getElementById('manual-voice')?.value || 'en-US-AndrewNeural';
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
            voice: 'en-US-AndrewNeural',
            voiceRate: '+10%',
            scenes: scenes
        });
    } catch (e) {
        showToast('Lỗi tải dữ liệu job: ' + e.message, 'error');
    }
}

/* ═══════════════════════════════════════════════════════════════
   NICHE INTELLIGENCE PROFILES CONTROLLER (HỒ SƠ NGÁCH KÊNH)
   ═══════════════════════════════════════════════════════════════ */

// allNiches hoisted to top

async function fetchNiches() {
    try {
        const res = await apiGet('/api/niches');
        if (res && res.niches) {
            allNiches = res.niches;
            renderNichesGrid(allNiches);
            updateChannelNicheSelectOptions(allNiches);
        }
    } catch (e) {
        console.error('Lỗi tải danh sách hồ sơ ngách:', e);
    }
}

function updateChannelNicheSelectOptions(niches) {
    const sel = document.getElementById('channel-input-niche');
    if (!sel) return;
    const curVal = sel.value;
    const items = (niches || []).map(n => `
        <option value="${esc(n.id)}">${esc(n.icon || '🎯')} ${esc(n.name)}</option>
    `).join('');
    sel.innerHTML = `<option value="">✨ Không chọn (Kênh tự do / Đa chủ đề)</option>` + items;
    if (curVal !== undefined && curVal !== null && (curVal === '' || (niches || []).some(n => n.id === curVal))) {
        sel.value = curVal;
    } else {
        sel.value = '';
    }
}

function renderNichesGrid(niches) {
    const container = document.getElementById('niches-grid-container');
    if (!container) return;

    if (!niches || niches.length === 0) {
        container.innerHTML = `
            <div style="grid-column: 1 / -1; text-align:center; padding:30px; color:var(--text-muted); background:rgba(255,255,255,0.02); border-radius:12px; border:1px dashed rgba(255,255,255,0.1);">
                <div style="font-size:32px; margin-bottom:8px;">🎯</div>
                <h4 style="color:var(--text-primary); margin-bottom:6px;">Chưa có Hồ Sơ Ngách nào</h4>
                <button class="btn btn-primary btn-sm" onclick="openAddNicheModal()">+ Tạo Hồ Sơ Ngách Đầu Tiên</button>
            </div>
        `;
        return;
    }

    container.innerHTML = niches.map(n => {
        const bp = n.script_blueprint || {};
        const vis = n.visual_aesthetics || {};
        const sub = n.subtitles || {};
        const aud = n.audio || {};

        // Find which channels are linked to this niche
        const linkedChannels = (allChannels || []).filter(c => c.niche === n.id);
        const linkedBadge = linkedChannels.length > 0
            ? `<div class="niche-channels-connected">
                 <span style="font-size:11px; color:var(--text-muted); margin-right:4px;">Kênh áp dụng:</span>
                 ${linkedChannels.map(c => `<span class="niche-ch-chip">📺 ${esc(c.name)}</span>`).join('')}
               </div>`
            : `<div class="niche-channels-connected">
                 <span style="font-size:11px; color:var(--text-muted); font-style:italic;">Chưa gắn vào kênh nào</span>
               </div>`;

        const firstHook = (bp.hook_formulas && bp.hook_formulas[0]) ? bp.hook_formulas[0] : 'Pattern Interrupt Hook';
        const artStyle = vis.art_style || 'Cinematic Realism';
        const highlightColor = sub.highlight_color || '#FFE600';
        const primaryColor = sub.primary_color || '#FFFFFF';
        const voiceDesc = aud.preferred_voice_en ? aud.preferred_voice_en.split('-')[2] || aud.preferred_voice_en : 'Default';

        return `
            <div class="niche-card" id="niche-card-${esc(n.id)}">
                <div>
                    <div class="niche-card-header">
                        <div style="display:flex; align-items:center; gap:8px;">
                            <span style="font-size:24px;">${esc(n.icon || '🎯')}</span>
                            <div>
                                <h4 style="margin:0; font-size:15px; font-weight:700; color:#fff;">${esc(n.name)}</h4>
                                <span style="font-size:10.5px; color:var(--text-muted); font-family:monospace;">ID: ${esc(n.id)}</span>
                            </div>
                        </div>
                        <span class="niche-badge-pill">${esc(n.badge || n.id)}</span>
                    </div>

                    <div class="niche-card-desc" title="${esc(n.description || '')}">
                        ${esc(n.description || 'Chưa có mô tả chi tiết')}
                    </div>

                    <div class="niche-spec-list">
                        <div class="niche-spec-item">
                            <span class="niche-spec-label">⚡ Hook 3s:</span>
                            <span class="niche-spec-val" title="${esc(firstHook)}">${esc(firstHook)}</span>
                        </div>
                        <div class="niche-spec-item">
                            <span class="niche-spec-label">🎨 Mỹ thuật:</span>
                            <span class="niche-spec-val" title="${esc(artStyle)}">${esc(artStyle)}</span>
                        </div>
                        <div class="niche-spec-item">
                            <span class="niche-spec-label">🔤 Phụ đề:</span>
                            <span class="niche-spec-val" style="display:flex; align-items:center; gap:6px;">
                                <span style="width:12px; height:12px; border-radius:3px; background:${esc(highlightColor)}; display:inline-block; border:1px solid rgba(0,0,0,0.5);"></span>
                                <strong style="color:${esc(highlightColor)};">${esc(highlightColor)}</strong>
                                <span style="font-size:10.5px; color:var(--text-muted);">(${esc(sub.font_family ? sub.font_family.split(',')[0] : 'Arial')})</span>
                            </span>
                        </div>
                        <div class="niche-spec-item">
                            <span class="niche-spec-label">🎙️ Giọng/Nhạc:</span>
                            <span class="niche-spec-val" title="${esc(aud.bgm_vibe || '')}">${esc(voiceDesc)} · ${esc(aud.bgm_vibe ? aud.bgm_vibe.split(',')[0] : 'Cinematic')}</span>
                        </div>
                    </div>

                    ${linkedBadge}
                </div>

                <div style="display:flex; gap:8px; margin-top:10px; padding-top:10px; border-top:1px solid rgba(255,255,255,0.06);">
                    <button class="btn btn-primary btn-sm" style="flex:1; justify-content:center;" onclick="openEditNicheModal('${esc(n.id)}')">
                        👁️ Xem & Tinh Chỉnh Khuôn Mẫu
                    </button>
                </div>
            </div>
        `;
    }).join('');
}

function openAddNicheModal() {
    const modal = document.getElementById('modal-niche-editor');
    if (!modal) {
        console.error('Không tìm thấy element #modal-niche-editor');
        return;
    }

    const setVal = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.value = val;
    };
    const setText = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
    };

    setVal('niche-field-mode', 'add');
    const tempId = `custom_${Date.now().toString(36)}`;
    setVal('niche-field-id', tempId);
    setText('niche-modal-title', 'Tạo Hồ Sơ Ngách Mới');
    setText('niche-modal-icon-badge', '✨');

    setVal('niche-input-name', '');
    setVal('niche-input-badge', '✨ Tùy Biến');
    setVal('niche-input-icon', '✨');
    setVal('niche-input-target-aud', 'Khán giả xem YouTube Shorts');
    setVal('niche-input-tone', 'Cuốn hút, sắc bén, kịch tính, nhịp điệu nhanh');
    setVal('niche-input-pacing', '2.5 - 3.2 giây/cảnh');
    setVal('niche-input-structure', 'Hook 3s -> Bóc trần tình tiết -> Điểm nhấn -> CTA & Infinite Loop');
    setVal('niche-input-hooks', "Curiosity Shock: 'Hầu hết mọi người không biết điều này...'\nThe Shock Law: 'Sự thật bất ngờ nhất là...'");
    setVal('niche-input-forbidden', "Chào mừng các bạn đã quay trở lại\nBạn có bao giờ tự hỏi\nTrong video hôm nay");
    setVal('niche-input-ctas', "Nhấn thích và theo dõi kênh để không bỏ lỡ phần tiếp theo... bởi vì...");
    setVal('niche-input-facts', "Kiểm chứng thông tin chính xác trước khi phát biểu");

    setVal('niche-input-art-style', 'Dark Cinematic Noir & 35mm Photography');
    setVal('niche-input-palette', 'High contrast, dramatic shadows, vibrant accents');
    setVal('niche-input-angles', 'Extreme close-up, dramatic low-angle, cinematic tracking');
    setVal('niche-input-prefix', '8k vertical film still, dramatic volumetric lighting, 35mm photography');
    setVal('niche-input-suffix', 'photorealistic 9:16 vertical composition, razor-sharp focus, no watermark, no text');
    setVal('niche-input-negatives', 'blurry, low quality, deformed, cartoon, anime, watermark, text');
    setVal('niche-input-stock-kws', 'people walking street, cinematic city, dramatic sunset');

    setVal('niche-input-font', 'Montserrat, sans-serif');
    setVal('niche-input-font-size', 40);
    setVal('niche-input-margin-v', 210);
    setVal('niche-color-primary', '#FFFFFF');
    setVal('niche-text-primary', '#FFFFFF');
    setVal('niche-color-highlight', '#FFE600');
    setVal('niche-text-highlight', '#FFE600');
    setVal('niche-color-outline', '#000000');
    setVal('niche-text-outline', '#000000');

    setVal('niche-input-voice-en', 'en-US-AndrewNeural');
    setVal('niche-input-voice-vi', 'vi-VN-NamMinhNeural');
    setVal('niche-input-rate', '0%');
    setVal('niche-input-pitch', '0Hz');
    setVal('niche-input-bgm', 'dynamic upbeat cinematic, modern groove');

    const delBtn = document.getElementById('btn-delete-niche');
    if (delBtn) delBtn.style.display = 'none';

    switchNicheTab('script');
    updateNicheLiveSubtitlePreview();
    modal.classList.add('visible');
    modal.style.display = 'flex';
}

async function openEditNicheModal(nicheId) {
    const modal = document.getElementById('modal-niche-editor');
    if (!modal) {
        console.error('Không tìm thấy element #modal-niche-editor');
        return;
    }

    if (!allNiches || allNiches.length === 0) {
        await fetchNiches();
    }
    let n = (allNiches || []).find(x => x.id === nicheId);
    if (!n) {
        try {
            const res = await apiGet(`/api/niches/${encodeURIComponent(nicheId)}`);
            if (res && res.niche) n = res.niche;
        } catch (e) {
            console.warn('Fallback fetch niche failed:', e);
        }
    }
    if (!n) {
        showToast('Không tìm thấy thông tin hồ sơ ngách ' + nicheId, 'error');
        return;
    }

    const setVal = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.value = val;
    };
    const setText = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
    };

    setVal('niche-field-mode', 'edit');
    setVal('niche-field-id', n.id);
    setText('niche-modal-title', `Chỉnh Sửa Khuôn Mẫu: ${n.name}`);
    setText('niche-modal-icon-badge', n.icon || '🎯');

    setVal('niche-input-name', n.name || '');
    setVal('niche-input-badge', n.badge || '');
    setVal('niche-input-icon', n.icon || '🎯');
    setVal('niche-input-target-aud', n.target_audience || '');

    const bp = n.script_blueprint || {};
    setVal('niche-input-tone', bp.tone_of_voice || '');
    setVal('niche-input-pacing', bp.pacing || '');
    setVal('niche-input-structure', bp.narrative_structure || '');
    setVal('niche-input-hooks', (bp.hook_formulas || []).join('\n'));
    setVal('niche-input-forbidden', (bp.forbidden_words || []).join('\n'));
    setVal('niche-input-ctas', (bp.cta_templates || []).join('\n'));
    setVal('niche-input-facts', (bp.anti_hallucination_rules || []).join('\n'));

    const vis = n.visual_aesthetics || {};
    setVal('niche-input-art-style', vis.art_style || '');
    setVal('niche-input-palette', vis.color_palette || '');
    setVal('niche-input-angles', vis.camera_angles || '');
    setVal('niche-input-prefix', vis.master_prompt_prefix || '');
    setVal('niche-input-suffix', vis.master_prompt_suffix || '');
    setVal('niche-input-negatives', vis.negative_prompts || '');
    setVal('niche-input-stock-kws', (vis.stock_video_keywords || []).join(', '));

    const sub = n.subtitles || {};
    setVal('niche-input-font', sub.font_family || 'Montserrat, sans-serif');
    setVal('niche-input-font-size', sub.font_size || 40);
    setVal('niche-input-margin-v', sub.margin_bottom || 210);
    setVal('niche-color-primary', sub.primary_color || '#FFFFFF');
    setVal('niche-text-primary', sub.primary_color || '#FFFFFF');
    setVal('niche-color-highlight', sub.highlight_color || '#FFE600');
    setVal('niche-text-highlight', sub.highlight_color || '#FFE600');
    setVal('niche-color-outline', sub.outline_color || '#000000');
    setVal('niche-text-outline', sub.outline_color || '#000000');

    const aud = n.audio || {};
    setVal('niche-input-voice-en', aud.preferred_voice_en || 'en-US-AndrewNeural');
    setVal('niche-input-voice-vi', aud.preferred_voice_vi || 'vi-VN-NamMinhNeural');
    setVal('niche-input-rate', aud.rate || '0%');
    setVal('niche-input-pitch', aud.pitch || '0Hz');
    setVal('niche-input-bgm', aud.bgm_vibe || '');

    // Show delete button only if not core niche
    const coreNiches = ['general', 'psychology', 'history_mystery', 'finance_wealth'];
    const delBtn = document.getElementById('btn-delete-niche');
    if (delBtn) {
        delBtn.style.display = coreNiches.includes(n.id) ? 'none' : 'block';
    }

    switchNicheTab('script');
    updateNicheLiveSubtitlePreview();
    modal.classList.add('visible');
    modal.style.display = 'flex';
}

function closeNicheModal() {
    const modal = document.getElementById('modal-niche-editor');
    if (modal) {
        modal.classList.remove('visible');
        modal.style.display = 'none';
    }
}

// Bind functions to window explicitly for global accessibility
window.openAddNicheModal = openAddNicheModal;
window.openEditNicheModal = openEditNicheModal;
window.closeNicheModal = closeNicheModal;

function switchNicheTab(tabName) {
    document.querySelectorAll('.niche-modal-tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.niche-pane').forEach(pane => {
        pane.classList.remove('active');
        pane.style.display = 'none';
    });

    const activeBtn = document.getElementById(`tab-btn-niche-${tabName}`);
    const activePane = document.getElementById(`niche-pane-${tabName}`);
    if (activeBtn) activeBtn.classList.add('active');
    if (activePane) {
        activePane.classList.add('active');
        activePane.style.display = 'block';
    }
}

function updateNicheLiveSubtitlePreview() {
    const font = document.getElementById('niche-input-font')?.value || 'Montserrat, sans-serif';
    const fontSize = parseInt(document.getElementById('niche-input-font-size')?.value) || 40;
    const primary = document.getElementById('niche-color-primary')?.value || '#FFFFFF';
    const highlight = document.getElementById('niche-color-highlight')?.value || '#FFE600';
    const outline = document.getElementById('niche-color-outline')?.value || '#000000';

    // Keep text inputs in sync
    const textPri = document.getElementById('niche-text-primary');
    const textHigh = document.getElementById('niche-text-highlight');
    const textOut = document.getElementById('niche-text-outline');
    if (textPri) textPri.value = primary;
    if (textHigh) textHigh.value = highlight;
    if (textOut) textOut.value = outline;

    const w1 = document.getElementById('sample-w1');
    const w2 = document.getElementById('sample-w2');
    const w3 = document.getElementById('sample-w3');

    const previewScale = 0.55; // Fit preview box comfortably
    const scaledSize = Math.round(fontSize * previewScale);
    const outlineShadow = `2px 2px 0 ${outline}, -2px -2px 0 ${outline}, 2px -2px 0 ${outline}, -2px 2px 0 ${outline}, 0 4px 10px rgba(0,0,0,0.8)`;

    [w1, w2, w3].forEach(w => {
        if (!w) return;
        w.style.fontFamily = font;
        w.style.fontSize = `${scaledSize}px`;
    });

    if (w1) {
        w1.style.color = primary;
        w1.style.textShadow = outlineShadow;
    }
    if (w2) {
        w2.style.color = highlight;
        w2.style.textShadow = `0 0 14px ${highlight}, ${outlineShadow}`;
    }
    if (w3) {
        w3.style.color = primary;
        w3.style.textShadow = outlineShadow;
    }
}

async function submitNicheForm() {
    const id = document.getElementById('niche-field-id').value.trim();
    const name = document.getElementById('niche-input-name').value.trim();
    if (!name) {
        showToast('Vui lòng nhập Tên Ngách', 'error');
        document.getElementById('niche-input-name')?.focus();
        return;
    }

    const parseLines = (val) => (val || '').split('\n').map(l => l.trim()).filter(Boolean);
    const parseCsv = (val) => (val || '').split(',').map(l => l.trim()).filter(Boolean);

    const payload = {
        id: id,
        name: name,
        badge: document.getElementById('niche-input-badge').value.trim() || name,
        icon: document.getElementById('niche-input-icon').value.trim() || '🎯',
        target_audience: document.getElementById('niche-input-target-aud').value.trim(),
        script_blueprint: {
            tone_of_voice: document.getElementById('niche-input-tone').value.trim(),
            pacing: document.getElementById('niche-input-pacing').value.trim(),
            narrative_structure: document.getElementById('niche-input-structure').value.trim(),
            hook_formulas: parseLines(document.getElementById('niche-input-hooks').value),
            forbidden_words: parseLines(document.getElementById('niche-input-forbidden').value),
            cta_templates: parseLines(document.getElementById('niche-input-ctas').value),
            anti_hallucination_rules: parseLines(document.getElementById('niche-input-facts').value),
        },
        visual_aesthetics: {
            art_style: document.getElementById('niche-input-art-style').value.trim(),
            color_palette: document.getElementById('niche-input-palette').value.trim(),
            camera_angles: document.getElementById('niche-input-angles').value.trim(),
            master_prompt_prefix: document.getElementById('niche-input-prefix').value.trim(),
            master_prompt_suffix: document.getElementById('niche-input-suffix').value.trim(),
            negative_prompts: document.getElementById('niche-input-negatives').value.trim(),
            stock_video_keywords: parseCsv(document.getElementById('niche-input-stock-kws').value),
        },
        subtitles: {
            font_family: document.getElementById('niche-input-font').value,
            font_size: parseInt(document.getElementById('niche-input-font-size').value) || 40,
            margin_bottom: parseInt(document.getElementById('niche-input-margin-v').value) || 210,
            primary_color: document.getElementById('niche-color-primary').value,
            highlight_color: document.getElementById('niche-color-highlight').value,
            outline_color: document.getElementById('niche-color-outline').value,
        },
        audio: {
            preferred_voice_en: document.getElementById('niche-input-voice-en').value,
            preferred_voice_vi: document.getElementById('niche-input-voice-vi').value,
            rate: document.getElementById('niche-input-rate').value.trim(),
            pitch: document.getElementById('niche-input-pitch').value.trim(),
            bgm_vibe: document.getElementById('niche-input-bgm').value.trim(),
        }
    };

    try {
        const res = await apiPost('/api/niches', payload);
        if (res && res.success) {
            showToast('✅ Đã lưu Hồ Sơ Ngách thành công!', 'success');
            closeNicheModal();
            await fetchNiches();
        } else {
            showToast('Lỗi lưu hồ sơ ngách: ' + (res?.message || 'Không rõ'), 'error');
        }
    } catch (e) {
        showToast('Lỗi: ' + e.message, 'error');
    }
}

async function deleteCurrentNiche() {
    const id = document.getElementById('niche-field-id').value;
    if (!confirm(`Bạn có chắc chắn muốn xóa hồ sơ ngách '${id}' không?`)) return;

    try {
        const resp = await fetch(`/api/niches/${id}`, { method: 'DELETE' });
        const res = await resp.json();
        if (resp.ok && res.success) {
            showToast('✅ Đã xóa hồ sơ ngách thành công!', 'success');
            closeNicheModal();
            await fetchNiches();
        } else {
            showToast('Lỗi xóa hồ sơ ngách: ' + (res?.detail || res?.message || 'Không rõ'), 'error');
        }
    } catch (e) {
        showToast('Lỗi: ' + e.message, 'error');
    }
}

function onChannelNicheSelectChanged(nicheId) {
    if (!nicheId) {
        showToast('✨ Đã chọn: Không gán hồ sơ ngách (Kênh tự do / Đa chủ đề)', 'info');
        return;
    }
    const n = allNiches.find(x => x.id === nicheId);
    if (!n) return;

    // Auto-suggest voice based on niche
    const lang = document.getElementById('channel-input-lang')?.value || 'en';
    const voiceSelect = document.getElementById('channel-input-voice');
    if (voiceSelect && n.audio) {
        const recVoice = lang === 'vi' ? n.audio.preferred_voice_vi : n.audio.preferred_voice_en;
        if (recVoice && Array.from(voiceSelect.options).some(opt => opt.value === recVoice)) {
            voiceSelect.value = recVoice;
        }
    }

    showToast(`🎯 Đã chọn ngách: ${n.badge || n.name} (Tự động tối ưu giọng đọc & visual)`, 'info');
}

// Global window bindings for Niche Profiles controller
window.openAddNicheModal = openAddNicheModal;
window.openEditNicheModal = openEditNicheModal;
window.closeNicheModal = closeNicheModal;
window.switchNicheTab = switchNicheTab;
window.updateNicheLiveSubtitlePreview = updateNicheLiveSubtitlePreview;
window.submitNicheForm = submitNicheForm;
window.deleteCurrentNiche = deleteCurrentNiche;
window.onChannelNicheSelectChanged = onChannelNicheSelectChanged;
window.fetchNiches = fetchNiches;
window.renderNichesGrid = renderNichesGrid;

/* ═══════════════════════════════════════════════════════
   SMART LONG-TO-SHORTS REPURPOSER CONTROLLER
══════════════════════════════════════════════════════════ */
let currentRepurposeData = null;

const REPURPOSER_NICHE_META = {
    animal_rescue: {
        badge: "🐾",
        name: "Cứu Hộ Động Vật (Paws & Hearts Rescue)",
        tone: "Cảm động, thấu cảm, hành trình hồi sinh kỳ diệu",
        hook_sample: "Khoảnh khắc sinh tử: ánh mắt sợ hãi ➔ niềm vui vỡ òa khi được cứu sống!",
        accent_color: "#10b981",
        alert_tone: "Tuyệt đối không dùng từ giật gân, câu like rẻ tiền. Giữ trọn sự ấm áp và tính nhân văn."
    },
    wildlife_predators: {
        badge: "🦁",
        name: "Động Vật Hoang Dã (Wild Odyssey)",
        tone: "Săn mồi đỉnh cao, thống số sát thương, sinh tồn khắc nghiệt",
        hook_sample: "Lực cắn 1,500 PSI, cú bứt tốc 0.3s — khoảnh khắc săn mồi triệu view!",
        accent_color: "#f59e0b",
        alert_tone: "Tập trung vào adrenaline, thống số sức mạnh thực tế và sinh tồn tự nhiên."
    },
    psychology: {
        badge: "🧠",
        name: "Tâm Lý Học (Psychology of the world)",
        tone: "Bóc trần thao túng, giải mã hành vi, bảo vệ bản thân",
        hook_sample: "Dấu hiệu thao túng tâm lý ngầm: 99% người không nhận ra!",
        accent_color: "#a855f7",
        alert_tone: "Tập trung vào phân tích tâm lý ứng dụng và giá trị bảo vệ người xem."
    },
    deep_sea_earth_mysteries: {
        badge: "🌊",
        name: "Bí Mật Thế Giới (The mysteries of the world)",
        tone: "Bí ẩn đại dương sâu, hiện tượng khoa học chưa giải thích, kích hoạt tò mò tột đỉnh",
        hook_sample: "Âm thanh bí ẩn ghi lại từ độ sâu 11,000m — khoa học vẫn bất lực!",
        accent_color: "#06b6d4",
        alert_tone: "Kích hoạt curiosity gap mạnh mẽ dựa trên tài liệu và hiện tượng thực tế."
    },
    general: {
        badge: "🎯",
        name: "Tổng Hợp & Đời Sống",
        tone: "Điểm nhấn nội dung, giá trị giải trí và bài học cuộc sống",
        hook_sample: "Khoảnh khắc đắt giá nhất ít người nhận ra...",
        accent_color: "#3b82f6",
        alert_tone: "Cân bằng giữa giải trí và cung cấp thông tin hấp dẫn."
    }
};

async function initRepurposerTab() {
    if (!allNiches || allNiches.length === 0) {
        if (typeof fetchNiches === 'function') await fetchNiches();
    }
    if (!allChannels || allChannels.length === 0) {
        if (typeof fetchChannels === 'function') await fetchChannels();
    }
    populateRepurposerNiches();
    populateRepurposerChannels();
    loadRepurposerLocalVideos();
    loadRepurposerClips();
}

function populateRepurposerNiches() {
    const sel = document.getElementById('repurposer-niche-select');
    if (!sel) return;
    const currentVal = sel.value;
    sel.innerHTML = '';
    if (!allNiches || allNiches.length === 0) {
        sel.innerHTML = `
            <option value="animal_rescue">🐾 Cứu Hộ Động Vật (Paws & Hearts Rescue)</option>
            <option value="wildlife_predators">🦁 Động Vật Hoang Dã (Wild Odyssey)</option>
            <option value="psychology">🧠 Tâm Lý Học (Psychology of the world)</option>
            <option value="deep_sea_earth_mysteries">🌊 Bí Mật Thế Giới (The mysteries of the world)</option>
            <option value="general">🎯 Tổng Hợp & Đời Sống</option>
        `;
    } else {
        allNiches.forEach(n => {
            const opt = document.createElement('option');
            opt.value = n.id;
            opt.textContent = `${n.badge || n.icon || '🎯'} ${n.name}`;
            sel.appendChild(opt);
        });
    }
    if (currentVal && sel.querySelector(`option[value="${currentVal}"]`)) {
        sel.value = currentVal;
    }
    updateRepurposerNicheBadge(sel.value || 'general');
}

function populateRepurposerChannels() {
    const sel = document.getElementById('repurposer-channel-select');
    if (!sel) return;
    sel.innerHTML = '';
    if (!allChannels || allChannels.length === 0) {
        sel.innerHTML = '<option value="">(Chưa có kênh - Dùng mặc định)</option>';
        return;
    }
    allChannels.forEach(ch => {
        const opt = document.createElement('option');
        opt.value = ch.id;
        opt.textContent = `${ch.name} (${ch.niche || 'general'})`;
        opt.dataset.niche = ch.niche || 'general';
        sel.appendChild(opt);
    });
    onRepurposerChannelChanged();
}

function onRepurposerChannelChanged() {
    const chanSel = document.getElementById('repurposer-channel-select');
    const nicheSel = document.getElementById('repurposer-niche-select');
    if (!chanSel || !nicheSel) return;
    const selectedOpt = chanSel.options[chanSel.selectedIndex];
    if (selectedOpt && selectedOpt.dataset.niche) {
        const targetNiche = selectedOpt.dataset.niche;
        if (nicheSel.querySelector(`option[value="${targetNiche}"]`)) {
            nicheSel.value = targetNiche;
        }
    }
    onRepurposerNicheChanged();
}

function onRepurposerNicheChanged() {
    const chanSel = document.getElementById('repurposer-channel-select');
    const nicheSel = document.getElementById('repurposer-niche-select');
    const lockBadge = document.getElementById('repurposer-niche-lock-status');
    if (!nicheSel) return;

    const currentNiche = nicheSel.value;
    const selectedChanOpt = chanSel ? chanSel.options[chanSel.selectedIndex] : null;
    const chanNiche = selectedChanOpt ? selectedChanOpt.dataset.niche : null;

    if (lockBadge) {
        if (chanNiche && chanNiche === currentNiche) {
            lockBadge.textContent = '🔒 Đã đồng bộ với Kênh';
            lockBadge.style.background = 'rgba(16,185,129,0.2)';
            lockBadge.style.color = '#10b981';
            lockBadge.style.borderColor = 'rgba(16,185,129,0.4)';
        } else {
            lockBadge.textContent = '✏️ Tùy chọn độc lập';
            lockBadge.style.background = 'rgba(245,158,11,0.2)';
            lockBadge.style.color = '#f59e0b';
            lockBadge.style.borderColor = 'rgba(245,158,11,0.4)';
        }
    }

    updateRepurposerNicheBadge(currentNiche);
}

function updateRepurposerNicheBadge(nicheId) {
    const iconEl = document.getElementById('repurposer-niche-badge-icon');
    const titleEl = document.getElementById('repurposer-niche-badge-title');
    const descEl = document.getElementById('repurposer-niche-badge-desc');
    const hookEl = document.getElementById('repurposer-niche-badge-hook');
    const bannerEl = document.getElementById('repurposer-niche-banner');

    const meta = REPURPOSER_NICHE_META[nicheId] || {
        badge: '🎯',
        name: 'Hồ Sơ Ngách Chuẩn Hóa',
        tone: 'Định hình kịch bản và khoảnh khắc cao trào theo từng kênh.',
        hook_sample: 'Mở đầu ấn tượng thu hút người xem trong 3s đầu',
        accent_color: '#38bdf8',
        alert_tone: 'Nội dung tối ưu cho YouTube Shorts & TikTok.'
    };

    if (iconEl) iconEl.textContent = meta.badge;
    if (titleEl) {
        titleEl.textContent = `${meta.badge} ${meta.name}`;
        titleEl.style.color = meta.accent_color;
    }
    if (descEl) {
        descEl.innerHTML = `<strong>Tone & Style:</strong> ${escapeHtml(meta.tone)}<br><span style="color:#94a3b8; font-size:11px;">⚠️ ${escapeHtml(meta.alert_tone)}</span>`;
    }
    if (hookEl) {
        hookEl.innerHTML = `💡 <strong>Hook đặc trưng:</strong> <em>"${escapeHtml(meta.hook_sample)}"</em>`;
        hookEl.style.borderLeftColor = meta.accent_color;
    }
    if (bannerEl) {
        bannerEl.style.borderColor = `${meta.accent_color}55`;
    }
}

function toggleRepurposerDescBox() {
    const box = document.getElementById('repurposer-desc-container');
    const icon = document.getElementById('repurposer-desc-toggle-icon');
    if (!box) return;
    if (box.style.display === 'none' || !box.style.display) {
        box.style.display = 'block';
        if (icon) icon.textContent = '➖';
    } else {
        box.style.display = 'none';
        if (icon) icon.textContent = '➕';
    }
}

function toggleRepurposerSourceInput() {
    const radios = document.getElementsByName('repurposer-src-type');
    let val = 'youtube';
    for (const r of radios) {
        if (r.checked) { val = r.value; break; }
    }
    const ytBox = document.getElementById('repurposer-yt-input-container');
    const localBox = document.getElementById('repurposer-local-input-container');
    if (val === 'youtube') {
        if (ytBox) ytBox.style.display = 'block';
        if (localBox) localBox.style.display = 'none';
    } else {
        if (ytBox) ytBox.style.display = 'none';
        if (localBox) localBox.style.display = 'block';
        loadRepurposerLocalVideos();
    }
}

function pasteSampleYouTubeUrl() {
    const inp = document.getElementById('repurposer-yt-url');
    if (inp) {
        inp.value = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ';
        showToast('💡 Đã điền link YouTube mẫu để thử nghiệm!', 'info');
    }
}

async function loadRepurposerLocalVideos() {
    const sel = document.getElementById('repurposer-local-video-select');
    if (!sel) return;
    try {
        const res = await fetch('/api/repurposer/local-videos');
        const data = await res.json();
        sel.innerHTML = '';
        if (data.videos && data.videos.length > 0) {
            data.videos.forEach(v => {
                const opt = document.createElement('option');
                opt.value = v.file_path;
                opt.textContent = `${v.title} (${v.duration_seconds}s - ${v.file_size_mb} MB)`;
                sel.appendChild(opt);
            });
        } else {
            sel.innerHTML = '<option value="">(Chưa có video MP4 nào trong thư mục output/)</option>';
        }
    } catch (e) {
        console.warn('loadRepurposerLocalVideos error:', e);
        sel.innerHTML = '<option value="">(Lỗi quét video cục bộ)</option>';
    }
}

async function startRepurposerAnalysis() {
    const radios = document.getElementsByName('repurposer-src-type');
    let srcType = 'youtube';
    for (const r of radios) {
        if (r.checked) { srcType = r.value; break; }
    }

    let urlOrPath = '';
    if (srcType === 'youtube') {
        const ytInput = document.getElementById('repurposer-yt-url');
        urlOrPath = ytInput ? ytInput.value.trim() : '';
        if (!urlOrPath) {
            showToast('Vui lòng dán link YouTube cần phân tích!', 'error');
            return;
        }
    } else {
        const localSel = document.getElementById('repurposer-local-video-select');
        urlOrPath = localSel ? localSel.value : '';
        if (!urlOrPath) {
            showToast('Vui lòng chọn 1 file video cục bộ có sẵn!', 'error');
            return;
        }
    }

    const mode = document.getElementById('repurposer-mode-select')?.value || 'self_owned';
    const nicheId = document.getElementById('repurposer-niche-select')?.value || 'general';
    const chanSel = document.getElementById('repurposer-channel-select');
    const channelName = chanSel?.options[chanSel.selectedIndex]?.text || '';
    const videoDesc = document.getElementById('repurposer-video-desc')?.value.trim() || '';

    const btn = document.getElementById('btn-repurposer-analyze');
    const btnText = document.getElementById('btn-repurposer-analyze-text');
    const statusText = document.getElementById('repurposer-status-text');

    if (btn) btn.disabled = true;
    if (btnText) btnText.textContent = 'Đang phân tích lời thoại bằng Gemini AI...';
    if (statusText) statusText.textContent = '⏳ Đang quét transcript & trích xuất khoảnh khắc vàng...';

    try {
        const res = await fetch('/api/repurposer/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                url_or_path: urlOrPath,
                niche_id: nicheId,
                mode: mode,
                channel_name: channelName,
                channel_id: chanSel?.value || '',
                target_lang: 'en',
                video_description: videoDesc,
                device_id: getDeviceId(),
                device_name: getDeviceName()
            })
        });

        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Lỗi khi phân tích video');
        }

        const data = await res.json();
        currentRepurposeData = data;
        renderRepurposerResults(data, urlOrPath);
        showToast('🎉 Đã phát hiện các khoảnh khắc viral triệu view thành công!', 'success');
    } catch (e) {
        showToast('Lỗi: ' + e.message, 'error');
    } finally {
        if (btn) btn.disabled = false;
        if (btnText) btnText.textContent = 'Quét & Phân Tích Khoảnh Khắc Viral';
        if (statusText) statusText.textContent = '';
    }
}

function switchRepurposeViewMode(mode) {
    const mashupContainer = document.getElementById('repurposer-mashup-container');
    const momentsContainer = document.getElementById('repurposer-moments-container');
    const mashupBtn = document.getElementById('repurpose-tab-mashup-btn');
    const momentsBtn = document.getElementById('repurpose-tab-moments-btn');

    if (mode === 'mashup') {
        if (mashupContainer) mashupContainer.style.display = 'block';
        if (momentsContainer) momentsContainer.style.display = 'none';
        if (mashupBtn) {
            mashupBtn.className = 'btn btn-primary';
            mashupBtn.style.background = 'linear-gradient(135deg, #10b981, #06b6d4)';
            mashupBtn.style.fontWeight = '800';
        }
        if (momentsBtn) {
            momentsBtn.className = 'btn btn-secondary';
            momentsBtn.style.background = '';
            momentsBtn.style.fontWeight = '600';
        }
    } else {
        if (mashupContainer) mashupContainer.style.display = 'none';
        if (momentsContainer) momentsContainer.style.display = 'block';
        if (mashupBtn) {
            mashupBtn.className = 'btn btn-secondary';
            mashupBtn.style.background = '';
            mashupBtn.style.fontWeight = '600';
        }
        if (momentsBtn) {
            momentsBtn.className = 'btn btn-primary';
            momentsBtn.style.background = 'linear-gradient(135deg, #10b981, #06b6d4)';
            momentsBtn.style.fontWeight = '800';
        }
    }
}

function renderRepurposerResults(data, originalSource) {
    const section = document.getElementById('repurposer-results-section');
    if (!section) return;
    section.style.display = 'block';

    const meta = data.metadata || {};
    const moments = data.moments || [];
    const mashups = data.mashups || [];

    const thumbImg = document.getElementById('repurposer-video-thumb');
    if (thumbImg) {
        thumbImg.src = meta.thumbnail || 'https://placehold.co/140x80/1e293b/94a3b8?text=Video';
        thumbImg.onerror = () => { thumbImg.src = 'https://placehold.co/140x80/1e293b/94a3b8?text=Video'; };
    }
    const titleEl = document.getElementById('repurposer-video-title');
    if (titleEl) titleEl.textContent = meta.title || 'Video Nguồn';
    const authorEl = document.getElementById('repurposer-video-author');
    if (authorEl) authorEl.textContent = meta.author || 'Tác Giả';
    const durEl = document.getElementById('repurposer-video-duration');
    if (durEl) durEl.textContent = `${meta.duration_seconds || 0} giây`;
    const transEl = document.getElementById('repurposer-video-transcript-status');
    if (transEl) {
        transEl.textContent = data.has_transcript 
            ? `Đã nạp ${data.transcript_count} dòng phụ đề chuẩn` 
            : 'Phân tích đa phương thức AI';
    }

    // Render Mashups
    renderRepurposerMashups(mashups, originalSource);

    // Switch view default: prioritize Mashup if available
    if (mashups && mashups.length > 0) {
        switchRepurposeViewMode('mashup');
    } else {
        switchRepurposeViewMode('moments');
    }

    const countBadge = document.getElementById('repurposer-moments-count');
    if (countBadge) countBadge.textContent = `${moments.length} Phân Đoạn Viral`;

    const listEl = document.getElementById('repurposer-moments-list');
    if (!listEl) return;
    listEl.innerHTML = '';

    moments.forEach((m, idx) => {
        const score = m.viral_score || 90;
        let scoreBadge = '';
        if (score >= 95) {
            scoreBadge = `<span style="background: linear-gradient(135deg, #ef4444, #f59e0b); color:#fff; font-weight:800; font-size:12px; padding:3px 10px; border-radius:12px; box-shadow:0 2px 8px rgba(239,68,68,0.4);">🔥 ${score}/100 VIRAL TOP 1</span>`;
        } else if (score >= 90) {
            scoreBadge = `<span style="background: linear-gradient(135deg, #10b981, #06b6d4); color:#fff; font-weight:800; font-size:12px; padding:3px 10px; border-radius:12px; box-shadow:0 2px 8px rgba(16,185,129,0.4);">⚡ ${score}/100 CAO TRÀO</span>`;
        } else {
            scoreBadge = `<span style="background: #3b82f6; color:#fff; font-weight:700; font-size:12px; padding:3px 10px; border-radius:12px;">✨ ${score}/100</span>`;
        }

        const momentTypeBadge = m.moment_type ? `<span style="background:rgba(245,158,11,0.15); color:#f59e0b; font-weight:700; font-size:11px; padding:3px 8px; border-radius:6px; border:1px solid rgba(245,158,11,0.3);">${escapeHtml(m.moment_type)}</span>` : '';

        const startMin = Math.floor(m.start_seconds / 60);
        const startSec = Math.floor(m.start_seconds % 60);
        const endMin = Math.floor(m.end_seconds / 60);
        const endSec = Math.floor(m.end_seconds % 60);
        const timeRangeStr = `${String(startMin).padStart(2,'0')}:${String(startSec).padStart(2,'0')} ➔ ${String(endMin).padStart(2,'0')}:${String(endSec).padStart(2,'0')} (${m.duration_seconds}s)`;

        // Hook options pill buttons
        const hookOpts = m.hook_options || [];
        let hookButtonsHtml = '';
        if (hookOpts.length > 0) {
            hookButtonsHtml = hookOpts.map((h, hIdx) => {
                const hType = escapeHtml(h.type || `Kiểu ${hIdx+1}`);
                return `<button type="button" class="btn" onclick="selectRepurposerHook(${idx}, ${hIdx})" style="background:rgba(255,255,255,0.08); border:1px solid rgba(255,255,255,0.18); color:#f8fafc; font-size:10.5px; padding:2px 8px; border-radius:12px; cursor:pointer;" title="Bấm để chọn hook này">
                    🎯 ${hType}
                </button>`;
            }).join(' ');
        }

        // Blueprint data
        const bp = m.edit_blueprint || {};
        const timelineSteps = bp.timeline_steps || [];
        const brollCues = bp.broll_cues || [];
        const subtitleHighlights = bp.subtitle_highlights || [];
        const audioSfx = bp.audio_sfx_cues || [];

        const timelineStepsHtml = timelineSteps.map(step => `
            <div style="display:flex; align-items:flex-start; gap:8px; margin-bottom:6px; font-size:12px; line-height:1.45;">
                <span style="color:#10b981; font-weight:700; flex-shrink:0;">▸</span>
                <span style="color:#e2e8f0;">${escapeHtml(step)}</span>
            </div>
        `).join('');

        const brollBadges = brollCues.map(c => `<span style="background:rgba(6,182,212,0.15); color:#38bdf8; border:1px solid rgba(6,182,212,0.3); font-size:11px; padding:2px 6px; border-radius:4px;">🎬 ${escapeHtml(c)}</span>`).join(' ');
        const subBadges = subtitleHighlights.map(k => `<span style="background:rgba(245,158,11,0.15); color:#fbbf24; border:1px solid rgba(245,158,11,0.3); font-size:11px; font-weight:700; padding:2px 6px; border-radius:4px;">🔤 ${escapeHtml(k)}</span>`).join(' ');
        const sfxBadges = audioSfx.map(s => `<span style="background:rgba(168,85,247,0.15); color:#c084fc; border:1px solid rgba(168,85,247,0.3); font-size:11px; padding:2px 6px; border-radius:4px;">🔊 ${escapeHtml(s)}</span>`).join(' ');

        // Omnichannel data
        const op = m.omnichannel_pack || {};
        const tiktokCaption = op.tiktok_reels_caption || '';
        const fbPost = op.facebook_post || '';
        const ytComment = op.youtube_pinned_comment || '';

        const card = document.createElement('div');
        card.className = 'panel-card';
        card.style.cssText = 'background: rgba(15, 23, 42, 0.85); border: 1px solid rgba(255,255,255,0.1); display:flex; flex-direction:column; justify-content:space-between; transition: transform 0.2s, border-color 0.2s;';
        card.onmouseenter = () => { card.style.borderColor = 'rgba(16, 185, 129, 0.5)'; card.style.transform = 'translateY(-2px)'; };
        card.onmouseleave = () => { card.style.borderColor = 'rgba(255, 255, 255, 0.1)'; card.style.transform = 'translateY(0)'; };

        card.innerHTML = `
            <div>
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; flex-wrap:wrap; gap:8px;">
                    <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                        <span style="font-weight:800; font-size:14px; color:#94a3b8;">#${m.segment_id || (idx+1)}</span>
                        ${momentTypeBadge}
                        ${scoreBadge}
                    </div>
                    <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap;">
                        <span style="font-size:12px; font-weight:700; color:#38bdf8; background:rgba(56,189,248,0.12); padding:3px 8px; border-radius:6px; border:1px solid rgba(56,189,248,0.25);">
                            ⏱️ ${timeRangeStr}
                        </span>
                        ${(m.duration_seconds >= 48 && m.duration_seconds <= 59) ? '<span style="font-size:11px; font-weight:700; color:#10b981; background:rgba(16,185,129,0.15); padding:3px 8px; border-radius:6px; border:1px solid rgba(16,185,129,0.3);">🎯 Chuẩn 50-58s</span>' : ''}
                    </div>
                </div>

                <div style="margin-bottom:12px;">
                    <label style="font-size:11px; color:#94a3b8; font-weight:600; text-transform:uppercase;">Tiêu đề Shorts đề xuất:</label>
                    <input type="text" id="moment-title-${idx}" class="form-input" value="${escapeHtml(m.title || '')}" style="font-size:13px; font-weight:700; color:#fff; margin-top:2px;">
                </div>

                <!-- HOOK 3S & VARIANT BUTTONS -->
                <div style="background:rgba(0,0,0,0.35); border-left:3px solid #f59e0b; padding:10px 12px; border-radius:6px; margin-bottom:12px;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; flex-wrap:wrap; gap:4px;">
                        <span style="font-size:11px; font-weight:700; color:#fbbf24;">🎯 Hook 3s đầu tiên:</span>
                        <div style="display:flex; gap:4px; flex-wrap:wrap;">
                            ${hookButtonsHtml}
                        </div>
                    </div>
                    <div id="moment-hook-display-${idx}" style="font-size:12px; font-style:italic; color:#e2e8f0; line-height:1.45; transition: color 0.3s;">
                        "${escapeHtml(m.hook_quote || '')}"
                    </div>
                </div>

                <div style="font-size:12px; color:#cbd5e1; line-height:1.5; margin-bottom:10px;">
                    <strong style="color:#94a3b8;">Tóm tắt:</strong> ${escapeHtml(m.summary || '')}
                </div>

                <div style="font-size:12px; color:#94a3b8; line-height:1.5; margin-bottom:10px; background:rgba(255,255,255,0.03); padding:8px; border-radius:6px;">
                    <strong style="color:#10b981;">💡 Lý do viral:</strong> ${escapeHtml(m.viral_reason || '')}
                </div>

                <div style="font-size:11px; color:#38bdf8; line-height:1.4; margin-bottom:12px; background:rgba(56,189,248,0.06); padding:8px; border-radius:6px; border:1px dashed rgba(56,189,248,0.3);">
                    <strong>⚖️ Góc nhìn an toàn (Fair Use):</strong> ${escapeHtml(m.safe_commentary_angle || 'Trích dẫn tạo giá trị mới')}
                </div>

                <!-- ACCORDION 1: KỊCH BẢN DỰNG & NHỊP EDIT -->
                <div style="margin-bottom:10px; border:1px solid rgba(255,255,255,0.1); border-radius:8px; overflow:hidden; background:rgba(15,23,42,0.6);">
                    <div onclick="toggleRepurposerSection('blueprint-box-${idx}')" style="display:flex; justify-content:space-between; align-items:center; padding:9px 12px; cursor:pointer; background:rgba(255,255,255,0.04); transition:background 0.2s;">
                        <span style="font-size:12px; font-weight:700; color:#38bdf8; display:flex; align-items:center; gap:6px;">
                            🎬 Kịch Bản Dựng & Nhịp Edit (Director Sheet)
                        </span>
                        <span style="font-size:11px; color:#94a3b8;">▼ Bấm mở</span>
                    </div>
                    <div id="blueprint-box-${idx}" style="display:none; padding:12px; border-top:1px solid rgba(255,255,255,0.08); background:rgba(0,0,0,0.25);">
                        <div style="font-size:11px; font-weight:700; color:#94a3b8; text-transform:uppercase; margin-bottom:6px;">⏱️ Timeline Nhịp Dựng & Cắt Cảnh:</div>
                        <div style="margin-bottom:10px;">
                            ${timelineStepsHtml || '<div style="font-size:11px; color:#64748b;">(Đang cập nhật timeline)</div>'}
                        </div>

                        ${brollCues.length > 0 ? `
                        <div style="margin-bottom:8px;">
                            <div style="font-size:11px; font-weight:700; color:#38bdf8; margin-bottom:4px;">🎥 Gợi ý B-roll chèn cảnh:</div>
                            <div style="display:flex; flex-wrap:wrap; gap:4px;">${brollBadges}</div>
                        </div>` : ''}

                        ${subtitleHighlights.length > 0 ? `
                        <div style="margin-bottom:8px;">
                            <div style="font-size:11px; font-weight:700; color:#fbbf24; margin-bottom:4px;">🔤 Highlight từ khóa phụ đề:</div>
                            <div style="display:flex; flex-wrap:wrap; gap:4px;">${subBadges}</div>
                        </div>` : ''}

                        ${audioSfx.length > 0 ? `
                        <div style="margin-bottom:10px;">
                            <div style="font-size:11px; font-weight:700; color:#c084fc; margin-bottom:4px;">🔊 Hiệu ứng âm thanh SFX:</div>
                            <div style="display:flex; flex-wrap:wrap; gap:4px;">${sfxBadges}</div>
                        </div>` : ''}

                        <button type="button" class="btn btn-secondary" onclick="copyRepurposerPackItem(${idx}, 'blueprint', this)" style="width:100%; justify-content:center; font-size:11px; padding:6px; margin-top:4px; font-weight:600;">
                            📋 Sao Chép Toàn Bộ Kịch Bản Edit
                        </button>
                    </div>
                </div>

                <!-- ACCORDION 2: GÓI PHÂN PHỐI ĐA NỀN TẢNG (OMNICHANNEL PACK) -->
                <div style="margin-bottom:12px; border:1px solid rgba(255,255,255,0.1); border-radius:8px; overflow:hidden; background:rgba(15,23,42,0.6);">
                    <div onclick="toggleRepurposerSection('omnichannel-box-${idx}')" style="display:flex; justify-content:space-between; align-items:center; padding:9px 12px; cursor:pointer; background:rgba(255,255,255,0.04); transition:background 0.2s;">
                        <span style="font-size:12px; font-weight:700; color:#10b981; display:flex; align-items:center; gap:6px;">
                            📱 Gói Đa Kênh (TikTok / Reels / Facebook / YouTube)
                        </span>
                        <span style="font-size:11px; color:#94a3b8;">▼ Bấm mở</span>
                    </div>
                    <div id="omnichannel-box-${idx}" style="display:none; padding:12px; border-top:1px solid rgba(255,255,255,0.08); background:rgba(0,0,0,0.25);">
                        <!-- TikTok / Reels -->
                        <div style="margin-bottom:10px; background:rgba(255,255,255,0.02); padding:8px 10px; border-radius:6px; border:1px solid rgba(255,255,255,0.06);">
                            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                                <span style="font-size:11px; font-weight:700; color:#f43f5e;">🎵 TikTok & Instagram Reels:</span>
                                <button type="button" class="btn" onclick="copyRepurposerPackItem(${idx}, 'tiktok', this)" style="font-size:10px; padding:2px 8px; background:rgba(244,63,94,0.15); color:#fda4af; border:1px solid rgba(244,63,94,0.3); border-radius:4px; cursor:pointer;">
                                    📋 Chép Caption
                                </button>
                            </div>
                            <div style="font-size:11.5px; color:#cbd5e1; line-height:1.45; font-style:italic;">
                                ${escapeHtml(tiktokCaption)}
                            </div>
                        </div>

                        <!-- Facebook Post -->
                        <div style="margin-bottom:10px; background:rgba(255,255,255,0.02); padding:8px 10px; border-radius:6px; border:1px solid rgba(255,255,255,0.06);">
                            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                                <span style="font-size:11px; font-weight:700; color:#3b82f6;">📘 Facebook Reels / Watch:</span>
                                <button type="button" class="btn" onclick="copyRepurposerPackItem(${idx}, 'facebook', this)" style="font-size:10px; padding:2px 8px; background:rgba(59,130,246,0.15); color:#93c5fd; border:1px solid rgba(59,130,246,0.3); border-radius:4px; cursor:pointer;">
                                    📋 Chép Bài Đăng
                                </button>
                            </div>
                            <div style="font-size:11.5px; color:#cbd5e1; line-height:1.45;">
                                ${escapeHtml(fbPost)}
                            </div>
                        </div>

                        <!-- YouTube Pinned Comment -->
                        <div style="background:rgba(255,255,255,0.02); padding:8px 10px; border-radius:6px; border:1px solid rgba(255,255,255,0.06);">
                            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                                <span style="font-size:11px; font-weight:700; color:#ef4444;">🔴 YouTube Pinned Comment:</span>
                                <button type="button" class="btn" onclick="copyRepurposerPackItem(${idx}, 'youtube', this)" style="font-size:10px; padding:2px 8px; background:rgba(239,68,68,0.15); color:#fca5a5; border:1px solid rgba(239,68,68,0.3); border-radius:4px; cursor:pointer;">
                                    📋 Chép Comment Ghim
                                </button>
                            </div>
                            <div style="font-size:11.5px; color:#cbd5e1; line-height:1.45;">
                                ${linkifyText(ytComment)}
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- RENDER SHORT CONTROLS -->
            <div style="border-top: 1px solid rgba(255,255,255,0.08); padding-top:14px; margin-top:8px;">
                <div style="display:grid; grid-template-columns: 1.15fr 0.95fr 0.95fr 0.7fr; gap:8px; margin-bottom:12px; font-size:12px;">
                    <div>
                        <label style="font-size:11px; color:#94a3b8; font-weight:600;">Bố Cục Video:</label>
                        <select id="moment-layout-${idx}" class="form-select" style="font-size:11px; padding:4px 8px; height:auto;">
                            <option value="strip_sandwich_clean_916" selected>✂️ Bóc tách video lõi giữa 9:16 (Xóa sạch Banner trên dưới)</option>
                            <option value="strip_sandwich_square">⏹️ Bóc tách video vuông 1:1 (1080x1080 như Ảnh mẫu)</option>
                            <option value="strip_sandwich_bokeh">🎨 Bóc tách video lõi giữa + Nền Bokeh 9:16</option>
                            <option value="smart_crop_full">🚀 Smart Fullscreen 9:16 (Phóng to lấp đầy màn hình)</option>
                            <option value="center_crop">🎯 Center Crop 9:16 (Cắt giữa)</option>
                            <option value="blur_background">⚠️ Blur Canvas (Khung 16:9 lọt thỏm)</option>
                        </select>
                    </div>
                    <div>
                        <label style="font-size:11px; color:#94a3b8; font-weight:600;">Tiêu Điểm Crop:</label>
                        <select id="moment-focus-${idx}" class="form-select" style="font-size:11px; padding:4px 8px; height:auto;" title="Chọn vị trí người nói để tránh bị lệch mặt">
                            <option value="center" selected>🎯 Giữa (Mặc định)</option>
                            <option value="left">👈 Khung Trái (Khách)</option>
                            <option value="right">👉 Khung Phải (MC)</option>
                        </select>
                    </div>
                    <div>
                        <label style="font-size:11px; color:#94a3b8; font-weight:600;">Âm Thanh (100% US):</label>
                        <select id="moment-audio-${idx}" class="form-select" onchange="toggleMomentVoiceoverPanel(${idx})" style="font-size:11px; padding:4px 8px; height:auto;">
                            <option value="mute_original_voiceover" selected>🎙️ Tắt tiếng cũ + Lồng giọng Mỹ AI (Khuyên dùng)</option>
                            <option value="original">🎵 Giữ âm thanh gốc (+ Anti-ID)</option>
                            <option value="duck_original_voiceover">🎚️ Lồng giọng Mỹ AI + Giảm nhỏ tiếng cũ</option>
                            <option value="mute">🔇 Tắt hoàn toàn âm thanh (Câm)</option>
                        </select>
                    </div>
                    <div>
                        <label style="font-size:11px; color:#94a3b8; font-weight:600;">Phụ Đề (English):</label>
                        <div style="display:flex; align-items:center; gap:6px; height:28px;">
                            <input type="checkbox" id="moment-sub-${idx}" checked style="cursor:pointer; transform:scale(1.2); accent-color:#10b981;">
                            <span style="font-size:11px; color:#34d399; font-weight:700;">Kinetic ASS</span>
                        </div>
                    </div>
                </div>

                <!-- ANTI-WATERMARK LOGO REMOVAL TOOLBAR -->
                <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:10px; padding:6px 12px; background:rgba(6, 182, 212, 0.08); border:1px solid rgba(6, 182, 212, 0.25); border-radius:6px; flex-wrap:wrap; gap:6px;">
                    <label style="display:flex; align-items:center; gap:6px; cursor:pointer; margin:0;">
                        <input type="checkbox" id="moment-delogo-${idx}" checked style="accent-color:#06b6d4; transform:scale(1.2);">
                        <span style="font-size:11.5px; font-weight:700; color:#38bdf8;">✨ Làm mờ / Xóa Logo Watermark (KOHA DISCOVERY, v.v.)</span>
                    </label>
                    <div style="display:flex; align-items:center; gap:6px;">
                        <span style="font-size:10.5px; color:#94a3b8;">Vị trí logo:</span>
                        <select id="moment-delogo-pos-${idx}" class="form-select" style="font-size:10.5px; padding:2px 6px; width:auto; background:#0b1120; color:#f8fafc;">
                            <option value="top_right" selected>Góc trên bên phải (Mặc định)</option>
                            <option value="top_left">Góc trên bên trái</option>
                            <option value="bottom_right">Góc dưới bên phải</option>
                            <option value="bottom_left">Góc dưới bên trái</option>
                        </select>
                    </div>
                </div>

                <!-- VOICEOVER STUDIO PANEL (HIỂN THỊ KHI CHỌN CHẾ ĐỘ LỒNG GIỌNG ĐỌC) -->
                <div id="moment-voiceover-panel-${idx}" style="display:block; margin-bottom:12px; padding:10px 12px; background:rgba(15, 23, 42, 0.75); border:1px solid rgba(56, 189, 248, 0.3); border-radius:8px;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; flex-wrap:wrap; gap:6px;">
                        <span style="font-size:11.5px; font-weight:700; color:#38bdf8; display:flex; align-items:center; gap:6px;">
                            <span>🎙️</span> Kịch bản giọng đọc Mỹ (US English Storytelling):
                        </span>
                        <div style="display:flex; gap:6px;">
                            <button type="button" class="btn btn-secondary" onclick="translateMomentVoiceText(${idx})" style="font-size:10px; padding:2px 8px; background:rgba(6,182,212,0.15); color:#38bdf8; border:1px solid rgba(6,182,212,0.3); border-radius:4px; cursor:pointer;" title="Dịch kịch bản tiếng Việt sang tiếng Anh chuẩn YouTube">
                                🇺🇸 Dịch sang Tiếng Anh
                            </button>
                            <button type="button" class="btn btn-secondary" onclick="resetMomentVoiceText(${idx})" style="font-size:10px; padding:2px 8px; background:rgba(255,255,255,0.06); color:#cbd5e1; border:1px solid rgba(255,255,255,0.12); border-radius:4px; cursor:pointer;" title="Lấy lại văn bản tóm tắt phân đoạn">
                                🔄 Lấy lại tóm tắt
                            </button>
                        </div>
                    </div>
                    <textarea id="moment-voicetext-${idx}" class="form-control" rows="3" style="width:100%; font-size:11.5px; line-height:1.45; background:#0b1120; color:#f8fafc; border:1px solid #334155; border-radius:6px; padding:6px 10px; resize:vertical;" placeholder="Nhập kịch bản thuyết minh tiếng Anh (nếu gõ tiếng Việt hệ thống sẽ tự động dịch sang tiếng Anh viral chuẩn YouTube)...">${escapeHtml(m.transcript_snippet || m.summary || m.hook_quote || m.title || '')}</textarea>
                    
                    <div style="display:grid; grid-template-columns: 1fr 1fr; gap:8px; margin-top:8px;">
                        <div>
                            <label style="font-size:10.5px; color:#94a3b8; display:block; margin-bottom:2px;">Chọn giọng đọc (100% US Neural Voices):</label>
                            <select id="moment-voiceid-${idx}" class="form-select" style="font-size:11px; padding:3px 6px; background:#0b1120;">
                                <option value="en-US-AndrewNeural" selected>🇺🇸 Andrew (US Male - Cuốn hút, chuẩn YouTube Host)</option>
                                <option value="en-US-ChristopherNeural">🇺🇸 Christopher (US Male - Tài liệu, uy quyền)</option>
                                <option value="en-US-GuyNeural">🇺🇸 Guy (US Male - Gay cấn, trinh thám)</option>
                                <option value="en-US-JennyNeural">🇺🇸 Jenny (US Female - Cảm xúc, kịch tính)</option>
                            </select>
                        </div>
                        <div>
                            <label style="font-size:10.5px; color:#94a3b8; display:block; margin-bottom:2px;">Nhạc nền BGM đệm nhẹ:</label>
                            <select id="moment-bgm-${idx}" class="form-select" style="font-size:11px; padding:3px 6px; background:#0b1120;">
                                <option value="none" selected>🔇 Không nhạc nền (Chỉ giọng đọc sạch)</option>
                                <option value="chill">☕ Chill Ambient (Êm đềm, ấm áp)</option>
                                <option value="suspense">🤫 Dark Suspense (Hồi hộp, kịch tính)</option>
                                <option value="epic">⚔️ Inspiring Epic (Hùng tráng)</option>
                                <option value="cyber">⚡ Sci-Fi Cyber (Công nghệ hiện đại)</option>
                            </select>
                        </div>
                    </div>
                    <div style="font-size:10.5px; color:#34d399; margin-top:6px; display:flex; align-items:center; gap:5px;">
                        <span>✨</span> Hệ thống sẽ <strong>tắt 100% âm thanh video cũ</strong>, đọc lời bình bằng <strong>100% tiếng Anh Mỹ</strong> và tự động tạo <strong>phụ đề Kinetic ASS tiếng Anh đồng bộ từng từ</strong>!
                    </div>
                </div>

                <div id="moment-render-box-${idx}">
                    <button class="btn btn-primary" onclick="renderRepurposedMoment(${idx}, '${escapeHtml(originalSource)}')" style="width:100%; justify-content:center; background: linear-gradient(135deg, #10b981, #06b6d4); font-size:13px; font-weight:700; padding:8px;">
                        ✂️ Render Short 9:16 Ngay
                    </button>
                </div>
            </div>
        `;
        listEl.appendChild(card);
    });

    section.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function selectRepurposerHook(idx, hIdx) {
    if (!currentRepurposeData || !currentRepurposeData.moments || !currentRepurposeData.moments[idx]) return;
    const m = currentRepurposeData.moments[idx];
    const hookObj = (m.hook_options && m.hook_options[hIdx]) ? m.hook_options[hIdx] : null;
    const hookText = hookObj ? hookObj.hook : (m.hook_quote || '');

    const el = document.getElementById(`moment-hook-display-${idx}`);
    if (el) {
        el.textContent = `"${hookText}"`;
        el.style.color = '#fbbf24';
        setTimeout(() => { el.style.color = '#e2e8f0'; }, 500);
    }
    m.hook_quote = hookText;
    showToast(`Đã chọn Hook: "${hookText.substring(0, 35)}..."`, 'info');
}

function toggleRepurposerSection(sectionId) {
    const el = document.getElementById(sectionId);
    if (!el) return;
    const isHidden = (el.style.display === 'none' || el.style.display === '');
    el.style.display = isHidden ? 'block' : 'none';
}

function copyRepurposerPackItem(idx, type, btnElement) {
    if (!currentRepurposeData || !currentRepurposeData.moments || !currentRepurposeData.moments[idx]) return;
    const m = currentRepurposeData.moments[idx];
    let textToCopy = '';

    if (type === 'blueprint') {
        const bp = m.edit_blueprint || {};
        const steps = (bp.timeline_steps || []).join('\n');
        const broll = (bp.broll_cues || []).join(', ');
        const subs = (bp.subtitle_highlights || []).join(', ');
        const sfx = (bp.audio_sfx_cues || []).join(', ');
        textToCopy = `🎬 KỊCH BẢN DỰNG SHORTS: ${m.title || ''}\nThời lượng: ${m.duration_seconds}s\nHook 3s: ${m.hook_quote || ''}\n\n⏱️ TIMELINE DỰNG:\n${steps}\n\n🎥 B-ROLL: ${broll}\n🔤 TỪ KHÓA SUBTITLE: ${subs}\n🔊 SFX: ${sfx}`;
    } else if (type === 'tiktok') {
        textToCopy = m.omnichannel_pack?.tiktok_reels_caption || '';
    } else if (type === 'facebook') {
        textToCopy = m.omnichannel_pack?.facebook_post || '';
    } else if (type === 'youtube') {
        textToCopy = m.omnichannel_pack?.youtube_pinned_comment || '';
    }

    if (!textToCopy) {
        showToast('Không có nội dung để sao chép', 'warning');
        return;
    }

    if (navigator.clipboard) {
        navigator.clipboard.writeText(textToCopy).then(() => {
            if (btnElement) {
                const oldText = btnElement.innerText;
                btnElement.innerText = '✓ Đã chép!';
                btnElement.style.background = 'rgba(16,185,129,0.3)';
                setTimeout(() => {
                    btnElement.innerText = oldText;
                    btnElement.style.background = '';
                }, 2000);
            }
            showToast('✓ Đã sao chép nội dung thành công!', 'success');
        }).catch(err => {
            console.warn('Lỗi clipboard:', err);
            showToast('Không thể sao chép tự động', 'error');
        });
    }
}

function toggleMomentVoiceoverPanel(idx) {
    const audioMode = document.getElementById(`moment-audio-${idx}`)?.value || 'original';
    const panel = document.getElementById(`moment-voiceover-panel-${idx}`);
    if (!panel) return;
    if (audioMode === 'mute_original_voiceover' || audioMode === 'duck_original_voiceover' || audioMode === 'ai_voiceover') {
        panel.style.display = 'block';
    } else {
        panel.style.display = 'none';
    }
}

function resetMomentVoiceText(idx) {
    if (!currentRepurposeData || !currentRepurposeData.moments || !currentRepurposeData.moments[idx]) return;
    const m = currentRepurposeData.moments[idx];
    const el = document.getElementById(`moment-voicetext-${idx}`);
    if (el) {
        el.value = m.transcript_snippet || m.summary || m.hook_quote || m.title || '';
        showToast('Đã đặt lại kịch bản theo tóm tắt AI!', 'info');
    }
}

async function translateMomentVoiceText(idx) {
    const el = document.getElementById(`moment-voicetext-${idx}`);
    if (!el || !el.value.trim()) {
        showToast('Chưa có nội dung để dịch!', 'warning');
        return;
    }
    const nicheId = currentRepurposeData?.niche_id || 'general';
    const oldText = el.value;
    el.style.opacity = '0.5';
    showToast('Đang dịch kịch bản sang Tiếng Anh US viral...', 'info');
    try {
        const res = await fetch('/api/repurposer/translate-text', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: oldText, niche_id: nicheId })
        });
        if (!res.ok) throw new Error('Lỗi máy chủ khi dịch');
        const data = await res.json();
        if (data.english_text) {
            el.value = data.english_text;
            showToast('✓ Đã chuyển sang Tiếng Anh chuẩn YouTube Mỹ!', 'success');
        }
    } catch (err) {
        showToast('Lỗi dịch: ' + err.message, 'error');
    } finally {
        el.style.opacity = '1';
    }
}

async function renderRepurposedMoment(idx, originalSource) {
    if (!currentRepurposeData || !currentRepurposeData.moments[idx]) return;
    const m = currentRepurposeData.moments[idx];

    const titleInput = document.getElementById(`moment-title-${idx}`);
    const title = titleInput ? titleInput.value.trim() : m.title;
    const layout = document.getElementById(`moment-layout-${idx}`)?.value || 'strip_sandwich_clean_916';
    const cropFocus = document.getElementById(`moment-focus-${idx}`)?.value || 'center';
    const audioMode = document.getElementById(`moment-audio-${idx}`)?.value || 'mute_original_voiceover';
    const customVoiceText = document.getElementById(`moment-voicetext-${idx}`)?.value?.trim() || '';
    const voiceId = document.getElementById(`moment-voiceid-${idx}`)?.value || 'en-US-AndrewNeural';
    const bgmMood = document.getElementById(`moment-bgm-${idx}`)?.value || 'none';
    const muteOriginal = (audioMode === 'mute_original_voiceover' || audioMode === 'mute');
    const blurWatermark = document.getElementById(`moment-delogo-${idx}`)?.checked ?? false;
    const watermarkPos = document.getElementById(`moment-delogo-pos-${idx}`)?.value || 'top_right';
    const addSub = document.getElementById(`moment-sub-${idx}`)?.checked ?? true;
    const nicheId = currentRepurposeData.niche_id || 'general';
    const mode = currentRepurposeData.mode || 'self_owned';

    const box = document.getElementById(`moment-render-box-${idx}`);
    if (box) {
        box.innerHTML = `
            <div style="text-align:center; padding:10px; background:rgba(16,185,129,0.1); border-radius:8px; border:1px solid rgba(16,185,129,0.3);">
                <div class="spinner" style="width:18px; height:18px; border-width:2px; margin:0 auto 6px auto;"></div>
                <div style="font-size:12px; font-weight:600; color:#10b981;">⚡ VAD, Thuyết Minh US, Bóc Tách & Delogo...</div>
                <div style="font-size:10px; color:#94a3b8; margin-top:2px;">Đang làm mờ logo, cắt khung hình sạch & tạo phụ đề ASS (5 - 15s)</div>
            </div>
        `;
    }

    try {
        const res = await fetch('/api/repurposer/render-clip', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                source: originalSource,
                start_seconds: m.start_seconds,
                end_seconds: m.end_seconds,
                title: title,
                niche_id: nicheId,
                layout_mode: layout,
                crop_focus: cropFocus,
                audio_mode: audioMode,
                mute_original: muteOriginal,
                voice_id: voiceId || null,
                custom_voiceover_text: customVoiceText || null,
                bgm_mood: bgmMood,
                blur_watermark: blurWatermark,
                watermark_position: watermarkPos,
                add_subtitles: addSub,
                subtitle_text: customVoiceText || m.transcript_snippet || m.title,
                timed_transcript: (audioMode === 'original' ? (m.timed_transcript || null) : null),
                mode: mode,
                source_url: currentRepurposeData?.metadata?.webpage_url || (originalSource.startsWith('http') ? originalSource : ''),
                author: currentRepurposeData?.metadata?.author || '',
                channel_name: document.getElementById('repurposer-channel-select')?.options[document.getElementById('repurposer-channel-select')?.selectedIndex]?.text || '',
                device_id: getDeviceId(),
                device_name: getDeviceName()
            })
        });

        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Lỗi khi render video');
        }

        const data = await res.json();
        showToast('🎉 Đã render Short 9:16 thành công!', 'success');

        if (box) {
            box.innerHTML = `
                <div style="display:flex; gap:8px;">
                    <button class="btn btn-primary" onclick="openRepurposedPlayer('${data.relative_url}', '${escapeHtml(title)}', '${data.filename || data.relative_url.split('/').pop()}')" style="flex:1; justify-content:center; background:#10b981; font-size:12px; font-weight:700;">
                        ▶️ Xem Video Short
                    </button>
                    <a href="${data.relative_url}" download class="btn btn-secondary" style="font-size:12px; padding:6px 10px;" title="Tải về">
                        ⬇️
                    </a>
                </div>
            `;
        }

        loadRepurposerClips();

    } catch (e) {
        showToast('Lỗi render: ' + e.message, 'error');
        if (box) {
            box.innerHTML = `
                <button class="btn btn-primary" onclick="renderRepurposedMoment(${idx}, '${escapeHtml(originalSource)}')" style="width:100%; justify-content:center; background: #ef4444; font-size:12px;">
                    ❌ Thử Lại (Lỗi: ${escapeHtml(e.message)})
                </button>
            `;
        }
    }
}

function renderRepurposerMashups(mashups, originalSource) {
    const mashupListEl = document.getElementById('repurposer-mashup-list');
    const mashupCountEl = document.getElementById('repurposer-mashup-count');
    if (mashupCountEl) mashupCountEl.textContent = `${mashups.length} Kịch bản Mashup`;
    if (!mashupListEl) return;
    mashupListEl.innerHTML = '';

    if (!mashups || mashups.length === 0) {
        mashupListEl.innerHTML = `
            <div style="text-align:center; padding:30px; color:var(--text-muted); font-size:13px; background:rgba(0,0,0,0.2); border-radius:10px;">
                Chưa có kịch bản Mashup nào được tạo cho video này.
            </div>
        `;
        return;
    }

    mashups.forEach((m, idx) => {
        const score = m.viral_score || 96;
        const scoreBadge = `<span style="background: linear-gradient(135deg, #ef4444, #f59e0b); color:#fff; font-weight:800; font-size:12px; padding:4px 12px; border-radius:12px; box-shadow:0 2px 8px rgba(239,68,68,0.4);">🔥 ${score}/100 MASHUP VIRAL TOP</span>`;

        let clipsHtml = '';
        (m.clips || []).forEach((c, cIdx) => {
            const roleColors = {
                'HOOK': '#ef4444',
                'SETUP': '#38bdf8',
                'TENSION': '#f59e0b',
                'CLIMAX': '#a855f7',
                'LOOP': '#10b981'
            };
            const roleColor = roleColors[c.role] || '#10b981';
            clipsHtml += `
                <div style="background: rgba(15, 23, 42, 0.7); border: 1px solid rgba(255,255,255,0.08); border-left: 3px solid ${roleColor}; border-radius: 8px; padding: 10px 14px; display: flex; flex-direction: column; gap: 4px;">
                    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:6px;">
                        <span style="font-size:11px; font-weight:800; color:${roleColor};">
                            #${c.clip_id || cIdx+1} [${c.role || 'CLIP'}] ${c.context_badge ? `· <span style="color:#f8fafc; font-weight:700;">${escapeHtml(c.context_badge)}</span>` : ''}
                        </span>
                        <span style="font-size:11px; color:#94a3b8; background:rgba(0,0,0,0.3); padding:2px 6px; border-radius:4px;">
                            ⏱️ ${c.start_seconds}s - ${c.end_seconds}s (~${c.duration}s) · 🔍 ${c.zoom_punch || 1.20}x Zoom
                        </span>
                    </div>
                    <div style="font-size:12px; color:#e2e8f0; font-style:italic;">
                        "${escapeHtml(c.transcript_snippet || c.summary || '')}"
                    </div>
                    ${c.summary && c.summary !== c.transcript_snippet ? `<div style="font-size:11px; color:#94a3b8;">${escapeHtml(c.summary)}</div>` : ''}
                </div>
            `;
        });

        const card = document.createElement('div');
        card.className = 'panel-card';
        card.style.background = 'linear-gradient(135deg, rgba(30, 41, 59, 0.7), rgba(15, 23, 42, 0.9))';
        card.style.border = '1px solid rgba(16, 185, 129, 0.35)';
        card.style.borderRadius = '12px';
        card.style.padding = '20px';

        card.innerHTML = `
            <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:12px; flex-wrap:wrap; gap:8px;">
                <div style="flex:1; min-width:280px;">
                    <div style="display:flex; align-items:center; gap:8px; margin-bottom:6px; flex-wrap:wrap;">
                        ${scoreBadge}
                        <span style="background:rgba(16,185,129,0.15); color:#10b981; font-weight:700; font-size:11px; padding:3px 8px; border-radius:6px;">⚡ ${(m.clips || []).length} Lát cắt xfade</span>
                        <span style="background:rgba(56,189,248,0.15); color:#38bdf8; font-weight:700; font-size:11px; padding:3px 8px; border-radius:6px;">⏱️ ~${m.total_duration || 50}s Tổng</span>
                    </div>
                    <label style="font-size:11px; font-weight:600; color:var(--text-muted); display:block; margin-bottom:4px;">Tiêu đề YouTube Shorts (Tối ưu CTR & Search):</label>
                    <input type="text" id="mashup-title-${idx}" value="${escapeHtml(m.title)}" class="form-input" style="font-weight:700; font-size:14px; width:100%; color:#fff; background:rgba(0,0,0,0.4); border-color:rgba(16,185,129,0.4);" />
                </div>
            </div>

            <!-- Story Arc Timeline -->
            <div style="background:rgba(0,0,0,0.3); border-radius:8px; padding:10px 14px; margin-bottom:14px; border-left:3px solid #06b6d4;">
                <div style="font-size:11px; color:#38bdf8; font-weight:700; margin-bottom:2px;">🧬 Cấu Trúc Nhịp Điệu Kịch Tính (Story Arc):</div>
                <div style="font-size:12px; color:#f1f5f9;">${escapeHtml(m.story_arc || m.summary || '')}</div>
            </div>

            <!-- Micro-Clips List -->
            <div style="display:flex; flex-direction:column; gap:8px; margin-bottom:16px;">
                <div style="font-size:12px; font-weight:700; color:#e2e8f0; display:flex; align-items:center; gap:6px;">
                    <span>🎞️</span> Trình tự lát cắt bóc tách từ video gốc (Ưu tiên cảnh hành động thực địa):
                </div>
                ${clipsHtml}
            </div>

            <!-- Mashup Controls -->
            <div style="background:rgba(15,23,42,0.6); border:1px solid rgba(255,255,255,0.06); border-radius:8px; padding:12px; margin-bottom:16px;">
                <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px; margin-bottom:10px;">
                    <div>
                        <label style="font-size:11px; color:var(--text-muted); display:block; margin-bottom:4px;">📐 Bố Cục Video:</label>
                        <select id="mashup-layout-${idx}" class="form-select" style="font-size:12px; padding:5px 8px; background:#0f172a;">
                            <option value="strip_sandwich_clean_916" selected>✂️ Bóc tách video lõi giữa 9:16 (Xóa sạch Banner trên dưới)</option>
                            <option value="strip_sandwich_square">⏹️ Bóc tách video vuông 1:1 (1080x1080 như Ảnh mẫu)</option>
                            <option value="strip_sandwich_bokeh">🎨 Bóc tách video lõi giữa + Nền Bokeh 9:16</option>
                            <option value="smart_crop_full">🚀 Smart Fullscreen 9:16 (Phóng to lấp đầy màn hình)</option>
                            <option value="center_crop">🎯 Center Crop 9:16 (Cắt chính giữa)</option>
                            <option value="blur_background">⚠️ Blur Canvas (Khung 16:9 lọt thỏm)</option>
                        </select>
                    </div>
                    <div>
                        <label style="font-size:11px; color:var(--text-muted); display:block; margin-bottom:4px;">🎯 Tâm điểm Crop Focus:</label>
                        <select id="mashup-focus-${idx}" class="form-select" style="font-size:12px; padding:5px 8px; background:#0f172a;">
                            <option value="center" selected>Căn Giữa Tâm (Center Focus)</option>
                            <option value="left">Khung Trái (Left Focus / Khách mời)</option>
                            <option value="right">Khung Phải (Right Focus / Host MC)</option>
                        </select>
                    </div>
                    <div>
                        <label style="font-size:11px; color:var(--text-muted); display:block; margin-bottom:4px;">🎙️ Chế độ Audio (100% US):</label>
                        <select id="mashup-audio-${idx}" class="form-select" style="font-size:12px; padding:5px 8px; background:#0f172a;">
                            <option value="mute_original_voiceover" selected>🎙️ Tắt tiếng gốc + Lồng giọng Mỹ AI (Khuyên dùng)</option>
                            <option value="original">🔊 Âm thanh gốc + BGM Ducking</option>
                            <option value="duck_original_voiceover">🎚️ Lồng giọng Mỹ AI + Giữ tiếng gốc nhỏ (Ducking)</option>
                            <option value="mute">🔇 Tắt hoàn toàn âm thanh (Câm)</option>
                        </select>
                    </div>
                    <div>
                        <label style="font-size:11px; color:var(--text-muted); display:block; margin-bottom:4px;">🗣️ Giọng Đọc (100% US Neural):</label>
                        <select id="mashup-voiceid-${idx}" class="form-select" style="font-size:12px; padding:5px 8px; background:#0f172a;">
                            <option value="en-US-AndrewNeural" selected>🇺🇸 Andrew (US Male - YouTube chuẩn)</option>
                            <option value="en-US-ChristopherNeural">🇺🇸 Christopher (US Male - Tài liệu)</option>
                            <option value="en-US-GuyNeural">🇺🇸 Guy (US Male - Gay cấn)</option>
                            <option value="en-US-JennyNeural">🇺🇸 Jenny (US Female - Kịch tính)</option>
                        </select>
                    </div>
                    <div>
                        <label style="font-size:11px; color:var(--text-muted); display:block; margin-bottom:4px;">🎵 Nhạc Nền BGM Ducking:</label>
                        <select id="mashup-bgm-${idx}" class="form-select" style="font-size:12px; padding:5px 8px; background:#0f172a;">
                            <option value="suspense" ${m.bgm_mood === 'suspense' ? 'selected' : ''}>🤫 Dark Suspense (Hồi hộp)</option>
                            <option value="epic" ${m.bgm_mood === 'epic' ? 'selected' : ''}>⚔️ Inspiring Epic (Hùng tráng)</option>
                            <option value="chill" ${m.bgm_mood === 'chill' ? 'selected' : ''}>☕ Chill Ambient (Thư giãn)</option>
                            <option value="cyber" ${m.bgm_mood === 'cyber' ? 'selected' : ''}>⚡ Sci-Fi Cyber (Công nghệ)</option>
                        </select>
                    </div>
                </div>

                <div style="display:flex; gap:16px; flex-wrap:wrap; font-size:12px; color:#cbd5e1; border-top:1px solid rgba(255,255,255,0.06); padding-top:8px;">
                    <label style="display:flex; align-items:center; gap:6px; cursor:pointer;" title="Làm mờ/xóa logo của kênh gốc">
                        <input type="checkbox" id="mashup-delogo-${idx}" checked style="accent-color:#06b6d4; transform:scale(1.15);" />
                        <span style="font-weight:700; color:#38bdf8;">✨ Xóa / Làm mờ Logo Watermark</span>
                    </label>
                    <label style="display:flex; align-items:center; gap:6px; cursor:pointer;" title="Bắt buộc giữ chân người xem Shorts">
                        <input type="checkbox" id="mashup-sub-${idx}" checked style="accent-color:#10b981; transform:scale(1.15);" />
                        <span style="font-weight:700; color:#34d399;">✨ Phụ đề Kinetic ASS Tiếng Anh (Bắt Buộc)</span>
                    </label>
                    <label style="display:flex; align-items:center; gap:6px; cursor:pointer;">
                        <input type="checkbox" id="mashup-sfx-${idx}" checked style="accent-color:#06b6d4;" />
                        <span>💨 SFX Whoosh chuyển cảnh</span>
                    </label>
                </div>
            </div>

            <!-- Action Render Box -->
            <div id="mashup-render-box-${idx}">
                <button class="btn btn-primary" onclick="renderRepurposedMashup(${idx}, '${escapeHtml(originalSource)}')" style="width:100%; justify-content:center; background:linear-gradient(135deg, #10b981, #06b6d4); font-weight:800; font-size:13.5px; padding:10px 16px; box-shadow:0 4px 14px rgba(16,185,129,0.3);">
                    🔥 Nấu Lại Video Shorts Ngay (Render Multi-Clip Mashup)
                </button>
            </div>
        `;
        mashupListEl.appendChild(card);
    });
}

async function renderRepurposedMashup(idx, originalSource) {
    if (!currentRepurposeData || !currentRepurposeData.mashups || !currentRepurposeData.mashups[idx]) return;
    const m = currentRepurposeData.mashups[idx];

    const titleInput = document.getElementById(`mashup-title-${idx}`);
    const title = titleInput ? titleInput.value.trim() : m.title;
    const layout = document.getElementById(`mashup-layout-${idx}`)?.value || 'strip_sandwich_clean_916';
    const cropFocus = document.getElementById(`mashup-focus-${idx}`)?.value || 'center';
    const audioMode = document.getElementById(`mashup-audio-${idx}`)?.value || 'mute_original_voiceover';
    const voiceId = document.getElementById(`mashup-voiceid-${idx}`)?.value || 'en-US-AndrewNeural';
    const muteOriginal = (audioMode === 'mute_original_voiceover' || audioMode === 'mute');
    const blurWatermark = document.getElementById(`mashup-delogo-${idx}`)?.checked ?? false;
    const addSub = document.getElementById(`mashup-sub-${idx}`)?.checked ?? true;
    const enableSfx = document.getElementById(`mashup-sfx-${idx}`)?.checked ?? true;
    const bgmMood = document.getElementById(`mashup-bgm-${idx}`)?.value || 'suspense';
    const nicheId = currentRepurposeData.niche_id || 'general';
    const mode = currentRepurposeData.mode || 'self_owned';

    const box = document.getElementById(`mashup-render-box-${idx}`);
    if (box) {
        box.innerHTML = `
            <div style="text-align:center; padding:12px; background:rgba(16,185,129,0.1); border-radius:8px; border:1px solid rgba(16,185,129,0.3);">
                <div class="spinner" style="width:20px; height:20px; border-width:2px; margin:0 auto 8px auto;"></div>
                <div style="font-size:12.5px; font-weight:700; color:#10b981;">⚡ Đang nấu lại Mashup Multi-Clip 100% US...</div>
                <div style="font-size:11px; color:#94a3b8; margin-top:2px;">Bóc tách ${m.clips.length} lát cắt, xóa watermark, lồng giọng đọc Mỹ & đốt phụ đề ASS (10 - 20s)</div>
            </div>
        `;
    }

    try {
        const res = await fetch('/api/repurposer/render-mashup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                source: originalSource,
                clips: m.clips,
                title: title,
                niche_id: nicheId,
                layout_mode: layout,
                crop_focus: cropFocus,
                audio_mode: audioMode,
                mute_original: muteOriginal,
                voice_id: voiceId || null,
                blur_watermark: blurWatermark,
                watermark_position: 'top_right',
                add_subtitles: addSub,
                enable_sfx: enableSfx,
                bgm_mood: bgmMood,
                mode: mode,
                source_url: currentRepurposeData?.metadata?.webpage_url || (originalSource.startsWith('http') ? originalSource : ''),
                author: currentRepurposeData?.metadata?.author || '',
                channel_name: document.getElementById('repurposer-channel-select')?.options[document.getElementById('repurposer-channel-select')?.selectedIndex]?.text || '',
                device_id: getDeviceId(),
                device_name: getDeviceName()
            })
        });

        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Lỗi khi render Mashup');
        }

        const data = await res.json();
        showToast('🎉 Đã nấu lại Video Shorts Mashup 9:16 thành công!', 'success');

        if (box) {
            box.innerHTML = `
                <div style="display:flex; gap:8px;">
                    <button class="btn btn-primary" onclick="openRepurposedPlayer('${data.relative_url}', '${escapeHtml(title)}', '${data.filename || data.relative_url.split('/').pop()}')" style="flex:1; justify-content:center; background:#10b981; font-size:12px; font-weight:700;">
                        ▶️ Xem Video Short Mashup
                    </button>
                    <a href="${data.relative_url}" download class="btn btn-secondary" style="font-size:12px; padding:6px 10px;" title="Tải về">
                        ⬇️
                    </a>
                </div>
            `;
        }

        loadRepurposerClips();
    } catch (err) {
        showToast('Lỗi: ' + err.message, 'error');
        if (box) {
            box.innerHTML = `
                <button class="btn btn-primary" onclick="renderRepurposedMashup(${idx}, '${escapeHtml(originalSource)}')" style="width:100%; justify-content:center; background:linear-gradient(135deg, #10b981, #06b6d4); font-weight:700;">
                    🔄 Thử Lại Nấu Video Mashup
                </button>
            `;
        }
    }
}

/* ═══════════════════════════════════════════════════════
   MULTI-DEVICE LAN IDENTIFICATION & CONCURRENCY CONTROLLER
══════════════════════════════════════════════════════════ */
let clientDeviceIp = '127.0.0.1';
let allRepurposedClips = [];
let currentClipFilter = 'all';

function getDeviceId() {
    let id = localStorage.getItem('ai_yt_device_id');
    if (!id) {
        id = 'dev_' + Math.random().toString(36).substring(2, 8) + Date.now().toString(36).slice(-4);
        localStorage.setItem('ai_yt_device_id', id);
    }
    return id;
}

function getDeviceName() {
    let name = localStorage.getItem('ai_yt_device_name');
    if (!name) {
        const ua = navigator.userAgent;
        if (/iPhone|iPad|iPod/i.test(ua)) name = 'iPhone / iPad';
        else if (/Android/i.test(ua)) name = 'Thiết Bị Android';
        else if (/Mac/i.test(ua)) name = 'MacBook';
        else if (/Windows/i.test(ua)) name = 'Máy Windows';
        else name = 'Máy Khách LAN';
        
        name += ' #' + getDeviceId().slice(-4);
        localStorage.setItem('ai_yt_device_name', name);
    }
    return name;
}

async function initDeviceIdentity() {
    const devName = getDeviceName();
    const devId = getDeviceId();
    
    const nameEl = document.getElementById('device-name-display');
    const ipEl = document.getElementById('device-ip-display');
    const iconEl = document.getElementById('device-icon-display');
    
    if (nameEl) nameEl.textContent = devName;
    if (iconEl) {
        if (/iPhone|Android|Mobile/i.test(navigator.userAgent)) iconEl.textContent = '📱';
        else iconEl.textContent = '💻';
    }
    
    try {
        const res = await fetch('/api/system/client-info');
        if (res.ok) {
            const data = await res.json();
            clientDeviceIp = data.client_ip || '127.0.0.1';
            if (ipEl) ipEl.textContent = clientDeviceIp;
        }
    } catch (e) {
        console.warn('initDeviceIdentity fetch IP error:', e);
    }
}

function openDeviceNameModal() {
    const modal = document.getElementById('modal-device-name');
    const input = document.getElementById('input-device-name');
    const lblId = document.getElementById('lbl-device-id');
    const lblIp = document.getElementById('lbl-device-ip');
    
    if (input) input.value = getDeviceName();
    if (lblId) lblId.textContent = getDeviceId();
    if (lblIp) lblIp.textContent = clientDeviceIp;
    if (modal) modal.classList.add('active');
}

function closeDeviceNameModal() {
    const modal = document.getElementById('modal-device-name');
    if (modal) modal.classList.remove('active');
}

function saveDeviceName() {
    const input = document.getElementById('input-device-name');
    if (!input) return;
    const newName = input.value.trim();
    if (!newName) {
        showToast('Vui lòng nhập tên thiết bị', 'error');
        return;
    }
    localStorage.setItem('ai_yt_device_name', newName);
    const nameEl = document.getElementById('device-name-display');
    if (nameEl) nameEl.textContent = newName;
    closeDeviceNameModal();
    showToast('✅ Đã lưu tên máy: ' + newName, 'success');
    renderFilteredRepurposedClips();
}

function setClipDeviceFilter(filter) {
    currentClipFilter = filter;
    document.querySelectorAll('.filter-btn-pill').forEach(btn => btn.classList.remove('active'));
    const activeBtn = document.getElementById(`clip-filter-${filter}`);
    if (activeBtn) activeBtn.classList.add('active');
    renderFilteredRepurposedClips();
}

function renderFilteredRepurposedClips() {
    const gallery = document.getElementById('repurposer-clips-gallery');
    if (!gallery) return;

    const myId = getDeviceId();
    let filtered = allRepurposedClips || [];

    if (currentClipFilter === 'mine') {
        filtered = filtered.filter(c => c.device_id === myId || (!c.device_id && !c.client_ip));
    } else if (currentClipFilter === 'others') {
        filtered = filtered.filter(c => c.device_id && c.device_id !== myId);
    }

    if (filtered.length === 0) {
        let msg = 'Chưa có video Shorts nào trong bộ lọc này.';
        if (currentClipFilter === 'mine') msg = 'Bạn chưa render clip nào từ máy này. Hãy chọn một phân đoạn ở trên và bấm Render!';
        else if (currentClipFilter === 'others') msg = 'Chưa có clip nào do các máy khác trong cùng mạng Wi-Fi tạo.';
        gallery.innerHTML = `
            <div style="grid-column: 1 / -1; text-align: center; padding: 30px; color: var(--text-muted); font-size: 13px;">
                ${msg}
            </div>
        `;
        return;
    }

    gallery.innerHTML = '';
    filtered.forEach(c => {
        const isMine = c.device_id === myId || (!c.device_id && !c.client_ip);
        const card = document.createElement('div');
        card.style.cssText = 'background: rgba(15, 23, 42, 0.7); border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; padding: 12px; display: flex; flex-direction: column; gap: 8px; position:relative;';
        
        let deviceBadgeHtml = '';
        if (isMine) {
            deviceBadgeHtml = `<span class="clip-device-badge clip-device-mine" title="Tạo từ máy này">💻 Máy Này</span>`;
        } else if (c.device_name || c.device_id) {
            const devLabel = c.device_name || (c.device_id ? c.device_id.slice(-6) : 'Máy Khác');
            deviceBadgeHtml = `<span class="clip-device-badge clip-device-other" title="Tạo bởi ${escapeHtml(devLabel)} (${c.client_ip || 'LAN'})">👥 ${escapeHtml(devLabel)}</span>`;
        } else {
            deviceBadgeHtml = `<span class="clip-device-badge" style="background:rgba(255,255,255,0.05); color:#94a3b8;">🎞️ Hệ Thống</span>`;
        }

        const displayTitle = c.title || c.filename;
        const pubStatus = c.publish_status || 'draft';
        
        let statusBadgeHtml = '';
        let thumbStatusHtml = '';

        if (pubStatus === 'published') {
            const ytLink = c.shorts_url || c.youtube_url || (c.video_id ? `https://www.youtube.com/shorts/${c.video_id}` : '');
            statusBadgeHtml = `
                <span class="repurpose-status-badge badge-published" 
                      style="font-size:10.5px; padding:2px 7px; border-radius:5px; background:rgba(16,185,129,0.15); color:#10b981; border:1px solid rgba(16,185,129,0.35); font-weight:700; display:inline-flex; align-items:center; gap:3px; white-space:nowrap;"
                      title="Đã đăng lên YouTube${c.published_channel_name ? ' (' + escapeHtml(c.published_channel_name) + ')' : ''}">
                    ${ytLink ? `<a href="${ytLink}" target="_blank" style="color:#10b981; text-decoration:none; display:inline-flex; align-items:center; gap:2px;">✅ Đã đăng <span style="font-size:9px;">↗</span></a>` : '✅ Đã đăng'}
                </span>
            `;
            thumbStatusHtml = `
                <span style="font-size:10px; padding:2px 6px; border-radius:4px; background:rgba(16,185,129,0.92); color:#fff; font-weight:700; box-shadow:0 2px 6px rgba(0,0,0,0.5); backdrop-filter:blur(4px);">
                    ✅ Đã đăng
                </span>
            `;
        } else if (pubStatus === 'scheduled' || pubStatus === 'queued') {
            const schedTitle = c.scheduled_at ? `Lịch: ${escapeHtml(c.scheduled_at)}` : 'Đang trong hàng đợi công chiếu';
            statusBadgeHtml = `
                <span class="repurpose-status-badge badge-queued" 
                      style="font-size:10.5px; padding:2px 7px; border-radius:5px; background:rgba(56,189,248,0.15); color:#38bdf8; border:1px solid rgba(56,189,248,0.35); font-weight:700; display:inline-flex; align-items:center; gap:3px; white-space:nowrap;"
                      title="${schedTitle}">
                    🗓️ Hàng đợi
                </span>
            `;
            thumbStatusHtml = `
                <span style="font-size:10px; padding:2px 6px; border-radius:4px; background:rgba(14,165,233,0.92); color:#fff; font-weight:700; box-shadow:0 2px 6px rgba(0,0,0,0.5); backdrop-filter:blur(4px);">
                    🗓️ Hàng đợi
                </span>
            `;
        } else {
            statusBadgeHtml = `
                <span class="repurpose-status-badge badge-draft" 
                      style="font-size:10.5px; padding:2px 7px; border-radius:5px; background:rgba(148,163,184,0.12); color:#94a3b8; border:1px solid rgba(148,163,184,0.25); font-weight:600; display:inline-flex; align-items:center; gap:3px; white-space:nowrap;"
                      title="Chưa đăng lên YouTube (Bấm Xem để đăng)">
                    ⚪ Chưa đăng
                </span>
            `;
            thumbStatusHtml = `
                <span style="font-size:10px; padding:2px 6px; border-radius:4px; background:rgba(71,85,105,0.85); color:#cbd5e1; font-weight:600; box-shadow:0 2px 6px rgba(0,0,0,0.4); backdrop-filter:blur(4px);">
                    ⚪ Chưa đăng
                </span>
            `;
        }

        card.innerHTML = `
            <div class="repurpose-card-thumb" style="position:relative; width:100%; height:160px; background:#000; border-radius:8px; overflow:hidden; display:flex; align-items:center; justify-content:center; cursor:pointer;" title="Bấm để xem video &amp; đăng kênh">
                <video src="${c.url}#t=0.5" preload="metadata" style="width:100%; height:100%; object-fit:contain; opacity:0.85; pointer-events:none;"></video>
                <div class="repurpose-play-icon" style="position:absolute; width:44px; height:44px; border-radius:50%; background:rgba(16,185,129,0.92); display:flex; align-items:center; justify-content:center; color:#fff; font-size:18px; box-shadow:0 4px 14px rgba(0,0,0,0.6); pointer-events:none;">
                    ▶
                </div>
                <div style="position:absolute; top:8px; left:8px; z-index:2; pointer-events:none;">
                    ${deviceBadgeHtml}
                </div>
                <div style="position:absolute; top:8px; right:8px; z-index:2; pointer-events:none;">
                    ${thumbStatusHtml}
                </div>
            </div>
            <div class="repurpose-card-title" style="font-size:12px; font-weight:600; color:#fff; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; cursor:pointer;" title="${escapeHtml(displayTitle)}">
                ${escapeHtml(displayTitle)}
            </div>
            <div style="display:flex; justify-content:space-between; align-items:center; font-size:11px; color:#94a3b8; margin-top:2px;">
                <span>💾 ${c.size_mb} MB</span>
                <div style="display:flex; gap:4px; align-items:center;">
                    ${statusBadgeHtml}
                    <button type="button" class="btn btn-primary repurposed-card-play-btn" style="font-size:11px; padding:3px 9px; background:#10b981; font-weight:700; border:none; border-radius:6px; cursor:pointer;" title="Xem video &amp; đăng kênh">▶ Xem</button>
                    <a href="${c.url}" download class="btn btn-secondary" style="font-size:11px; padding:3px 8px; border-radius:6px;" title="Tải video về máy">⬇ Tải</a>
                    <button type="button" class="btn btn-secondary repurposed-card-del-btn" style="font-size:11px; padding:3px 7px; color:#f87171; border-color:rgba(239,68,68,0.3); background:rgba(239,68,68,0.08); border-radius:6px; cursor:pointer;" title="Xóa video khỏi danh sách">🗑️</button>
                </div>
            </div>
        `;

        // Gắn sự kiện mở player trực tiếp qua JS event listener (tránh lỗi escape HTML onclick)
        const handleOpen = (e) => {
            if (e) {
                e.preventDefault();
                e.stopPropagation();
            }
            openRepurposedPlayer(c.url, displayTitle, c.filename);
        };

        const thumbEl = card.querySelector('.repurpose-card-thumb');
        if (thumbEl) thumbEl.addEventListener('click', handleOpen);

        const titleEl = card.querySelector('.repurpose-card-title');
        if (titleEl) titleEl.addEventListener('click', handleOpen);

        const playBtn = card.querySelector('.repurposed-card-play-btn');
        if (playBtn) playBtn.addEventListener('click', handleOpen);

        // Gắn sự kiện xóa video an toàn có hộp thoại xác nhận
        const delBtn = card.querySelector('.repurposed-card-del-btn');
        if (delBtn) {
            delBtn.addEventListener('click', async (e) => {
                if (e) {
                    e.preventDefault();
                    e.stopPropagation();
                }
                const confirmMsg = `⚠️ Bạn có chắc muốn XÓA vĩnh viễn video này?\n\n"${displayTitle}"\n\nVideo, phụ đề và dữ liệu metadata liên quan sẽ bị xóa khỏi máy chủ.`;
                if (!confirm(confirmMsg)) return;

                delBtn.disabled = true;
                delBtn.textContent = '⏳';
                try {
                    const res = await fetch(`/api/repurposer/clips/${encodeURIComponent(c.filename)}`, {
                        method: 'DELETE'
                    });
                    const data = await res.json();
                    if (res.ok && data.success) {
                        showToast(data.message || 'Đã xóa video thành công!', 'success');
                        allRepurposedClips = allRepurposedClips.filter(item => item.filename !== c.filename);
                        renderFilteredRepurposedClips();
                    } else {
                        showToast(data.detail || 'Không thể xóa video', 'error');
                        delBtn.disabled = false;
                        delBtn.textContent = '🗑️';
                    }
                } catch (err) {
                    showToast('Lỗi khi xóa video: ' + err.message, 'error');
                    delBtn.disabled = false;
                    delBtn.textContent = '🗑️';
                }
            });
        }

        gallery.appendChild(card);
    });
}

async function loadRepurposerClips() {
    try {
        const res = await fetch('/api/repurposer/clips');
        const data = await res.json();
        allRepurposedClips = data.clips || [];
        renderFilteredRepurposedClips();
    } catch (e) {
        console.warn('loadRepurposerClips error:', e);
    }
}

// ── Biến lưu context của clip đang mở player ──────────────────────────────────
let _currentPlayerClipFilename = '';
let _currentPlayerClipUrl = '';

async function openRepurposedPlayer(videoUrl, title, clipFilename) {
    const modal = document.getElementById('modal-repurposed-player');
    const vidEl = document.getElementById('repurposed-video-element');
    const titleEl = document.getElementById('repurposed-player-title');
    const dlBtn = document.getElementById('repurposed-download-btn');
    const titleInput = document.getElementById('repurposed-publish-title');
    const descInput = document.getElementById('repurposed-publish-desc');
    const descBox = document.getElementById('rpub-desc-box');
    const descIcon = document.getElementById('rpub-desc-icon');
    const statusDiv = document.getElementById('repurposed-publish-status');

    if (!modal || !vidEl) {
        console.error('Không tìm thấy #modal-repurposed-player hoặc #repurposed-video-element');
        return;
    }

    // Lưu context
    _currentPlayerClipFilename = clipFilename || (videoUrl ? videoUrl.split('/').pop() : '');
    _currentPlayerClipUrl = videoUrl;

    // Tìm thông tin clip trong danh sách đã tải
    const currentClipObj = (allRepurposedClips || []).find(item => item.filename === _currentPlayerClipFilename || item.url === videoUrl);

    // Set tiêu đề header
    if (titleEl) titleEl.textContent = title || 'Xem Trước Video Short 9:16';

    // Pre-fill publish title
    if (titleInput) titleInput.value = title || '';

    // Pre-fill publish description & pinned comment (tự động gắn link Store ID: kizakawa-20)
    const pinnedCommentInput = document.getElementById('repurposed-publish-pinned-comment');
    if (descInput) {
        if (currentClipObj && currentClipObj.description) {
            descInput.value = currentClipObj.description;
            if (descBox) descBox.style.display = 'flex';
            if (descIcon) descIcon.textContent = '➖';
        } else {
            descInput.value = '';
        }
    }
    if (pinnedCommentInput) {
        pinnedCommentInput.value = (currentClipObj && currentClipObj.pinned_comment) ? currentClipObj.pinned_comment : '';
    }

    // Cập nhật liên kết thử nghiệm Amazon Store ID kizakawa-20 trực tiếp
    const amzLinkBtn = document.getElementById('repurposed-preview-amazon-link');
    let amzUrl = currentClipObj?.amazon_product?.affiliate_url;
    if (!amzUrl && currentClipObj?.description) {
        const amzMatch = currentClipObj.description.match(/https:\/\/www\.amazon\.com\/[^\s]+/);
        if (amzMatch) amzUrl = amzMatch[0];
    }
    if (amzLinkBtn) amzLinkBtn.href = amzUrl || 'https://www.amazon.com/s?k=wildlife+gear&tag=kizakawa-20';

    // Cập nhật Pre-flight QA Banner
    const qaBanner = document.getElementById('repurposed-qa-banner');
    const qaText = document.getElementById('repurposed-qa-text');
    const qaScore = document.getElementById('repurposed-qa-score');
    if (qaBanner && qaText && qaScore) {
        if (currentClipObj && currentClipObj.qa_report) {
            const qr = currentClipObj.qa_report;
            qaBanner.style.display = 'flex';
            qaText.textContent = qr.summary || (qr.passed ? '✅ QA PASSED' : '⚠️ CẦN LƯU Ý');
            qaScore.textContent = `${qr.score || 95}/100`;
            if (qr.passed) {
                qaBanner.style.borderColor = 'rgba(16,185,129,0.4)';
                qaBanner.style.background = 'rgba(16,185,129,0.12)';
                qaBanner.style.color = '#34d399';
                qaScore.style.background = '#10b981';
                qaScore.style.color = '#000';
            } else {
                qaBanner.style.borderColor = 'rgba(239,68,68,0.4)';
                qaBanner.style.background = 'rgba(239,68,68,0.12)';
                qaBanner.style.color = '#f87171';
                qaScore.style.background = '#ef4444';
                qaScore.style.color = '#fff';
            }
        } else {
            qaBanner.style.display = 'none';
        }
    }

    // Reset status
    if (statusDiv) { statusDiv.style.display = 'none'; statusDiv.textContent = ''; }

    // Download button
    if (dlBtn) dlBtn.href = videoUrl;

    // HIỂN THỊ MODAL NGAY LẬP TỨC (đảm bảo hiển thị bất kể cache hay network)
    modal.classList.add('visible', 'active');
    modal.style.display = 'flex';
    modal.style.opacity = '1';
    modal.style.visibility = 'visible';
    modal.style.zIndex = '99999';

    // Đợi layout frame hoàn tất để video element nhận diện được kích thước khung hình
    requestAnimationFrame(() => {
        try {
            vidEl.pause();
            vidEl.currentTime = 0;
            vidEl.src = videoUrl;
            vidEl.load();
            const playPromise = vidEl.play();
            if (playPromise !== undefined) {
                playPromise.catch(err => {
                    console.warn('Tự động phát bị chặn (chờ người dùng tương tác):', err);
                });
            }
        } catch(e) {
            console.warn('Lỗi load video element:', e);
        }
    });

    // Duration badge
    vidEl.onloadedmetadata = () => {
        const durBadge = document.getElementById('repurposed-player-duration-badge');
        if (durBadge && vidEl.duration) {
            const d = Math.round(vidEl.duration);
            durBadge.textContent = `⏱️ ${d}s • 9:16 Short Ready`;
        }
    };

    // Load danh sách kênh YouTube bất đồng bộ (không chặn việc hiển thị modal)
    try {
        await populateRepurposedPublishChannels();
    } catch (e) {
        console.warn('populateRepurposedPublishChannels error:', e);
    }
}

function closeRepurposedPlayerModal() {
    const modal = document.getElementById('modal-repurposed-player');
    const vidEl = document.getElementById('repurposed-video-element');
    if (vidEl) {
        vidEl.pause();
        vidEl.removeAttribute('src');
        vidEl.load();
    }
    if (modal) {
        modal.classList.remove('visible', 'active');
        modal.style.display = 'none';
        modal.style.opacity = '0';
        modal.style.visibility = 'hidden';
    }
    _currentPlayerClipFilename = '';
    _currentPlayerClipUrl = '';
}

async function deleteCurrentRepurposedPlayerClip() {
    if (!_currentPlayerClipFilename) {
        showToast('Không xác định được file video để xóa!', 'error');
        return;
    }
    const title = document.getElementById('repurposed-publish-title')?.value || _currentPlayerClipFilename;
    const confirmMsg = `⚠️ Bạn có chắc muốn XÓA vĩnh viễn video này khỏi hệ thống?\n\n"${title}"\n\nVideo, phụ đề và metadata liên quan sẽ bị xóa hoàn toàn.`;
    if (!confirm(confirmMsg)) return;

    try {
        const res = await fetch(`/api/repurposer/clips/${encodeURIComponent(_currentPlayerClipFilename)}`, {
            method: 'DELETE'
        });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast(data.message || 'Đã xóa video thành công!', 'success');
            allRepurposedClips = allRepurposedClips.filter(item => item.filename !== _currentPlayerClipFilename);
            closeRepurposedPlayerModal();
            renderFilteredRepurposedClips();
        } else {
            showToast(data.detail || 'Không thể xóa video', 'error');
        }
    } catch (err) {
        showToast('Lỗi khi xóa video: ' + err.message, 'error');
    }
}

/* ═══════════════════════════════════════════════════════
   GOOGLE TRENDS & BẮT TREND AI TAB CONTROLLER
══════════════════════════════════════════════════════════ */

let _cachedTrendsData = [];
let _isTrendsInitialized = false;
let _lastTrendBridgeSuggestions = [];

function updateTrendsChannelDropdown(channels) {
    const sel = document.getElementById('trends-channel');
    if (!sel) return;
    const currentVal = sel.value;
    sel.innerHTML = '<option value="">-- Mặc định (hoặc nhập tự do) --</option>';
    if (channels && channels.length > 0) {
        channels.forEach(ch => {
            const opt = document.createElement('option');
            opt.value = ch.id;
            const langTag = (ch.language || 'en').toUpperCase();
            opt.textContent = `${ch.title || ch.name || ch.id} (${ch.niche || 'Tổng hợp'} · ${langTag})`;
            sel.appendChild(opt);
        });
    }
    if (currentVal && sel.querySelector(`option[value="${currentVal}"]`)) {
        sel.value = currentVal;
    } else if (channels && channels.length > 0) {
        sel.value = channels[0].id;
    }
}

function onTrendsChannelChange() {
    const channelId = document.getElementById('trends-channel')?.value || '';
    const geoSel = document.getElementById('trends-geo');
    if (channelId && typeof allChannels !== 'undefined' && Array.isArray(allChannels) && geoSel) {
        const ch = allChannels.find(c => c.id === channelId);
        if (ch && ch.language) {
            const langLow = ch.language.toLowerCase();
            const targetGeo = langLow.startsWith('pt') ? 'BR' : (langLow.startsWith('vi') ? 'VN' : 'US');
            if (geoSel.value !== targetGeo) {
                geoSel.value = targetGeo;
                fetchLiveTrends();
            }
        }
    }
    generateTrendBridges();
}

function initTrendsTab() {
    if (typeof allChannels !== 'undefined' && allChannels.length > 0) {
        updateTrendsChannelDropdown(allChannels);
    }
    if (!_isTrendsInitialized) {
        _isTrendsInitialized = true;
        onTrendsChannelChange();
        fetchLiveTrends();
    }
}

async function fetchLiveTrends() {
    const geo = document.getElementById('trends-geo')?.value || 'US';
    const listContainer = document.getElementById('trends-list-container') || document.getElementById('trends-live-list');
    const spinner = document.getElementById('trends-loading-spinner');
    const badge = document.getElementById('trends-count-badge');

    if (spinner) spinner.style.display = 'block';
    if (listContainer) {
        listContainer.innerHTML = `
            <div style="text-align: center; padding: 40px; color: var(--text-muted);">
                <div class="spinner" style="margin: 0 auto 12px; width: 28px; height: 28px; border: 3px solid rgba(255,255,255,0.1); border-top-color: var(--accent-blue); border-radius: 50%; animation: spin 0.8s linear infinite;"></div>
                Đang quét dữ liệu thời gian thực từ Google Trends (${geo})...
            </div>
        `;
    }

    try {
        const res = await fetch(`/api/trends/live?geo=${geo}`);
        const data = await res.json();

        if (spinner) spinner.style.display = 'none';

        if (data.status === 'success' && Array.isArray(data.trends) && data.trends.length > 0) {
            _cachedTrendsData = data.trends;
            if (badge) badge.textContent = `${data.trends.length} xu hướng`;
            renderLiveTrends(_cachedTrendsData);
        } else {
            if (badge) badge.textContent = '0 xu hướng';
            if (listContainer) {
                listContainer.innerHTML = `
                    <div style="text-align: center; padding: 30px; color: var(--accent-rose);">
                        ⚠️ Không thể tải danh sách Google Trends. Vui lòng bấm "Làm Mới Xu Hướng" để thử lại.
                    </div>
                `;
            }
        }
    } catch (err) {
        if (spinner) spinner.style.display = 'none';
        console.error('Error fetching live trends:', err);
        if (listContainer) {
            listContainer.innerHTML = `
                <div style="text-align: center; padding: 30px; color: var(--accent-rose);">
                    ❌ Lỗi kết nối Google Trends: ${err.message}
                </div>
            `;
        }
    }
}

function renderLiveTrends(trends) {
    const listContainer = document.getElementById('trends-list-container') || document.getElementById('trends-live-list');
    if (!listContainer) return;

    if (!trends || trends.length === 0) {
        listContainer.innerHTML = `
            <div style="text-align: center; padding: 30px; color: var(--text-muted);">
                Không có chủ đề nào khớp với bộ lọc.
            </div>
        `;
        return;
    }

    listContainer.innerHTML = trends.map((item, idx) => {
        const titleSafe = (item.title || item.query || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
        const newsItems = (item.news_items || []).slice(0, 2);
        const approxVol = item.approx_traffic || item.traffic || 'Đang tăng mạnh';

        return `
            <div class="trend-card" style="background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; padding: 14px; display: flex; flex-direction: column; justify-content: space-between; transition: all 0.2s;" onmouseenter="this.style.borderColor='rgba(59,130,246,0.4)'; this.style.transform='translateY(-2px)'" onmouseleave="this.style.borderColor='rgba(255,255,255,0.08)'; this.style.transform='none'">
                <div>
                    <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; background: rgba(59,130,246,0.15); color: #60a5fa; border: 1px solid rgba(59,130,246,0.3); border-radius: 12px; padding: 2px 8px;">
                            #${idx + 1}
                        </span>
                        <span style="font-size: 11px; font-weight: 600; background: rgba(16,185,129,0.15); color: #34d399; border-radius: 4px; padding: 2px 6px;">
                            🔥 ${escapeHtml(approxVol)}
                        </span>
                    </div>
                    <h4 style="font-size: 14.5px; font-weight: 700; margin: 0 0 6px 0; color: #f8fafc; line-height: 1.4;">
                        ${escapeHtml(item.title || item.query)}
                    </h4>
                    ${item.snippet ? `
                        <p style="font-size: 12px; color: var(--text-muted); margin: 0 0 8px 0; line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">
                            ${escapeHtml(item.snippet)}
                        </p>
                    ` : ''}
                    ${newsItems.length > 0 ? `
                        <div style="border-top: 1px dashed rgba(255,255,255,0.1); padding-top: 6px; margin-top: 6px;">
                            ${newsItems.map(n => `
                                <div style="font-size: 11px; color: #cbd5e1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-bottom: 2px;">
                                    📰 <a href="${escapeHtml(n.url)}" target="_blank" style="color: #93c5fd; text-decoration: none;">${escapeHtml(n.title)}</a>
                                </div>
                            `).join('')}
                        </div>
                    ` : ''}
                </div>
                <div style="margin-top: 10px; display: flex; gap: 6px;">
                    <button class="btn btn-primary btn-sm" style="flex: 1.2; font-size: 11.5px; padding: 6px 8px; display: flex; align-items: center; justify-content: center; gap: 4px; background: linear-gradient(135deg, #ef4444, #f97316); border: none;" onclick="generateTrendBridges('${titleSafe}')">
                        ⚡ Bắc Cầu Sang Kênh
                    </button>
                    <button class="btn btn-secondary btn-sm" style="flex: 1; font-size: 11px; padding: 6px 8px;" onclick="quickCreateFromTrend('${titleSafe}', '', 'Google Trends')">
                        🚀 Sản Xuất
                    </button>
                    <button class="btn btn-secondary btn-sm" style="font-size: 11px; padding: 6px 8px;" title="Điền vào ô chủ đề" onclick="fillTopicToCreate('${titleSafe}')">
                        ✍️ Sửa
                    </button>
                </div>
            </div>
        `;
    }).join('');
}

function filterTrendCards() {
    const q = (document.getElementById('trends-search')?.value || '').toLowerCase().trim();
    if (!q) {
        renderLiveTrends(_cachedTrendsData);
        return;
    }
    const filtered = _cachedTrendsData.filter(item => 
        (item.title && item.title.toLowerCase().includes(q)) ||
        (item.snippet && item.snippet.toLowerCase().includes(q))
    );
    renderLiveTrends(filtered);
}

async function generateTrendBridges(specificKeyword = '') {
    const geo = document.getElementById('trends-geo')?.value || 'US';
    const channelId = document.getElementById('trends-channel')?.value || '';
    const bridgeContainer = document.getElementById('bridge-list-container') || document.getElementById('trend-suggestions-results');
    const bannerEl = document.getElementById('bridge-portfolio-banner');
    const spinner = document.getElementById('bridge-loading-spinner');
    const btn = document.getElementById('btn-bridge-trends');

    if (btn) btn.disabled = true;
    if (spinner) spinner.style.display = 'block';

    if (bridgeContainer) {
        bridgeContainer.innerHTML = `
            <div style="text-align: center; padding: 35px; color: var(--text-muted);">
                <div class="spinner" style="margin: 0 auto 12px; width: 28px; height: 28px; border: 3px solid rgba(255,255,255,0.1); border-top-color: var(--accent-purple); border-radius: 50%; animation: spin 0.8s linear infinite;"></div>
                AI đang bắc cầu xu hướng ${specificKeyword ? `<strong>"${escapeHtml(specificKeyword)}"</strong>` : `Google Trends (${geo})`} sang ngách & ngôn ngữ kênh của bạn...
            </div>
        `;
    }

    try {
        let url = `/api/trends/channel-suggestions?geo=${encodeURIComponent(geo)}`;
        if (channelId) url += `&channel_id=${encodeURIComponent(channelId)}`;
        if (specificKeyword) url += `&keyword=${encodeURIComponent(specificKeyword)}`;

        const res = await fetch(url);
        const data = await res.json();

        if (spinner) spinner.style.display = 'none';

        if (bannerEl) {
            const satList = Array.isArray(data.saturated_concepts) ? data.saturated_concepts : [];
            const langBadge = (data.language || 'en').toUpperCase();
            bannerEl.style.display = 'block';
            bannerEl.innerHTML = `
                <div style="display:flex; justify-content:space-between; flex-wrap:wrap; gap:8px; align-items:center;">
                    <div>
                        <span class="badge badge-blue" style="margin-right:6px;">🌐 Ngôn ngữ đích: ${escapeHtml(langBadge)}</span>
                        <span class="badge badge-purple">🎯 Ngách: ${escapeHtml(data.niche || 'general')}</span>
                    </div>
                    ${data.top_performer ? `<div style="color:#34d399; font-size:11.5px;">🏆 <strong>Top 1 Kênh:</strong> ${escapeHtml(data.top_performer)}</div>` : ''}
                </div>
                ${satList.length > 0 ? `<div style="margin-top:5px; color:#fca5a5; font-size:11px;">🚫 <strong>Đang khóa chống bão hòa:</strong> ${satList.map(k => `<code style="background:rgba(239,68,68,0.15); padding:1px 5px; border-radius:4px; margin-right:4px;">${escapeHtml(k)}</code>`).join('')}</div>` : ''}
            `;
        }

        if (data.status === 'success' && Array.isArray(data.suggestions) && data.suggestions.length > 0) {
            _lastTrendBridgeSuggestions = data.suggestions;
            renderTrendSuggestions(data.suggestions, data.niche, data.language);
        } else {
            if (bridgeContainer) {
                bridgeContainer.innerHTML = `
                    <div style="text-align: center; padding: 25px; color: var(--accent-rose);">
                        ⚠️ Chưa sinh được ý tưởng bắt trend. Vui lòng bấm "⚡ Bắc Cầu Bắt Trend (AI)" để thử lại!
                    </div>
                `;
            }
        }
    } catch (err) {
        if (spinner) spinner.style.display = 'none';
        console.error('Lỗi khi bắc cầu trend:', err);
        if (bridgeContainer) {
            bridgeContainer.innerHTML = `
                <div style="text-align: center; padding: 25px; color: var(--accent-rose);">
                    ❌ Lỗi: ${err.message}
                </div>
            `;
        }
    } finally {
        if (btn) btn.disabled = false;
    }
}

function renderTrendSuggestions(suggestions, niche, language = 'en') {
    const container = document.getElementById('bridge-list-container') || document.getElementById('trend-suggestions-results');
    if (!container) return;

    container.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; padding: 0 4px; flex-wrap:wrap; gap:6px;">
            <div style="font-size: 13px; font-weight: 700; color: #a5b4fc;">
                🎯 5 CONCEPT BẮT TREND NGÁCH: <span style="color:#facc15;">${escapeHtml(niche || 'Tất cả')}</span> · <span style="color:#38bdf8;">${escapeHtml((language || 'en').toUpperCase())}</span>
            </div>
            <span style="font-size: 11px; color: var(--text-muted);">(Chuẩn Hook 3s & Chống Trùng Lặp)</span>
        </div>
        <div style="display: flex; flex-direction: column; gap: 12px;">
            ${suggestions.map((s, idx) => {
                const retTxt = s.predicted_retention || '85% - 92%';
                const fixTxt = s.fixes_flaw || '';

                return `
                    <div style="background: rgba(0,0,0,0.35); border: 1px solid rgba(168,85,247,0.3); border-radius: 8px; padding: 14px; display: flex; flex-direction: column; justify-content: space-between;">
                        <div>
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; flex-wrap:wrap; gap:6px;">
                                <span style="font-size: 11px; font-weight: 700; color: #c084fc;">Ý tưởng #${idx + 1} · ${escapeHtml(s.viral_formula || 'Trend-Jacking')}</span>
                                <div style="display:flex; gap:6px; align-items:center;">
                                    <span class="badge badge-green" style="font-size:10px;">📈 Retention: ${escapeHtml(retTxt)}</span>
                                    <span style="font-size: 10px; color: #94a3b8; background: rgba(255,255,255,0.06); padding: 1px 6px; border-radius: 4px;">🔥 Trend: ${escapeHtml(s.origin_trend || s.trending_source_keyword || 'Hot')}</span>
                                </div>
                            </div>
                            <div style="font-size: 14.5px; font-weight: 700; color: #f8fafc; margin-bottom: 6px; line-height: 1.35;">
                                ${escapeHtml(s.viral_title || s.topic)}
                            </div>
                            <div style="font-size: 12px; color: #e2e8f0; margin-bottom: 6px; line-height: 1.4;">
                                🎣 <strong style="color:#60a5fa;">Hook 3 Giây:</strong> "${escapeHtml(s.hook_3s || s.hook || '')}"
                            </div>
                            <div style="font-size: 12px; color: #cbd5e1; margin-bottom: 6px; line-height: 1.35;">
                                🌉 <strong style="color:#a7f3d0;">Vì sao bùng nổ:</strong> ${escapeHtml(s.angle || s.why_viral || '')}
                            </div>
                            ${fixTxt ? `<div style="font-size: 11.5px; color: #fca5a5; margin-bottom: 8px; line-height: 1.35;">🛡️ <strong>Chống Flop:</strong> ${escapeHtml(fixTxt)}</div>` : ''}
                            <div style="background: rgba(234,179,8,0.12); border: 1px dashed rgba(234,179,8,0.35); border-radius: 4px; padding: 6px 10px; margin-bottom: 10px; display: flex; align-items: center; gap: 8px;">
                                <span style="font-size: 11px; color: #fde047; font-weight: 700;">🖼️ Thumbnail 1 Giây:</span>
                                <span style="font-size: 12.5px; font-weight: 800; color: #ffffff; letter-spacing: 0.5px;">"${escapeHtml(s.thumbnail_text || 'WATCH THIS')}"</span>
                            </div>
                        </div>
                        <div style="display: flex; gap: 8px; margin-top: 4px;">
                            <button class="btn btn-primary btn-sm" style="flex: 1.5; font-size: 11.5px; padding: 7px 10px; background: linear-gradient(135deg, #10b981, #059669); border: none; font-weight: 700;" onclick="applyTrendBridgeIdea(${idx}, 'produce')">
                                🚀 Sản Xuất Video Ngay
                            </button>
                            <button class="btn btn-secondary btn-sm" style="flex: 1; font-size: 11.5px; padding: 7px 10px;" onclick="applyTrendBridgeIdea(${idx}, 'edit')">
                                ✍️ Nạp Vào Xưởng Kịch Bản
                            </button>
                        </div>
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

function applyTrendBridgeIdea(idx, mode = 'edit') {
    const s = _lastTrendBridgeSuggestions[idx];
    if (!s) return;
    const titleOrTopic = s.viral_title || s.topic || '';
    const hookTxt = s.hook_3s || s.hook || '';
    const origTrend = s.origin_trend || s.trending_source_keyword || 'Google Trends';

    if (mode === 'produce') {
        quickCreateFromTrend(titleOrTopic, hookTxt, origTrend);
        return;
    }

    const trendsCh = document.getElementById('trends-channel')?.value || '';
    const notesParts = [];
    if (s.topic && s.viral_title && s.topic !== s.viral_title) {
        notesParts.push(`Triển khai cốt truyện: ${s.topic}`);
    }
    if (hookTxt) notesParts.push(`Bắt buộc dùng câu Hook 0-3s: "${hookTxt}"`);
    if (s.thumbnail_text) notesParts.push(`Chữ Thumbnail 1 giây: "${s.thumbnail_text}"`);
    if (s.pinned_comment) notesParts.push(`Câu hỏi ghim bình luận: "${s.pinned_comment}"`);
    if (s.fixes_flaw) notesParts.push(`Lưu ý chống flop: ${s.fixes_flaw}`);

    fillTopicToCreate(titleOrTopic, notesParts.join(' | '), trendsCh);
}

function fillTopicToCreate(topic, directorNotes = '', channelId = '') {
    switchTab('create');
    if (channelId) {
        const chSel = document.getElementById('input-channel');
        if (chSel && chSel.querySelector(`option[value="${channelId}"]`)) {
            chSel.value = channelId;
            if (typeof chSel.onchange === 'function') chSel.onchange();
        }
    }
    const topicInput = document.getElementById('input-topic');
    if (topicInput) {
        topicInput.value = topic;
        topicInput.focus();
        topicInput.style.borderColor = '#10b981';
        setTimeout(() => { topicInput.style.borderColor = ''; }, 1500);
    }
    if (directorNotes) {
        const notesInput = document.getElementById('input-director-notes');
        if (notesInput) {
            notesInput.value = directorNotes;
            notesInput.style.borderColor = '#38bdf8';
            setTimeout(() => { notesInput.style.borderColor = ''; }, 1500);
        }
    }
    showToast('🎯 Đã nạp chủ đề & chỉ đạo đạo diễn vào trình tạo kịch bản AI!', 'success');
}

async function quickCreateFromTrend(topic, hook, category) {
    if (!confirm(`Bạn có muốn đưa chủ đề bắt trend sau vào Pipeline sản xuất ngay?

"${topic}"`)) {
        return;
    }

    const channelId = document.getElementById('trends-channel')?.value || '';
    showToast('Đang gửi lệnh sản xuất video vào hàng đợi...', 'info');

    try {
        const res = await fetch('/api/trends/quick-create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                topic: topic,
                hook: hook || '',
                category: category || 'Google Trends',
                channel_id: channelId || null,
                aspect_ratio: '9:16'
            })
        });
        const data = await res.json();
        if (data.status === 'success' || data.job_id) {
            showToast(`✅ Đã tạo Job #${data.job_id}! Chuyển sang Pipeline để theo dõi.`, 'success');
            setTimeout(() => {
                switchTab('pipeline');
                if (typeof loadJobs === 'function') loadJobs();
            }, 1000);
        } else {
            showToast(`Lỗi: ${data.message || 'Không thể tạo job'}`, 'error');
        }
    } catch (err) {
        console.error('Error in quickCreateFromTrend:', err);
        showToast(`❌ Lỗi kết nối: ${err.message}`, 'error');
    }
}

// Aliases for compatibility
window.fetchLiveTrends = fetchLiveTrends;
window.refreshLiveTrends = fetchLiveTrends;
window.generateTrendBridges = generateTrendBridges;
window.fetchTrendSuggestions = generateTrendBridges;
window.renderLiveTrends = renderLiveTrends;
window.renderTrendSuggestions = renderTrendSuggestions;
window.quickCreateFromTrend = quickCreateFromTrend;
window.fillTopicToCreate = fillTopicToCreate;


async function populateRepurposedPublishChannels() {
    const sel = document.getElementById('repurposed-publish-channel');
    if (!sel) return;

    // Auto-fetch channels nếu chưa có
    if (!allChannels || allChannels.length === 0) {
        await fetchChannels();
    }

    sel.innerHTML = '';
    if (!allChannels || allChannels.length === 0) {
        sel.innerHTML = '<option value="">-- Chưa có kênh được liên kết --</option>';
        return;
    }
    allChannels.forEach(ch => {
        const opt = document.createElement('option');
        opt.value = ch.id;
        opt.textContent = `${ch.name} (${ch.niche || 'general'})`;
        // Pre-select kênh khớp với niche đang chọn trong repurposer
        const activeNiche = document.getElementById('repurposer-niche-select')?.value;
        if (activeNiche && ch.niche === activeNiche) opt.selected = true;
        sel.appendChild(opt);
    });
}

function toggleRepurposedDescBox() {
    const box = document.getElementById('rpub-desc-box');
    const icon = document.getElementById('rpub-desc-icon');
    if (!box) return;
    if (box.style.display === 'none' || !box.style.display) {
        box.style.display = 'flex';
        if (icon) icon.textContent = '➖';
    } else {
        box.style.display = 'none';
        if (icon) icon.textContent = '➕';
    }
}

async function publishRepurposedClip(mode) {
    // mode: 'now' = đăng ngay public | 'queue' = auto hẹn giờ vàng
    const channelSel = document.getElementById('repurposed-publish-channel');
    const titleInput = document.getElementById('repurposed-publish-title');
    const descInput  = document.getElementById('repurposed-publish-desc');
    const pinnedInput= document.getElementById('repurposed-publish-pinned-comment');
    const tagsInput  = document.getElementById('repurposed-publish-tags');
    const statusDiv  = document.getElementById('repurposed-publish-status');
    const btnNow     = document.getElementById('btn-repurposed-publish');
    const btnQueue   = document.getElementById('btn-repurposed-queue');

    const channelId  = channelSel?.value;
    const title      = titleInput?.value.trim() || '';
    const desc       = descInput?.value.trim() || '';
    const pinnedComm = pinnedInput?.value.trim() || '';
    const tags       = tagsInput?.value.trim() || '';

    if (!_currentPlayerClipFilename) {
        showToast('Không xác định được file clip!', 'error');
        return;
    }
    if (!channelId) {
        showToast('Vui lòng chọn kênh YouTube đăng tải!', 'error');
        channelSel?.focus();
        return;
    }
    if (!title) {
        showToast('Vui lòng nhập tiêu đề video!', 'error');
        titleInput?.focus();
        return;
    }

    const isQueue = (mode === 'queue');
    const actionLabel = isQueue ? '🗓️ Đang lên lịch hẹn giờ vàng...' : '🚀 Đang đăng lên YouTube...';

    // Disable buttons
    if (btnNow) btnNow.disabled = true;
    if (btnQueue) btnQueue.disabled = true;
    if (statusDiv) {
        statusDiv.style.display = 'block';
        statusDiv.style.background = 'rgba(56,189,248,0.1)';
        statusDiv.style.color = '#38bdf8';
        statusDiv.style.border = '1px solid rgba(56,189,248,0.3)';
        statusDiv.textContent = actionLabel;
    }

    try {
        const res = await fetch('/api/repurposer/publish-clip', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                clip_filename: _currentPlayerClipFilename,
                channel_id: channelId,
                title: title,
                description: desc,
                tags: tags,
                auto_schedule: isQueue,
                pinned_comment: pinnedComm,
            })
        });
        const data = await res.json();

        if (!res.ok) {
            throw new Error(data.detail || 'Lỗi từ server');
        }

        // Success
        if (statusDiv) {
            statusDiv.style.background = 'rgba(16,185,129,0.12)';
            statusDiv.style.color = '#10b981';
            statusDiv.style.border = '1px solid rgba(16,185,129,0.3)';
            const ytLink = data.shorts_url || data.youtube_url || '';
            statusDiv.innerHTML = `
                ${data.message || '✅ Thành công!'}<br>
                ${ytLink ? `<a href="${escapeHtml(ytLink)}" target="_blank" style="color:#38bdf8; text-decoration:underline; font-size:11px;">🔗 Xem trên YouTube Shorts</a>` : ''}
                ${data.scheduled_at ? `<br><span style="font-size:10.5px; color:#94a3b8;">📅 Hẹn giờ: ${escapeHtml(data.scheduled_at)}</span>` : ''}
            `;
        }

        showToast(data.message || '🎉 Đã đăng thành công lên YouTube!', 'success');
        // Tự động làm mới danh sách clip để cập nhật trạng thái Đã đăng / Hàng đợi
        if (typeof loadRepurposerClips === 'function') {
            loadRepurposerClips();
        }

    } catch (e) {
        if (statusDiv) {
            statusDiv.style.background = 'rgba(239,68,68,0.12)';
            statusDiv.style.color = '#f87171';
            statusDiv.style.border = '1px solid rgba(239,68,68,0.3)';
            statusDiv.textContent = '❌ ' + (e.message || 'Lỗi không xác định');
        }
        showToast('Lỗi đăng video: ' + e.message, 'error');
        if (typeof loadRepurposerClips === 'function') {
            loadRepurposerClips();
        }
    } finally {
        if (btnNow) btnNow.disabled = false;
        if (btnQueue) btnQueue.disabled = false;
    }
}

// Lắng nghe phím Escape để đóng modal xem video
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        const modal = document.getElementById('modal-repurposed-player');
        if (modal && (modal.style.display === 'flex' || modal.classList.contains('visible') || modal.classList.contains('active'))) {
            closeRepurposedPlayerModal();
        }
    }
});

async function promptRegenerateThumbnail(jobId) {
    const customText = prompt('Nhập chữ hiển thị trên Thumbnail (2-4 từ, IN HOA, cực kỳ kích thích tò mò):\n(Để trống nếu muốn AI tự tính toán theo kịch bản)', '');
    if (customText === null) return; // user cancelled

    showToast('Đang tạo lại Thumbnail độ tương phản cao...', 'info');
    try {
        const res = await fetch(`/api/videos/${jobId}/regenerate-thumbnail`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                custom_text: customText.trim() || null
            })
        });
        const data = await res.json();
        if (data.status === 'success') {
            showToast('✅ Đã tạo lại Thumbnail thành công!', 'success');
            if (typeof openJobDetailModal === 'function') {
                openJobDetailModal(jobId);
            }
        } else {
            showToast(`Lỗi: ${data.message || 'Không thể tạo lại thumbnail'}`, 'error');
        }
    } catch (err) {
        showToast(`❌ Lỗi: ${err.message}`, 'error');
    }
}

// ═════════════════════════════════════════════════════════════════════════════
// ─── AUTOPILOT A-Z & CHANNEL TODAY SCHEDULE CONTROLLER ─────────────────────
// ═════════════════════════════════════════════════════════════════════════════

async function fetchAutoPilotStatus() {
    const grid = document.getElementById('autopilot-channels-grid');
    if (!grid) return;

    try {
        const res = await fetch('/api/autopilot/status');
        const data = await res.json();
        if (!data.success) {
            grid.innerHTML = `<div style="color:var(--accent-red); padding:15px;">Lỗi tải dữ liệu Auto-Pilot: ${data.detail || 'Không xác định'}</div>`;
            return;
        }

        renderAutoPilotChannels(data);
    } catch (err) {
        console.warn('Lỗi fetchAutoPilotStatus:', err);
        grid.innerHTML = `<div style="color:var(--text-muted); padding:15px;">Không thể kết nối API Auto-Pilot.</div>`;
    }
}

function renderAutoPilotChannels(summary) {
    const grid = document.getElementById('autopilot-channels-grid');
    if (!grid) return;

    const channels = summary.channels || [];
    if (!channels.length) {
        grid.innerHTML = `<div style="color:var(--text-muted); padding:15px;">Chưa cấu hình kênh nào trong channels.yaml.</div>`;
        return;
    }

    let html = '';
    channels.forEach(ch => {
        const isFull = ch.today_video_count >= 2;
        const hasOne = ch.today_video_count === 1;
        const isEmpty = ch.is_empty;
        const pending = ch.pending_job;
        const nextSlot = ch.next_golden_slot?.display_vn || '20:00 Tối Nay';

        let badgeHtml = '';
        let borderStyle = '';
        if (isFull) {
            badgeHtml = `<span class="badge" style="background:#10b981; color:#fff;">🟢 2/2 VIDEO (ĐỦ LỊCH)</span>`;
            borderStyle = 'border-color: rgba(16, 185, 129, 0.4);';
        } else if (hasOne) {
            badgeHtml = `<span class="badge" style="background:#f59e0b; color:#000;">🟡 1/2 VIDEO</span>`;
            borderStyle = 'border-color: rgba(245, 158, 11, 0.4);';
        } else if (pending) {
            badgeHtml = `<span class="badge" style="background:#6366f1; color:#fff;">⏳ ĐANG DỰNG (${pending.progress_percent || 0}%)</span>`;
            borderStyle = 'border-color: rgba(99, 102, 241, 0.4);';
        } else {
            badgeHtml = `<span class="badge" style="background:#ef4444; color:#fff;">🔴 0 VIDEO (CHƯA CÓ LỊCH)</span>`;
            borderStyle = 'border-color: rgba(239, 68, 68, 0.4); background: rgba(239, 68, 68, 0.02);';
        }

        let videosListHtml = '';
        if (ch.today_videos && ch.today_videos.length) {
            videosListHtml = ch.today_videos.map(v => `
                <div style="font-size:12px; color:var(--text-main); margin-top:4px; display:flex; justify-content:space-between; align-items:center;">
                    <span>⏰ <strong>${v.time_vn}</strong>: ${escapeHtml((v.title || '').substring(0, 24))}...</span>
                    ${v.shorts_url ? `<a href="${v.shorts_url}" target="_blank" style="color:var(--accent-blue); text-decoration:none; font-size:11px;">[Xem]</a>` : ''}
                </div>
            `).join('');
        } else {
            videosListHtml = `<div style="font-size:12px; color:var(--text-muted); font-style:italic; margin-top:4px;">Chưa có video nào hôm nay.</div>`;
        }

        let actionBtn = '';
        if (isFull) {
            actionBtn = `<button class="btn btn-secondary btn-sm" disabled style="opacity:0.6; width:100%;">Đã Đủ Lịch Hôm Nay</button>`;
        } else if (pending) {
            actionBtn = `<button class="btn btn-secondary btn-sm" disabled style="opacity:0.8; width:100%;">Đang Render #${pending.job_id}...</button>`;
        } else {
            const btnText = isEmpty ? '🚀 Chạy Auto-Pilot A-Z' : '➕ Auto-Pilot Thêm Video #2';
            actionBtn = `
                <button class="btn btn-primary btn-sm" onclick="runAutoPilotChannel('${ch.channel_id}', '${escapeHtml(ch.channel_name)}')" style="width:100%; font-weight:600;">
                    ${btnText}
                </button>
            `;
        }

        html += `
            <div class="panel-card" style="margin-bottom:0; padding:16px; ${borderStyle}">
                <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:10px;">
                    <div>
                        <strong style="font-size:14px; color:var(--text-bright); display:block;">${escapeHtml(ch.channel_name)}</strong>
                        <span style="font-size:11.5px; color:var(--text-muted);">${escapeHtml(ch.handle || ch.channel_id)}</span>
                    </div>
                    ${badgeHtml}
                </div>
                <div style="margin-bottom:12px; background:rgba(0,0,0,0.15); padding:8px 10px; border-radius:6px;">
                    <div style="font-size:11.5px; color:var(--text-muted); margin-bottom:2px;">Hôm nay (${summary.date_vn}):</div>
                    ${videosListHtml}
                </div>
                <div style="font-size:12px; color:var(--text-muted); margin-bottom:12px;">
                    🎯 Khe giờ tiếp theo: <strong style="color:var(--accent-amber);">${escapeHtml(nextSlot)}</strong>
                </div>
                <div>
                    ${actionBtn}
                </div>
            </div>
        `;
    });

    grid.innerHTML = html;
}

async function runAutoPilotChannel(channelId, channelName) {
    if (!confirm(`Bạn có chắc muốn kích hoạt Auto-Pilot A-Z cho kênh "${channelName}"?\n\nAI sẽ tự động: Lên kịch bản viral độc quyền -> Render video 1080p -> Lên lịch khung giờ vàng trống tiếp theo.`)) {
        return;
    }

    showToast(`Đang khởi động Auto-Pilot cho kênh ${channelName}...`, 'info');
    try {
        const res = await fetch('/api/autopilot/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ channel_id: channelId })
        });
        const data = await res.json();
        if (data.success) {
            showToast(`🚀 ${data.message}`, 'success');
            setTimeout(fetchAutoPilotStatus, 1500);
        } else {
            showToast(`Lỗi: ${data.detail || data.error}`, 'error');
        }
    } catch (err) {
        showToast(`❌ Lỗi kết nối: ${err.message}`, 'error');
    }
}

async function runAutoPilotAll() {
    if (!confirm('Bạn có chắc muốn chạy Auto-Pilot A-Z cho TẤT CẢ CÁC KÊNH chưa có lịch hôm nay?\n\nBot sẽ tự động quét các kênh trống và tuần tự sản xuất + lên lịch công chiếu.')) {
        return;
    }

    showToast('Đang kích hoạt Auto-Pilot cho tất cả kênh trống...', 'info');
    try {
        const res = await fetch('/api/autopilot/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ all_empty: true })
        });
        const data = await res.json();
        if (data.success) {
            showToast(`🔥 ${data.message}`, 'success');
            setTimeout(fetchAutoPilotStatus, 1500);
        } else {
            showToast(`Lỗi: ${data.detail || data.error}`, 'error');
        }
    } catch (err) {
        showToast(`❌ Lỗi kết nối: ${err.message}`, 'error');
    }
}

async function sendAutoPilotReminderAlert() {
    showToast('Đang quét và gửi thông báo nhắc nhở qua Telegram...', 'info');
    try {
        const res = await fetch('/api/autopilot/remind', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            if (data.result?.notified) {
                showToast(`📢 Đã gửi báo cáo & nhắc nhở (${data.result.empty_count} kênh trống) tới Telegram!`, 'success');
            } else {
                showToast(`ℹ️ ${data.result?.reason || 'Không cần gửi nhắc nhở'}`, 'info');
            }
        } else {
            showToast('Lỗi gửi nhắc nhở Telegram', 'error');
        }
    } catch (err) {
        showToast(`❌ Lỗi: ${err.message}`, 'error');
    }
}
/* ══════════════════════════════════════════════════════════════════════════════
   1. FORMULA DECONSTRUCTOR & CLONER CONTROLLER (OPENMONTAGE STYLE)
   ══════════════════════════════════════════════════════════════════════════════ */

let currentBlueprint = null;

function switchCreatorMode(mode) {
    const btnStd = document.getElementById('btn-mode-standard');
    const btnClone = document.getElementById('btn-mode-clone');
    const formStd = document.getElementById('create-script-form');
    const formClone = document.getElementById('clone-formula-form');

    if (mode === 'clone') {
        if (btnStd) btnStd.classList.remove('active', 'btn-primary');
        if (btnStd) btnStd.classList.add('btn-secondary');
        if (btnClone) btnClone.classList.add('active', 'btn-primary');
        if (btnClone) btnClone.classList.remove('btn-secondary');
        if (formStd) formStd.style.display = 'none';
        if (formClone) formClone.style.display = 'block';

        // Tự động sao chép danh sách kênh sang selector của clone
        const srcCh = document.getElementById('input-channel');
        const tgtCh = document.getElementById('input-clone-channel');
        if (srcCh && tgtCh && tgtCh.children.length <= 1) {
            tgtCh.innerHTML = srcCh.innerHTML;
        }
    } else {
        if (btnStd) btnStd.classList.add('active', 'btn-primary');
        if (btnStd) btnStd.classList.remove('btn-secondary');
        if (btnClone) btnClone.classList.remove('active', 'btn-primary');
        if (btnClone) btnClone.classList.add('btn-secondary');
        if (formStd) formStd.style.display = 'block';
        if (formClone) formClone.style.display = 'none';
    }
}

async function deconstructFormula() {
    const urlInput = document.getElementById('input-clone-url');
    const langSelect = document.getElementById('input-clone-source-lang');
    const btn = document.getElementById('btn-deconstruct');
    const val = urlInput ? urlInput.value.trim() : '';

    if (!val) {
        showToast('Vui lòng nhập đường link video hoặc đoạn transcript để giải phẫu.', 'error');
        return;
    }

    const origText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner" style="display:inline-block;width:12px;height:12px;border:2px solid #fff;border-top-color:transparent;border-radius:50%;animation:spin 0.6s linear infinite;margin-right:6px;"></span> Đang phân tích công thức viral...`;

    try {
        const res = await apiPost('/api/formula/deconstruct', {
            url: val,
            language: langSelect ? langSelect.value : 'en'
        });

        if (res && res.success && res.blueprint) {
            currentBlueprint = res.blueprint;
            renderBlueprintCard(res.blueprint, res.metadata);
            showToast('✅ Đã giải mã thành công công thức & nhịp điệu video!', 'success');
        } else {
            showToast('Không thể giải phẫu video: ' + (res?.detail || 'Lỗi không xác định'), 'error');
        }
    } catch (e) {
        showToast('Lỗi giải phẫu công thức: ' + e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = origText;
    }
}

function renderBlueprintCard(bp, meta) {
    const card = document.getElementById('blueprint-display-card');
    const targetBox = document.getElementById('clone-target-box');
    if (!card) return;

    card.style.display = 'block';
    if (targetBox) targetBox.style.display = 'block';

    const titleEl = document.getElementById('bp-video-title');
    if (titleEl) titleEl.textContent = (meta && meta.title) || bp.title || 'Video Phân Tích';

    const metricsEl = document.getElementById('bp-metrics');
    if (metricsEl) metricsEl.textContent = `⏱️ ~${Math.round(bp.total_estimated_seconds || 50)}s · ~${bp.words_per_minute || 150} WPM · Nhịp ngắt: ~${bp.retention_curve?.pattern_interrupt_interval_seconds || 3.5}s`;

    const badgeEl = document.getElementById('bp-badge-formula');
    if (badgeEl) badgeEl.textContent = bp.viral_formula_summary ? `⚡ ${bp.viral_formula_summary.slice(0, 45)}...` : 'Cấu Trúc Viral';

    const hookTypeEl = document.getElementById('bp-hook-type');
    if (hookTypeEl) hookTypeEl.textContent = bp.hook_analysis?.hook_archetype || 'Curiosity Gap';

    const hookTrigEl = document.getElementById('bp-hook-trigger');
    if (hookTrigEl) hookTrigEl.textContent = bp.hook_analysis?.retention_trigger || (bp.hook_analysis?.text ? `"${bp.hook_analysis.text.slice(0, 70)}..."` : '—');

    const loopEl = document.getElementById('bp-loop-phrase');
    if (loopEl) loopEl.textContent = bp.loop_mechanism?.closing_phrase ? `"${bp.loop_mechanism.closing_phrase}" ➔ Nối liền về Hook` : 'Nối liền câu kết thúc về đầu video';

    const beatsList = document.getElementById('bp-beats-list');
    if (beatsList) {
        beatsList.innerHTML = (bp.beats || []).map(b => `
            <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.06); border-radius:6px; padding:6px 10px; font-size:11.5px;">
                <div style="display:flex; align-items:center; gap:8px;">
                    <span class="badge ${b.role === 'HOOK' ? 'badge-rose' : (b.role === 'SEAMLESS_LOOP' ? 'badge-purple' : 'badge-cyan')}" style="font-size:10px; padding:1px 6px;">
                        ${esc(b.role || 'SCENE')}
                    </span>
                    <span style="color:#e2e8f0;">${esc(b.story_function || 'Phân cảnh')}</span>
                </div>
                <div style="color:var(--text-muted); font-weight:700;">~${b.duration_seconds || 4}s</div>
            </div>
        `).join('');
    }
}

async function generateFromFormula() {
    if (!currentBlueprint) {
        showToast('Vui lòng phân tích một video trước khi nhân bản kịch bản.', 'error');
        return;
    }

    const topicInput = document.getElementById('input-clone-new-topic');
    const chSelect = document.getElementById('input-clone-channel');
    const langSelect = document.getElementById('input-clone-target-lang');
    const btn = document.getElementById('btn-generate-cloned');

    const topic = topicInput ? topicInput.value.trim() : '';
    if (!topic) {
        showToast('Vui lòng nhập chủ đề cho video mới.', 'error');
        if (topicInput) topicInput.focus();
        return;
    }

    const origText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner" style="display:inline-block;width:14px;height:14px;border:2px solid #fff;border-top-color:transparent;border-radius:50%;animation:spin 0.6s linear infinite;margin-right:8px;"></span> Đang nhân bản kịch bản chuẩn nhịp điệu...`;

    try {
        const res = await apiPost('/api/formula/generate', {
            blueprint: currentBlueprint,
            new_topic: topic,
            channel_id: chSelect ? chSelect.value : 'channel_maya',
            language: langSelect ? langSelect.value : 'en'
        });

        if (res && res.success && res.job_id) {
            showToast('🎉 Đã tạo kịch bản nhân bản thành công! Đang mở Storyboard...', 'success');
            // Mở trực tiếp Storyboard Swapper để người dùng duyệt media
            openStoryboardModal(res.job_id);
            fetchVideos();
        } else {
            showToast('Lỗi: ' + (res?.detail || 'Không thể tạo kịch bản'), 'error');
        }
    } catch (e) {
        showToast('Lỗi nhân bản kịch bản: ' + e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = origText;
    }
}


/* ══════════════════════════════════════════════════════════════════════════════
   2. STORYBOARD DIRECTOR & MEDIA SWAPPER GATE CONTROLLER
   ══════════════════════════════════════════════════════════════════════════════ */

let currentStoryboardJobId = null;
let targetUploadSceneId = null;

async function openStoryboardModal(jobId) {
    currentStoryboardJobId = jobId;
    const modal = document.getElementById('modal-storyboard-swapper');
    const grid = document.getElementById('storyboard-scenes-grid');
    const titleEl = document.getElementById('storyboard-modal-title');
    const badgeEl = document.getElementById('storyboard-job-badge');
    const infoEl = document.getElementById('storyboard-footer-info');

    if (!modal || !grid) return;

    if (badgeEl) badgeEl.textContent = `Job #${jobId}`;
    grid.innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:40px;color:var(--text-muted);"><span class="spinner" style="display:inline-block;width:24px;height:24px;border:3px solid var(--accent-cyan);border-top-color:transparent;border-radius:50%;animation:spin 0.6s linear infinite;margin-bottom:12px;"></span><br>Đang nạp Storyboard và tài nguyên phân cảnh...</div>`;
    modal.classList.add('visible');

    try {
        const res = await apiGet(`/api/videos/${jobId}/storyboard`);
        if (!res || !res.scenes) {
            grid.innerHTML = `<div style="grid-column:1/-1;color:var(--accent-rose);text-align:center;padding:30px;">Không tìm thấy phân cảnh nào trong Job này.</div>`;
            return;
        }

        if (titleEl) titleEl.textContent = res.title || `Job #${jobId}`;
        if (infoEl) infoEl.innerHTML = `<span>📊 <strong>${res.scenes.length} phân cảnh</strong> · Kênh: <strong style="color:var(--accent-cyan);">${esc(res.target_channel_id || 'Chưa gán')}</strong> · Trạng thái: <strong style="color:#f59e0b;">${res.status}</strong></span>`;

        renderStoryboardGrid(res.scenes, jobId);
    } catch (e) {
        grid.innerHTML = `<div style="grid-column:1/-1;color:var(--accent-rose);text-align:center;padding:30px;">Lỗi tải Storyboard: ${esc(e.message)}</div>`;
    }
}

function closeStoryboardModal() {
    const modal = document.getElementById('modal-storyboard-swapper');
    if (modal) modal.classList.remove('visible');
    currentStoryboardJobId = null;
}

function renderStoryboardGrid(scenes, jobId) {
    const grid = document.getElementById('storyboard-scenes-grid');
    if (!grid) return;

    if (scenes.length === 0) {
        grid.innerHTML = `<div style="grid-column:1/-1;color:var(--text-muted);text-align:center;padding:30px;">Chưa có phân cảnh nào.</div>`;
        return;
    }

    grid.innerHTML = scenes.map((s) => {
        const isVid = (s.media_type === 'video');
        const hasMedia = s.has_media && s.media_url;

        let badgeSource = '';
        if (s.source === 'pexels_video') {
            badgeSource = '<span class="badge badge-amber" style="font-size:10.5px;">🎥 Pexels 9:16 Video</span>';
        } else if (s.source === 'pexels_photo') {
            badgeSource = '<span class="badge badge-cyan" style="font-size:10.5px;">📸 Pexels Photo</span>';
        } else if (s.source === 'ai_image') {
            badgeSource = '<span class="badge badge-purple" style="font-size:10.5px;">✨ Ảnh AI 8K</span>';
        } else if (s.source === 'ai_video') {
            badgeSource = '<span class="badge" style="background:linear-gradient(135deg,#ec4899,#8b5cf6);color:#fff;font-size:10.5px;">🔮 AI Video Motion</span>';
        } else if (s.source === 'user_upload') {
            badgeSource = '<span class="badge badge-green" style="font-size:10.5px;">📁 Tải lên cá nhân</span>';
        } else {
            badgeSource = s.is_hook ? '<span class="badge badge-rose" style="font-size:10.5px;">⚡ Scene 1 Hook</span>' : '<span class="badge" style="font-size:10.5px;background:rgba(255,255,255,0.08);">⏳ Tự động khi Render</span>';
        }

        let mediaDisplay = '';
        if (hasMedia) {
            if (isVid) {
                mediaDisplay = `<video src="${s.media_url}" controls muted loop playsinline style="width:100%;height:180px;object-fit:cover;border-radius:8px;background:#000;"></video>`;
            } else {
                mediaDisplay = `<div style="width:100%;height:180px;border-radius:8px;background:url('${s.media_url}') center/cover no-repeat;border:1px solid rgba(255,255,255,0.1);"></div>`;
            }
        } else {
            mediaDisplay = `
                <div style="width:100%;height:180px;border-radius:8px;background:linear-gradient(135deg,rgba(15,23,42,0.8),rgba(30,41,59,0.9));border:1px dashed rgba(255,255,255,0.15);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;padding:12px;text-align:center;">
                    <span style="font-size:26px;">${s.is_hook ? '🔥' : '🎬'}</span>
                    <span style="font-size:11px;color:var(--text-muted);">Sẽ tự động tìm video 9:16 tốt nhất hoặc bấm nút bên dưới để chọn ngay</span>
                </div>
            `;
        }

        return `
        <div class="card" id="storyboard-scene-card-${s.scene_id}" style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08);border-radius:12px;padding:14px;display:flex;flex-direction:column;gap:10px;">
            <!-- Scene Header -->
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <div style="display:flex;align-items:center;gap:6px;">
                    <strong style="color:#fff;font-size:13px;">Cảnh ${s.scene_id}</strong>
                    <span style="font-size:11px;color:var(--text-muted);">(~${Math.round(s.estimated_seconds)}s)</span>
                </div>
                ${badgeSource}
            </div>

            <!-- Media Preview -->
            <div id="scene-media-preview-${s.scene_id}">
                ${mediaDisplay}
            </div>

            <!-- Narration & Keywords -->
            <div style="flex-grow:1;display:flex;flex-direction:column;gap:4px;">
                <div style="font-size:12px;color:#f1f5f9;line-height:1.45;background:rgba(0,0,0,0.2);padding:8px 10px;border-radius:6px;border-left:3px solid var(--accent-cyan);">
                    "${esc(s.narration)}"
                </div>
                <div style="font-size:11px;color:var(--text-muted);display:flex;align-items:center;gap:4px;margin-top:2px;">
                    <span>🔍 Từ khóa:</span>
                    <span style="color:#38bdf8;font-weight:600;">${esc(s.stock_query || s.visual_keyword || 'auto')}</span>
                </div>
            </div>

            <!-- Swapper Toolbar -->
            <div style="display:flex;gap:6px;flex-wrap:wrap;border-top:1px solid rgba(255,255,255,0.06);padding-top:10px;">
                <button type="button" class="btn btn-secondary btn-sm" onclick="promptSwapMedia(${jobId}, ${s.scene_id}, 'pexels_video')" title="Tìm và thay thế bằng video Pexels 9:16 khác" style="flex:1;min-width:90px;font-size:11px;padding:4px 8px;border-color:rgba(245,158,11,0.4);color:#fbbf24;">
                    🎥 Đổi Video
                </button>
                <button type="button" class="btn btn-secondary btn-sm" onclick="promptSwapMedia(${jobId}, ${s.scene_id}, 'ai_image')" title="Sinh ảnh AI 8K mới cho cảnh này" style="flex:1;min-width:80px;font-size:11px;padding:4px 8px;border-color:rgba(192,132,252,0.4);color:#c084fc;">
                    ✨ Vẽ Lại AI
                </button>
                <button type="button" class="btn btn-secondary btn-sm" onclick="triggerStoryboardUpload(${jobId}, ${s.scene_id})" title="Tải file video hoặc ảnh từ máy tính lên" style="font-size:11px;padding:4px 8px;">
                    📁 Tải File
                </button>
            </div>
        </div>
        `;
    }).join('');
}

async function promptSwapMedia(jobId, sceneId, mode) {
    let modeLabel = 'Video Pexels 9:16';
    let defaultVal = '';
    if (mode === 'pexels_video') {
        modeLabel = 'Video chuyển động Pexels (9:16)';
        defaultVal = prompt(`Nhập từ khóa tiếng Anh để tìm clip ${modeLabel} mới cho Cảnh ${sceneId}:\n(Ví dụ: lion running savanna, stormy ocean waves, dark neon city)`, '');
    } else if (mode === 'ai_image') {
        modeLabel = 'Ảnh AI điện ảnh 8K';
        defaultVal = prompt(`Nhập Prompt mô tả hình ảnh AI mới cho Cảnh ${sceneId}:`, '');
    } else {
        defaultVal = prompt(`Nhập từ khóa tìm kiếm:`, '');
    }

    if (defaultVal === null) return; // User cancelled

    const previewBox = document.getElementById(`scene-media-preview-${sceneId}`);
    if (previewBox) {
        previewBox.innerHTML = `<div style="height:180px;display:flex;align-items:center;justify-content:center;background:#000;border-radius:8px;color:#38bdf8;font-size:12px;"><span class="spinner" style="display:inline-block;width:14px;height:14px;border:2px solid #38bdf8;border-top-color:transparent;border-radius:50%;animation:spin 0.6s linear infinite;margin-right:6px;"></span> Đang tìm nạp & xử lý media mới...</div>`;
    }

    try {
        const payload = { mode };
        if (mode === 'ai_image') payload.custom_prompt = defaultVal.trim();
        else payload.search_query = defaultVal.trim();

        const res = await apiPost(`/api/videos/${jobId}/scenes/${sceneId}/swap`, payload);
        if (res && res.success) {
            showToast(`✅ Đã thay thế media Cảnh ${sceneId} thành công!`, 'success');
            openStoryboardModal(jobId);
        } else {
            showToast(`Lỗi: ${res?.detail || 'Không thể đổi media'}`, 'error');
            openStoryboardModal(jobId);
        }
    } catch (e) {
        showToast(`Lỗi hoán đổi media: ${e.message}`, 'error');
        openStoryboardModal(jobId);
    }
}

function triggerStoryboardUpload(jobId, sceneId) {
    targetUploadSceneId = sceneId;
    const fileInput = document.getElementById('storyboard-upload-input');
    if (fileInput) {
        fileInput.value = '';
        fileInput.click();
    }
}

async function handleStoryboardFileSelected(event) {
    const file = event.target.files && event.target.files[0];
    if (!file || !currentStoryboardJobId || !targetUploadSceneId) return;

    const sceneId = targetUploadSceneId;
    const jobId = currentStoryboardJobId;

    const previewBox = document.getElementById(`scene-media-preview-${sceneId}`);
    if (previewBox) {
        previewBox.innerHTML = `<div style="height:180px;display:flex;align-items:center;justify-content:center;background:#000;border-radius:8px;color:#10b981;font-size:12px;"><span class="spinner" style="display:inline-block;width:14px;height:14px;border:2px solid #10b981;border-top-color:transparent;border-radius:50%;animation:spin 0.6s linear infinite;margin-right:6px;"></span> Đang tải lên file cá nhân...</div>`;
    }

    try {
        const formData = new FormData();
        formData.append('file', file);

        const res = await fetch(`/api/videos/${jobId}/scenes/${sceneId}/upload`, {
            method: 'POST',
            body: formData
        });
        const data = await res.json();
        if (data && data.success) {
            showToast(`✅ Đã tải lên file cho Cảnh ${sceneId} thành công!`, 'success');
            openStoryboardModal(jobId);
        } else {
            showToast(`Lỗi upload: ${data.detail || 'Không thể tải file'}`, 'error');
            openStoryboardModal(jobId);
        }
    } catch (e) {
        showToast(`Lỗi tải lên file: ${e.message}`, 'error');
        openStoryboardModal(jobId);
    }
}

async function renderApprovedStoryboard() {
    if (!currentStoryboardJobId) return;
    const jobId = currentStoryboardJobId;
    const btn = document.getElementById('btn-render-approved');

    const origText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner" style="display:inline-block;width:14px;height:14px;border:2px solid #fff;border-top-color:transparent;border-radius:50%;animation:spin 0.6s linear infinite;margin-right:8px;"></span> Đang bắt đầu render...`;

    try {
        const res = await apiPost(`/api/videos/${jobId}/render-approved`, {});
        if (res && res.success) {
            showToast(`🚀 Video #${jobId} đã duyệt Storyboard và bắt đầu render hoàn chỉnh!`, 'success');
            closeStoryboardModal();
            switchTab('pipeline');
            fetchVideos();
        } else {
            showToast(`Lỗi render: ${res?.detail || 'Không thể kích hoạt render'}`, 'error');
        }
    } catch (e) {
        showToast(`Lỗi render: ${e.message}`, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = origText;
        }
    }
}



async function suggestTagsForPublishModal(silent = false) {
    const jobId = document.getElementById('publish-job-id')?.value;
    const channelId = document.getElementById('publish-channel-select')?.value || '';
    if (!jobId) return;
    try {
        const res = await fetch(`/api/videos/${jobId}/suggest-tags` + (channelId ? `?channel_id=${encodeURIComponent(channelId)}` : ''));
        const data = await res.json();
        if (data.success) {
            const tagsInput = document.getElementById('publish-tags-input');
            if (tagsInput) tagsInput.value = data.tags_csv || (data.tags || []).join(', ');
            const titleInput = document.getElementById('publish-title-input');
            if (titleInput && data.enriched_title) {
                titleInput.value = data.enriched_title;
                updateTitleCounter();
            }
            if (!silent) {
                showToast(`✨ Đã gợi ý ${data.tags?.length || 0} tags & hashtags chuẩn ngách!`, 'success');
            }
        }
    } catch (e) {
        if (!silent) showToast('Không thể lấy gợi ý tag tự động', 'error');
    }
}

function _renderVideoTagChips(jobId, tagsList, newTitle) {
    const chipsBox = document.getElementById(`detail-tags-chips-${jobId}`);
    if (chipsBox && Array.isArray(tagsList)) {
        chipsBox.innerHTML = tagsList.map(t => `<span style="font-size:11px;padding:2px 8px;border-radius:12px;background:rgba(59,130,246,0.18);color:#93c5fd;border:1px solid rgba(59,130,246,0.3);">#${esc(String(t).replace(/^#+/, ''))}</span>`).join('');
    }
    if (newTitle) {
        const titleEl = document.getElementById(`detail-video-title-${jobId}`);
        if (titleEl) titleEl.textContent = newTitle;
    }
}

async function addCustomTagsToVideo(jobId) {
    const inputEl = document.getElementById(`detail-add-tags-input-${jobId}`);
    const rawTags = inputEl ? inputEl.value.trim() : '';
    if (!rawTags) {
        showToast('Vui lòng nhập ít nhất 1 tag để gắn thêm vào video', 'warning');
        return;
    }
    try {
        showToast(`🏷️ Đang gắn thêm tag vào Video #${jobId}...`, 'info');
        const res = await fetch(`/api/videos/${jobId}/tags`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                tags: rawTags,
                append: true,
                sync_youtube: true,
                update_title_hashtags: true,
                update_description_hashtags: true
            })
        });
        const data = await res.json();
        if (res.ok && data.success) {
            if (inputEl) inputEl.value = '';
            _renderVideoTagChips(jobId, data.tags || [], data.title);
            showToast(`✅ ${data.message}`, 'success');
            await fetchVideos();
        } else {
            showToast(data.detail || data.error || 'Lỗi khi gắn thêm tag', 'error');
        }
    } catch (e) {
        showToast('Lỗi kết nối khi gắn thêm tag', 'error');
    }
}

async function autoSuggestAndApplyVideoTags(jobId) {
    try {
        showToast(`✨ Đang phân tích chủ đề & gắn bộ Tags chuẩn ngách cho Video #${jobId}...`, 'info');
        const sugRes = await fetch(`/api/videos/${jobId}/suggest-tags`);
        const sugData = await sugRes.json();
        if (!sugData.success) {
            showToast('Không thể tạo gợi ý tag', 'error');
            return;
        }
        const res = await fetch(`/api/videos/${jobId}/tags`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                tags: sugData.tags || [],
                append: true,
                sync_youtube: true,
                update_title_hashtags: true,
                update_description_hashtags: true
            })
        });
        const data = await res.json();
        if (res.ok && data.success) {
            _renderVideoTagChips(jobId, data.tags || [], data.title);
            showToast(`✅ ${data.message}`, 'success');
            await fetchVideos();
        }
    } catch (e) {
        showToast('Lỗi khi tự động gắn tag', 'error');
    }
}
