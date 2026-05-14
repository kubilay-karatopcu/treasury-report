/* ═══════════════════════════════════════════════════════════════════════
   DEPOSIT PANEL – Parametreler Page JS  (v3)
   ═══════════════════════════════════════════════════════════════════════ */

(function () {
    "use strict";

    const STRATEGY_MAP = {
        low:  { 0: "0.65|0.70|0.75", 1: "0.70|0.75|0.75", 2: "0.75|0.80|0.80" },
        high: { 0: "0.70|0.75|0.80", 1: "0.75|0.80|0.80", 2: "0.80|0.80|0.85" },
    };
    const STRATEGY_NAMES = { 0: "Maliyet Odaklı", 1: "Dengeli", 2: "Hacim Odaklı" };
    const STATE_CLASSES  = ["state-0", "state-1", "state-2"];

    const BASE      = (typeof DEPOSIT_PANEL_BASE !== "undefined") ? DEPOSIT_PANEL_BASE : "/deposit-panel";
    const canEditHP = (typeof CAN_EDIT_HP !== "undefined") ? CAN_EDIT_HP : true;

    const $marketMax     = document.getElementById("inp-market-max");
    const $newFunding    = document.getElementById("inp-new-funding");
    const $custTpAdj     = document.getElementById("inp-cust-tp-adj");
    const $marketAdjInps = document.querySelectorAll(".market-adj-inp");
    const $btnUpdate     = document.getElementById("btn-update");
    const $toastEl       = document.getElementById("toast-msg");
    const $toastBody     = document.getElementById("toast-body");

    let chart = null;

    function getGroup(b) { return b <= 1 ? "low" : "high"; }
    function bandValue(b, l) { return STRATEGY_MAP[getGroup(b)][l]; }

    function buildPrcngString() {
        const parts = [];
        for (let b = 0; b < 4; b++) {
            const s = document.getElementById("slider-band-" + b);
            parts.push(bandValue(b, parseInt(s.value)));
        }
        return parts.join("_");
    }

    function buildMarketAdjustString() {
        const vals = [];
        $marketAdjInps.forEach(inp => vals.push(parseFloat(inp.value) || 0));
        return vals.join("|");
    }

    function showToast(msg, isError) {
        $toastBody.textContent = msg;
        $toastEl.className = "toast align-items-center border-0 " +
            (isError ? "text-bg-danger" : "text-bg-success");
        bootstrap.Toast.getOrCreateInstance($toastEl).show();
    }

    function reverseStrategy(prcng) {
        if (!prcng) return [1, 1, 1, 1];
        const bands = prcng.split("_");
        const positions = [];
        for (let b = 0; b < 4; b++) {
            const val = bands[b] || "";
            const grp = getGroup(b);
            let found = 1;
            for (let lvl = 0; lvl <= 2; lvl++) {
                if (STRATEGY_MAP[grp][lvl] === val) { found = lvl; break; }
            }
            positions.push(found);
        }
        return positions;
    }

    function updateSliderUI(b) {
        const slider = document.getElementById("slider-band-" + b);
        const detail = document.getElementById("detail-band-" + b);
        if (!slider) return;
        const level = parseInt(slider.value);
        STATE_CLASSES.forEach(c => slider.classList.remove(c));
        slider.classList.add(STATE_CLASSES[level]);
        if (detail) detail.textContent = STRATEGY_NAMES[level] + "  \u2192  " + bandValue(b, level);
    }

    // ── ApexCharts ──────────────────────────────────────────────────────
    function renderChart(data) {
        const dates      = data.map(r => r.ASOFDATE);
        const marketMax  = data.map(r => parseFloat(r.MARKET_MAX_RT));
        const newFunding = data.map(r => parseFloat(r.NEW_FUNDING_RT));

        const options = {
            chart: {
                type: "line", height: 320, fontFamily: "inherit",
                toolbar: { show: true, tools: { download: false, pan: true, zoom: true, reset: true } },
                zoom: { enabled: true },
            },
            series: [
                { name: "Market Max", data: marketMax },
                { name: "New Funding Rate", data: newFunding },
            ],
            xaxis: {
                categories: dates, type: "datetime",
                labels: { format: "dd MMM yy", rotate: -45, style: { fontSize: "11px" } },
                tickAmount: Math.min(dates.length, 20),
            },
            yaxis: {
                labels: { formatter: v => v != null ? v.toFixed(2) : "" },
                title: { text: "Oran (%)" },
            },
            stroke: { width: [2.5, 2.5], curve: "smooth" },
            colors: ["#206bc4", "#d63939"],
            markers: { size: 0, hover: { size: 5 } },
            tooltip: {
                x: { format: "dd MMM yyyy" },
                y: { formatter: v => v != null ? v.toFixed(2) + "%" : "-" },
            },
            legend: { position: "top" },
            grid: { borderColor: "#e0e6ed", strokeDashArray: 3 },
        };

        if (chart) { chart.updateOptions(options); }
        else { chart = new ApexCharts(document.getElementById("chart-params-ts"), options); chart.render(); }
    }

    // ── Load ────────────────────────────────────────────────────────────
    async function loadParams() {
        try {
            const res = await fetch(BASE + "/api/get-params", {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
            });
            const json = await res.json();
            if (!json.ok) throw new Error(json.error || "fail");
            const data = json.data;
            if (data.length > 0) {
                const latest = data[data.length - 1];
                $marketMax.value  = parseFloat(latest.MARKET_MAX_RT).toFixed(2);
                $newFunding.value = parseFloat(latest.NEW_FUNDING_RT).toFixed(2);
            }
            renderChart(data);
        } catch (e) { console.error("loadParams:", e); }
    }

    async function loadHyperparams() {
        try {
            const res = await fetch(BASE + "/api/get-hyperparams", {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
            });
            const json = await res.json();
            if (!json.ok) throw new Error(json.error || "fail");
            const hp = json.data;

            if (hp.CUST_TP_ADJ != null && $custTpAdj) $custTpAdj.value = parseFloat(hp.CUST_TP_ADJ).toFixed(2);
            if (hp.MARKET_ADJUST) {
                const parts = hp.MARKET_ADJUST.split("|");
                $marketAdjInps.forEach((inp, i) => { inp.value = parseFloat(parts[i] || 0).toFixed(2); });
            }
            if (hp.PRCNG_STRATEGIES) {
                const positions = reverseStrategy(hp.PRCNG_STRATEGIES);
                for (let b = 0; b < 4; b++) {
                    const s = document.getElementById("slider-band-" + b);
                    if (s) { s.value = positions[b]; updateSliderUI(b); }
                }
            }
            if ($btnUpdate) $btnUpdate.disabled = false;
        } catch (e) {
            console.error("loadHyperparams:", e);
            if ($btnUpdate) $btnUpdate.disabled = false;
        }
    }

    // ── Save ────────────────────────────────────────────────────────────
    async function saveAll() {
        $btnUpdate.disabled = true;
        $btnUpdate.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>G\u00fcncelleniyor...';
        let hasError = false;

        // 1. APP_PARAMS (everyone)
        try {
            const res = await fetch(BASE + "/api/set-params", {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
                body: JSON.stringify({
                    market_max_rt: parseFloat($marketMax.value),
                    new_funding_rt: parseFloat($newFunding.value),
                }),
            });
            const j = await res.json();
            if (!j.ok) throw new Error(j.error);
        } catch (e) { console.error("saveParams:", e); hasError = true; }

        // 2. HYPERPARAMETERS — restricted keys only sent when authorized
        try {
            const hpParams = { PRCNG_STRATEGIES: buildPrcngString() };
            if (canEditHP) {
                hpParams.CUST_TP_ADJ   = parseFloat($custTpAdj.value) || 0;
                hpParams.MARKET_ADJUST = buildMarketAdjustString();
            }
            const res = await fetch(BASE + "/api/set-hyperparams", {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
                body: JSON.stringify({ params: hpParams }),
            });
            const j = await res.json();
            if (!j.ok) throw new Error(j.error);
        } catch (e) { console.error("saveHyperparams:", e); hasError = true; }

        $btnUpdate.disabled = false;
        $btnUpdate.innerHTML =
            '<svg xmlns="http://www.w3.org/2000/svg" class="icon icon-tabler icon-tabler-device-floppy me-1" width="20" height="20" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round" stroke-linejoin="round"><path stroke="none" d="M0 0h24v24H0z" fill="none"/><path d="M6 4h10l4 4v10a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2v-14a2 2 0 0 1 2 -2"/><path d="M12 14m-2 0a2 2 0 1 0 4 0a2 2 0 1 0 -4 0"/><path d="M14 4l0 4l-6 0l0 -4"/></svg>G\u00fcncelle';

        showToast(hasError ? "G\u00fcncelleme s\u0131ras\u0131nda hata olu\u015ftu!" : "Parametreler ba\u015far\u0131yla g\u00fcncellendi.", hasError);
        if (!hasError) loadParams();
    }

    // ── Init ────────────────────────────────────────────────────────────
    document.addEventListener("DOMContentLoaded", function () {
        for (let b = 0; b < 4; b++) {
            const slider = document.getElementById("slider-band-" + b);
            if (slider) { slider.addEventListener("input", () => updateSliderUI(b)); updateSliderUI(b); }
        }
        if ($btnUpdate) $btnUpdate.addEventListener("click", saveAll);
        loadParams();
        loadHyperparams();
    });
})();