/* ═══════════════════════════════════════════════════════
   THƯ VIỆN VIDEO ĐÃ ĐĂNG (Published Video Library)
   Panel ở màn hình Tạo Kịch Bản + cảnh báo trùng chủ đề
══════════════════════════════════════════════════════════ */
(function () {
    let libTimer = null;
    let dupTimer = null;
    let libLoadedFor = null;

    const $ = (id) => document.getElementById(id);
    const escH = (s) => String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    const fmtViews = (n) => (n || 0).toLocaleString('vi-VN');

    function currentChannel() {
        const sel = $('input-channel');
        return sel ? sel.value : '';
    }

    async function loadLibrary(force) {
        const list = $('library-list');
        if (!list) return;
        const ch = currentChannel();
        const q = ($('library-search') || {}).value || '';
        if (!force && libLoadedFor === ch + '|' + q) return;
        libLoadedFor = ch + '|' + q;

        try {
            const qs = `channel_id=${encodeURIComponent(ch)}&q=${encodeURIComponent(q.trim())}&limit=200`;
            const res = await fetch('/api/library?' + qs);
            const data = await res.json();
            const vids = data.videos || [];
            $('library-count').textContent = `${vids.length} video`;
            if (!vids.length) {
                list.innerHTML = '<div style="color:var(--text-muted); font-size:13px;">' +
                    (q ? 'Không tìm thấy video nào khớp từ khóa.' :
                        'Chưa có video nào cho kênh này. Bấm "🔄 Đồng bộ YouTube" để lấy danh sách.') + '</div>';
            } else {
                list.innerHTML = vids.map((v) => `
                    <div style="padding:10px 12px; border-radius:10px; background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08);">
                        <div style="display:flex; justify-content:space-between; gap:10px; align-items:flex-start;">
                            <a href="${escH(v.youtube_url)}" target="_blank" rel="noopener" style="font-weight:700; color:#e2e8f0; text-decoration:none;">${escH(v.title)}</a>
                            <span style="font-size:11px; color:var(--text-muted); white-space:nowrap;">${escH(v.published_at)} · 👁 ${fmtViews(v.views)}</span>
                        </div>
                        <div style="font-size:12.5px; color:#94a3b8; margin-top:4px;">${escH(v.summary) || '<i>Chưa có tóm tắt</i>'}</div>
                        ${(v.topic_cluster || (v.topic_tags || []).length) ? `<div style="margin-top:6px; display:flex; gap:5px; flex-wrap:wrap;">
                            ${v.topic_cluster ? `<span class="badge badge-purple" style="font-size:10px;">${escH(v.topic_cluster)}</span>` : ''}
                            ${(v.topic_tags || []).map((t) => `<span style="font-size:10px; padding:2px 7px; border-radius:10px; background:rgba(255,255,255,0.07); color:#94a3b8;">#${escH(t)}</span>`).join('')}
                        </div>` : ''}
                    </div>`).join('');
            }
            loadTopics(ch);
        } catch (e) {
            list.innerHTML = '<div style="color:#f87171; font-size:13px;">Không tải được thư viện: ' + escH(e.message) + '</div>';
        }
    }

    async function loadTopics(ch) {
        const box = $('library-topics');
        if (!box) return;
        try {
            const res = await fetch('/api/library/topics?channel_id=' + encodeURIComponent(ch || ''));
            const data = await res.json();
            box.innerHTML = (data.topics || []).slice(0, 12).map((t) =>
                `<button type="button" class="btn-chip" onclick="window.libraryFilterTopic('${escH(t.cluster).replace(/'/g, '&#39;')}')"
                    style="font-size:11px; padding:3px 9px; border-radius:12px; background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.12); color:#94a3b8; cursor:pointer;">
                    ${escH(t.cluster)} (${t.count})</button>`).join('');
        } catch (e) { /* bỏ qua */ }
    }

    window.libraryFilterTopic = function (cluster) {
        const s = $('library-search');
        if (s) { s.value = cluster === 'Chưa phân loại' ? '' : cluster; loadLibrary(true); }
    };

    window.syncLibrary = async function () {
        const btn = $('btn-library-sync');
        if (!btn) return;
        const old = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '⏳ Đang đồng bộ…';
        try {
            const res = await fetch('/api/library/sync', { method: 'POST' });
            const data = await res.json();
            if (data.ok === false) {
                showToast('⚠️ ' + (data.error || 'Không đồng bộ được'), 'warning');
            } else {
                const added = (data.channels || []).reduce((a, c) => a + (c.added || 0), 0);
                const failed = (data.channels || []).filter((c) => !c.ok);
                showToast(`✅ Đồng bộ xong: +${added} video mới` +
                    (failed.length ? ` · ${failed.length} kênh lỗi token (${failed.map((c) => c.channel_id).join(', ')})` : ''),
                    failed.length ? 'warning' : 'success');
            }
            await loadLibrary(true);
        } catch (e) {
            showToast('Lỗi đồng bộ: ' + e.message, 'error');
        } finally {
            btn.disabled = false;
            btn.innerHTML = old;
        }
    };

    async function checkDuplicate() {
        const box = $('topic-dup-warning');
        const topic = (($('input-topic') || {}).value || '').trim();
        if (!box) return;
        if (topic.length < 6) { box.style.display = 'none'; return; }
        try {
            const res = await fetch('/api/library/check-duplicate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channel_id: currentChannel(), topic }),
            });
            const data = await res.json();
            const dups = data.duplicates || [];
            if (!dups.length) { box.style.display = 'none'; return; }
            box.innerHTML = '⚠️ <b>Chủ đề này gần giống video đã đăng trên kênh:</b><br>' + dups.map((d) =>
                `• <a href="${escH(d.youtube_url)}" target="_blank" rel="noopener" style="color:#fde68a; text-decoration:underline;">${escH(d.title)}</a>
                 <span style="opacity:.75;">(${escH(d.published_at)} · khớp ${Math.round(d.similarity * 100)}%)</span>`).join('<br>') +
                '<br><span style="opacity:.8;">Anh vẫn có thể tạo kịch bản — hãy chọn góc nhìn khác để tránh lặp.</span>';
            box.style.display = 'block';
        } catch (e) { box.style.display = 'none'; }
    }

    document.addEventListener('DOMContentLoaded', () => {
        const topic = $('input-topic');
        if (topic) topic.addEventListener('input', () => { clearTimeout(dupTimer); dupTimer = setTimeout(checkDuplicate, 600); });
        const search = $('library-search');
        if (search) search.addEventListener('input', () => { clearTimeout(libTimer); libTimer = setTimeout(() => loadLibrary(true), 350); });

        const sel = $('input-channel');
        if (sel) {
            sel.addEventListener('change', () => { loadLibrary(true); checkDuplicate(); });
            // Danh sách kênh được nạp bất đồng bộ → theo dõi để tải thư viện khi sẵn sàng
            new MutationObserver(() => loadLibrary(false)).observe(sel, { childList: true });
        }
        setTimeout(() => loadLibrary(false), 1500);
    });
})();
