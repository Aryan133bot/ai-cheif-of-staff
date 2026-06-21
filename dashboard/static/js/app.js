/* ═══════════════════════════════════════════════════════════════════════════
   AI Chief of Staff — Dashboard App
   Client-side SPA with hash-based routing, API client, and view renderers.
   ═══════════════════════════════════════════════════════════════════════════ */

// ─── Data Store (avoids inline JSON in HTML attributes) ─────────────────────

const Store = {
    _tasks: [],
    _events: [],
    _drafts: [],
    setTasks(t) { this._tasks = t; },
    getTask(id) { return this._tasks.find(t => t.id === id); },
    setEvents(e) { this._events = e; },
    getEvent(id) { return this._events.find(e => e.id === id); },
    setDrafts(d) { this._drafts = d; },
    getDraft(id) { return this._drafts.find(d => d.id === id); },
};

// ─── Auth Token ─────────────────────────────────────────────────────────────

function getToken() { return localStorage.getItem('auth_token'); }
function setToken(t) { localStorage.setItem('auth_token', t); }
function clearToken() { localStorage.removeItem('auth_token'); }

function authHeaders() {
    const t = getToken();
    return t ? { 'Authorization': `Bearer ${t}` } : {};
}

// ─── API Client (with auth) ─────────────────────────────────────────────────

const API = {
    async _handle(res) {
        if (res.status === 401) { clearToken(); showAuth(); throw new Error('Session expired'); }
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            let detail = err.detail;
            if (Array.isArray(detail)) {
                detail = detail.map((d) => (typeof d === 'object' && d.msg) ? d.msg : String(d)).join(', ');
            }
            throw new Error(detail || `HTTP ${res.status}`);
        }
        return res.json();
    },
    async get(url) {
        return this._handle(await fetch(url, { headers: authHeaders() }));
    },
    async post(url, body) {
        return this._handle(await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...authHeaders() },
            body: JSON.stringify(body),
        }));
    },
    async patch(url, body) {
        return this._handle(await fetch(url, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json', ...authHeaders() },
            body: JSON.stringify(body),
        }));
    },
    async del(url) {
        return this._handle(await fetch(url, { method: 'DELETE', headers: authHeaders() }));
    },
};

// ─── Utilities ──────────────────────────────────────────────────────────────

let _isSubmitting = false;
function lockSubmit() {
    if (_isSubmitting) return false;
    _isSubmitting = true;
    document.body.style.cursor = 'wait';
    return true;
}
function unlockSubmit() {
    _isSubmitting = false;
    document.body.style.cursor = 'default';
}

function escHtml(str) {
    const el = document.createElement('span');
    el.textContent = str || '';
    return el.innerHTML;
}

function localDateStr(d) {
    /* Returns YYYY-MM-DD in local timezone — avoids UTC-shift from toISOString */
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
}

function pillClass(urgency) {
    const map = { critical: 'pill-critical', high: 'pill-high', medium: 'pill-medium', low: 'pill-low' };
    return map[urgency] || 'pill-low';
}

function urgencyColor(u) {
    return { critical: 'var(--critical)', high: 'var(--high)', medium: 'var(--medium)', low: 'var(--low)' }[u] || 'var(--low)';
}

function timeAgo(iso) {
    if (!iso) return '';
    const diff = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    return `${days}d ago`;
}

// ─── Toast Notifications ────────────────────────────────────────────────────

function toast(message, type = 'info') {
    const icons = { success: '✓', error: '✗', info: 'ℹ' };
    const container = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.innerHTML = `<span>${icons[type] || 'ℹ'}</span> <span>${escHtml(message)}</span>`;
    container.appendChild(el);
    setTimeout(() => {
        el.classList.add('hiding');
        setTimeout(() => el.remove(), 250);
    }, 5000);
}

// ─── Modal ──────────────────────────────────────────────────────────────────

function openModal(title, bodyHtml, footerHtml = '') {
    const root = document.getElementById('modal-root');
    root.innerHTML = `
        <div class="modal-overlay" id="modal-overlay">
            <div class="modal">
                <div class="modal-header">
                    <h3>${escHtml(title)}</h3>
                    <button class="btn-icon" onclick="closeModal()">✕</button>
                </div>
                <div class="modal-body">${bodyHtml}</div>
                ${footerHtml ? `<div class="modal-footer">${footerHtml}</div>` : ''}
            </div>
        </div>`;
    document.getElementById('modal-overlay').addEventListener('click', e => {
        if (e.target.id === 'modal-overlay') closeModal();
    });
}

function closeModal() {
    document.getElementById('modal-root').innerHTML = '';
}

// ─── Router ─────────────────────────────────────────────────────────────────

const views = { dashboard: renderDashboard, calendar: renderCalendar, 'work-mails': renderWorkMails, 'non-work-mails': renderNonWorkMails, replies: renderReplies, commitments: renderCommitments, relationships: renderRelationships, knowledge: renderKnowledgeBase, settings: renderSettings };

function toggleMobileMenu() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    if (sidebar) sidebar.classList.toggle('open');
    if (overlay) overlay.classList.toggle('open');
}

function navigate() {
    let rawHash = (location.hash || '#dashboard').slice(1);
    let [hashPath, query] = rawHash.split('?');
    const view = views[hashPath] || views.dashboard;

    // Close mobile menu on navigate
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    if (sidebar) sidebar.classList.remove('open');
    if (overlay) overlay.classList.remove('open');

    // Show error toast if redirected with ?error=
    if (query && query.includes('error=')) {
        const params = new URLSearchParams(query);
        const err = params.get('error');
        if (err) {
            toast('Error: ' + err.replace(/_/g, ' '), 'error');
            // Clean up the URL so it doesn't persist on reload
            history.replaceState(null, null, '#' + hashPath);
        }
    }

    document.querySelectorAll('.nav-item').forEach(el => {
        el.classList.toggle('active', el.dataset.view === hashPath);
    });
    view();
}

// ─── Dashboard View ─────────────────────────────────────────────────────────

async function renderDashboard() {
    const main = document.getElementById('main-content');
    main.innerHTML = `
        <div class="page-header" style="display:flex;justify-content:space-between;align-items:center;">
            <div>
                <h2>Dashboard</h2>
                <p class="page-subtitle">Your executive briefing at a glance</p>
            </div>
            <div id="dashboard-email-btn" style="margin-right:1rem;"></div>
        </div>
        <div class="page-body">
            <div class="loading-overlay"><div class="spinner"></div><span>Loading...</span></div>
        </div>`;

    try {
        const [stats, priorities] = await Promise.all([
            API.get('/api/stats'),
            API.get('/api/tasks/priorities?limit=10'),
        ]);
        Store.setTasks([...priorities]);

        const body = main.querySelector('.page-body');

        // Fetch email data in parallel with stats
        let workEmails = [], miscEmails = [];
        try {
            [workEmails, miscEmails] = await Promise.all([
                API.get('/api/emails/fetched?limit=5&category=work'),
                API.get('/api/emails/fetched?limit=5&category=miscellaneous'),
            ]);
        } catch (_) {}

        const formatDate = (iso) => {
            if (!iso || iso === 'None' || iso === '') return '';
            const d = new Date(iso);
            return isNaN(d.getTime()) ? '' : d.toLocaleDateString();
        };

        const renderEmailPreviewRow = (e) => `
            <div style="display:flex; justify-content:space-between; align-items:flex-start; padding:0.6rem 0; border-bottom:1px solid var(--border-color); gap:0.75rem; cursor:pointer;" onclick="location.hash='#${e.category === 'work' ? 'work-mails' : 'non-work-mails'}'">
                <div style="flex:1; min-width:0;">
                    <div style="font-size:0.85rem; font-weight:600; color:var(--text-primary); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escHtml(e.subject || '(no subject)')}</div>
                    <div style="font-size:0.75rem; color:var(--text-tertiary); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escHtml(e.sender || '')}</div>
                </div>
                <div style="font-size:0.72rem; color:var(--text-tertiary); flex-shrink:0; padding-top:2px;">${formatDate(e.received_at)}</div>
            </div>`;

        const workPreview = workEmails.length
            ? workEmails.map(renderEmailPreviewRow).join('')
            : `<div style="padding:1.5rem 0; text-align:center; color:var(--text-tertiary); font-size:0.85rem;">No work emails yet — click Process Emails</div>`;

        const miscPreview = miscEmails.length
            ? miscEmails.map(renderEmailPreviewRow).join('')
            : `<div style="padding:1.5rem 0; text-align:center; color:var(--text-tertiary); font-size:0.85rem;">No non-work emails yet</div>`;

        body.innerHTML = `
            <!-- Summary Cards -->
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-value">${stats.active}</div>
                    <div class="stat-label">Active Tasks</div>
                </div>
                <div class="stat-card critical">
                    <div class="stat-value">${stats.by_urgency.critical || 0}</div>
                    <div class="stat-label">Critical Items</div>
                </div>
                <div class="stat-card warning">
                    <div class="stat-value">${stats.pending_reviews}</div>
                    <div class="stat-label">Pending Reviews</div>
                </div>
                <div class="stat-card success">
                    <div class="stat-value">${stats.by_status.completed || 0}</div>
                    <div class="stat-label">Completed</div>
                </div>
            </div>

            <!-- Email Inbox Summary -->
            <div class="email-sections-grid" style="display:grid; grid-template-columns:1fr 1fr; gap:1.5rem; margin-bottom:1.5rem;">
                <!-- Work Mails -->
                <div class="card" style="border-top:3px solid var(--success);">
                    <div class="card-header" style="display:flex; justify-content:space-between; align-items:center;">
                        <div style="display:flex; align-items:center; gap:0.6rem;">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--success)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"></polyline><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"></path></svg>
                            <h3 style="margin:0;">Work Mails</h3>
                        </div>
                        <div style="display:flex; align-items:center; gap:0.75rem;">
                            <span style="background:var(--success); color:#fff; border-radius:999px; padding:0.15rem 0.6rem; font-size:0.72rem; font-weight:700;">${workEmails.length > 0 ? workEmails.length + (workEmails.length === 5 ? '+' : '') : '0'}</span>
                            <a href="#work-mails" class="btn btn-ghost btn-sm" style="font-size:0.72rem; padding:0.2rem 0.6rem;">View All →</a>
                        </div>
                    </div>
                    <div class="card-body" style="padding-top:0;">
                        ${workPreview}
                    </div>
                </div>

                <!-- Non-Work Mails -->
                <div class="card" style="border-top:3px solid var(--text-tertiary);">
                    <div class="card-header" style="display:flex; justify-content:space-between; align-items:center;">
                        <div style="display:flex; align-items:center; gap:0.6rem;">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--text-tertiary)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path><polyline points="22,6 12,13 2,6"></polyline></svg>
                            <h3 style="margin:0;">Non-Work Mails</h3>
                        </div>
                        <div style="display:flex; align-items:center; gap:0.75rem;">
                            <span style="background:var(--bg-tertiary); color:var(--text-secondary); border:1px solid var(--border-color); border-radius:999px; padding:0.15rem 0.6rem; font-size:0.72rem; font-weight:700;">${miscEmails.length > 0 ? miscEmails.length + (miscEmails.length === 5 ? '+' : '') : '0'}</span>
                            <a href="#non-work-mails" class="btn btn-ghost btn-sm" style="font-size:0.72rem; padding:0.2rem 0.6rem;">View All →</a>
                        </div>
                    </div>
                    <div class="card-body" style="padding-top:0;">
                        ${miscPreview}
                    </div>
                </div>
            </div>

            <div class="grid-3">
                <!-- Priority Tasks -->
                <div class="card">
                    <div class="card-header">
                        <h3>Top Priorities</h3>
                        <span style="font-size:0.7rem;color:var(--text-tertiary)">${priorities.length} items</span>
                    </div>
                    <div class="card-body">
                        ${priorities.length ? renderTaskList(priorities) : emptyState('No active tasks', 'Process some emails to get started')}
                    </div>
                </div>

                <!-- Right column -->
                <div style="display:flex;flex-direction:column;gap:1.5rem;">
                    <!-- Urgency Distribution -->
                    <div class="card">
                        <div class="card-header"><h3>By Urgency</h3></div>
                        <div class="card-body">${renderBarChart(stats.by_urgency)}</div>
                    </div>
                    <!-- Type Distribution -->
                    <div class="card">
                        <div class="card-header"><h3>By Category</h3></div>
                        <div class="card-body">${renderBarChart(stats.by_type)}</div>
                    </div>
                </div>
            </div>`;

        // Update badge
        const badge = document.getElementById('nav-tasks-badge');
        if (stats.active > 0) {
            badge.textContent = stats.active;
            badge.style.display = '';
        }

        // Smart email button: only show Process if email is set up
        const emailBtnContainer = document.getElementById('dashboard-email-btn');
        if (emailBtnContainer) {
            try {
                const emailStatus = await API.get('/api/emails/status');
                const hasConnected = (emailStatus.providers || []).some(p => p.authenticated);
                if (hasConnected) {
                    emailBtnContainer.innerHTML = '<button class="btn btn-primary btn-sm" onclick="processEmails()" id="btn-process-emails">Process Emails</button>';
                } else {
                    emailBtnContainer.innerHTML = '<a href="#settings" class="btn btn-ghost btn-sm" style="font-size:0.78rem">Setup Email &rarr;</a>';
                }
            } catch (_) {
                emailBtnContainer.innerHTML = '';
            }
        }
    } catch (err) {
        main.querySelector('.page-body').innerHTML = `
            <div class="empty-state">
                <div class="empty-title">Failed to load dashboard</div>
                <div class="empty-desc">${escHtml(err.message)}</div>
            </div>`;
    }
}

