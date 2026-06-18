// ── Tab switching ────────────────────────────────────────────────────────────
function switchTab(name, el) {
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  if (el) el.classList.add('active');
  if (name === 'inbox') loadEmails();
  if (name === 'config') loadConfig();
}

// ── Status polling ────────────────────────────────────────────────────────────
function updateStatus() {
  fetch('/api/status')
    .then(r => r.json())
    .then(d => {
      document.getElementById('s-processed').textContent = d.processed;
      document.getElementById('s-sent').textContent      = d.sent;
      document.getElementById('s-skipped').textContent   = d.skipped;
      document.getElementById('s-qa').textContent        = d.qa_avg !== null ? d.qa_avg + '/5' : '—';

      const dot  = document.getElementById('sidebar-dot');
      const txt  = document.getElementById('sidebar-status-text');
      const btnS = document.getElementById('btn-start');
      const btnP = document.getElementById('btn-stop');

      if (d.running) {
        dot.className = 'status-dot running';
        txt.textContent = 'Running';
        btnS.disabled = true;
        btnP.disabled = false;
      } else {
        dot.className = 'status-dot stopped';
        txt.textContent = 'Stopped';
        btnS.disabled = false;
        btnP.disabled = true;
      }
    })
    .catch(() => {});
}
setInterval(updateStatus, 3000);
updateStatus();

// ── Monitor controls ──────────────────────────────────────────────────────────
function startMonitor() {
  fetch('/api/start', { method: 'POST' })
    .then(r => r.json())
    .then(d => {
      if (!d.ok) {
        pushLocalLog(d.msg, 'error');
      }
      updateStatus();
    });
}

function stopMonitor() {
  fetch('/api/stop', { method: 'POST' })
    .then(() => updateStatus());
}

// ── Console / Log ─────────────────────────────────────────────────────────────
const consoleEl = document.getElementById('console');

function pushLocalLog(msg, level) {
  const line = document.createElement('span');
  line.className = 'log-line log-' + (level || 'info');
  const now = new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  line.innerHTML = `<span class="log-time">${now}</span><span class="log-msg">${escHtml(msg)}</span>`;
  consoleEl.appendChild(line);
  consoleEl.scrollTop = consoleEl.scrollHeight;
}

function clearLog() { consoleEl.innerHTML = ''; }

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Server-Sent Events for live log ──────────────────────────────────────────
function connectSSE() {
  const es = new EventSource('/api/logs');
  es.onmessage = e => {
    try {
      const d = JSON.parse(e.data);
      if (d.ping) return;
      pushLocalLog(d.msg, d.level);
      highlightStep(d.msg);
    } catch {}
  };
  es.onerror = () => {
    setTimeout(connectSSE, 3000);
    es.close();
  };
}
connectSSE();

// ── Pipeline step highlighting ────────────────────────────────────────────────
function highlightStep(msg) {
  const m = msg.toLowerCase();
  const map = [
    [1, 'detecting language'],
    [2, 'translating to english'],
    [3, 'drafting persona'],
    [4, 'translating reply'],
    [5, 'sending reply'],
  ];
  for (const [n, kw] of map) {
    if (m.includes(kw)) {
      document.querySelectorAll('.step').forEach(s => s.classList.remove('active'));
      const el = document.getElementById('step-' + n);
      if (el) el.classList.add('active');
      if (n > 1) {
        const prev = document.getElementById('step-' + (n - 1));
        if (prev) { prev.classList.remove('active'); prev.classList.add('done'); }
      }
    }
    if (m.includes('reply sent') || m.includes('smtp')) {
      document.querySelectorAll('.step').forEach(s => {
        s.classList.remove('active'); s.classList.add('done');
      });
      setTimeout(() => {
        document.querySelectorAll('.step').forEach(s => s.classList.remove('done'));
      }, 4000);
    }
  }
}

// ── Inbox ─────────────────────────────────────────────────────────────────────
function loadEmails() {
  fetch('/api/emails')
    .then(r => r.json())
    .then(emails => {
      const list = document.getElementById('email-list');
      if (!emails.length) {
        list.innerHTML = '<div class="empty-state">No emails processed yet. Start the monitor to begin.</div>';
        return;
      }
      list.innerHTML = emails.map((e, i) => `
        <div class="email-card" onclick="showDetail(${i})">
          <div class="email-from">${escHtml(e.from)}</div>
          <div class="email-subject">${escHtml(e.subject)}</div>
          <div class="email-pills">
            <span class="pill pill-lang">${escHtml(e.language)}</span>
            <span class="pill ${e.tone === 'Friendly' ? 'pill-tone-f' : 'pill-tone-o'}">${escHtml(e.tone)}</span>
            <span class="pill pill-qa">QA ${e.qa_score}/5</span>
            <span class="pill pill-time">${escHtml(e.time)}</span>
          </div>
        </div>
      `).join('');
      window._emails = emails;
    });
}

function showDetail(i) {
  const e = window._emails[i];
  if (!e) return;
  document.getElementById('detail-subject').textContent  = e.subject;
  document.getElementById('detail-meta').textContent     = `From: ${e.from}  |  Language: ${e.language}  |  Tone: ${e.tone}  |  QA: ${e.qa_score}/5  |  ${e.time}`;
  document.getElementById('detail-original').textContent = e.english_text  || '(empty)';
  document.getElementById('detail-english').textContent  = e.english_reply || '(empty)';
  document.getElementById('detail-native').textContent   = e.native_reply  || '(empty)';
  document.getElementById('email-detail').classList.remove('hidden');
  document.getElementById('email-detail').scrollIntoView({ behavior: 'smooth' });
}

function closeDetail() {
  document.getElementById('email-detail').classList.add('hidden');
}

// ── Config ────────────────────────────────────────────────────────────────────
function loadConfig() {
  fetch('/api/config')
    .then(r => r.json())
    .then(d => {
      document.getElementById('cfg-email').value  = d.email_user  || '';
      document.getElementById('cfg-fast').value   = d.fast_model  || '';
      document.getElementById('cfg-strong').value = d.strong_model || '';
      document.getElementById('cfg-poll').value   = d.poll_interval || 30;
      document.getElementById('cfg-name').value   = d.persona_name  || '';
      document.getElementById('cfg-uni').value    = d.persona_uni   || '';
      document.getElementById('cfg-field').value  = d.persona_field || '';
    });
}

function saveConfig() {
  const payload = {
    email_user:    document.getElementById('cfg-email').value.trim(),
    email_pass:    document.getElementById('cfg-pass').value,
    nvapi_key:     document.getElementById('cfg-nvapi').value.trim(),
    fast_model:    document.getElementById('cfg-fast').value.trim(),
    strong_model:  document.getElementById('cfg-strong').value.trim(),
    poll_interval: parseInt(document.getElementById('cfg-poll').value) || 30,
    persona_name:  document.getElementById('cfg-name').value.trim(),
    persona_uni:   document.getElementById('cfg-uni').value.trim(),
    persona_field: document.getElementById('cfg-field').value.trim(),
  };
  fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  .then(r => r.json())
  .then(d => {
    const msg = document.getElementById('save-msg');
    msg.textContent = d.ok ? '✓ Saved successfully' : '✗ Save failed';
    setTimeout(() => msg.textContent = '', 3000);
  });
}
