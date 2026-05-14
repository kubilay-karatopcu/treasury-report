// ============================================================================
// competitor.js — Rakip Mevduat Faiz Analizi (v5)
// ============================================================================

function initCompetitorDashboard(rawData) {

    const pickerInput  = document.getElementById('litepicker-range');
    const filterStats  = document.getElementById('filter-stats');
    const summaryText  = document.getElementById('summary-text');
    const snapshotDate = document.getElementById('snapshot-date-label');
    const sourceLinks  = document.getElementById('source-links');

    // ────────── BANK COLOR MAP ──────────────────────────────────────────────

    const BANK_COLORS = {
        'DENİZBANK':   'rgb(0, 51, 160)',
        'DENIZBANK':   'rgb(0, 51, 160)',
        'Denizbank':   'rgb(0, 51, 160)',
        'AKBANK':      'rgb(220, 0, 5)',
        'Akbank':      'rgb(220, 0, 5)',
        'VAKIFBANK':   'rgb(253, 185, 19)',
        'VakıfBank':   'rgb(253, 185, 19)',
        'Vakıfbank':   'rgb(253, 185, 19)',
        'YAPI KREDİ': 'rgb(19, 102, 178)',
        'Yapı Kredi':  'rgb(19, 102, 178)',
        'YAPIKREDI':   'rgb(19, 102, 178)',
        'ENPARA':      'rgb(180, 78, 167)',
        'Enpara':      'rgb(180, 78, 167)',
    };

    const FALLBACK_PALETTE = [
        '#2fb344','#f76707','#ae3ec9','#0ca678','#d6336c',
        '#3bc9db','#fcc419','#868e96','#4263eb','#f06595',
        '#20c997','#fab005','#e64980','#7048e8','#15aabf'
    ];
    let _fbIdx = 0;
    const _fbMap = {};

    function bankColor(name) {
        if (!name) return '#868e96';
        if (BANK_COLORS[name]) return BANK_COLORS[name];
        const up = name.toUpperCase().replace(/İ/g, 'I').replace(/Ş/g, 'S');
        for (const [k, v] of Object.entries(BANK_COLORS)) {
            if (k.toUpperCase().replace(/İ/g, 'I').replace(/Ş/g, 'S') === up) return v;
        }
        if (!_fbMap[name]) { _fbMap[name] = FALLBACK_PALETTE[_fbIdx % FALLBACK_PALETTE.length]; _fbIdx++; }
        return _fbMap[name];
    }

    // ────────── HELPERS ─────────────────────────────────────────────────────

    function getSelectedVadeRanges() {
        return Array.from(document.querySelectorAll('.filter-vade-check:checked')).map(cb => {
            const p = cb.value.split('-').map(Number);
            return { min: p[0], max: p[1] };
        });
    }

    function getSelectedBanks() {
        const master = document.querySelector('.select-all-master[data-group=".filter-bank-check"]');
        if (master && master.checked) return null;
        return Array.from(document.querySelectorAll('.filter-bank-check:checked')).map(cb => cb.value);
    }

    function rangesOverlap(ranges, rMin, rMax) {
        if (ranges.length === 0) return false;
        return ranges.some(f => rMin <= f.max && rMax >= f.min);
    }

    function fmtDate(str) {
        if (!str) return "";
        const dt = new Date(str.replace(/-/g, "/"));
        return dt.toLocaleDateString('tr-TR', { day: '2-digit', month: 'short' });
    }

    // ────────── CHART INITS ─────────────────────────────────────────────────

    const chartSnapshotBars = new ApexCharts(document.querySelector('#chart-snapshot-bars'), {
        chart: { type: 'bar', height: 300, toolbar: { show: false } },
        plotOptions: { bar: { horizontal: false, columnWidth: '55%', borderRadius: 4, distributed: true } },
        dataLabels: { enabled: true, formatter: v => v != null ? v.toFixed(2) + '%' : '', style: { fontSize: '11px' } },
        xaxis: { type: 'category', categories: [], labels: { style: { fontSize: '12px' } } },
        yaxis: { labels: { formatter: v => v != null ? v.toFixed(1) : '' } },
        tooltip: { y: { formatter: v => v != null ? v.toFixed(2) + '%' : '-' } },
        legend: { show: false },
        colors: [],
        series: [],
        noData: { text: 'Veri bulunamadı' }
    });
    chartSnapshotBars.render();

    const chartSnapshotChange = new ApexCharts(document.querySelector('#chart-snapshot-change'), {
        chart: { type: 'bar', height: 200, toolbar: { show: false } },
        plotOptions: { bar: { horizontal: false, columnWidth: '55%', borderRadius: 3, distributed: true } },
        dataLabels: {
            enabled: true,
            formatter: v => { if (v == null) return ''; return (v > 0 ? '+' : '') + v.toFixed(2); },
            style: { fontSize: '11px' }
        },
        xaxis: { type: 'category', categories: [] },
        yaxis: { title: { text: 'Günlük Değişim (bps)' }, labels: { formatter: v => v != null ? v.toFixed(2) : '' } },
        tooltip: { y: { formatter: v => { const s = v > 0 ? '+' : ''; return s + v.toFixed(2) + ' bps'; } } },
        legend: { show: false },
        colors: [],
        series: [],
        noData: { text: 'Değişim verisi yok' }
    });
    chartSnapshotChange.render();

    const chartTrend = new ApexCharts(document.querySelector('#chart-trend'), {
        chart: { type: 'line', height: 400, toolbar: { show: true }, zoom: { enabled: true } },
        stroke: { width: 2, curve: 'smooth' },
        tooltip: { shared: true, intersect: false },
        xaxis: { type: 'category', labels: { rotate: -45, style: { fontSize: '11px' } } },
        yaxis: { labels: { formatter: v => v != null ? v.toFixed(2) : '' } },
        legend: { position: 'top' },
        series: [],
        noData: { text: 'Veri bulunamadı' }
    });
    chartTrend.render();

    let gridApi = null;

    // ────────── LLM SUMMARY ─────────────────────────────────────────────────

    let _summaryAbort = null;

    function fetchLLMSummary(sorted, prevRates, latestDay) {
        // Build structured data for the LLM
        const llmData = sorted.map(e => {
            const prev = prevRates[e[0]];
            return {
                banka: e[0],
                bugun: e[1],
                dun: prev != null ? prev : null,
                degisim: prev != null ? parseFloat((e[1] - prev).toFixed(2)) : null
            };
        });

        // Show loading state
        if (summaryText) {
            summaryText.innerHTML = '<span class="text-muted"><em>Piyasa özeti oluşturuluyor...</em></span>';
        }

        // Cancel any in-flight request
        if (_summaryAbort) _summaryAbort.abort();
        _summaryAbort = new AbortController();

        fetch('/competitor/summary', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ data: llmData }),
            signal: _summaryAbort.signal
        })
        .then(r => r.json())
        .then(data => {
            if (summaryText && data.summary) {
                summaryText.textContent = data.summary;
            }
        })
        .catch(err => {
            if (err.name === 'AbortError') return;
            console.error('LLM summary failed:', err);
            // Fallback: generate locally
            if (summaryText) {
                const top = sorted[0];
                const bot = sorted[sorted.length - 1];
                const avg = (sorted.reduce((a, e) => a + e[1], 0) / sorted.length).toFixed(2);
                summaryText.textContent =
                    `${fmtDate(latestDay)} itibarıyla en yüksek oran %${top[1].toFixed(2)} (${top[0]}), ` +
                    `en düşük %${bot[1].toFixed(2)} (${bot[0]}). Sektör ortalaması %${avg}.`;
            }
        });
    }

    // ────────── SOURCE LINKS ────────────────────────────────────────────────

    function updateSourceLinks(filteredRows) {
        if (!sourceLinks) return;
        sourceLinks.innerHTML = '';

        const sources = new Set();
        filteredRows.forEach(row => {
            const k = row.KAYNAK;
            if (k && k !== '0' && k !== 0) sources.add(String(k));
        });

        if (sources.size === 0) {
            sourceLinks.innerHTML = '<span class="text-muted small">Kaynak bilgisi bulunamadı.</span>';
            return;
        }

        Array.from(sources).sort().forEach(src => {
            const a = document.createElement('a');
            // If src looks like a URL, link directly; otherwise just show text
            if (src.startsWith('http://') || src.startsWith('https://')) {
                a.href = src;
                a.textContent = new URL(src).hostname.replace('www.', '');
            } else {
                // Might be a domain or plain text — try to make it a link
                a.href = src.includes('.') ? 'https://' + src.replace(/^https?:\/\//, '') : '#';
                a.textContent = src;
            }
            a.target = '_blank';
            a.rel = 'noopener noreferrer';
            a.className = 'badge bg-blue-lt text-blue text-decoration-none';
            a.style.cssText = 'font-size: 12px; padding: 4px 10px;';
            sourceLinks.appendChild(a);
        });
    }

    // ────────── MAIN PROCESS ────────────────────────────────────────────────

    window.processAndDisplay = function processAndDisplay() {
        const selectedVade  = getSelectedVadeRanges();
        const selectedBanks = getSelectedBanks();

        let startStr = null, endStr = null;
        if (window.litePickerInstance) {
            const d1 = window.litePickerInstance.getStartDate();
            const d2 = window.litePickerInstance.getEndDate();
            if (d1) startStr = d1.format('YYYY-MM-DD');
            if (d2) endStr   = d2.format('YYYY-MM-DD');
        }

        const filtered = rawData.filter(row => {
            if (startStr && row.DATE_STR < startStr) return false;
            if (endStr   && row.DATE_STR > endStr)   return false;
            const vMin = parseInt(row.VADE_MIN) || 0;
            const vMax = parseInt(row.VADE_MAX) || 0;
            if (!rangesOverlap(selectedVade, vMin, vMax)) return false;
            if (selectedBanks !== null && !selectedBanks.includes(row.BANKA_ADI)) return false;
            return true;
        });

        if (filterStats) {
            filterStats.textContent = filtered.length + ' kayıt' +
                (startStr && endStr ? ` (${startStr} – ${endStr})` : '');
        }

        // Source links (from filtered data)
        updateSourceLinks(filtered);

        // MAX rate per (date, bank)
        const maxByDayBank = {};
        filtered.forEach(row => {
            const d = row.DATE_STR, bank = row.BANKA_ADI, rate = parseFloat(row.FAIZ);
            if (!d || !bank || isNaN(rate) || rate <= 0) return;
            if (!maxByDayBank[d]) maxByDayBank[d] = {};
            if (!maxByDayBank[d][bank] || rate > maxByDayBank[d][bank])
                maxByDayBank[d][bank] = rate;
        });

        const sortedDays = Object.keys(maxByDayBank).sort();
        const allBanks = new Set();
        sortedDays.forEach(d => Object.keys(maxByDayBank[d]).forEach(b => allBanks.add(b)));
        const bankList = Array.from(allBanks).sort();

        // ═══ SECTION 1: SNAPSHOT ═══════════════════════════════════════════

        const latestDay = sortedDays[sortedDays.length - 1] || null;
        const prevDay   = sortedDays[sortedDays.length - 2] || null;

        if (snapshotDate) snapshotDate.textContent = latestDay ? fmtDate(latestDay) : '';

        if (latestDay && maxByDayBank[latestDay]) {
            const todayRates = maxByDayBank[latestDay];
            const sorted = Object.entries(todayRates).sort((a, b) => b[1] - a[1]);

            const barLabels  = sorted.map(e => e[0]);
            const barValues  = sorted.map(e => e[1]);
            const barColors  = sorted.map(e => bankColor(e[0]));

            const prevRates = prevDay ? (maxByDayBank[prevDay] || {}) : {};
            const changeValues = sorted.map(e => {
                const prev = prevRates[e[0]];
                return prev != null ? parseFloat((e[1] - prev).toFixed(2)) : null;
            });

            // Y-axis min: 2% below the lowest bar so differences are visible
            const minRate = Math.min(...barValues);
            const yMin = parseFloat((minRate - 1).toFixed(1));
            
            const maxRate = Math.max(...barValues);
            const yMax = parseFloat((maxRate + 1).toFixed(1));
            
            chartSnapshotBars.updateOptions({
                colors: barColors,
                xaxis: { categories: barLabels },
                yaxis: { min: yMin, labels: { formatter: v => v != null ? v.toFixed(1) : '' } },
                series: [{ name: 'Maks. Oran (%)', data: barValues }]
            });

            chartSnapshotChange.updateOptions({
                colors: barColors,
                xaxis: { categories: barLabels },
                series: [{ name: 'Değişim', data: changeValues }]
            });

            // LLM summary (async, non-blocking)
            fetchLLMSummary(sorted, prevRates, latestDay);

        } else {
            chartSnapshotBars.updateOptions({ series: [] });
            chartSnapshotChange.updateOptions({ series: [] });
            if (summaryText) summaryText.textContent = 'Seçili filtrelerde veri bulunamadı.';
        }

        // ═══ SECTION 2: TREND ══════════════════════════════════════════════

        const trendLabels = sortedDays.map(fmtDate);

        const trendSeries = bankList.map(bank => ({
            name: bank,
            data: sortedDays.map(d => maxByDayBank[d][bank] || null),
            color: bankColor(bank)
        }));

        const avgData = sortedDays.map(d => {
            const vals = Object.values(maxByDayBank[d]);
            if (vals.length === 0) return null;
            return parseFloat((vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(2));
        });

        trendSeries.unshift({ name: 'Sektör Ortalaması', data: avgData, color: '#1d273b', type: 'line' });

        const strokeWidths = [3].concat(bankList.map(() => 2));
        const strokeDash   = [5].concat(bankList.map(() => 0));

        chartTrend.updateOptions({
            stroke: { width: strokeWidths, dashArray: strokeDash, curve: 'smooth' },
            xaxis: { type: 'category', categories: trendLabels, labels: { rotate: -45, style: { fontSize: '11px' } } },
            series: trendSeries
        });

        // ═══ SECTION 3: AG GRID ════════════════════════════════════════════

        buildGroupedGrid(filtered);
    };

    // ────────── AG GRID ─────────────────────────────────────────────────────

    function buildGroupedGrid(filteredRows) {
        const rowData = filteredRows.map(row => ({
            BANKA_ADI: row.BANKA_ADI || '',
            VADE:      row.VADE      || '',
            TUTAR:     row.TUTAR     || '',
            TARIH:     row.DATE_STR  || '',
            FAIZ:      parseFloat(row.FAIZ) || null,
            DOVIZ:     row.DOVIZ_CINSI || ''
        }));

        const columnDefs = [
            { field: 'BANKA_ADI', headerName: 'Banka',     rowGroup: true, hide: true, enableRowGroup: true },
            { field: 'VADE',      headerName: 'Vade',       rowGroup: true, hide: true, enableRowGroup: true },
            { field: 'TUTAR',     headerName: 'Tutar',      enableRowGroup: true },
            { field: 'TARIH',     headerName: 'Tarih',      sort: 'desc', valueFormatter: p => p.value ? fmtDate(p.value) : '', enableRowGroup: true },
            { field: 'FAIZ',      headerName: 'Faiz (%)',   type: 'numericColumn', valueFormatter: p => p.value != null ? p.value.toFixed(2) + '%' : '-', aggFunc: 'max', enableValue: true },
            { field: 'DOVIZ',     headerName: 'Döviz',      enableRowGroup: true }
        ];

        const gridDiv = document.querySelector('#competitor-grid');
        if (gridApi) { gridApi.destroy(); gridApi = null; gridDiv.innerHTML = ''; }

        gridApi = agGrid.createGrid(gridDiv, {
            columnDefs,
            rowData,
            defaultColDef: { sortable: true, resizable: true, filter: true, flex: 1, minWidth: 100 },
            autoGroupColumnDef: { headerName: 'Grup', minWidth: 250, cellRendererParams: { suppressCount: false } },
            groupDefaultExpanded: 0,
            rowGroupPanelShow: 'always',
            animateRows: true,
            suppressAggFuncInHeader: true,
            domLayout: 'normal'
        });
    }

    // ────────── LITEPICKER ──────────────────────────────────────────────────

    const picker = new Litepicker({
        element: pickerInput, singleMode: false, numberOfMonths: 2, numberOfColumns: 2, format: 'YYYY-MM-DD',
        setup: (p) => { p.on('selected', () => processAndDisplay()); }
    });
    window.litePickerInstance = picker;

    const endDt = new Date(), startDt = new Date();
    startDt.setDate(endDt.getDate() - 30);
    picker.setDateRange(startDt, endDt);

    // ────────── FILTER LISTENERS ────────────────────────────────────────────

    document.querySelectorAll('.filter-vade-check, .filter-bank-check').forEach(cb => {
        cb.addEventListener('change', processAndDisplay);
    });

    document.querySelectorAll('.select-all-master').forEach(master => {
        const sel = master.getAttribute('data-group');
        const children = document.querySelectorAll(sel);
        if (children.length === 0) return;
        master.addEventListener('change', e => { children.forEach(c => c.checked = e.target.checked); processAndDisplay(); });
        children.forEach(c => c.addEventListener('change', () => { master.checked = Array.from(children).every(ch => ch.checked); }));
    });

    processAndDisplay();
}

document.addEventListener("DOMContentLoaded", function () {
    if (window.rawCompetitorData) initCompetitorDashboard(window.rawCompetitorData);
});