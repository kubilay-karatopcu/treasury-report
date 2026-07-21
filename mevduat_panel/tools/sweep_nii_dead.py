# -*- coding: utf-8 -*-
"""Faz A7 — olu NII fonksiyon govdelerinin toplu supurulmesi.

DASHBOARD_ADAPTATION_PLAN.md §8: A0 boot baglama kodunu soktu; cagirilmayan
NII fonksiyonlari (sim/cross/BSE/dd- render'lari, refreshDates, setDataSource
vb.) JS'te duruyordu. Bu arac static/mevduat_panel.js'ten o govdeleri siler.

Liste, acorn AST tabanli cagri-grafigi analiziyle uretildi: giris noktalari =
IIFE top-level kodu + index.html referanslari + window.* atamalari + JS string
literallerinde gecen isimler; fixed-point ile canli kumeden erisilemeyen 59
fonksiyon (52 top-level span, ic ice olanlar birlestirildi). Plan §8'deki
paylasilan helper'lar (renderFig, renderWaterfall, sweepPlotly/Apex,
initChartFullscreen, bubble helper'lari) analizde CANLI dogrulandi.

Guvenlik disiplini (excise_nii_boot.py ile ayni): her span baslangic + bitis
satiri icerigiyle dogrulanir; satirlar kaymissa benzersiz baslangic satiri
aranarak span yeniden konumlanir; dogrulanamayan span hata verir, dosya
yazilmaz. Silinen spanin hemen ustundeki bitisik `//` yorum blogu da silinir.

Kullanim:
    python mevduat_panel/tools/sweep_nii_dead.py
    node --check mevduat_panel/static/mevduat_panel.js

DIKKAT: transform_a0.py dosyayi SIFIRDAN uretirse bu spanlar gecersizlesir;
yeniden koşmadan once analiz turunu tekrarlayin (bkz. plan §8 tek diff turu).
"""
import io
import os
import sys

JS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                  "..", "static", "mevduat_panel.js")

# (isimler, baslangic, bitis, baslangic-snippet, bitis-snippet) — 1-tabanli,
# snippet'ler strip edilmis satirin ilk 58 karakteri.
DEAD_SPANS = [
    ("addLoansProduct", 144, 187,
     "async function addLoansProduct(name, currency) {",
     "}"),
    ("_svgToHighResJpeg", 204, 234,
     "function _svgToHighResJpeg(containerId, scale) {",
     "}"),
    ("exportSimResultsPdf+fit", 236, 274,
     "async function exportSimResultsPdf() {",
     "}"),
    ("handleWfBarDblClick", 484, 491,
     "function handleWfBarDblClick(bar) {",
     "}"),
    ("navigateToProduct", 493, 516,
     "function navigateToProduct(bsType, productName) {",
     "}"),
    ("doNavigateInGrid", 518, 577,
     "function doNavigateInGrid(bsType, productName) {",
     "}"),
    ("renderWfSlide", 579, 600,
     "function renderWfSlide(idx) {",
     "}"),
    ("renderDdSlide+_ddDrill", 1964, 2020,
     "function renderDdSlide(idx) {",
     "}"),
    ("fetchDepositDetailWaterfalls", 2022, 2054,
     "async function fetchDepositDetailWaterfalls() {",
     "}"),
    ("toggleSection", 6651, 6673,
     "function toggleSection(name) {",
     "}"),
    ("fetchSection", 6675, 6689,
     "async function fetchSection(name) {",
     "}"),
    ("onParamsChange", 6692, 6707,
     "function onParamsChange() {",
     "}"),
    ("getSourceForApi", 6709, 6711,
     "function getSourceForApi() {",
     "}"),
    ("setSimScenarioMode", 6732, 6750,
     "function setSimScenarioMode() {",
     "}"),
    ("_bseTitle", 7997, 8000,
     "function _bseTitle(text, size) {",
     "}"),
    ("renderBseWaterfall", 8003, 8038,
     "function renderBseWaterfall(id, fig) {",
     "}"),
    ("renderBseLine", 8041, 8063,
     "function renderBseLine(id, fig) {",
     "}"),
    ("renderBseBubble+shortLabel", 8067, 8139,
     "function renderBseBubble(id, fig) {",
     "}"),
    ("_showBubbleDrillDown", 8142, 8184,
     "function _showBubbleDrillDown(bubbleId, productName, tsDat",
     "}"),
    ("renderStackedArea", 8187, 8215,
     "function renderStackedArea(id, data, opts) {",
     "}"),
    ("renderDualAxisLine", 8218, 8246,
     "function renderDualAxisLine(id, data, opts) {",
     "}"),
    ("renderBseBar", 8249, 8271,
     "function renderBseBar(id, fig) {",
     "}"),
    ("renderBseMismatchBar", 8276, 8355,
     "function renderBseMismatchBar(id, fig) {",
     "}"),
    ("_showMismatchDrillDown", 8360, 8440,
     "function _showMismatchDrillDown(mismatchId, productName, c",
     "}"),
    ("renderBseSectionA", 8443, 8450,
     "function renderBseSectionA(data) {",
     "}"),
    ("toggleBseSection", 8453, 8471,
     "function toggleBseSection(key) {",
     "}"),
    ("_bseIdentityBadge", 8473, 8484,
     "function _bseIdentityBadge(badgeId, info) {",
     "}"),
    ("fetchBseSection", 8486, 8522,
     "async function fetchBseSection(key) {",
     "}"),
    ("_wrStatCard", 8950, 8957,
     "function _wrStatCard(label, value, sub) {",
     "}"),
    ("_wrSegmentCellStyle", 9152, 9155,
     "function _wrSegmentCellStyle(p) {",
     "}"),
    ("_wrSimpleGrid", 9156, 9170,
     "function _wrSimpleGrid(hostId, rows, columnDefs) {",
     "}"),
    ("buildRawColDefs+fmtNum+fmtRate+fmtDeltaBal+deltaStyle", 9334, 9448,
     "function buildRawColDefs(date0, date1, view) {",
     "}"),
    ("syncTblNav", 9450, 9454,
     "function syncTblNav() {",
     "}"),
    ("setRawTableView", 9456, 9462,
     "function setRawTableView(v) {",
     "}"),
    ("initRawGrid", 9464, 9519,
     "function initRawGrid(records, summary, date0, date1) {",
     "}"),
    ("fetchRawData", 9521, 9541,
     "async function fetchRawData() {",
     "}"),
    ("fetchHistoric", 9543, 9550,
     "async function fetchHistoric() {",
     "}"),
    ("fetchWaterfall", 9552, 9569,
     "async function fetchWaterfall() {",
     "}"),
    ("_exportRealizedLastDate", 9573, 9580,
     "function _exportRealizedLastDate() {",
     "}"),
    ("refreshDates", 9582, 9617,
     "async function refreshDates() {",
     "}"),
    ("setDataSource", 9691, 9710,
     "function setDataSource(source) {",
     "}"),
    ("setCrossScenarioMode", 9762, 9772,
     "function setCrossScenarioMode() {",
     "}"),
    ("refreshCrossDates", 9774, 9795,
     "async function refreshCrossDates() {",
     "}"),
    ("onCrossParamsChange", 9797, 9800,
     "function onCrossParamsChange() {",
     "}"),
    ("fetchCrossWaterfall", 9802, 9820,
     "async function fetchCrossWaterfall() {",
     "}"),
    ("fetchCrossHistoric", 9822, 9832,
     "async function fetchCrossHistoric() {",
     "}"),
    ("fetchCrossRawData", 9834, 9851,
     "async function fetchCrossRawData() {",
     "}"),
    ("_npGetChecked", 10280, 10286,
     "function _npGetChecked(containerId) {",
     "}"),
    ("_npGetSelected", 10288, 10296,
     "function _npGetSelected(selId) {",
     "}"),
    ("_npBuildParams", 10298, 10316,
     "function _npBuildParams() {",
     "}"),
    ("_npVpBuildParams", 10678, 10690,
     "function _npVpBuildParams() {",
     "}"),
    ("secTitle", 11271, 11275,
     "var secTitle = function(t) {",
     "};"),
]


