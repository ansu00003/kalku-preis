/* ── State ────────────────────────────────────────────────── */
let state = {
    projectId: null,
    projectName: '',
    currentStep: 1,
    // Staged files (not yet uploaded)
    lvFile: null,
    gaebFile: null,
    offerFiles: [],      // { file, supplier } objects
    // After processing
    lvLoaded: false,
    offers: [],
    matches: [],
    matchWarnings: [],
    report: null,
};

/* ── Navigation ──────────────────────────────────────────── */
function goStep(n) {
    if (n > 1 && !state.projectId) return;
    if (n > 2 && !state.lvLoaded) return;
    if (n > 3 && state.offers.length === 0) return;

    state.currentStep = n;

    document.querySelectorAll('.panel').forEach(p => p.classList.add('hidden'));
    document.getElementById(`panel-${n}`).classList.remove('hidden');

    document.querySelectorAll('.step').forEach(s => {
        const stepN = parseInt(s.dataset.step);
        s.classList.remove('active');
        if (stepN === n) s.classList.add('active');
        if (stepN < n) s.classList.add('done');
    });
}

/* ── Project ─────────────────────────────────────────────── */
async function createProject() {
    const name = document.getElementById('projectName').value.trim();
    if (!name) return alert('Bitte Projektnamen eingeben');

    const form = new FormData();
    form.append('name', name);

    try {
        const res = await fetch('/api/project/create', { method: 'POST', body: form });
        const data = await res.json();
        state.projectId = data.project_id;
        state.projectName = name;
        goStep(2);
    } catch (e) {
        alert('Fehler: ' + e.message);
    }
}

/* ── File staging (no upload yet) ────────────────────────── */
function addLvFile(file) {
    if (!file) return;
    state.lvFile = file;
    const el = document.getElementById('lvFileInfo');
    el.innerHTML = `<div class="offer-card"><div class="offer-info"><span class="offer-filename">${file.name}</span><span class="offer-meta">${(file.size / 1024).toFixed(0)} KB</span></div><span class="offer-status ok">LV</span></div>`;
    el.classList.remove('hidden');
    updateProcessBtn();
}

function addGaebFile(file) {
    if (!file) return;
    state.gaebFile = file;
    const el = document.getElementById('gaebFileInfo');
    el.innerHTML = `<div class="offer-card"><div class="offer-info"><span class="offer-filename">${file.name}</span><span class="offer-meta">${(file.size / 1024).toFixed(0)} KB</span></div><span class="offer-status ok">GAEB</span></div>`;
    el.classList.remove('hidden');
    updateProcessBtn();
}

// Files with 'mail' in name are never offers
const SKIP_FILE = (name) => {
    if (/mail/i.test(name)) {
        console.log('[SKIP] Filtering out mail file:', name);
        return true;
    }
    return false;
};

function addOfferFiles(files) {
    if (!files || files.length === 0) return;
    for (const f of files) {
        if (SKIP_FILE(f.name)) continue;
        // Guess supplier from filename
        let supplier = f.name.replace(/\.(pdf|xlsx|xls)$/i, '');
        for (const p of ['Angebot_', 'angebot_', 'Angebot', 'angebot', 'AG_', 'ag_']) supplier = supplier.replace(p, '');
        supplier = supplier.trim().replace(/[_-]+/g, ' ').trim() || 'Unbekannt';
        state.offerFiles.push({ file: f, supplier });
    }
    renderStagedOffers();
    updateProcessBtn();
}