async function processEmails() {
    const btn = document.getElementById('btn-process-emails') || document.getElementById('btn-settings-process');
    const originalText = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = 'Processing...'; }
    try {
        const result = await API.post('/api/emails/process');
        if (result.ok) {
            const parts = [];
            if (result.emails_fetched) parts.push(`${result.emails_fetched} fetched`);
            if (result.emails_processed) parts.push(`${result.emails_processed} processed`);
            if (result.tasks_created) parts.push(`${result.tasks_created} tasks created`);
            if (result.tasks_updated) parts.push(`${result.tasks_updated} updated`);
            if (result.emails_skipped) parts.push(`${result.emails_skipped} skipped`);
            if (result.reply_drafts_created) parts.push(`${result.reply_drafts_created} reply drafts queued`);
            
            if (result.fetch_errors && result.fetch_errors.length > 0) {
                toast(result.message || result.fetch_errors.join('; '), 'error');
            } else {
                toast(parts.length ? parts.join(', ') : (result.message || 'No new emails found'), 'success');
            }
            // Refresh whichever view we're on
            navigate();
        } else {
            toast(result.error || 'Processing failed', 'error');
        }
    } catch (err) { toast(err.message, 'error'); }
    finally { if (btn) { btn.disabled = false; btn.textContent = originalText; } }
}

function renderTaskList(tasks, compact = false) {
    // Merge into Store for later lookup
    tasks.forEach(t => { if (!Store.getTask(t.id)) Store._tasks.push(t); });

    return `<div class="task-list">${tasks.map(t => `
        <div class="task-item" data-id="${t.id}" onclick="openTaskDetail(${t.id})">
            <div class="task-priority-bar ${t.urgency}"></div>
            <div class="task-content">
                <div class="task-title">${escHtml(t.title)}</div>
                <div class="task-meta">
                    <span class="pill ${pillClass(t.urgency)}">${t.urgency}</span>
                    <span>${escHtml(t.deadline_type)}</span>
                    <span style="color: var(--text-secondary);">${escHtml(t.deadline_date || 'No deadline')}</span>
                    ${!compact ? `<span style="color: var(--text-secondary);">${escHtml(t.source_sender)}</span>` : ''}
                    ${t.review_required ? '<span class="pill pill-warning">review</span>' : ''}
                </div>
            </div>
            <div class="task-actions">
                <button class="btn btn-ghost btn-sm" title="Complete" onclick="event.stopPropagation();updateTaskStatus(${t.id},'completed')">Complete</button>
                <button class="btn btn-ghost btn-sm" title="Dismiss" onclick="event.stopPropagation();updateTaskStatus(${t.id},'dismissed')">Dismiss</button>
                <button class="btn btn-primary btn-sm" title="Draft Reply" onclick="event.stopPropagation();openReplyModalForTask(${t.id})">Reply</button>
            </div>
        </div>`).join('')}</div>`;
}

function renderBarChart(data) {
    if (!data || !Object.keys(data).length) return '<div class="empty-state"><div class="empty-desc">No data yet</div></div>';
    const max = Math.max(...Object.values(data), 1);
    return `<div class="chart-bars">${Object.entries(data).map(([key, val]) => `
        <div class="chart-row">
            <span class="chart-label">${escHtml(key.replace(/_/g, ' '))}</span>
            <div class="chart-bar-track">
                <div class="chart-bar-fill bar-${key}" style="width:${(val / max) * 100}%"></div>
            </div>
            <span class="chart-value">${val}</span>
        </div>`).join('')}</div>`;
}

function emptyState(title, desc) {
    return `<div class="empty-state"><div class="empty-title">${title}</div><div class="empty-desc">${desc}</div></div>`;
}

// ─── Task Detail Modal ──────────────────────────────────────────────────────

function openTaskDetail(taskId) {
    const t = Store.getTask(taskId);
    if (!t) return;

    const statusOptions = ['created','reviewed','in_progress','blocked','completed','dismissed'];
    const body = `
        <div style="margin-bottom:1rem;">
            <span class="pill ${pillClass(t.urgency)}" style="margin-right:0.5rem;">${t.urgency}</span>
            <span class="pill" style="background:var(--bg-tertiary);color:var(--text-secondary);">${t.deadline_type}</span>
            ${t.review_required ? '<span class="pill pill-warning" style="margin-left:0.5rem;">needs review</span>' : ''}
        </div>
        <div class="form-group">
            <label>Source Email</label>
            <div style="font-size:0.85rem;color:var(--text-secondary);">
                <strong>${escHtml(t.source_subject)}</strong><br>
                From: ${escHtml(t.source_sender)} · Received: ${escHtml(t.received_at || '')}
            </div>
        </div>
        <div class="form-group">
            <label>Source Quote</label>
            <div class="reply-original">${escHtml(t.source_quote)}</div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>Deadline</label>
                <div style="font-size:0.85rem;">${escHtml(t.deadline_date || 'Not specified')}</div>
            </div>
            <div class="form-group">
                <label>Assigned To</label>
                <div style="font-size:0.85rem;">${escHtml(t.assigned_to || 'Unassigned')}</div>
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>Counterparty</label>
                <div style="font-size:0.85rem;">${escHtml(t.counterparty || '—')}</div>
            </div>
            <div class="form-group">
                <label>Confidence</label>
                <div style="font-size:0.85rem;">${Math.round((t.confidence || 0) * 100)}%</div>
            </div>
        </div>
        <div class="form-group">
            <label>Action Needed</label>
            <div style="font-size:0.85rem;color:var(--text-secondary);">${escHtml(t.action_needed || 'None specified')}</div>
        </div>
        <div class="form-group">
            <label>Change Status</label>
            <select id="task-detail-status" style="width:auto;">
                ${statusOptions.map(s => `<option value="${s}" ${s === t.status ? 'selected' : ''}>${s.replace(/_/g, ' ')}</option>`).join('')}
            </select>
        </div>`;

    openModal(t.title, body, `
        <button class="btn btn-ghost" onclick="openReplyModalForTask(${t.id});closeModal()">Draft Reply</button>
        <div style="flex:1"></div>
        <button class="btn btn-ghost" onclick="closeModal()">Close</button>
        <button class="btn btn-primary" onclick="saveTaskDetailStatus(${t.id})">Save Status</button>`);
}

async function saveTaskDetailStatus(taskId) {
    if (!lockSubmit()) return;
    const status = document.getElementById('task-detail-status').value;
    try {
        await API.patch(`/api/tasks/${taskId}/status`, { status });
        closeModal();
        toast(`Task marked as ${status.replace(/_/g, ' ')}`, 'success');
        navigate();
    } catch (err) { toast(err.message, 'error'); }
    finally { unlockSubmit(); }
}

// ─── Task Status Update ─────────────────────────────────────────────────────

async function updateTaskStatus(id, status) {
    if (!lockSubmit()) return;
    try {
        await API.patch(`/api/tasks/${id}/status`, { status });
        toast(`Task ${status.replace(/_/g, ' ')}`, 'success');
        navigate();
    } catch (err) {
        toast(err.message, 'error');
    } finally { unlockSubmit(); }
}

// ─── Work / Non-Work Mails View ─────────────────────────────────────────────

async function renderWorkMails() {
    await renderEmailList('work', 'Work Mails', 'Categorized professional communication');
}

async function renderNonWorkMails() {
    await renderEmailList('miscellaneous', 'Non-Work Mails', 'Newsletters, alerts, and miscellaneous');
}

// Per-view email store (keyed by category) — fixes shared global state bug
const _emailStore = {};

async function renderEmailList(category, title, subtitle) {
    const main = document.getElementById('main-content');
    // Track pagination state per category
    _emailStore[category] = _emailStore[category] || { emails: [], offset: 0, hasMore: false };
    const PAGE_SIZE = 50;

    main.innerHTML = `
        <div class="page-header" style="display:flex; justify-content:space-between; align-items:center;">
            <div>
                <h2>${escHtml(title)}</h2>
                <p class="page-subtitle">${escHtml(subtitle)}</p>
            </div>
            <button class="btn btn-primary btn-sm" onclick="processEmails()" id="btn-process-emails">Process Emails</button>
        </div>
        <div class="page-body">
            <div class="loading-overlay"><div class="spinner"></div><span>Loading...</span></div>
        </div>`;

    await _loadEmailPage(category, PAGE_SIZE, 0, true);
}