def _snip(line: str) -> str:
    return line.strip()[:58]


def _relocate(lines: list, l0: int, snippet: str, name: str) -> int:
    """Beklenen satirda snippet yoksa benzersiz eslesmeyi ara (kayma tolerans)."""
    hits = [i + 1 for i, ln in enumerate(lines) if _snip(ln) == snippet]
    if len(hits) == 1:
        return hits[0]
    raise SystemExit(
        "HATA: %s icin baslangic dogrulanamadi (satir %d, %d aday). "
        "Spanlar bayat — analiz turunu tekrarlayin." % (name, l0, len(hits)))


def main() -> None:
    with io.open(JS, encoding="utf-8") as f:
        lines = f.read().split("\n")

    removed = 0
    # Sondan basa: onceki silmeler alt spanlarin numarasini bozmasin.
    for name, l0, l1, snip0, snip1 in sorted(DEAD_SPANS, reverse=True,
                                             key=lambda s: s[1]):
        if _snip(lines[l0 - 1]) != snip0:
            new0 = _relocate(lines, l0, snip0, name)
            l1, l0 = l1 + (new0 - l0), new0
        if _snip(lines[l1 - 1]) != snip1:
            raise SystemExit("HATA: %s icin bitis dogrulanamadi (satir %d: %r)"
                             % (name, l1, lines[l1 - 1].strip()))
        # spanin hemen ustundeki bitisik // yorum blogu da olu sayilir
        start = l0
        while start > 1 and lines[start - 2].strip().startswith("//"):
            start -= 1
        removed += l1 - start + 1
        del lines[start - 1:l1]
        # silme sinirinda olusan cift bos satiri tekille
        i = start - 1
        while (0 < i < len(lines) and lines[i].strip() == ""
               and lines[i - 1].strip() == ""):
            del lines[i]

    with io.open(JS, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines))
    print("supuruldu: %d span, %d satir (yorumlar dahil); dosya %d satir"
          % (len(DEAD_SPANS), removed, len(lines)))


if __name__ == "__main__":
    main()