function addOfferFolder(files) {
    // files comes from <input webkitdirectory> — flat list with webkitRelativePath
    if (!files || files.length === 0) return;
    showLoading(`${files.length} Dateien im Ordner gefunden, suche Angebote…`);

    // Use setTimeout to let the loading overlay render before we process
    setTimeout(() => {
        const found = [];
        // Skip only mail-related files (not offers)
        const skipPatterns = [
            /mail/i,   // mailempfang, _Mail.pdf, etc.
        ];

        for (const f of files) {
            // Only PDFs
            if (!/\.pdf$/i.test(f.name)) continue;
            // Skip known non-offer patterns
            if (SKIP_FILE(f.name)) continue;

            const path = f.webkitRelativePath || f.name;
            const parts = path.split('/');

            // Skip files directly in root folder (need at least root/something/file)
            if (parts.length < 3) continue;

            // Use immediate parent folder as supplier name
            // e.g. 04_Angebote/04_Vermessung/Berg/file.pdf → "Berg"
            // e.g. 04_Angebote/Kohler/file.pdf → "Kohler"
            const supplier = parts[parts.length - 2].replace(/[_-]+/g, ' ').trim();
            found.push({ file: f, supplier });
        }

        for (const item of found) {
            state.offerFiles.push(item);
        }

        hideLoading();
        renderStagedOffers();
        updateProcessBtn();

        if (found.length === 0) {
            alert(`Keine Angebots-Dateien im Ordner gefunden (${files.length} Dateien durchsucht)`);
        }
    }, 50);
}

function removeOffer(index) {
    state.offerFiles.splice(index, 1);
    renderStagedOffers();
    updateProcessBtn();
}

function renderStagedOffers() {
    // Safety net: remove any mail files that slipped through
    state.offerFiles = state.offerFiles.filter(o => !SKIP_FILE(o.file.name));
    const el = document.getElementById('offerFilesList');
    el.innerHTML = state.offerFiles.map((o, i) => `
        <div class="offer-card">
            <div class="offer-info">
                <span class="offer-filename">${o.file.name}</span>
                <span class="offer-meta">${o.supplier} — ${(o.file.size / 1024).toFixed(0)} KB</span>
            </div>
            <button class="btn btn-sm btn-danger" onclick="removeOffer(${i})">✕</button>
        </div>
    `).join('');
}

function updateProcessBtn() {
    document.getElementById('btnProcess').disabled = !state.lvFile;
}

/* ── Drag & Drop handlers ────────────────────────────────── */
function handleLvDrop(e) {
    e.preventDefault();
    if (e.dataTransfer.files[0]) addLvFile(e.dataTransfer.files[0]);
}

function handleGaebDrop(e) {
    e.preventDefault();
    if (e.dataTransfer.files[0]) addGaebFile(e.dataTransfer.files[0]);
}

async function handleOffersDrop(e) {
    e.preventDefault();
    const items = e.dataTransfer.items;

    // Check if any item is a directory (folder drop)
    let hasDirectory = false;
    const entries = [];
    if (items) {
        for (const item of items) {
            const entry = item.webkitGetAsEntry ? item.webkitGetAsEntry() : null;
            if (entry) {
                entries.push(entry);
                if (entry.isDirectory) hasDirectory = true;
            }
        }
    }

    if (hasDirectory) {
        // Recursively read directory entries and find Angebot_* files
        const found = [];
        await scanDirectoryEntries(entries, '', found);

        for (const item of found) {
            state.offerFiles.push(item);
        }
        renderStagedOffers();
        updateProcessBtn();
    } else {
        // Plain file drop
        addOfferFiles(e.dataTransfer.files);
    }
}

async function scanDirectoryEntries(entries, parentPath, results) {
    for (const entry of entries) {
        const currentPath = parentPath ? `${parentPath}/${entry.name}` : entry.name;

        if (entry.isFile) {
            const filename = entry.name.toLowerCase();
            if (!SKIP_FILE(entry.name) && filename.startsWith('angebot') && /\.(pdf|xlsx|xls)$/i.test(filename)) {
                const file = await getFileFromEntry(entry);
                const supplier = parentPath.split('/').pop() || entry.name;
                results.push({
                    file,
                    supplier: supplier.replace(/[_-]+/g, ' ').trim() || 'Unbekannt'
                });
            }
        } else if (entry.isDirectory) {
            const children = await readDirectoryEntries(entry);
            await scanDirectoryEntries(children, currentPath, results);
        }
    }

    // Fallback: if no Angebot_* found at this level, grab all PDFs/Excel
    if (parentPath === '' && results.length === 0) {
        await scanDirectoryEntriesAll(entries, '', results);
    }
}