async function _loadEmailPage(category, limit, offset, replace) {
    const main = document.getElementById('main-content');
    const pageBody = main.querySelector('.page-body');
    if (!pageBody) return;

    try {
        const emails = await API.get(`/api/emails/fetched?limit=${limit + 1}&offset=${offset}&category=${category}`);
        const hasMore = emails.length > limit;
        const page = hasMore ? emails.slice(0, limit) : emails;

        // Update scoped store
        if (replace) {
            _emailStore[category] = { emails: page, offset, hasMore };
        } else {
            _emailStore[category].emails = [..._emailStore[category].emails, ...page];
            _emailStore[category].offset = offset;
            _emailStore[category].hasMore = hasMore;
        }

        const allEmails = _emailStore[category].emails;

        if (!allEmails || allEmails.length === 0) {
            pageBody.innerHTML = '<div style="padding:2rem;text-align:center;color:var(--text-tertiary)">No emails fetched yet. Click "Process Emails" to sync.</div>';
            return;
        }

        const getStatusColor = (s) => {
            if (s === 'processed') return 'var(--success)';
            if (s === 'skipped') return 'var(--text-tertiary)';
            if (s === 'error') return 'var(--critical)';
            return 'var(--primary)';
        };

        // Fix #8: safe date formatting guard
        const formatDate = (iso) => {
            if (!iso || iso === 'None' || iso === '') return 'Unknown date';
            const d = new Date(iso);
            return isNaN(d.getTime()) ? 'Unknown date' : d.toLocaleString();
        };

        const rows = allEmails.map(e => `
            <div class="email-row" style="background:var(--bg-secondary); border:1px solid var(--border-color); border-radius:0.5rem; padding:1rem; margin-bottom:0.75rem; display:flex; flex-direction:column; gap:0.5rem; cursor:pointer;"
                 onclick="_openEmailModal('${category}', ${e.id})">
                <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                    <div style="font-weight:600; color:var(--text-primary); font-size:1rem; margin-bottom:0.25rem;">${escHtml(e.subject || '(no subject)')}</div>
                    <span class="pill" style="font-size:0.75rem; padding:0.15rem 0.5rem; border:1px solid ${getStatusColor(e.processing_status)}; color:${getStatusColor(e.processing_status)}; background:transparent; flex-shrink:0; margin-left:0.5rem;">${e.processing_status}</span>
                </div>
                <div style="font-size:0.85rem; color:var(--text-secondary);">
                    <span style="color:var(--text-primary);">${escHtml(e.sender || '')}</span> • ${formatDate(e.received_at)}
                </div>
                <div style="font-size:0.85rem; color:var(--text-tertiary); overflow:hidden; text-overflow:ellipsis; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;">
                    ${escHtml(e.body_preview || '')}
                </div>
            </div>
        `).join('');

        const loadMoreBtn = _emailStore[category].hasMore
            ? `<div style="text-align:center; padding:1rem;">
                <button class="btn btn-ghost btn-sm" onclick="_loadEmailPage('${category}', ${limit}, ${_emailStore[category].offset + limit}, false)">Load More</button>
               </div>`
            : `<div style="text-align:center; padding:0.5rem; color:var(--text-tertiary); font-size:0.8rem;">All emails loaded (${allEmails.length} total)</div>`;

        pageBody.innerHTML = `<div class="inbox-list" style="margin-top:1rem;">${rows}</div>${loadMoreBtn}`;

    } catch (err) {
        pageBody.innerHTML = `<div class="error" style="color:var(--critical); padding:2rem; text-align:center;">Failed to load emails: ${escHtml(err.message)}</div>`;
    }
}

// Fix #7: scoped to specific category store — no shared global
function _openEmailModal(category, id) {
    const store = _emailStore[category];
    if (!store) return;
    const email = store.emails.find(e => e.id === id);
    if (!email) return;
    viewEmailDetails(email);
}

function viewEmailDetails(email) {
    if (!email) return;

    // Fix #8: date guard in modal too
    const formatDate = (iso) => {
        if (!iso || iso === 'None' || iso === '') return 'Unknown date';
        const d = new Date(iso);
        return isNaN(d.getTime()) ? 'Unknown date' : d.toLocaleString();
    };

    const bodyHtml = `
        <div style="display:flex; flex-direction:column; gap:1rem;">
            <div>
                <strong>From:</strong> ${escHtml(email.sender || '')}<br>
                <strong>Date:</strong> ${formatDate(email.received_at)}<br>
                <strong>Status:</strong> ${escHtml(email.processing_status || '')}<br>
                <strong>Category:</strong> <span style="color:${email.category === 'work' ? 'var(--success)' : 'var(--text-tertiary)'};">${escHtml(email.category || 'miscellaneous')}</span>
            </div>
            <div style="background:var(--bg-primary); padding:1rem; border-radius:0.5rem; white-space:pre-wrap; font-family:monospace; font-size:0.85rem; max-height:400px; overflow-y:auto;">
                ${escHtml(email.body_preview || 'No body content available')}
            </div>
        </div>
    `;
    openModal(
        email.subject || '(no subject)', 
        bodyHtml, 
        `<button class="btn btn-ghost" onclick="closeModal()">Close</button>
         <button class="btn btn-primary" onclick="closeModal(); setTimeout(() => openReplyModalForEmail('${email.category || 'miscellaneous'}', ${email.id}), 300);">Draft Reply</button>`
    );
}


// ─── Commitments View ─────────────────────────────────────────────────────────

async function renderCommitments() {
    const main = document.getElementById('main-content');
    main.innerHTML = `
        <div class="page-header" style="display:flex; justify-content:space-between; align-items:center;">
            <div>
                <h2>Commitments</h2>
                <p class="page-subtitle">Your active tasks and deadlines</p>
            </div>
            <button class="btn btn-primary btn-sm" onclick="showNewCommitmentModal()">+ New Commitment</button>
        </div>
        <div class="page-body">
            <div style="display:flex;gap:0.5rem;margin-bottom:1rem;flex-wrap:wrap;">
                <button class="btn btn-ghost btn-sm task-filter active" data-filter="">All</button>
                <button class="btn btn-ghost btn-sm task-filter" data-filter="created">Created</button>
                <button class="btn btn-ghost btn-sm task-filter" data-filter="in_progress">In Progress</button>
                <button class="btn btn-ghost btn-sm task-filter" data-filter="blocked">Blocked</button>
                <button class="btn btn-ghost btn-sm task-filter" data-filter="completed">Completed</button>
                <button class="btn btn-ghost btn-sm task-filter" data-filter="dismissed">Dismissed</button>
            </div>
            <div id="tasks-list-container"><div class="loading-overlay"><div class="spinner"></div><span>Loading...</span></div></div>
        </div>`;

    loadTasksFiltered('');

    main.querySelectorAll('.task-filter').forEach(btn => {
        btn.addEventListener('click', () => {
            main.querySelectorAll('.task-filter').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            loadTasksFiltered(btn.dataset.filter);
        });
    });
}

async function loadTasksFiltered(status) {
    const container = document.getElementById('tasks-list-container');
    try {
        const url = status ? `/api/tasks?status=${status}&limit=100` : '/api/tasks?limit=100';
        const tasks = await API.get(url);
        Store.setTasks(tasks);
        container.innerHTML = tasks.length
            ? renderTaskList(tasks)
            : emptyState('No tasks found', 'Try a different filter');
    } catch (err) {
        container.innerHTML = `<div class="empty-state"><div class="empty-title">Error</div><div class="empty-desc">${escHtml(err.message)}</div></div>`;
    }
}

function showNewCommitmentModal() {
    const bodyHtml = `
        <form id="new-commitment-form" onsubmit="handleNewCommitment(event)">
            <div class="form-group">
                <label>Title <span style="color:var(--critical)">*</span></label>
                <input type="text" id="commit-title" required minlength="2" maxlength="500"
                    placeholder="e.g. Send proposal to client by Friday" autocomplete="off">
            </div>
            <div class="form-group">
                <label>Urgency</label>
                <select id="commit-urgency">
                    <option value="low">Low</option>
                    <option value="medium" selected>Medium</option>
                    <option value="high">High</option>
                    <option value="critical">Critical</option>
                </select>
            </div>
            <div class="form-group">
                <label>Deadline <span style="color:var(--text-tertiary); font-size:0.8rem;">(Optional)</span></label>
                <input type="date" id="commit-date">
            </div>
            <div class="form-group">
                <label>Notes / Action Needed <span style="color:var(--text-tertiary); font-size:0.8rem;">(Optional)</span></label>
                <input type="text" id="commit-notes" maxlength="300" placeholder="What needs to be done?">
            </div>
            <div id="commit-error" style="color:var(--critical); font-size:0.85rem; margin-top:0.5rem; display:none;"></div>
        </form>
    `;
    const footerHtml = `
        <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
        <button class="btn btn-primary" onclick="document.getElementById('new-commitment-form').requestSubmit()">Create</button>
    `;
    openModal('New Commitment', bodyHtml, footerHtml);
    // Focus the title after modal renders
    setTimeout(() => { const t = document.getElementById('commit-title'); if (t) t.focus(); }, 50);
}

async function handleNewCommitment(e) {
    e.preventDefault();
    if (!lockSubmit()) return;

    const errorEl = document.getElementById('commit-error');
    const title = (document.getElementById('commit-title').value || '').trim();

    // Client-side validation
    if (title.length < 2) {
        if (errorEl) { errorEl.textContent = 'Title must be at least 2 characters.'; errorEl.style.display = 'block'; }
        unlockSubmit();
        return;
    }
    if (title.length > 500) {
        if (errorEl) { errorEl.textContent = 'Title must be under 500 characters.'; errorEl.style.display = 'block'; }
        unlockSubmit();
        return;
    }
    if (errorEl) errorEl.style.display = 'none';

    try {
        const body = {
            title,
            urgency: document.getElementById('commit-urgency').value,
            deadline_date: document.getElementById('commit-date').value || null,
            action_needed: document.getElementById('commit-notes')?.value?.trim() || null,
        };
        // Remove null values before sending
        Object.keys(body).forEach(k => body[k] === null && delete body[k]);
        await API.post('/api/tasks', body);
        toast('Commitment created successfully', 'success');
        closeModal();
        if (location.hash === '#commitments') renderCommitments();
    } catch (err) {
        if (errorEl) { errorEl.textContent = 'Error: ' + err.message; errorEl.style.display = 'block'; }
        toast('Failed to create commitment: ' + err.message, 'error');
    } finally {
        unlockSubmit();
    }
}

// ─── Calendar View ──────────────────────────────────────────────────────────

let calState = { view: 'month', date: new Date() };

async function renderCalendar() {
    const main = document.getElementById('main-content');
    main.innerHTML = `
        <div class="page-header">
            <h2>Calendar</h2>
            <p class="page-subtitle">Schedule and deadlines</p>
        </div>
        <div class="page-body">
            <div class="calendar-toolbar">
                <div class="calendar-nav">
                    <button class="btn btn-ghost btn-sm" id="cal-prev">&lt;</button>
                    <button class="btn btn-ghost btn-sm" id="cal-today">Today</button>
                    <button class="btn btn-ghost btn-sm" id="cal-next">&gt;</button>
                    <span class="calendar-title" id="cal-title"></span>
                </div>
                <div style="display:flex;gap:0.5rem;align-items:center;">
                    <button class="btn btn-ghost btn-sm" id="cal-sync">Sync Calendar</button>
                    <button class="btn btn-primary btn-sm" id="cal-new-event">+ New Event</button>
                    <div class="view-switcher">
                        <button data-view="month" class="${calState.view === 'month' ? 'active' : ''}">Month</button>
                        <button data-view="week" class="${calState.view === 'week' ? 'active' : ''}">Week</button>
                        <button data-view="day" class="${calState.view === 'day' ? 'active' : ''}">Day</button>
                    </div>
                </div>
            </div>
            <div id="cal-grid"></div>
        </div>`;

    document.getElementById('cal-prev').onclick = () => { calNav(-1); };
    document.getElementById('cal-next').onclick = () => { calNav(1); };
    document.getElementById('cal-today').onclick = () => { calState.date = new Date(); renderCalGrid(); };
    document.getElementById('cal-new-event').onclick = () => openEventModal();
    document.getElementById('cal-sync').onclick = syncGoogleCalendar;
    document.querySelectorAll('.view-switcher button').forEach(btn => {
        btn.onclick = () => {
            calState.view = btn.dataset.view;
            document.querySelectorAll('.view-switcher button').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            renderCalGrid();
        };
    });

    renderCalGrid();
}

