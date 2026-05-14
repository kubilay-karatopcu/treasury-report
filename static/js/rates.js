// ---------------------------------HELPER FNS------------------------------------------

function getSafeDate(row) {
    const dateStr = String(row.DATE_TIME_STR || "").replace(/-/g, "/");
    return new Date(dateStr);
}

function getAmountBucketLabel(amt) {
    if (amt <= 5000000) return '0-5';
    if (amt <= 10000000) return '5-10';
    if (amt <= 25000000) return '10-25';
    if (amt <= 100000000) return '25-100'
    if (amt <= 200000000) return '100-200'
    if (amt <= 500000000) return '200-500'
    if (amt <= 1000000000) return '500-1000'
    return '1000+';
}

function groupRowsByHour(rows) {
    let buckets = {};
    rows.forEach(row => {
        let dt = getSafeDate(row);
        if (isNaN(dt.getTime())) return;
        let hour = dt.getHours();
        let hourLabel = (hour < 10 ? "0" + hour : hour) + ":00";
        if (!buckets[hourLabel]) buckets[hourLabel] = [];
        buckets[hourLabel].push(row);
    });
    return buckets;
}

function calculateDistribution(rows, rateKey) {
    let distMap = {};
    
    rows.forEach(row => {
        let rate = row[rateKey];
        if (rate === null || rate === undefined || rate === 0) return;
        
        let rateLabel = parseFloat(rate).toFixed(2);
        
        distMap[rateLabel] = (distMap[rateLabel] || 0) + 1;
    });

    const sortedRates = Object.keys(distMap).sort((a, b) => parseFloat(a) - parseFloat(b));
    const counts = sortedRates.map(r => distMap[r]);

    return { labels: sortedRates, data: counts };
}

function generateHourlyWeightedAvg(sortedHours, buckets, valKey, weightKey) {
    let seriesData = [];
    
    sortedHours.forEach(h => {
        let rows = buckets[h] || [];
        let totalNum = 0;
        let totalDen = 0;

        rows.forEach(row => {
            let val = row[valKey] || 0;
            let w = row[weightKey] || 0;
            if (val > 0) {
                totalNum += val * w;
                totalDen += w;
            }
        });

        let currentAvg = 0;
        if (totalDen > 0) currentAvg = parseFloat((totalNum / totalDen).toFixed(2));
        
        seriesData.push(totalDen > 0 ? currentAvg : null);
    });
    return seriesData;
}

function generateCumulativeWeightedAvg(sortedHours, buckets, valKey, weightKey) {
    let seriesData = [];
    let totalNum = 0;
    let totalDen = 0;
    sortedHours.forEach(h => {
        let rows = buckets[h] || [];
        rows.forEach(row => {
            let val = row[valKey] || 0;
            let w = row[weightKey] || 0;
            if (val > 0) {
                totalNum += val * w;
                totalDen += w;
            }
        });
        let currentAvg = 0;
        if (totalDen > 0) currentAvg = parseFloat((totalNum / totalDen).toFixed(2));
        seriesData.push(currentAvg);
    });
    return seriesData;
}

function generateCumulativeSpread(sortedHours, buckets, valKey, weightKey) {
    let seriesData = [];
    
    let totalNum = 0;
    let totalDen = 0;

    sortedHours.forEach(h => {
        let rows = buckets[h] || [];
        
        let marketMax = 0;
        if (rows.length > 0) {
            marketMax = parseFloat(rows[0].MARKET_MAX_RT) || 0;
        }

        rows.forEach(row => {
            let val = parseFloat(row[valKey]) || 0;
            let w = parseFloat(row[weightKey]) || 0;
            
            if (val > 0 && w > 0) {
                totalNum += val * w;
                totalDen += w;
            }
        });

        let currentSpread = null;
        if (totalDen > 0) {
            let currentAvg = totalNum / totalDen;
            
            currentSpread = parseFloat((currentAvg - marketMax).toFixed(2));
        }
        seriesData.push(currentSpread);
    });

    return seriesData;
}

function generateCumulativeCount(sortedHours, buckets) {
    let seriesData = [];
    let runningCount = 0;
    sortedHours.forEach(h => {
        let rows = buckets[h] || [];
        runningCount += rows.length;
        seriesData.push(runningCount);
    });
    return seriesData;
}