async function scanDirectoryEntriesAll(entries, parentPath, results) {
    for (const entry of entries) {
        const currentPath = parentPath ? `${parentPath}/${entry.name}` : entry.name;
        if (entry.isFile && !SKIP_FILE(entry.name) && /\.(pdf|xlsx|xls)$/i.test(entry.name)) {
            const file = await getFileFromEntry(entry);
            const supplier = parentPath.split('/').pop() || 'Unbekannt';
            results.push({
                file,
                supplier: supplier.replace(/[_-]+/g, ' ').trim() || 'Unbekannt'
            });
        } else if (entry.isDirectory) {
            const children = await readDirectoryEntries(entry);
            await scanDirectoryEntriesAll(children, currentPath, results);
        }
    }
}

function readDirectoryEntries(dirEntry) {
    return new Promise((resolve) => {
        const reader = dirEntry.createReader();
        const allEntries = [];
        const readBatch = () => {
            reader.readEntries((entries) => {
                if (entries.length === 0) {
                    resolve(allEntries);
                } else {
                    allEntries.push(...entries);
                    readBatch();
                }
            });
        };
        readBatch();
    });
}

function getFileFromEntry(fileEntry) {
    return new Promise((resolve) => fileEntry.file(resolve));
}

/* ── Upload all + process ────────────────────────────────── */
async function uploadAndProcess() {
    if (!state.lvFile) return alert('Bitte LV-Datei wählen');
    showLoading('Dateien werden hochgeladen…', true);
    startProgressPolling();

    // Step 1: Upload all files
    const form = new FormData();
    form.append('lv_file', state.lvFile);
    form.append('sheet_name', document.getElementById('sheetName').value || 'Kalkulation');

    if (state.gaebFile) {
        form.append('gaeb_file', state.gaebFile);
    }

    const suppliers = state.offerFiles.map(o => o.supplier);

    for (const o of state.offerFiles) {
        form.append('offer_files', o.file);
    }
    form.append('supplier_names', suppliers.join(','));

    try {
        const uploadRes = await fetch(`/api/project/${state.projectId}/upload-files`, {
            method: 'POST', body: form
        });
        const uploadData = await uploadRes.json();
        if (!uploadRes.ok) throw new Error(uploadData.detail || 'Upload fehlgeschlagen');

        // Step 2: Process everything
        showLoading('Dateien werden analysiert…', true);
        const processRes = await fetch(`/api/project/${state.projectId}/process`, { method: 'POST' });
        const processData = await processRes.json();
        if (!processRes.ok) throw new Error(processData.detail || 'Verarbeitung fehlgeschlagen');

        // Show results
        const resultsEl = document.getElementById('processResults');
        resultsEl.classList.remove('hidden');

        // Hide upload zones
        document.getElementById('lvDropZone').classList.add('hidden');
        document.getElementById('lvFileInfo').classList.add('hidden');
        document.querySelectorAll('#panel-2 .section-divider').forEach(d => d.classList.add('hidden'));
        document.getElementById('offersDropZone').classList.add('hidden');
        document.getElementById('offerFilesList').classList.add('hidden');
        document.getElementById('btnProcess').classList.add('hidden');
        document.querySelector('#panel-2 .upload-zone-small')?.parentElement?.querySelector('.upload-zone-small')?.classList.add('hidden');
        document.getElementById('gaebFileInfo')?.classList.add('hidden');
        document.querySelector('#panel-2 .form-group')?.classList.add('hidden');

        // LV stats
        if (processData.lv && !processData.lv.error) {
            state.lvLoaded = true;
            const statsEl = document.getElementById('lvStats');
            const s = processData.lv.stats;
            statsEl.innerHTML = `
                <div class="stat-card">
                    <div class="stat-value">${s.total}</div>
                    <div class="stat-label">Positionen</div>
                </div>
                <div class="stat-card green">
                    <div class="stat-value">${s.filled_stoffe}</div>
                    <div class="stat-label">Stoffe befüllt</div>
                </div>
                <div class="stat-card orange">
                    <div class="stat-value">${s.filled_nu}</div>
                    <div class="stat-label">NU befüllt</div>
                </div>
                <div class="stat-card red">
                    <div class="stat-value">${s.empty}</div>
                    <div class="stat-label">Offen</div>
                </div>
            `;
        } else if (processData.lv?.error) {
            alert('LV Fehler: ' + processData.lv.error);
        }

        // GAEB info
        if (processData.gaeb && !processData.gaeb.error) {
            const el = document.getElementById('gaebInfo');
            el.innerHTML = `<div class="offer-card"><div class="offer-info"><span class="offer-filename">${processData.gaeb.filename}</span><span class="offer-meta">${processData.gaeb.format} — ${processData.gaeb.total_positions} Positionen</span></div><span class="offer-status ok">GAEB</span></div>`;
            el.classList.remove('hidden');
        }

        // Offer results
        if (processData.offers && processData.offers.length > 0) {
            state.offers = processData.offers;
            const el = document.getElementById('offersList');
            el.innerHTML = processData.offers.map(o => `
                <div class="offer-card">
                    <div class="offer-info">
                        <span class="offer-filename">${o.filename}</span>
                        <span class="offer-meta">${o.supplier} — ${o.items_found} Positionen${o.nk_zuschlag ? ` — NK +${o.nk_zuschlag}%` : ''}</span>
                    </div>
                    <span class="offer-status ${o.error ? 'error' : 'ok'}">${o.error ? '✗ ' + o.error : o.items_found + ' Pos.'}</span>
                </div>
            `).join('');
        }

    } catch (e) {
        alert('Fehler: ' + e.message);
    }
    hideLoading();
}