function calNav(dir) {
    const d = calState.date;
    if (calState.view === 'month') d.setMonth(d.getMonth() + dir);
    else if (calState.view === 'week') d.setDate(d.getDate() + dir * 7);
    else d.setDate(d.getDate() + dir);
    renderCalGrid();
}

async function renderCalGrid() {
    const grid = document.getElementById('cal-grid');
    if (!grid) return;

    const d = calState.date;
    let start, end;
    if (calState.view === 'month') {
        start = new Date(d.getFullYear(), d.getMonth(), 1);
        end = new Date(d.getFullYear(), d.getMonth() + 1, 0, 23, 59, 59);
        start.setDate(start.getDate() - start.getDay());
        end.setDate(end.getDate() + (6 - end.getDay()));
    } else if (calState.view === 'week') {
        start = new Date(d);
        start.setDate(start.getDate() - start.getDay());
        start.setHours(0, 0, 0, 0);
        end = new Date(start);
        end.setDate(end.getDate() + 6);
        end.setHours(23, 59, 59);
    } else {
        start = new Date(d);
        start.setHours(0, 0, 0, 0);
        end = new Date(d);
        end.setHours(23, 59, 59);
    }

    const title = document.getElementById('cal-title');
    if (calState.view === 'month') title.textContent = d.toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
    else if (calState.view === 'week') title.textContent = `Week of ${start.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`;
    else title.textContent = d.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });

    try {
        const events = await API.get(`/api/calendar/events?start=${start.toISOString()}&end=${end.toISOString()}`);
        Store.setEvents(events);
        if (calState.view === 'month') renderMonthView(grid, start, end, events);
        else if (calState.view === 'week') renderWeekView(grid, start, events);
        else renderDayView(grid, d, events);
    } catch (err) {
        grid.innerHTML = `<div class="empty-state"><div class="empty-title">Error</div><div class="empty-desc">${escHtml(err.message)}</div></div>`;
    }
}

function eventDateStr(evt) {
    /* Extract YYYY-MM-DD from start_time, handling ISO with timezone offsets */
    return evt.start_time.slice(0, 10);
}

function renderMonthView(grid, start, end, events) {
    const today = new Date();
    const todayStr = localDateStr(today);
    const curMonth = calState.date.getMonth();
    const dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

    let html = '<div class="cal-month-grid">';
    dayNames.forEach(d => { html += `<div class="cal-day-header">${d}</div>`; });

    const cursor = new Date(start);
    while (cursor <= end) {
        const dateStr = localDateStr(cursor);
        const isToday = dateStr === todayStr;
        const isOther = cursor.getMonth() !== curMonth;
        const dayEvents = events.filter(e => eventDateStr(e) === dateStr);

        html += `<div class="cal-day ${isOther ? 'other-month' : ''} ${isToday ? 'today' : ''}" data-date="${dateStr}" onclick="openEventModal('${dateStr}')">`;
        html += `<div class="day-number">${cursor.getDate()}</div>`;
        dayEvents.slice(0, 3).forEach(e => {
            html += `<div class="cal-event-pill" style="background:${e.color || '#6366f1'}22;color:${e.color || '#6366f1'};border-left:2px solid ${e.color || '#6366f1'}" onclick="event.stopPropagation();openEditEventModal(${e.id})" title="${escHtml(e.title)}">${escHtml(e.title)}</div>`;
        });
        if (dayEvents.length > 3) html += `<div style="font-size:0.6rem;color:var(--text-tertiary)">+${dayEvents.length - 3} more</div>`;
        html += '</div>';
        cursor.setDate(cursor.getDate() + 1);
    }
    html += '</div>';
    grid.innerHTML = html;
}

function renderWeekView(grid, weekStart, events) {
    const today = new Date();
    const todayStr = localDateStr(today);
    const days = [];
    for (let i = 0; i < 7; i++) {
        const d = new Date(weekStart);
        d.setDate(d.getDate() + i);
        days.push(d);
    }

    let html = '<div class="cal-week-grid">';
    html += '<div class="cal-week-header" style="border-right:1px solid var(--glass-border)">Time</div>';
    days.forEach(d => {
        const ds = localDateStr(d);
        html += `<div class="cal-week-header ${ds === todayStr ? 'today-col' : ''}">${d.toLocaleDateString('en-US', { weekday: 'short', day: 'numeric' })}</div>`;
    });

    for (let h = 7; h <= 22; h++) {
        const label = h <= 12 ? `${h} ${h < 12 ? 'AM' : 'PM'}` : `${h - 12} PM`;
        html += `<div class="cal-time-label">${label}</div>`;
        days.forEach(d => {
            const dateStr = localDateStr(d);
            const hourEvents = events.filter(e => {
                return eventDateStr(e) === dateStr && parseInt(e.start_time.slice(11, 13), 10) === h;
            });
            html += `<div class="cal-hour-cell" onclick="openEventModal('${dateStr}','${String(h).padStart(2,'0')}:00')">`;
            hourEvents.forEach(e => {
                const startH = parseInt(e.start_time.slice(11, 13), 10);
                const endH = parseInt(e.end_time.slice(11, 13), 10) || startH + 1;
                const duration = Math.max(1, endH - startH);
                html += `<div class="cal-event-block" style="top:0;height:${duration * 48 - 4}px;background:${e.color || '#6366f1'}22;color:${e.color || '#6366f1'};border-color:${e.color || '#6366f1'}" onclick="event.stopPropagation();openEditEventModal(${e.id})">${escHtml(e.title)}</div>`;
            });
            html += '</div>';
        });
    }
    html += '</div>';
    grid.innerHTML = html;
}

function renderDayView(grid, day, events) {
    const dateStr = localDateStr(day);
    let html = '<div class="cal-day-grid">';

    for (let h = 0; h <= 23; h++) {
        const label = h === 0 ? '12 AM' : h <= 12 ? `${h} ${h < 12 ? 'AM' : 'PM'}` : `${h - 12} PM`;
        html += `<div class="cal-time-label">${label}</div>`;

        const hourEvents = events.filter(e => {
            return eventDateStr(e) === dateStr && parseInt(e.start_time.slice(11, 13), 10) === h;
        });

        html += `<div class="cal-hour-cell" onclick="openEventModal('${dateStr}','${String(h).padStart(2,'0')}:00')">`;
        hourEvents.forEach(e => {
            const startH = parseInt(e.start_time.slice(11, 13), 10);
            const endH = parseInt(e.end_time.slice(11, 13), 10) || startH + 1;
            const duration = Math.max(1, endH - startH);
            const startMin = parseInt(e.start_time.slice(14, 16), 10) || 0;
            const topOffset = (startMin / 60) * 48;
            html += `<div class="cal-event-block" style="top:${topOffset}px;height:${duration * 48 - 4}px;background:${e.color || '#6366f1'}22;color:${e.color || '#6366f1'};border-color:${e.color || '#6366f1'}" onclick="event.stopPropagation();openEditEventModal(${e.id})"><strong>${e.start_time.slice(11,16)}</strong> ${escHtml(e.title)}</div>`;
        });
        html += '</div>';
    }
    html += '</div>';
    grid.innerHTML = html;
}

// ─── Calendar Event Modals ──────────────────────────────────────────────────

function openEventModal(date, time) {
    const now = new Date();
    const defaultDate = date || localDateStr(now);
    const defaultStart = time || `${String(now.getHours()).padStart(2, '0')}:00`;
    const startHour = parseInt(defaultStart.split(':')[0], 10);
    const defaultEnd = `${String(Math.min(startHour + 1, 23)).padStart(2, '0')}:00`;

    const body = `
        <div class="form-group">
            <label>Event Title</label>
            <input type="text" id="evt-title" placeholder="Meeting with team..." autofocus>
        </div>
        <div class="form-group">
            <label>Description</label>
            <textarea id="evt-desc" rows="2" placeholder="Notes..."></textarea>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>Date</label>
                <input type="date" id="evt-date" value="${defaultDate}">
            </div>
            <div class="form-group">
                <label>Type</label>
                <select id="evt-type">
                    <option value="custom">Custom</option>
                    <option value="meeting">Meeting</option>
                    <option value="task">Task</option>
                    <option value="payment">Payment</option>
                    <option value="reminder">Reminder</option>
                </select>
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>Start Time</label>
                <input type="time" id="evt-start" value="${defaultStart}">
            </div>
            <div class="form-group">
                <label>End Time</label>
                <input type="time" id="evt-end" value="${defaultEnd}">
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>Urgency</label>
                <select id="evt-urgency">
                    <option value="low">Low</option>
                    <option value="medium" selected>Medium</option>
                    <option value="high">High</option>
                    <option value="critical">Critical</option>
                </select>
            </div>
            <div class="form-group">
                <label>Reminder (min before)</label>
                <input type="number" id="evt-reminder" value="30" min="0">
            </div>
        </div>
        <div class="form-group">
            <label>Color</label>
            <input type="color" id="evt-color" value="#6366f1" style="height:36px;width:60px;padding:2px;">
        </div>`;

    openModal('New Event', body, `
        <button class="btn btn-ghost" onclick="closeModal()">Cancel</button>
        <button class="btn btn-primary" onclick="saveNewEvent()">Create Event</button>`);
}

async function saveNewEvent() {
    const date = document.getElementById('evt-date').value;
    const start = document.getElementById('evt-start').value;
    const end = document.getElementById('evt-end').value;
    
    if (!date || !start || !end) { toast('Date, start time, and end time are required', 'error'); return; }
    if (start >= end) { toast('Start time must be before end time', 'error'); return; }

    const data = {
        title: document.getElementById('evt-title').value.trim(),
        description: document.getElementById('evt-desc').value.trim(),
        start_time: `${date}T${start}:00`,
        end_time: `${date}T${end}:00`,
        event_type: document.getElementById('evt-type').value,
        urgency: document.getElementById('evt-urgency').value,
        color: document.getElementById('evt-color').value,
        reminder_minutes: parseInt(document.getElementById('evt-reminder').value, 10) || null,
    };
    if (!data.title) { toast('Event title is required', 'error'); return; }
    if (!lockSubmit()) return;
    try {
        await API.post('/api/calendar/events', data);
        closeModal();
        toast('Event created', 'success');
        renderCalGrid();
    } catch (err) { toast(err.message, 'error'); }
    finally { unlockSubmit(); }
}