function generateHourlyCount(sortedHours, buckets) {
    let seriesData = [];
    sortedHours.forEach(h => {
        let rows = buckets[h] || [];
        seriesData.push(rows.length);
    });
    return seriesData;
}

function generateSpreadSeries(sortedHours, buckets, weightKey) {
    let seriesData = [];
    
    sortedHours.forEach(h => {
        let rows = buckets[h] || [];
        
        let marketMax = 0;
        if (rows.length > 0) {
            marketMax = parseFloat(rows[0].MARKET_MAX_RT) || 0;
        }

        let totalNum = 0;
        let totalDen = 0;
        
        rows.forEach(row => {
            let val = parseFloat(row.OFFERED_RATE) || 0;
            let w = parseFloat(row[weightKey]) || 0;
            
            if (val > 0 && w > 0) {
                totalNum += val * w;
                totalDen += w;
            }
        });

        let weightedAvg = 0;
        if (totalDen > 0) {
            weightedAvg = totalNum / totalDen;
        }

        let spread = (totalDen > 0) ? (weightedAvg) : 0;
        
        seriesData.push(parseFloat(spread.toFixed(2)));
    });
    
    return seriesData;
}

function getFixedValue(sortedHourlyKeys, hourlyBuckets, key) {
    let counts = {}; 
    let mode = null;
    let maxCount = 0;

    sortedHourlyKeys.forEach(hKey => {
        let rows = hourlyBuckets[hKey] || [];
        
        rows.forEach(r => {
            let val = parseFloat(r[key]);
            if (!isNaN(val) && val > 0) {
                counts[val] = (counts[val] || 0) + 1;
            }
        });
    });

    for (let val in counts) {
        if (counts[val] > maxCount) {
            maxCount = counts[val];
            mode = parseFloat(val);
        }
    }

    let seriesData = [];
    sortedHourlyKeys.forEach(() => {
        seriesData.push(mode);
    });

    return seriesData;
}

// ------------------------DASHBOARD INITIALIZATION---------------------------------