/* ── Matching ────────────────────────────────────────────── */
async function runMatching() {
    showLoading('Fuzzy-Matching + Claude AI…', true);
    goStep(3);
    startProgressPolling();

    const progress = document.querySelector('.progress-fill');
    progress.style.width = '30%';

    try {
        const res = await fetch(`/api/project/${state.projectId}/match`, { method: 'POST' });
        progress.style.width = '90%';
        const data = await res.json();

        if (!res.ok) throw new Error(data.detail || 'Matching fehlgeschlagen');

        state.matches = data.matches || [];
        state.matchWarnings = data.warnings || [];

        progress.style.width = '100%';
        setTimeout(() => {
            document.getElementById('matchProgress').classList.add('hidden');
            renderMatchResults(data);
        }, 300);
    } catch (e) {
        alert('Fehler: ' + e.message);
    }
    hideLoading();
}

function renderMatchResults(data) {
    const resultsEl = document.getElementById('matchResults');
    resultsEl.classList.remove('hidden');

    document.getElementById('matchSummary').textContent =
        `${data.total_matches} Zuordnungen gefunden`;

    const stoffeCount = data.by_column?.stoffe_X || 0;
    const nuCount = data.by_column?.nu_M || 0;
    const warnCount = data.warnings?.length || 0;
    const unmatchedWarn = data.warnings?.find(w => w.includes('ohne Angebotspreis')) || '';
    const unmatchedMatch = unmatchedWarn.match(/(\d+) Positionen/);
    const unmatchedCount = unmatchedMatch ? parseInt(unmatchedMatch[1]) : 0;
    document.getElementById('matchStats').innerHTML = `
        <div class="stat-card green">
            <div class="stat-value">${data.total_matches}</div>
            <div class="stat-label">Zuordnungen gesamt</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">${stoffeCount}</div>
            <div class="stat-label">Stoffe (X)</div>
            <div class="stat-bar"><div class="stat-bar-fill green" style="width:${data.total_matches ? (stoffeCount/data.total_matches*100) : 0}%"></div></div>
        </div>
        <div class="stat-card orange">
            <div class="stat-value">${nuCount}</div>
            <div class="stat-label">Nachunternehmer (M)</div>
            <div class="stat-bar"><div class="stat-bar-fill orange" style="width:${data.total_matches ? (nuCount/data.total_matches*100) : 0}%"></div></div>
        </div>
        <div class="stat-card ${warnCount > 0 ? 'red' : ''}">
            <div class="stat-value">${warnCount}</div>
            <div class="stat-label">Warnungen</div>
        </div>
        ${unmatchedCount > 0 ? `<div class="stat-card">
            <div class="stat-value">${unmatchedCount}</div>
            <div class="stat-label">Ohne Angebot</div>
        </div>` : ''}
    `;

    renderMatchTable(state.matches);

    if (state.matchWarnings.length > 0) {
        const warnEl = document.getElementById('warnings');
        warnEl.classList.remove('hidden');
        const maxShow = 10;
        const warnings = state.matchWarnings;
        warnEl.innerHTML = `
            <div class="warnings-header">
                <h3>Warnungen & nicht zugeordnet</h3>
                <span class="warnings-count">${warnings.length}</span>
            </div>
            <div class="warnings-list ${warnings.length > maxShow ? 'collapsed' : ''}">
                ${warnings.map(w => `<div class="warning-item">${w}</div>`).join('')}
            </div>
            ${warnings.length > maxShow ? `<button class="warnings-toggle" onclick="this.previousElementSibling.classList.toggle('collapsed'); this.textContent = this.textContent.includes('mehr') ? 'Weniger anzeigen' : '${warnings.length - maxShow} weitere anzeigen';">${warnings.length - maxShow} weitere anzeigen</button>` : ''}
        `;
    }
}