async function openEditEventModal(eventId) {
    try {
        /* Use Store first, fall back to API */
        let evt = Store.getEvent(eventId);
        if (!evt) {
            const events = await API.get('/api/calendar/events');
            Store.setEvents(events);
            evt = Store.getEvent(eventId);
        }
        if (!evt) { toast('Event not found', 'error'); return; }

        const body = `
            <div class="form-group">
                <label>Event Title</label>
                <input type="text" id="evt-title" value="${escHtml(evt.title)}">
            </div>
            <div class="form-group">
                <label>Description</label>
                <textarea id="evt-desc" rows="2">${escHtml(evt.description)}</textarea>
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>Date</label>
                    <input type="date" id="evt-date" value="${evt.start_time.slice(0,10)}">
                </div>
                <div class="form-group">
                    <label>Type</label>
                    <select id="evt-type">
                        ${['custom','meeting','task','payment','reminder'].map(t => `<option value="${t}" ${t===evt.event_type?'selected':''}>${t}</option>`).join('')}
                    </select>
                </div>
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>Start Time</label>
                    <input type="time" id="evt-start" value="${evt.start_time.slice(11,16)}">
                </div>
                <div class="form-group">
                    <label>End Time</label>
                    <input type="time" id="evt-end" value="${evt.end_time.slice(11,16)}">
                </div>
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>Urgency</label>
                    <select id="evt-urgency">
                        ${['low','medium','high','critical'].map(u => `<option value="${u}" ${u===evt.urgency?'selected':''}>${u}</option>`).join('')}
                    </select>
                </div>
                <div class="form-group">
                    <label>Color</label>
                    <input type="color" id="evt-color" value="${evt.color || '#6366f1'}" style="height:36px;width:60px;padding:2px;">
                </div>
            </div>
            ${evt.description ? `<div class="form-group"><label>Linked Info</label><div class="reply-original" style="font-size:0.75rem">${escHtml(evt.description)}</div></div>` : ''}`;

        openModal('Edit Event', body, `
            <button class="btn btn-danger btn-sm" onclick="deleteEvent(${eventId})">Delete</button>
            <div style="flex:1"></div>
            <button class="btn btn-ghost" onclick="closeModal()">Cancel</button>
            <button class="btn btn-primary" onclick="saveEditEvent(${eventId})">Save</button>`);
    } catch (err) { toast(err.message, 'error'); }
}

async function saveEditEvent(eventId) {
    if (!lockSubmit()) return;
    const date = document.getElementById('evt-date').value;
    const start = document.getElementById('evt-start').value;
    const end = document.getElementById('evt-end').value;
    
    if (!date || !start || !end) { unlockSubmit(); toast('Date, start time, and end time are required', 'error'); return; }
    if (start >= end) { unlockSubmit(); toast('Start time must be before end time', 'error'); return; }

    const data = {
        title: document.getElementById('evt-title').value.trim(),
        description: document.getElementById('evt-desc').value.trim(),
        start_time: `${date}T${start}:00`,
        end_time: `${date}T${end}:00`,
        event_type: document.getElementById('evt-type').value,
        urgency: document.getElementById('evt-urgency').value,
        color: document.getElementById('evt-color').value,
    };
    if (!data.title) { unlockSubmit(); toast('Event title is required', 'error'); return; }

    try {
        await API.patch(`/api/calendar/events/${eventId}`, data);
        closeModal();
        toast('Event updated', 'success');
        renderCalGrid();
    } catch (err) { toast(err.message, 'error'); }
    finally { unlockSubmit(); }
}

async function deleteEvent(eventId) {
    if (!confirm('Are you sure you want to delete this event?')) return;
    try {
        await API.del(`/api/calendar/events/${eventId}`);
        closeModal();
        toast('Event deleted', 'success');
        renderCalGrid();
    } catch (err) { toast(err.message, 'error'); }
}

async function syncTasksToCalendar() {
    try {
        const result = await API.post('/api/calendar/sync-tasks');
        if (result.events_created > 0) {
            toast(`Synced ${result.events_created} task(s) to calendar`, 'success');
        } else {
            toast('All tasks already synced — nothing new to add', 'info');
        }
        renderCalGrid();
    } catch (err) { toast(err.message, 'error'); }
}

async function syncGoogleCalendar() {
    const btn = document.getElementById('cal-sync');
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Syncing...';
    try {
        const result = await API.post('/api/calendar/sync-gcal');
        const summary = `Synced with Google Calendar!\n` +
                        `• ${result.uploaded_to_gcal} uploaded\n` +
                        `• ${result.updated_on_gcal} updated on Google\n` +
                        `• ${result.downloaded_from_gcal} downloaded\n` +
                        `• ${result.local_tasks_synced} local tasks imported`;
        toast(summary, 'success');
        renderCalGrid();
    } catch (err) {
        toast(err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = originalText;
    }
}

// ─── Reply Engine View ──────────────────────────────────────────────────────

async function renderReplies() {
    const main = document.getElementById('main-content');
    main.innerHTML = `
        <div class="page-header">
            <h2>Reply Engine</h2>
            <p class="page-subtitle">AI drafts replies from your email — approve to send automatically via Gmail</p>
        </div>
        <div class="page-body">
            <div class="grid-2">
                <div class="card">
                    <div class="card-header"><h3>Draft a New Reply</h3></div>
                    <div class="card-body">
                        <p style="font-size:0.8rem;color:var(--text-secondary);margin-bottom:1rem;">Select a task to draft a reply for the source email.</p>
                        <div id="reply-tasks-list"><div class="loading-overlay"><div class="spinner"></div></div></div>
                    </div>
                </div>
                <div class="card">
                    <div class="card-header"><h3>Draft Queue</h3></div>
                    <div class="card-body" id="reply-drafts-list">
                        <div class="loading-overlay"><div class="spinner"></div></div>
                    </div>
                </div>
            </div>
        </div>`;

    loadReplyTasks();
    loadReplyDrafts();
}

async function loadReplyTasks() {
    const container = document.getElementById('reply-tasks-list');
    try {
        const tasks = await API.get('/api/tasks/priorities?limit=20');
        Store.setTasks(tasks);
        if (!tasks.length) {
            container.innerHTML = emptyState('No tasks', 'Process emails to see tasks here');
            return;
        }
        container.innerHTML = tasks.map(t => `
            <div class="reply-card">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:0.5rem">
                    <div>
                        <strong style="font-size:0.85rem">${escHtml(t.source_subject)}</strong>
                        <div style="font-size:0.7rem;color:var(--text-tertiary)">From: ${escHtml(t.source_sender)} · ${escHtml(t.deadline_date || 'No date')}</div>
                    </div>
                    <span class="pill ${pillClass(t.urgency)}">${t.urgency}</span>
                </div>
                <div style="font-size:0.8rem;color:var(--text-secondary);margin-bottom:0.75rem">${escHtml((t.source_quote || '').slice(0, 150))}${(t.source_quote || '').length > 150 ? '...' : ''}</div>
                <button class="btn btn-primary btn-sm" onclick="openReplyModalForTask(${t.id})">Draft Reply</button>
            </div>`).join('');
    } catch (err) {
        container.innerHTML = `<div class="empty-state"><div class="empty-desc">${escHtml(err.message)}</div></div>`;
    }
}

async function loadReplyDrafts() {
    const container = document.getElementById('reply-drafts-list');
    try {
        const drafts = await API.get('/api/replies?limit=20');
        Store.setDrafts(drafts);
        if (!drafts.length) {
            container.innerHTML = emptyState('No drafts yet', 'Draft a reply from a task to see it here');
            return;
        }
        container.innerHTML = drafts.map(d => `
            <div class="reply-card">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem">
                    <strong style="font-size:0.85rem">RE: ${escHtml(d.original_subject)}</strong>
                    <div>
                        ${d.is_auto_sent ? '<span class="pill pill-high" style="margin-right: 0.5rem; background: var(--border-color); color: var(--text-primary); border: 1px solid var(--text-tertiary);">🤖 Auto-Sent</span>' : ''}
                        <span class="pill ${d.status === 'sent' ? 'pill-success' : d.status === 'pending' ? 'pill-warning' : d.status === 'sending' ? 'pill-warning' : d.status === 'approved' ? 'pill-success' : 'pill-low'}">${d.status}</span>
                    </div>
                </div>
                <div style="font-size:0.75rem;color:var(--text-tertiary);margin-bottom:0.5rem">To: ${escHtml(d.original_sender)} · ${escHtml(d.model_used)} · ${timeAgo(d.created_at)}</div>
                <div class="reply-original">${escHtml((d.edited_text || d.draft_text || '').slice(0, 200))}${(d.edited_text || d.draft_text || '').length > 200 ? '...' : ''}</div>
                <div style="display:flex;gap:0.35rem;margin-top:0.5rem;flex-wrap:wrap">
                    ${d.status === 'sending' ? `<span style="font-size:0.72rem;color:var(--text-secondary)">Sending…</span>` : ''}
                    ${d.status === 'pending' || d.status === 'approved' ? `
                        <button class="btn btn-success btn-sm" onclick="approveReply(${d.id})">Approve &amp; Send</button>
                        <button class="btn btn-ghost btn-sm" onclick="editReplyModal(${d.id})">Edit</button>
                        <button class="btn btn-danger btn-sm" onclick="discardReply(${d.id})">Discard</button>
                    ` : ''}
                    ${d.send_error ? `<span style="font-size:0.72rem;color:var(--critical)">${escHtml(d.send_error)}</span>` : ''}
                </div>
            </div>`).join('');
    } catch (err) {
        container.innerHTML = `<div class="empty-state"><div class="empty-desc">${escHtml(err.message)}</div></div>`;
    }
}

// ─── Reply Modal (safe — uses Store, no inline JSON) ────────────────────────

function openReplyModalForTask(taskId) {
    const t = Store.getTask(taskId);
    if (!t) { toast('Task not found — refresh the page', 'error'); return; }

    const intents = ['follow_up', 'acknowledge', 'request_info', 'decline'];
    const body = `
        <div class="reply-original">
            <strong>Subject:</strong> ${escHtml(t.source_subject)}<br>
            <strong>From:</strong> ${escHtml(t.source_sender)}<br><br>
            ${escHtml(t.source_quote)}
        </div>
        <div class="form-group">
            <label>Reply Intent</label>
            <div class="reply-intent-picker">
                ${intents.map((i, idx) => `<span class="intent-chip ${idx === 0 ? 'active' : ''}" data-intent="${i}" onclick="selectIntent(this)">${i.replace(/_/g, ' ')}</span>`).join('')}
            </div>
        </div>
        <div id="reply-draft-output" style="display:none">
            <div class="form-group">
                <label>AI Draft — edit as needed</label>
                <textarea class="reply-draft-area" id="reply-draft-text" rows="6"></textarea>
            </div>
        </div>
        <div id="reply-loading" style="display:none">
            <div class="loading-overlay"><div class="spinner"></div><span>Generating reply...</span></div>
        </div>`;

    /* Store the active task ID on the generate button using a data attribute */
    openModal('Draft Reply', body, `
        <button class="btn btn-ghost" onclick="closeModal()">Cancel</button>
        <button class="btn btn-primary" id="btn-generate-reply" data-task-id="${taskId}" onclick="generateReplyFromBtn()">Generate Reply</button>
        <button class="btn btn-success" id="btn-save-reply" style="display:none" onclick="saveGeneratedReply()">Save Draft</button>`);
}

function selectIntent(chip) {
    document.querySelectorAll('.intent-chip').forEach(c => c.classList.remove('active'));
    chip.classList.add('active');
}

async function generateReplyFromBtn() {
    const genBtn = document.getElementById('btn-generate-reply');
    const taskId = parseInt(genBtn.dataset.taskId, 10);
    const t = Store.getTask(taskId);
    if (!t) { toast('Task data lost — close and try again', 'error'); return; }

    const intent = document.querySelector('.intent-chip.active')?.dataset.intent || 'follow_up';
    const loading = document.getElementById('reply-loading');
    const output = document.getElementById('reply-draft-output');
    const saveBtn = document.getElementById('btn-save-reply');

    loading.style.display = '';
    output.style.display = 'none';
    genBtn.disabled = true;
    genBtn.textContent = '⏳ Generating...';

    try {
        const result = await API.post('/api/replies/draft', {
            task_id: t.id || null,
            original_subject: t.source_subject,
            original_sender: t.source_sender,
            original_body: t.source_quote,
            reply_intent: intent,
            gmail_message_id: t.source_email_id || null,
        });
        document.getElementById('reply-draft-text').value = result.draft_text;
        output.style.display = '';
        saveBtn.style.display = '';
        saveBtn.dataset.draftId = result.id;
        toast('Reply drafted successfully', 'success');
    } catch (err) {
        toast(err.message, 'error');
    } finally {
        loading.style.display = 'none';
        genBtn.disabled = false;
        genBtn.textContent = 'Regenerate';
    }
}

async function saveGeneratedReply() {
    if (!lockSubmit()) return;
    const draftId = document.getElementById('btn-save-reply').dataset.draftId;
    const editedText = document.getElementById('reply-draft-text').value;
    try {
        await API.patch(`/api/replies/${draftId}`, { edited_text: editedText });
        closeModal();
        toast('Draft saved — find it in the Draft Queue', 'success');
        /* Refresh drafts list if we're on the replies page */
        const draftsContainer = document.getElementById('reply-drafts-list');
        if (draftsContainer) loadReplyDrafts();
    } catch (err) { toast(err.message, 'error'); }
    finally { unlockSubmit(); }
}

async function approveReply(id, editedText = null) {
    if (!confirm('Send this reply via Gmail? This cannot be undone.')) return;
    if (!lockSubmit()) return;
    try {
        const payload = editedText != null ? { edited_text: editedText } : {};
        const result = await API.post(`/api/replies/${id}/approve`, payload);
        toast(result.message || 'Reply sent via Gmail', 'success');
        loadReplyDrafts();
        closeModal();
    } catch (err) { toast(err.message, 'error'); }
    finally { unlockSubmit(); }
}

async function discardReply(id) {
    if (!lockSubmit()) return;
    try {
        await API.del(`/api/replies/${id}`);
        toast('Draft discarded', 'success');
        loadReplyDrafts();
    } catch (err) { toast(err.message, 'error'); }
    finally { unlockSubmit(); }
}

async function editReplyModal(draftId) {
    try {
        let d = Store.getDraft(draftId);
        if (!d) {
            const drafts = await API.get('/api/replies');
            Store.setDrafts(drafts);
            d = Store.getDraft(draftId);
        }
        if (!d) { toast('Draft not found', 'error'); return; }

        openModal('Edit Draft', `
            <div class="reply-original">
                <strong>RE:</strong> ${escHtml(d.original_subject)}<br>
                <strong>To:</strong> ${escHtml(d.original_sender)}
            </div>
            <div class="form-group">
                <label>Reply Text</label>
                <textarea class="reply-draft-area" id="edit-reply-text" rows="8">${escHtml(d.edited_text || d.draft_text)}</textarea>
            </div>`,
            `<button class="btn btn-ghost" onclick="closeModal()">Cancel</button>
             <button class="btn btn-primary" onclick="saveEditedReply(${draftId})">Save Changes</button>
             <button class="btn btn-success" onclick="approveReply(${draftId}, document.getElementById('edit-reply-text').value)">Approve &amp; Send</button>`);
    } catch (err) { toast(err.message, 'error'); }
}

async function saveEditedReply(id) {
    if (!lockSubmit()) return;
    const text = document.getElementById('edit-reply-text').value;
    try {
        await API.patch(`/api/replies/${id}`, { edited_text: text });
        closeModal();
        toast('Draft updated', 'success');
        loadReplyDrafts();
    } catch (err) { toast(err.message, 'error'); }
    finally { unlockSubmit(); }
}

async function copyReplyText(id) {
    try {
        let d = Store.getDraft(id);
        if (!d) {
            const drafts = await API.get('/api/replies');
            Store.setDrafts(drafts);
            d = Store.getDraft(id);
        }
        if (d) {
            await navigator.clipboard.writeText(d.edited_text || d.draft_text);
            toast('Reply copied to clipboard', 'success');
        }
    } catch (err) { toast('Failed to copy — try selecting the text manually', 'error'); }
}

// ─── Keyboard Shortcuts ─────────────────────────────────────────────────────

document.addEventListener('keydown', e => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
    if (e.key === '1') { location.hash = '#dashboard'; }
    else if (e.key === '2') { location.hash = '#calendar'; }
    else if (e.key === '3') { location.hash = '#replies'; }
    else if (e.key === '4') { location.hash = '#tasks'; }
    else if (e.key === '5') { location.hash = '#settings'; }
    else if (e.key === 'n' || e.key === 'N') {
        if (location.hash === '#calendar') openEventModal();
    }
    else if (e.key === 'Escape') closeModal();
});