function initDashboard(initialDate) {
    const datePicker = document.getElementById('litepicker-date');
    const displayDate = document.getElementById('display-date');
    const filterStats = document.getElementById('filter-stats');
    
    const filterSrc = document.getElementById('filter-src');
    const filterCcy = document.getElementById('filter-ccy');
    
    let currentRawData = [];
    const clientCache = {};
    
    function createTimeSeriesChart(selector, color1, color2, title1, title2 = "Yeni Oran") {
        const options = {
            chart: { 
                type: 'line', 
                height: 350, 
                toolbar: {
                    show: true,
                    tools: {
                        download: true, selection: true, zoom: true,
                        zoomin: true, zoomout: true, pan: true, reset: true
                    },
                    autoSelected: 'zoom'
                },
                animations: { enabled: true } 
            },
            colors: ["#206bc4", color1, color2],
            stroke: { width: [0, 5, 3], dashArray: [0, 0, 2], curve: 'smooth' },
            plotOptions: { bar: { columnWidth: '50%', borderRadius: 4 } },
            fill: { opacity: [0.8, 1, 0.6], type: ['solid', 'solid', 'solid'] },
            dataLabels: { enabled: false },
            series: [],
            noData: { text: 'Veri Bekleniyor...' },
            xaxis: { type: 'category', tickAmount: 10, labels: { rotate: -45, style: { fontSize: '11px' } } },
            
            yaxis: [
                // INDEX 0 / Sol Eksen / Bar Chart
                { 
                    seriesName: 'İşlem Sayısı',
                    title: { text: "Kümülatif Adet", style: { color: '#206bc4' } }, 
                    labels: { formatter: (val) => val.toFixed(0) } 
                },
                
                // INDEX 1 / Sağ Eksen / Düz Çizgi (Oranlar)
                { 
                    seriesName: title1,
                    opposite: true, 
                    title: { text: title1 + " (%)", style: { color: color1 } }, 
                    labels: { formatter: (val) => val.toFixed(2) + "%", style: { colors: color1 } } 
                },

                // INDEX 2 / Sağ Eksen / Kesikli Çizgi (Ekstrem Yetki)
                { 
                    seriesName: title1,
                    opposite: true, 
                    show: false,
                }
            ],
            
            legend: { position: 'top' },
            grid: { strokeDashArray: 4 }
        };
        
        window.chart = new ApexCharts(document.querySelector(selector), options);
        chart.render();
        return chart;
    }

    function createDistributionChart(selector, color, title) {
        const options = {
            chart: { type: 'bar', height: 350, toolbar: {
            show: true,
            tools: {
                download: true,
                selection: true,
                zoom: true,
                zoomin: true,
                zoomout: true,
                pan: true,
                reset: true
            },
            autoSelected: 'zoom'
        }, animations: { enabled: true } },
            colors: [color],
            plotOptions: { bar: { borderRadius: 4, horizontal: false, columnWidth: '60%' } }, // Dikey Çubuklar
            dataLabels: { enabled: false },
            series: [],
            noData: { text: 'Veri Yok' },
            xaxis: { 
                title: { text: 'Oran (%)' },
                labels: { style: { fontSize: '11px' } } 
            },
            yaxis: { title: { text: 'Tekrar Sayısı (Frekans)' } },
            tooltip: {
                y: { formatter: function (val) { return val + " işlem" } }
            }
        };
        window.chart = new ApexCharts(document.querySelector(selector), options);
        chart.render();
        return chart;
    }
    function createComparisonLineChart(selector, title, curveType = 'smooth') {
        const options = {
            chart: { 
                type: 'line', 
                height: 350, 
                toolbar: { show: true, tools: { download: true, zoom: true, reset: true } } 
            },

            colors: ['#008FFB', '#00E396'], 
            
            stroke: { width: 3, curve: curveType }, 
            
            dataLabels: { enabled: false },
            series: [],
            noData: { text: 'Veri Bekleniyor...' },
            
            xaxis: { 
                type: 'category', 
                labels: { rotate: -45, style: { fontSize: '11px' } },
                tooltip: { enabled: false }
            },
            yaxis: { 
                title: { text: title },
                labels: { formatter: (val) => val.toFixed(2) } 
            },
            grid: { strokeDashArray: 4 },
            
            tooltip: {
                shared: true, 
                intersect: false,
                y: { formatter: function (val) { return val !== null ? val + " puan" : ""; } }
            }
        };
        
        window.chart = new ApexCharts(document.querySelector(selector), options);
        chart.render();
        return chart;
    }

    const chartOffered = createTimeSeriesChart("#chart-offered", "#f76707", "#1e293b", "Verilen Oran", "Ekstrem Yetki");
    const chartOfferedHourly = createTimeSeriesChart("#chart-offered-hourly", "#f76707", "#1e293b", "Verilen Oran (Saatlik)", "Ekstrem Yetki");
    const chartOfferedDist = createDistributionChart("#chart-offered-dist", "#f76707", "#1e293b", "Verilen Oran Dağılımı", "Ekstrem Yetki");

    const chartDemanded = createTimeSeriesChart("#chart-demanded", "#565656", "#1e293b", "İstenen Oran", "Ekstrem Yetki");
    const chartDemandedHourly = createTimeSeriesChart("#chart-demanded-hourly", "#565656", "#1e293b", "İstenen Oran (Saatlik)", "Ekstrem Yetki");
    const chartDemandedDist = createDistributionChart("#chart-demanded-dist", "#565656", "#1e293b", "İstenen Oran Dağılımı", "Ekstrem Yetki");

    const chartComp = createTimeSeriesChart("#chart-competitor", "#2fb344", "#1e293b", "Rakip Oranı", "Ekstrem Yetki");
    const chartCompHourly = createTimeSeriesChart("#chart-competitor-hourly", "#2fb344", "#1e293b", "Rakip Oranı (Saatlik)", "Ekstrem Yetki");
    const chartCompDist = createDistributionChart("#chart-competitor-dist", "#2fb344", "#1e293b", "Rakip Oran Dağılımı", "Ekstrem Yetki");
    
    const chartSpread = createComparisonLineChart(
        "#chart-spread", 
        "Saatlik Spread", 
        "straight" 
    );

    const chartSpreadCumulative = createComparisonLineChart(
        "#chart-spread-cumulative", 
        "Kümülatif Spread", 
        "smooth" 
    );


    function processAndDisplay() {
        
    
        if (!currentRawData || currentRawData.length === 0) {
            updateChartsWithData({ 
                labels: [], dataOff: [], dataDema: [], dataComp: [], dataRez: [],
                distOff: {labels:[], data:[]}, distDema: {labels:[], data:[]}, distComp: {labels:[], data:[]}
            });
            filterStats.textContent = "Veri yok.";
            return;
        }
        

        const srcVal = filterSrc.value;
        const ccyVal = filterCcy.value;
        
        const selectedRevize = Array.from(document.querySelectorAll('.filter-revize-check:checked')).map(cb => cb.value);
        const selectedBuckets = Array.from(document.querySelectorAll('.filter-bucket-check:checked')).map(cb => cb.value);
        const selectedVadeRanges = Array.from(document.querySelectorAll('.filter-vade-check:checked')).map(cb => cb.value);
        const selectedCust = Array.from(document.querySelectorAll('.filter-cust-check:checked')).map(cb => cb.value);

        const filteredRows = currentRawData.filter(row => {
            const isTreasury = (row.DATA_SRC === 'TREASURY');

            if (srcVal !== 'ALL' && row.DATA_SRC !== srcVal) return false;
            if (ccyVal !== row.CCY_CODE) return false
            
            const rowBucketLabel = getAmountBucketLabel(row.RESERVATION_AMT || 0);
            if (!selectedBuckets.includes(rowBucketLabel)) return false

            const rowVade = parseInt(row.VADE_BASLANGIC || 0);
            
            if (selectedVadeRanges.length === 0) return false;
            
            const isVadeInRange = selectedVadeRanges.some(rangeStr => {
                const [min, max] = rangeStr.split('-').map(Number);
                return rowVade >= min && rowVade <= max;
            });
        
            if (!isVadeInRange) return false;

            const custTp = String(row.CUST_TP || "").trim(); 
            if (!selectedCust.includes(custTp)) return false

            if (selectedRevize.includes('ALL')) {
                return true; 
            }

            if (selectedRevize.length > 0) {
                let isMatch = false;

                if (selectedRevize.includes('MAX')) {
                    if (row.IS_MAX_REVIZE === true || row.IS_MAX_REVIZE === 1) {
                        isMatch = true;
                    }
                }

                const rowRevNo = String(row.TALEP_REVIZE_NO);
                if (selectedRevize.includes(rowRevNo)) {
                    isMatch = true;
                }

                if (!isMatch) return false;
            } else {
                return false; 
            }

            return true;
        });

        filterStats.textContent = `${filteredRows.length} işlem listeleniyor.`;

        filteredRows.sort((a, b) => {
            const da = String(a.DATE_TIME_STR || "");
            const db = String(b.DATE_TIME_STR || "");
            return da.localeCompare(db);
        });
        
        const chartData = calculateStats(filteredRows);
        updateChartsWithData(chartData);
    }

    function calculateStats(rows) {
        const buckets = groupRowsByHour(rows);
        const sortedHours = Object.keys(buckets).sort();
        
        const dataEkstrem = getFixedValue(sortedHours, buckets, 'EKSTREM_YETKI')
        
        const dataRez = generateCumulativeCount(sortedHours, buckets);
        const dataOff = generateCumulativeWeightedAvg(sortedHours, buckets, 'OFFERED_RATE', 'RESERVATION_AMT');
        const dataDema = generateCumulativeWeightedAvg(sortedHours, buckets, 'PERCENTILE_DEMANDED_RTS', 'RESERVATION_AMT');
        const dataComp = generateCumulativeWeightedAvg(sortedHours, buckets, 'PERCENTILE_COMPETITOR_RTS', 'RESERVATION_AMT');
        
        const dataRezHourly = generateHourlyCount(sortedHours, buckets);
        const dataOffHourly = generateHourlyWeightedAvg(sortedHours, buckets, 'OFFERED_RATE', 'RESERVATION_AMT');
        const dataDemaHourly = generateHourlyWeightedAvg(sortedHours, buckets, 'PERCENTILE_DEMANDED_RTS', 'RESERVATION_AMT');
        const dataCompHourly = generateHourlyWeightedAvg(sortedHours, buckets, 'PERCENTILE_COMPETITOR_RTS', 'RESERVATION_AMT');

        const distOff = calculateDistribution(rows, 'OFFERED_RATE');
        const distDema = calculateDistribution(rows, 'PERCENTILE_DEMANDED_RTS');
        const distComp = calculateDistribution(rows, 'PERCENTILE_COMPETITOR_RTS');
        
        const dataSpreadCurr = generateSpreadSeries(sortedHours, buckets, 'CURRENTAMOUNT');
        const dataSpreadInc = generateSpreadSeries(sortedHours, buckets, 'INCOMING_AMT');
        
        const dataSpreadCurrCumul = generateCumulativeSpread(sortedHours, buckets, 'OFFERED_RATE', 'CURRENTAMOUNT');
        const dataSpreadIncCumul = generateCumulativeSpread(sortedHours, buckets, 'OFFERED_RATE', 'INCOMING_AMT');
        
        // Calculate Metric Ratios (Ekstrem and Ekstrem + Yetki)
        let validOff = 0, extYetkiOverOff = 0, extOverOff = 0;
        let validDema = 0, extYetkiOverDema = 0, extOverDema = 0;
        let validComp = 0, extYetkiOverComp = 0, extOverComp = 0;

        rows.forEach(row => {
            let offered = parseFloat(row.OFFERED_RATE);
            let demanded = parseFloat(row.PERCENTILE_DEMANDED_RTS);
            let comp = parseFloat(row.PERCENTILE_COMPETITOR_RTS);
            let ekstremYetki = parseFloat(row.EKSTREM_YETKI);
            let ekstrem = parseFloat(row.EKSTREM);

            // 1. Verilen Oran (Offered)
            if (!isNaN(offered) && offered > 0) {
                validOff++;
                if (!isNaN(ekstremYetki) && offered > ekstremYetki) extYetkiOverOff++;
                if (!isNaN(ekstrem) && offered > ekstrem) extOverOff++;
            }
            // 2. İstenen Oran (Demanded)
            if (!isNaN(demanded) && demanded > 0) {
                validDema++;
                if (!isNaN(ekstremYetki) && demanded > ekstremYetki) extYetkiOverDema++;
                if (!isNaN(ekstrem) && demanded > ekstrem) extOverDema++;
                console.log(ekstrem, demanded)
            }
            // 3. Rakip Banka (Competitor)
            if (!isNaN(comp) && comp > 0) {
                validComp++;
                if (!isNaN(ekstremYetki) && comp > ekstremYetki) extYetkiOverComp++;
                if (!isNaN(ekstrem) && comp > ekstrem) extOverComp++;
            }
        });

        const safeRatio = (num, den) => den > 0 ? (num / den) * 100 : 0;

        const metricRatios = {
            extYetkiOff: safeRatio(extYetkiOverOff, validOff),
            extOff: safeRatio(extOverOff, validOff),
            extYetkiDema: safeRatio(extYetkiOverDema, validDema),
            extDema: safeRatio(extOverDema, validDema),
            extYetkiComp: safeRatio(extYetkiOverComp, validComp),
            extComp: safeRatio(extOverComp, validComp)
        };
        
        return { 
            labels: sortedHours,
            dataEkstrem,
            dataOff, dataDema, dataComp, dataRez,
            dataOffHourly, dataDemaHourly, dataCompHourly, dataRezHourly,
            distOff, distDema, distComp,
            dataSpreadCurr, dataSpreadInc,
            dataSpreadCurrCumul, dataSpreadIncCumul,
            metricRatios // Pass the computed ratios
        };
    }

    function updateChartsWithData(data) {
        chartOffered.updateOptions({ 
            xaxis: { categories: data.labels }, 
            series: [
                { name: 'İşlem Sayısı', type: 'column', data: data.dataRez }, 
                { name: 'Verilen Oran', type: 'line', data: data.dataOff },
                { name: 'Ekstrem Yetki', type: 'line', data: data.dataEkstrem }
            ] 
        });
        
        chartOfferedHourly.updateOptions({ 
            xaxis: { categories: data.labels }, 
            series: [
                { name: 'Saatlik Adet', type: 'column', data: data.dataRezHourly }, 
                { name: 'Verilen Oran (Saatlik)', type: 'line', data: data.dataOffHourly },
                { name: 'Ekstrem Yetki', type: 'line', data: data.dataEkstrem }
            ] 
        });
        
        chartDemanded.updateOptions({ 
            xaxis: { categories: data.labels }, 
            series: [
                { name: 'İşlem Sayısı', type: 'column', data: data.dataRez }, 
                { name: 'İstenen Oran', type: 'line', data: data.dataDema },
                { name: 'Ekstrem Yetki', type: 'line', data: data.dataEkstrem }
            ] 
        });
        
        chartDemandedHourly.updateOptions({ 
            xaxis: { categories: data.labels }, 
            series: [
                { name: 'Saatlik Adet', type: 'column', data: data.dataRezHourly }, 
                { name: 'İstenen Oran (Saatlik)', type: 'line', data: data.dataDemaHourly },
                { name: 'Ekstrem Yetki', type: 'line', data: data.dataEkstrem }
            ] 
        });
        
        chartComp.updateOptions({
             xaxis: { categories: data.labels }, 
             series: [
                 { name: 'İşlem Sayısı', type: 'column', data: data.dataRez }, 
                 { name: 'Rakip Oranı', type: 'line', data: data.dataComp },
                 { name: 'Ekstrem Yetki', type: 'line', data: data.dataEkstrem }
            ]
        });
        
        chartCompHourly.updateOptions({
             xaxis: { categories: data.labels }, 
             series: [
                 { name: 'Saatlik Adet', type: 'column', data: data.dataRezHourly }, 
                 { name: 'Rakip Oranı (Saatlik)', type: 'line', data: data.dataCompHourly },
                 { name: 'Ekstrem Yetki', type: 'line', data: data.dataEkstrem }
            ]
        });

        chartOfferedDist.updateOptions({ 
            xaxis: { categories: data.distOff.labels },
            series: [{ name: 'İşlem Sayısı', data: data.distOff.data }] 
        });
        
        chartDemandedDist.updateOptions({
            xaxis: { categories: data.distDema.labels },
            series: [{ name: 'İşlem Sayısı', data: data.distDema.data }] 
        });
        
        chartCompDist.updateOptions({ 
            xaxis: { categories: data.distComp.labels }, 
            series: [{ name: 'İşlem Sayısı', data: data.distComp.data }] 
        });

        chartSpread.updateOptions({
            xaxis: { categories: data.labels },
            series: [
                { name: 'Roll (Saatlik)', data: data.dataSpreadCurr },
                { name: 'Yeni (Saatlik)', data: data.dataSpreadInc }
            ]
        });

        chartSpreadCumulative.updateOptions({
            xaxis: { categories: data.labels },
            series: [
                { name: 'Roll (Kümülatif)', data: data.dataSpreadCurrCumul },
                { name: 'Yeni (Kümülatif)', data: data.dataSpreadIncCumul }
            ]
        });
        
        const updateMetricUI = (id, val) => {
            const el = document.getElementById(id);
            if (el) el.textContent = "%" + val.toFixed(2);
        };

        if (data.metricRatios) {
            updateMetricUI('metric-ekstrem-yetki-off', data.metricRatios.extYetkiOff);
            updateMetricUI('metric-ekstrem-off', data.metricRatios.extOff);
            
            updateMetricUI('metric-ekstrem-yetki-dem', data.metricRatios.extYetkiDema);
            updateMetricUI('metric-ekstrem-dem', data.metricRatios.extDema);
            
            updateMetricUI('metric-ekstrem-yetki-comp', data.metricRatios.extYetkiComp);
            updateMetricUI('metric-ekstrem-comp', data.metricRatios.extComp);
        }
        
    }
    
    

    // EVENT LISTENERS
    function loadDataForDate(dateStr) {
        if (!dateStr) return;
        
        // If we already downloaded this date, use the cache (instant filtering!)
        if (clientCache[dateStr]) {
            currentRawData = clientCache[dateStr];
            processAndDisplay();
            return;
        }

        // Show a loading indicator in the UI while fetching
        if (filterStats) filterStats.textContent = "Veri indiriliyor...";
        
        // Determine the correct endpoint based on the page you are on
        // Use `/api/data/miktarlar/` if you are in amounts.js
        const apiUrl = `/api/data/oranlar/${dateStr}`; 

        fetch(apiUrl)
            .then(response => response.json())
            .then(data => {
                // Save to cache so we don't download it again
                clientCache[dateStr] = data;
                currentRawData = data;
                processAndDisplay();
            })
            .catch(err => {
                console.error("Data fetch error:", err);
                if (filterStats) filterStats.textContent = "Veri yüklenirken hata oluştu.";
            });
    }

    // 3. UPDATE THE LITEPICKER LISTENER
    if (datePicker) {
        datePicker.addEventListener('date:updated', function(e) {
            const dateStr = e.target.value; 
            if (displayDate) displayDate.textContent = dateStr;
            
            // Trigger the fetch instead of reading from masterData
            loadDataForDate(dateStr);
        });
    }

    const filters = [filterSrc, filterCcy];
    
    filters.forEach(el => {
        if (el) {
            el.addEventListener('change', processAndDisplay);
        }
    });

    const allCheckboxFilters = document.querySelectorAll(
        '.filter-bucket-check, .filter-revize-check, .filter-vade-check, .filter-cust-check'
    );
    
    allCheckboxFilters.forEach(cb => {
        cb.addEventListener('change', processAndDisplay);
    });
    
    document.querySelectorAll('.select-all-master').forEach(master => {
    const targetSelector = master.getAttribute('data-group');
    const children = document.querySelectorAll(targetSelector);

    if (children.length === 0) return;

    master.addEventListener('change', function(e) {
        const isChecked = e.target.checked;
        
        children.forEach(child => {
            child.checked = isChecked;
        });

        processAndDisplay();
    });

    children.forEach(child => {
        child.addEventListener('change', function() {
            const allChecked = Array.from(children).every(c => c.checked);
            
            master.checked = allChecked;
        });
    });
});

    // İNİT
    if (initialDate) {
        if(datePicker) datePicker.value = initialDate;
        if(displayDate) displayDate.textContent = initialDate;
        
        // Trigger the initial fetch
        loadDataForDate(initialDate);
    }
}