function renderMatchTable(matches, filter = 'all') {
    const filtered = filter === 'all' ? matches :
        filter === 'warning' ? matches.filter(m => m.warning) :
        filter === 'estimated' ? matches.filter(m => m.match_source === 'AI-Schätzung') :
        matches.filter(m => m.column === filter);

    const el = document.getElementById('matchTable');
    let html = `
        <table class="match-tbl">
            <thead>
                <tr>
                    <th class="col-oz">OZ</th>
                    <th class="col-bez">Bezeichnung</th>
                    <th class="col-spalte">Spalte</th>
                    <th class="col-ep">EP</th>
                    <th class="col-supplier">Lieferant</th>
                    <th class="col-source">Quelle</th>
                </tr>
            </thead>
            <tbody>`;

    // Sort by row (LV order)
    const sorted = [...filtered].sort((a, b) => (a.row || 0) - (b.row || 0));

    sorted.forEach((m, i) => {
        const globalIdx = state.matches.indexOf(m);
        let sourceTag;
        if (m.match_source === 'LV-POS-NR') {
            sourceTag = '<span class="source-tag direct">Direkt</span>';
        } else if (m.match_source === 'AI-Schätzung') {
            sourceTag = '<span class="source-tag estimated">Geschätzt</span>';
        } else {
            sourceTag = '<span class="source-tag claude">AI</span>';
        }
        const warningClass = m.warning ? (m.warning.includes('PREIS PRUEFEN') || m.warning.includes('UNPLAUSIBEL') ? ' has-warning price-check' : ' has-warning') : '';
        const estimatedClass = m.match_source === 'AI-Schätzung' ? ' estimated-row' : '';
        html += `
            <tr class="match-tr${warningClass}${estimatedClass}" title="${(m.explanation || '').replace(/"/g, '&quot;')}" onclick="openEditModal(${globalIdx})" style="cursor:pointer">
                <td class="col-oz"><code>${m.oz}</code></td>
                <td class="col-bez">
                    <span class="bez-text">${m.bezeichnung}</span>
                    ${m.warning ? '<span class="warning-badge" title="' + m.warning.replace(/"/g, '&quot;') + '">!</span>' : ''}
                </td>
                <td class="col-spalte"><span class="col-tag ${m.column}">${m.column === 'X' ? 'Stoffe' : 'NU'}</span></td>
                <td class="col-ep"><strong>${m.ep.toLocaleString('de-DE', {minimumFractionDigits: 2, maximumFractionDigits: 2})}</strong> <small>€/${m.lv_einheit || '?'}</small></td>
                <td class="col-supplier">${m.supplier}</td>
                <td class="col-source">${sourceTag}</td>
            </tr>`;
    });

    html += `</tbody></table>`;
    el.innerHTML = html;
}

function filterMatches(filter) {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    event.target.classList.add('active');
    renderMatchTable(state.matches, filter);
}

/* ── Edit Modal ─────────────────────────────────────────── */
let _editIndex = -1;