// ─── Relationships View ──────────────────────────────────────────────────────

async function renderRelationships() {
    const main = document.getElementById('main-content');
    main.innerHTML = `
        <div class="page-header">
            <h2>Contact Relationships</h2>
            <p class="page-subtitle">Manage priorities and AI reply tones for specific contacts</p>
        </div>
        <div class="page-body">
            <div class="loading-overlay"><div class="spinner"></div><span>Loading...</span></div>
        </div>
    `;

    try {
        const relationships = await API.get('/api/relationships');
        
        const rows = relationships.map(r => `
            <tr style="border-bottom:1px solid var(--border-color);">
                <td style="padding:1rem; word-break:break-all;">${escHtml(r.email_address)}</td>
                <td style="padding:1rem;"><span class="status-badge" style="background:var(--bg-secondary)">${escHtml(r.role)}</span></td>
                <td style="padding:1rem;">
                    <div style="display:flex;align-items:center;gap:0.5rem">
                        <div style="flex:1;height:4px;background:var(--border-color);border-radius:2px;overflow:hidden">
                            <div style="height:100%;background:var(--primary);width:${r.importance}%"></div>
                        </div>
                        <span style="font-size:0.8rem;color:var(--text-secondary)">${r.importance}</span>
                    </div>
                </td>
                <td style="padding:1rem; text-transform:capitalize">${escHtml(r.tone_preference)}</td>
                <td style="padding:1rem; text-align:right">
                    <button class="btn btn-sm" style="color:var(--critical);border-color:transparent;background:transparent" onclick="deleteRelationship(${r.id})">Delete</button>
                </td>
            </tr>
        `).join('') || `<tr><td colspan="5" style="text-align:center;color:var(--text-tertiary);padding:3rem 1rem;">No relationships defined yet</td></tr>`;

        main.querySelector('.page-body').innerHTML = `
            <div style="display:grid;grid-template-columns:1fr 380px;gap:2rem;align-items:start;margin-top:1rem;">
                
                <!-- Relationships List Card -->
                <div class="card" style="padding:1.5rem; border-radius:12px; box-shadow:0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -1px rgba(0,0,0,0.06); background:var(--bg-primary);">
                    <div style="margin-bottom:1.5rem;display:flex;justify-content:space-between;align-items:center;">
                        <h3 style="font-size:1.1rem;font-weight:600;color:var(--text-primary);margin:0;">Active Contacts</h3>
                    </div>
                    <div style="overflow-x:auto; border:1px solid var(--border-color); border-radius:8px;">
                        <table class="table" style="width:100%; border-collapse:collapse; table-layout:fixed;">
                            <thead style="background:var(--bg-secondary);">
                                <tr>
                                    <th style="width:30%; padding:1rem; text-align:left; font-size:0.8rem; text-transform:uppercase; color:var(--text-tertiary); font-weight:600; border-bottom:1px solid var(--border-color);">Email</th>
                                    <th style="width:20%; padding:1rem; text-align:left; font-size:0.8rem; text-transform:uppercase; color:var(--text-tertiary); font-weight:600; border-bottom:1px solid var(--border-color);">Role</th>
                                    <th style="width:25%; padding:1rem; text-align:left; font-size:0.8rem; text-transform:uppercase; color:var(--text-tertiary); font-weight:600; border-bottom:1px solid var(--border-color);">Importance</th>
                                    <th style="width:15%; padding:1rem; text-align:left; font-size:0.8rem; text-transform:uppercase; color:var(--text-tertiary); font-weight:600; border-bottom:1px solid var(--border-color);">Tone</th>
                                    <th style="width:10%; padding:1rem; text-align:right; border-bottom:1px solid var(--border-color);"></th>
                                </tr>
                            </thead>
                            <tbody>${rows}</tbody>
                        </table>
                    </div>
                </div>

                <!-- Add/Edit Form Card -->
                <div class="card" style="padding:1.5rem; border-radius:12px; box-shadow:0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -1px rgba(0,0,0,0.06); background:var(--bg-primary);">
                    <h3 style="margin:0 0 1.5rem 0; font-size:1.1rem; font-weight:600; color:var(--text-primary);">Add / Update Contact</h3>
                    
                    <form onsubmit="handleUpsertRelationship(event)" style="display:flex;flex-direction:column;gap:1.25rem;">
                        <div class="form-group" style="margin:0;">
                            <label style="display:block; margin-bottom:0.4rem; font-size:0.85rem; font-weight:500; color:var(--text-secondary);">Email Address</label>
                            <input type="email" id="rel-email" required placeholder="vip@company.com" style="width:100%; padding:0.6rem 0.75rem; border-radius:6px; border:1px solid var(--border-color); background:var(--bg-secondary); color:var(--text-primary); transition:border-color 0.2s;">
                        </div>
                        
                        <div class="form-group" style="margin:0;">
                            <label style="display:block; margin-bottom:0.4rem; font-size:0.85rem; font-weight:500; color:var(--text-secondary);">Role</label>
                            <input type="text" id="rel-role" required placeholder="e.g. Boss, Client, Vendor" style="width:100%; padding:0.6rem 0.75rem; border-radius:6px; border:1px solid var(--border-color); background:var(--bg-secondary); color:var(--text-primary); transition:border-color 0.2s;">
                        </div>
                        
                        <div class="form-group" style="margin:0;">
                            <label style="display:block; margin-bottom:0.4rem; font-size:0.85rem; font-weight:500; color:var(--text-secondary);">Importance (1-100)</label>
                            <div style="display:flex; gap:1rem; align-items:center;">
                                <input type="range" id="rel-importance" min="1" max="100" value="50" style="flex:1; accent-color:var(--primary);" oninput="document.getElementById('rel-imp-val').textContent=this.value">
                                <span id="rel-imp-val" style="width:2.5rem; text-align:right; font-size:0.95rem; font-weight:600; color:var(--text-primary);">50</span>
                            </div>
                            <span style="display:block; margin-top:0.4rem; font-size:0.75rem; color:var(--text-tertiary);">Higher importance accelerates task priority.</span>
                        </div>
                        
                        <div class="form-group" style="margin:0;">
                            <label style="display:block; margin-bottom:0.4rem; font-size:0.85rem; font-weight:500; color:var(--text-secondary);">Tone Preference</label>
                            <select id="rel-tone" style="width:100%; padding:0.6rem 0.75rem; border-radius:6px; border:1px solid var(--border-color); background:var(--bg-secondary); color:var(--text-primary); cursor:pointer;">
                                <option value="professional">Professional</option>
                                <option value="casual">Casual & Friendly</option>
                                <option value="deferential">Deferential & Respectful</option>
                                <option value="assertive">Assertive & Direct</option>
                            </select>
                        </div>
                        
                        <div style="margin-top:0.5rem;">
                            <button type="submit" class="btn btn-primary" style="width:100%; padding:0.75rem; border-radius:6px; font-weight:500;">Save Relationship</button>
                        </div>
                    </form>
                </div>
            </div>
        `;
    } catch (err) {
        main.querySelector('.page-body').innerHTML = `<div class="error-state">Failed to load relationships</div>`;
    }
}