function initDateNavigation() {
    const btnPrev = document.getElementById('btn-prev-date');
    const btnNext = document.getElementById('btn-next-date');
    
    const changeDateBy = (offset) => {
        const picker = window.litePickerInstance; 
        
        // Ensure we have a picker and our array of valid dates
        if (!picker || !window.availableDates || window.availableDates.length === 0) return;

        const currentDate = picker.getDate(); 
        if (!currentDate) return;

        // Get the current date as a string (YYYY-MM-DD)
        const currentStr = currentDate.format('YYYY-MM-DD');

        // Find where we are in the array of valid dates
        let currentIndex = window.availableDates.indexOf(currentStr);

        if (currentIndex !== -1) {
            // Calculate the new index based on the arrow clicked
            let newIndex = currentIndex + offset;
            
            // Ensure we don't go out of bounds (past the oldest or newest date)
            if (newIndex >= 0 && newIndex < window.availableDates.length) {
                const newDateStr = window.availableDates[newIndex];
                
                // Update the calendar
                picker.setDate(newDateStr);
                
                // Manually update the input and trigger the fetch event
                const inputEl = document.getElementById('litepicker-date');
                if (inputEl) {
                    inputEl.value = newDateStr;
                    inputEl.dispatchEvent(new Event('date:updated'));
                }
            }
        }
    };

    if (btnPrev) {
        btnPrev.onclick = (e) => {
            e.preventDefault(); 
            changeDateBy(-1); // Go back one valid date
        };
    }

    if (btnNext) {
        btnNext.onclick = (e) => {
            e.preventDefault(); 
            changeDateBy(1); // Go forward one valid date
        };
    }
}

document.addEventListener("DOMContentLoaded", function () {
    initDateNavigation();
});

window.addEventListener('theme:changed', () => {
    const isDark = document.body.classList.contains('theme-dark');
    const charts = [chartOffered, chartDemanded, chartComp, chartOfferedDist, chartDemandedDist, chartCompDist, chartSpread, chartSpreadCumulative];
    
    charts.forEach(c => {
        if (c) {
            c.updateOptions({ theme: { mode: isDark ? 'dark' : 'light' } });
        }
    });
});