function openEditModal(idx) {
    const m = state.matches[idx];
    if (!m) return;
    _editIndex = idx;
    document.getElementById('editOz').textContent = m.oz;
    document.getElementById('editBez').textContent = m.bezeichnung;
    document.getElementById('editEp').value = m.ep;
    document.getElementById('editUnit').textContent = '€/' + (m.lv_einheit || '?');
    document.getElementById('editColumn').value = m.column;
    document.getElementById('editReason').value = '';
    document.getElementById('editSaveRule').checked = false;
    document.getElementById('editModal').classList.remove('hidden');
}

function closeEditModal() {
    document.getElementById('editModal').classList.add('hidden');
    _editIndex = -1;
}

async function saveEdit() {
    if (_editIndex < 0) return;
    const ep = parseFloat(document.getElementById('editEp').value);
    const column = document.getElementById('editColumn').value;
    const reason = document.getElementById('editReason').value.trim();
    const saveAsRule = document.getElementById('editSaveRule').checked;

    if (isNaN(ep) || ep < 0) return alert('Bitte gültigen Preis eingeben');
    if (!reason) return alert('Bitte Grund für die Änderung angeben');

    const form = new FormData();
    form.append('ep', ep);
    form.append('column', column);
    form.append('reason', reason);
    form.append('save_as_rule', saveAsRule);

    try {
        const res = await fetch(`/api/project/${state.projectId}/match/${_editIndex}/update`, {
            method: 'POST', body: form
        });
        const data = await res.json();
        if (!res.ok) throw new Error('Speichern fehlgeschlagen');

        // Update local state
        state.matches[_editIndex].ep = ep;
        state.matches[_editIndex].column = column;
        if (reason) {
            state.matches[_editIndex].edit_reason = reason;
            const w = state.matches[_editIndex].warning || '';
            state.matches[_editIndex].warning = (w ? w + ' | ' : '') + 'Manuell: ' + reason;
        }

        closeEditModal();
        renderMatchTable(state.matches);
    } catch (e) {
        alert('Fehler: ' + e.message);
    }
}

/* ── Rules Modal ────────────────────────────────────────── */
async function openRulesModal() {
    document.getElementById('rulesModal').classList.remove('hidden');
    await loadAndRenderRules();
}

function closeRulesModal() {
    document.getElementById('rulesModal').classList.add('hidden');
}

async function loadAndRenderRules() {
    try {
        const res = await fetch('/api/rules');
        const data = await res.json();
        const rules = data.rules || [];

        const listEl = document.getElementById('rulesList');
        const emptyEl = document.getElementById('rulesEmpty');

        if (rules.length === 0) {
            listEl.innerHTML = '';
            emptyEl.classList.remove('hidden');
            return;
        }

        emptyEl.classList.add('hidden');
        listEl.innerHTML = rules.map(r => `
            <div class="rule-card">
                <div class="rule-info">
                    <div class="rule-title">
                        ${r.type === 'price_override' ? `<strong>${r.bezeichnung || r.oz}</strong> → <strong>${Number(r.ep).toLocaleString('de-DE', {minimumFractionDigits:2})} €</strong>` : `Keyword: "${r.keyword}"`}
                    </div>
                    <div class="rule-desc">${r.description || ''}</div>
                    <div class="rule-meta">${r.created ? new Date(r.created).toLocaleDateString('de-DE') : ''}</div>
                </div>
                <button class="btn btn-sm btn-danger" onclick="deleteRuleAndRefresh(${r.id})">Löschen</button>
            </div>
        `).join('');
    } catch (e) {
        console.error('Rules load error:', e);
    }
}

async function deleteRuleAndRefresh(ruleId) {
    try {
        await fetch(`/api/rules/${ruleId}`, { method: 'DELETE' });
        await loadAndRenderRules();
    } catch (e) {
        alert('Fehler: ' + e.message);
    }
}

/* ── Write to Excel ──────────────────────────────────────── */
async function writeToExcel() {
    showLoading('Preise werden eingetragen…');

    try {
        const res = await fetch(`/api/project/${state.projectId}/write`, {
            method: 'POST'
        });
        const data = await res.json();

        if (!res.ok) throw new Error(data.detail || 'Schreiben fehlgeschlagen');

        state.report = data.report;
        renderReport(data);
        goStep(4);
    } catch (e) {
        alert('Fehler: ' + e.message);
    }
    hideLoading();
}