async function handleUpsertRelationship(e) {
    e.preventDefault();
    const btn = e.target.querySelector('button');
    const oldText = btn.textContent;
    btn.textContent = 'Saving...';
    btn.disabled = true;

    try {
        await API.post('/api/relationships', {
            email_address: document.getElementById('rel-email').value,
            role: document.getElementById('rel-role').value,
            importance: parseInt(document.getElementById('rel-importance').value),
            tone_preference: document.getElementById('rel-tone').value
        });
        toast('Relationship saved', 'success');
        renderRelationships();
    } catch (err) {
        toast('Failed to save', 'error');
        btn.textContent = oldText;
        btn.disabled = false;
    }
}

async function deleteRelationship(id) {
    if (!confirm('Are you sure you want to delete this relationship?')) return;
    try {
        await API.delete('/api/relationships/' + id);
        toast('Deleted successfully', 'success');
        renderRelationships();
    } catch (err) {
        toast('Failed to delete', 'error');
    }
}

// ─── Knowledge Base View ────────────────────────────────────────────────────

async function renderKnowledgeBase() {
    const main = document.getElementById('main-content');
    main.innerHTML = `
        <div class="page-header">
            <h2>Knowledge Base</h2>
            <p class="page-subtitle">Add context (FAQs, policies, pricing) for the AI to use when drafting replies</p>
        </div>
        <div class="page-body">
            <div class="loading-overlay"><div class="spinner"></div><span>Loading...</span></div>
        </div>
    `;

    try {
        const kbEntries = await API.get('/api/knowledge');
        
        const rows = kbEntries.map(k => `
            <div class="card" style="margin-bottom:1rem;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem;padding-bottom:0.5rem;border-bottom:1px solid var(--border-color)">
                    <strong style="font-size:1rem">${escHtml(k.title)}</strong>
                    <button class="btn btn-sm" style="color:var(--critical);border-color:transparent;background:transparent" onclick="deleteKnowledgeEntry(${k.id})">Delete</button>
                </div>
                <div style="font-size:0.85rem;color:var(--text-secondary);white-space:pre-wrap;max-height:150px;overflow-y:auto;padding-right:0.5rem;">${escHtml(k.content)}</div>
            </div>
        `).join('') || `<div class="empty-state"><div class="empty-desc">No knowledge base entries yet.</div></div>`;

        main.querySelector('.page-body').innerHTML = `
            <div style="display:grid;grid-template-columns:1fr 400px;gap:1.5rem;align-items:start">
                <div style="display:flex;flex-direction:column;gap:1rem;">
                    ${rows}
                </div>

                <div class="card" style="position:sticky;top:20px;">
                    <h3 style="margin-bottom:1rem;font-size:1rem;color:var(--text-primary)">Add New Entry</h3>
                    <form onsubmit="handleUpsertKnowledge(event)" style="display:flex;flex-direction:column;gap:1rem">
                        <div class="form-group">
                            <label style="display:block;margin-bottom:0.25rem;font-size:0.85rem">Title</label>
                            <input type="text" id="kb-title" required placeholder="e.g., Pricing Information" style="width:100%;padding:0.5rem;border-radius:4px;border:1px solid var(--border-color);background:var(--bg-primary);color:var(--text-primary)">
                        </div>
                        <div class="form-group">
                            <label style="display:block;margin-bottom:0.25rem;font-size:0.85rem">Content</label>
                            <textarea id="kb-content" required placeholder="Paste FAQ, policy, or instructions here..." style="width:100%;height:200px;padding:0.5rem;border-radius:4px;border:1px solid var(--border-color);background:var(--bg-primary);color:var(--text-primary);resize:vertical;"></textarea>
                        </div>
                        <button type="submit" class="btn btn-primary" style="margin-top:0.5rem">Save to Knowledge Base</button>
                    </form>
                </div>
            </div>
        `;
    } catch (err) {
        main.querySelector('.page-body').innerHTML = `<div class="error-state">Failed to load knowledge base</div>`;
    }
}

async function handleUpsertKnowledge(e) {
    e.preventDefault();
    const btn = e.target.querySelector('button');
    const oldText = btn.textContent;
    btn.textContent = 'Saving...';
    btn.disabled = true;

    try {
        await API.post('/api/knowledge', {
            title: document.getElementById('kb-title').value,
            content: document.getElementById('kb-content').value
        });
        toast('Knowledge entry saved', 'success');
        renderKnowledgeBase();
    } catch (err) {
        toast('Failed to save', 'error');
        btn.textContent = oldText;
        btn.disabled = false;
    }
}

async function deleteKnowledgeEntry(id) {
    if (!confirm('Are you sure you want to delete this knowledge entry?')) return;
    try {
        await API.delete('/api/knowledge/' + id);
        toast('Deleted successfully', 'success');
        renderKnowledgeBase();
    } catch (err) {
        toast('Failed to delete', 'error');
    }
}


// ─── Settings View ──────────────────────────────────────────────────────────

async function renderSettings() {
    const main = document.getElementById('main-content');
    main.innerHTML = `
        <div class="page-header"><h2>Settings</h2><p class="page-subtitle">System configuration and account management</p></div>
        <div class="page-body"><div class="loading-overlay"><div class="spinner"></div><span>Loading...</span></div></div>`;

    try {
        const [emailStatus, emailHistory, me] = await Promise.all([
            API.get('/api/emails/status'),
            API.get('/api/emails/history?limit=10'),
            API.get('/api/auth/me'),
        ]);

        const gmailProvider = (emailStatus.providers || []).find(p => p.provider === 'gmail');
        const isGmailConfigured = gmailProvider && gmailProvider.configured;
        const isGmailConnected = gmailProvider && gmailProvider.authenticated;
        const gmailNeedsReconnect = isGmailConnected && gmailProvider && gmailProvider.can_send === false;

        const providers = (emailStatus.providers || []).map(p => `
            <div class="settings-row">
                <span class="settings-label">${escHtml(p.display_name)}</span>
                <span class="settings-value">
                    <span class="status-dot ${p.authenticated ? 'connected' : 'disconnected'}"></span>
                    ${p.authenticated
                        ? (p.can_send === false ? 'Connected (reconnect to enable sending)' : 'Connected (read + send)')
                        : (p.configured ? 'Not authenticated' : 'Not configured')}
                </span>
            </div>`).join('');

        const gmailBtn = isGmailConnected
            ? `${gmailNeedsReconnect ? '<button class="btn btn-primary btn-sm" onclick="connectGmail()">Reconnect Gmail (enable send)</button>' : ''}
               <button class="btn btn-sm" style="border:1px solid rgba(239,68,68,0.3);color:var(--critical);background:transparent" onclick="disconnectGmail()">Disconnect Gmail</button>`
            : isGmailConfigured
                ? '<button class="btn btn-primary btn-sm" onclick="connectGmail()">Connect Gmail</button>'
                : '<span style="font-size:0.78rem;color:var(--text-tertiary)">Place credentials.json in the email processor folder to enable Gmail</span>';

        const lastRun = emailStatus.last_run;
        const lastRunInfo = lastRun
            ? `${new Date(lastRun.completed_at).toLocaleString()} — ${lastRun.emails_fetched} fetched, ${lastRun.tasks_created} tasks created`
            : 'Never';

        const historyRows = emailHistory.map(r => `
            <tr>
                <td>${new Date(r.started_at).toLocaleString()}</td>
                <td>${r.trigger}</td>
                <td>${r.emails_fetched}</td>
                <td>${r.emails_processed}</td>
                <td>${r.tasks_created}</td>
                <td><span class="run-status ${r.status}">${r.status}</span></td>
            </tr>`).join('') || '<tr><td colspan="6" style="text-align:center;color:var(--text-tertiary)">No runs yet</td></tr>';

        main.querySelector('.page-body').innerHTML = `
            <div class="settings-grid">
                <!-- Email Processing -->
                <div class="settings-section">
                    <h3>Email Processing</h3>
                    <div class="settings-row">
                        <span class="settings-label">Auto-poll</span>
                        <span class="settings-value">${emailStatus.auto_poll_enabled ? `Enabled (every ${emailStatus.poll_interval_minutes} min)` : 'Disabled'}</span>
                    </div>
                    <div class="settings-row">
                        <span class="settings-label">Autonomous Auto-Send</span>
                        <span class="settings-value">
                            <label class="toggle-switch">
                                <input type="checkbox" id="auto-send-toggle" ${me.auto_send_enabled ? 'checked' : ''} onchange="toggleAutoSend(this.checked)">
                                <span class="slider"></span>
                            </label>
                        </span>
                    </div>
                    <div class="settings-row">
                        <span class="settings-label">Last Sync</span>
                        <span class="settings-value">${escHtml(lastRunInfo)}</span>
                    </div>
                    ${providers}
                    <div style="margin-top:1rem;display:flex;gap:0.75rem;align-items:center">
                        ${gmailBtn}
                        ${isGmailConnected ? '<button class="btn btn-primary btn-sm" onclick="processEmails()" id="btn-settings-process">Process Now</button>' : ''}
                    </div>
                </div>

                <!-- Processing History -->
                <div class="settings-section">
                    <h3>Processing History</h3>
                    <table class="history-table">
                        <thead><tr><th>Time</th><th>Trigger</th><th>Fetched</th><th>Processed</th><th>Tasks</th><th>Status</th></tr></thead>
                        <tbody>${historyRows}</tbody>
                    </table>
                </div>

                <!-- Account -->
                <div class="settings-section">
                    <h3>Account</h3>
                    <div class="settings-row">
                        <span class="settings-label">Name</span>
                        <span class="settings-value">${escHtml(me.name)}</span>
                    </div>
                    <div class="settings-row">
                        <span class="settings-label">Email</span>
                        <span class="settings-value">${escHtml(me.email)}</span>
                    </div>
                    <div style="margin-top:1rem">
                        <h4 style="font-size:0.85rem;margin-bottom:0.5rem">Change Password</h4>
                        <div class="change-pw-form">
                            <div class="form-group"><input type="password" id="pw-current" placeholder="Current password"></div>
                            <div class="form-group"><input type="password" id="pw-new" placeholder="New password (min 8 chars)"></div>
                            <button class="btn btn-primary btn-sm" onclick="handleChangePassword()">Update Password</button>
                            <span id="pw-msg" style="margin-left:0.75rem;font-size:0.8rem"></span>
                        </div>
                    </div>
                </div>
            </div>`;
    } catch (err) {
        main.querySelector('.page-body').innerHTML = `<div class="empty-state"><div class="empty-title">Failed to load settings</div><div class="empty-desc">${escHtml(err.message)}</div></div>`;
    }
}

async function toggleAutoSend(enabled) {
    try {
        await API.post('/api/settings/auto-send', { enabled });
        toast(enabled ? 'Auto-send enabled' : 'Auto-send disabled', 'success');
    } catch (err) {
        toast('Failed to update auto-send setting: ' + err.message, 'error');
        // Revert toggle visually
        const toggle = document.getElementById('auto-send-toggle');
        if (toggle) toggle.checked = !enabled;
    }
}

async function handleChangePassword() {
    const cur = document.getElementById('pw-current').value;
    const nw = document.getElementById('pw-new').value;
    const msg = document.getElementById('pw-msg');
    if (!cur || !nw) { msg.textContent = 'Fill in both fields'; msg.style.color = 'var(--critical)'; return; }
    try {
        await API.post('/api/auth/change-password', { current_password: cur, new_password: nw });
        msg.textContent = 'Password updated'; msg.style.color = 'var(--success)';
        document.getElementById('pw-current').value = '';
        document.getElementById('pw-new').value = '';
    } catch (err) { msg.textContent = err.message; msg.style.color = 'var(--critical)'; }
}

async function connectGmail() {
    toast('Generating Google sign-in link...', 'info');
    try {
        const result = await API.get('/api/gmail/auth-url');
        if (result.auth_url) {
            window.location.href = result.auth_url;
        } else {
            toast(result.message || 'Gmail is already connected', 'success');
            renderSettings();
        }
    } catch (err) {
        if (err.message && err.message.includes('credentials.json')) {
            toast('Setup required: Download OAuth credentials from Google Cloud Console first.', 'error');
        } else {
            toast(err.message || 'Gmail connection failed', 'error');
        }
    }
}

async function disconnectGmail() {
    if (!confirm('Disconnect Gmail? Email auto-polling will stop until you reconnect.')) return;
    try {
        const result = await API.post('/api/gmail/disconnect');
        toast(result.message || 'Gmail disconnected', 'success');
        renderSettings();
    } catch (err) { toast(err.message, 'error'); }
}

// ─── Auth Handlers ──────────────────────────────────────────────────────────

function showAuth() {
    document.getElementById('auth-screen').style.display = '';
    document.getElementById('app-layout').style.display = 'none';
}

function showApp(user) {
    document.getElementById('auth-screen').style.display = 'none';
    document.getElementById('app-layout').style.display = '';
    const userEl = document.getElementById('sidebar-user');
    if (userEl && user) userEl.textContent = user.name || user.email;
}

async function handleRegister(e) {
    e.preventDefault();
    const errEl = document.getElementById('setup-error');
    const btn = document.getElementById('setup-btn');
    errEl.style.display = 'none';
    const name = document.getElementById('setup-name').value;
    const email = document.getElementById('setup-email').value;
    const pw = document.getElementById('setup-password').value;
    const confirm = document.getElementById('setup-confirm').value;
    if (pw !== confirm) { errEl.textContent = 'Passwords do not match.'; errEl.style.display = ''; return; }
    btn.disabled = true; btn.textContent = 'Creating account...';
    try {
        const res = await fetch('/api/auth/register', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, email, password: pw }),
        });
        const data = await res.json();
        if (!res.ok) { errEl.textContent = data.detail || 'Registration failed'; errEl.style.display = ''; return; }
        // Auto-login after registration
        btn.textContent = 'Signing in...';
        const loginRes = await fetch('/api/auth/login', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password: pw }),
        });
        const loginData = await loginRes.json();
        if (loginRes.ok) {
            setToken(loginData.token);
            showApp(loginData.user);
            location.hash = '#dashboard';
            navigate();
        }
    } catch (err) { errEl.textContent = err.message; errEl.style.display = ''; }
    finally { btn.disabled = false; btn.textContent = 'Create Account'; }
}

async function handleLogin(e) {
    e.preventDefault();
    const errEl = document.getElementById('login-error');
    const btn = document.getElementById('login-btn');
    errEl.style.display = 'none';
    const email = document.getElementById('login-email').value;
    const pw = document.getElementById('login-password').value;
    btn.disabled = true; btn.textContent = 'Signing in...';
    try {
        const res = await fetch('/api/auth/login', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password: pw }),
        });
        const data = await res.json();
        if (!res.ok) { errEl.textContent = data.detail || 'Login failed'; errEl.style.display = ''; return; }
        setToken(data.token);
        showApp(data.user);
        if (!location.hash || location.hash === '#') location.hash = '#dashboard';
        navigate();
    } catch (err) { errEl.textContent = err.message; errEl.style.display = ''; }
    finally { btn.disabled = false; btn.textContent = 'Sign In'; }
}

function handleLogout() {
    clearToken();
    showAuth();
    // Show login form
    document.getElementById('setup-form').style.display = 'none';
    document.getElementById('login-form').style.display = '';
}

function toggleAuth(type) {
    if (type === 'setup') {
        document.getElementById('setup-form').style.display = '';
        document.getElementById('login-form').style.display = 'none';
    } else {
        document.getElementById('setup-form').style.display = 'none';
        document.getElementById('login-form').style.display = '';
    }
}

// ─── Init ───────────────────────────────────────────────────────────────────

async function initApp() {
    // Check auth status
    try {
        const status = await (await fetch('/api/auth/status')).json();
        if (!status.has_account) {
            // First time — show setup
            showAuth();
            document.getElementById('setup-form').style.display = '';
            document.getElementById('login-form').style.display = 'none';
            return;
        }
        // Account exists — check token
        const token = getToken();
        if (!token) { showAuth(); document.getElementById('login-form').style.display = ''; return; }
        // Validate token
        const meRes = await fetch('/api/auth/me', { headers: { 'Authorization': `Bearer ${token}` } });
        if (!meRes.ok) { clearToken(); showAuth(); document.getElementById('login-form').style.display = ''; return; }
        const me = await meRes.json();
        showApp(me);
        if (!location.hash || location.hash === '#') location.hash = '#dashboard';
        navigate();
    } catch (err) {
        console.error('Init failed:', err);
        showAuth();
        document.getElementById('login-form').style.display = '';
    }
}

window.addEventListener('hashchange', navigate);
window.addEventListener('DOMContentLoaded', initApp);

// ─── Reply Modal for Emails (Non-Work/Work Mails) ──────────────────────────

function openReplyModalForEmail(category, emailId) {
    const store = _emailStore[category];
    if (!store) { toast('Email store not found', 'error'); return; }
    const email = store.emails.find(e => e.id === emailId);
    if (!email) { toast('Email not found — refresh the page', 'error'); return; }

    const intents = ['follow_up', 'acknowledge', 'request_info', 'decline'];
    const body = `
        <div class="reply-original">
            <strong>Subject:</strong> ${escHtml(email.subject || '(no subject)')}<br>
            <strong>From:</strong> ${escHtml(email.sender || '(no sender)')}<br><br>
            ${escHtml(email.body_preview || '(No body available)')}
        </div>
        <div class="form-group">
            <label>Reply Intent</label>
            <div class="reply-intent-picker">
                ${intents.map((i, idx) => `<span class="intent-chip ${idx === 0 ? 'active' : ''}" data-intent="${i}" onclick="selectIntent(this)">${i.replace(/_/g, ' ')}</span>`).join('')}
            </div>
        </div>
        <div id="reply-draft-output-email" style="display:none">
            <div class="form-group">
                <label>AI Draft — edit as needed</label>
                <textarea class="reply-draft-area" id="reply-draft-text-email" rows="6"></textarea>
            </div>
        </div>
        <div id="reply-loading-email" style="display:none">
            <div class="loading-overlay"><div class="spinner"></div><span>Generating reply...</span></div>
        </div>`;

    openModal('Draft Reply', body, `
        <button class="btn btn-ghost" onclick="closeModal()">Cancel</button>
        <button class="btn btn-primary" id="btn-generate-reply-email" data-email-id="${emailId}" data-category="${category}" onclick="generateReplyFromEmailBtn()">Generate Reply</button>
        <button class="btn btn-success" id="btn-save-reply-email" style="display:none" onclick="saveGeneratedReplyEmail()">Save Draft</button>`);
}

async function generateReplyFromEmailBtn() {
    const genBtn = document.getElementById('btn-generate-reply-email');
    const emailId = parseInt(genBtn.dataset.emailId, 10);
    const category = genBtn.dataset.category;
    
    const store = _emailStore[category];
    if (!store) { toast('Email store lost', 'error'); return; }
    const email = store.emails.find(e => e.id === emailId);
    if (!email) { toast('Email data lost', 'error'); return; }

    const intent = document.querySelector('.intent-chip.active')?.dataset.intent || 'follow_up';
    const loading = document.getElementById('reply-loading-email');
    const output = document.getElementById('reply-draft-output-email');
    const saveBtn = document.getElementById('btn-save-reply-email');

    loading.style.display = '';
    output.style.display = 'none';
    genBtn.disabled = true;
    genBtn.textContent = '⏳ Generating...';

    try {
        const result = await API.post('/api/replies/draft', {
            task_id: null,
            original_subject: email.subject || '',
            original_sender: email.sender || '',
            original_body: email.body_preview || '',
            reply_intent: intent,
            gmail_message_id: email.id.toString(),
            gmail_thread_id: email.id.toString()
        });
        document.getElementById('reply-draft-text-email').value = result.draft_text;
        output.style.display = '';
        saveBtn.style.display = '';
        saveBtn.dataset.draftId = result.id;
        toast('Reply drafted successfully', 'success');
    } catch (err) {
        toast(err.message, 'error');
    } finally {
        loading.style.display = 'none';
        genBtn.disabled = false;
        genBtn.textContent = 'Regenerate';
    }
}

async function saveGeneratedReplyEmail() {
    if (!lockSubmit()) return;
    const saveBtn = document.getElementById('btn-save-reply-email');
    const draftId = saveBtn.dataset.draftId;
    const editedText = document.getElementById('reply-draft-text-email').value;
    try {
        await API.patch(`/api/replies/${draftId}`, { edited_text: editedText });
        closeModal();
        toast('Reply saved successfully. View it in the Reply Engine tab.', 'success');
    } catch (err) {
        toast(err.message, 'error');
    } finally {
        unlockSubmit();
    }
}