function renderReport(data) {
    const r = data.report;
    const el = document.getElementById('reportContent');

    el.innerHTML = `
        <div class="stat-grid">
            <div class="stat-card green">
                <div class="stat-value">${r.stats.written}</div>
                <div class="stat-label">Eingetragen</div>
            </div>
            <div class="stat-card orange">
                <div class="stat-value">${r.stats.replaced}</div>
                <div class="stat-label">Ersetzt (günstiger)</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${r.stats.skipped}</div>
                <div class="stat-label">Übersprungen</div>
            </div>
        </div>

        ${r.written.length > 0 ? `
        <div class="report-section">
            <h3>Eingetragene Preise</h3>
            ${r.written.map(w => `
                <div class="report-item">
                    <span class="label">${w.oz} ${w.bezeichnung.substring(0, 50)}</span>
                    <span class="value green">${w.ep.toFixed(2)} € → ${w.column} (${w.supplier})</span>
                </div>
            `).join('')}
        </div>` : ''}

        ${r.replaced.length > 0 ? `
        <div class="report-section">
            <h3>Ersetzungen</h3>
            ${r.replaced.map(rp => `
                <div class="report-item">
                    <span class="label">${rp.oz} ${rp.bezeichnung.substring(0, 40)}</span>
                    <span class="value green">${rp.old_price.toFixed(2)} → ${rp.new_price.toFixed(2)} € (−${rp.savings.toFixed(2)} €)</span>
                </div>
            `).join('')}
        </div>` : ''}

        ${r.warnings.length > 0 ? `
        <div class="report-section">
            <h3>Warnungen</h3>
            ${r.warnings.map(w => `
                <div class="report-item">
                    <span class="label">${w.oz}</span>
                    <span class="value red">${w.warning}</span>
                </div>
            `).join('')}
        </div>` : ''}
    `;

    document.getElementById('downloadLink').href = data.download_url;
}

/* ── Helpers ──────────────────────────────────────────────── */
let _progressInterval = null;

function showLoading(text, withProgress = false) {
    document.getElementById('loadingText').textContent = text || 'Verarbeite…';
    document.getElementById('loadingOverlay').classList.remove('hidden');
    const bar = document.getElementById('loadingProgressBar');
    const fill = document.getElementById('loadingProgressFill');
    const pct = document.getElementById('loadingProgressPct');
    const step = document.getElementById('loadingProgressStep');
    if (withProgress) {
        fill.style.width = '2%'; // start with a sliver so it's visible
        pct.textContent = '0%';
        step.textContent = '';
        bar.classList.remove('hidden');
        pct.classList.remove('hidden');
        step.classList.remove('hidden');
    } else {
        bar.classList.add('hidden');
        pct.classList.add('hidden');
        step.classList.add('hidden');
    }
}

function startProgressPolling() {
    if (!state.projectId) return;
    stopProgressPolling();
    _progressInterval = setInterval(async () => {
        try {
            const res = await fetch(`/api/project/${state.projectId}/progress`);
            const data = await res.json();
            document.getElementById('loadingProgressFill').style.width = data.pct + '%';
            document.getElementById('loadingProgressPct').textContent = data.pct + '%';
            document.getElementById('loadingProgressStep').textContent = data.step || '';
        } catch (e) { /* ignore */ }
    }, 600);
}

function stopProgressPolling() {
    if (_progressInterval) {
        clearInterval(_progressInterval);
        _progressInterval = null;
    }
}

function hideLoading() {
    stopProgressPolling();
    document.getElementById('loadingOverlay').classList.add('hidden');
}

/* ── Init ─────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('projectName').addEventListener('keydown', e => {
        if (e.key === 'Enter') createProject();
    });

    document.querySelectorAll('.upload-zone').forEach(zone => {
        zone.addEventListener('dragenter', () => zone.classList.add('dragover'));
        zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
        zone.addEventListener('drop', () => zone.classList.remove('dragover'));
    });
});
