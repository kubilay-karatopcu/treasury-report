// ---------------------------------HELPER FNS------------------------------------------

function getSafeDate(row) {
    const dateStr = String(row.DATE_TIME_STR || "").replace(/-/g, "/");
    return new Date(dateStr);
}

function getAmountBucketLabel(amt) {
    if (amt <= 5000000) return '0-5';
    if (amt <= 10000000) return '5-10';
    if (amt <= 25000000) return '10-25';
    if (amt <= 100000000) return '25-100';
    if (amt <= 200000000) return '100-200';
    if (amt <= 500000000) return '200-500';
    if (amt <= 1000000000) return '500-1000';
    return '1000+';
}

function groupRowsByDay(rows) {
    let buckets = {};
    rows.forEach(row => {
        let dateStr = String(row.DATE_TIME_STR || "").split(' ')[0];
        if (!dateStr) return;
        
        if (!buckets[dateStr]) buckets[dateStr] = [];
        buckets[dateStr].push(row);
    });
    return buckets;
}

function groupRowsByHourFull(rows) {
    let buckets = {};
    rows.forEach(row => {
        let dt = getSafeDate(row);
        if (isNaN(dt.getTime())) return;

        let year = dt.getFullYear();
        let month = String(dt.getMonth() + 1).padStart(2, '0');
        let day = String(dt.getDate()).padStart(2, '0');
        let hour = String(dt.getHours()).padStart(2, '0');

        let key = `${year}-${month}-${day} ${hour}:00`;

        if (!buckets[key]) buckets[key] = [];
        buckets[key].push(row);
    });
    return buckets;
}

function generateHourlyCount(sortedHours, buckets) {
    let seriesData = [];
    
    sortedHours.forEach(h => {
        let rows = buckets[h] || [];
        let count = rows.length;
        
        seriesData.push(count);
        
        if (count > 0) { 
            console.log(`Örnek Dolu Saat -> [${h}]: ${count} işlem var.`);
        }
    });
    
    console.log("3. Çıkan İşlem Sayıları (seriesData):");
    
    return seriesData;
}

function generateWeightedAvg(sortedDates, buckets, valKey, weightKey) {
    let seriesData = [];
    sortedDates.forEach(date => {
        let rows = buckets[date] || [];
        let totalNum = 0;
        let totalDen = 0;

        rows.forEach(row => {
            let val = parseFloat(row[valKey]) || 0;
            let w = parseFloat(row[weightKey]) || 0;
            if (val > 0 && w > 0) {
                totalNum += val * w;
                totalDen += w;
            }
        });

        let avg = 0;
        if (totalDen > 0 && totalNum > 0) avg = parseFloat((totalNum / totalDen).toFixed(2));
        if (avg > 0) { seriesData.push(avg);
        } else {
            seriesData.push(null);
        }
        
    });
    return seriesData;
}

function generateIntradayCumulativeWeightedAvg(sortedKeys, buckets, valKey, weightKey) {
    let seriesData = [];
    
    let currentDay = null;
    let dailyTotalNum = 0;
    let dailyTotalDen = 0;

    sortedKeys.forEach(key => {
        let datePart = key.split(' ')[0];

        if (currentDay !== datePart) {
            currentDay = datePart;
            dailyTotalNum = 0;
            dailyTotalDen = 0;
        }

        let rows = buckets[key] || [];

        rows.forEach(row => {
            let val = parseFloat(row[valKey]) || 0;
            let w = parseFloat(row[weightKey]) || 0;
            
            if (val !== 0 && w !== 0) {
                dailyTotalNum += val * w;
                dailyTotalDen += w;
            }
        });

        if (dailyTotalDen > 0) {
            seriesData.push(parseFloat((dailyTotalNum / dailyTotalDen).toFixed(2)));
        } else {
            seriesData.push(null);
        }
    });

    return seriesData;
}

function generateSimpleCumulativePercentile(sortedKeys, hourBuckets, valKey, percentile) {
    let seriesData = [];
    let currentDay = null;
    let dailyRates = []; 

    sortedKeys.forEach(key => {
        let datePart = key.split(' ')[0];

        if (currentDay !== datePart) {
            currentDay = datePart;
            dailyRates = [];
        }

        let hourRows = hourBuckets[key] || [];
    
        hourRows.forEach(r => {
            let val = parseFloat(r[valKey]);
            if (!isNaN(val) && val > 0) {
                dailyRates.push(val);
            }
        });

        if (dailyRates.length === 0) {
            seriesData.push(null);
            return;
        }

        dailyRates.sort((a, b) => a - b);


        let index = Math.floor((dailyRates.length - 1) * percentile);
        
        seriesData.push(dailyRates[index]);
    });

    return seriesData;
}

function getDailyFixedValue(sortedHourlyKeys, hourlyBuckets, key) {
    
    let dailyCounts = {}; 

    sortedHourlyKeys.forEach(hKey => {
        let datePart = hKey.split(' ')[0];
        let rows = hourlyBuckets[hKey] || [];

        if (!dailyCounts[datePart]) dailyCounts[datePart] = {};

        rows.forEach(r => {
            let val = parseFloat(r[key]);
            if (!isNaN(val) && val > 0) {
                dailyCounts[datePart][val] = (dailyCounts[datePart][val] || 0) + 1;
            }
        });
    });

    let dailyModes = {};
    
    console.log(dailyCounts)

    Object.keys(dailyCounts).forEach(date => {
        let counts = dailyCounts[date];
        let winnerVal = null;
        let maxCount = 0;

        for (let val in counts) {
            if (counts[val] > maxCount) {
                maxCount = counts[val];
                winnerVal = parseFloat(val);
            }
        }
        dailyModes[date] = winnerVal;
    });

    let seriesData = [];
    let lastKnownMode = null;

    sortedHourlyKeys.forEach(hKey => {
        let datePart = hKey.split(' ')[0];
        let mode = dailyModes[datePart];

        if (mode !== undefined && mode !== null) {
            seriesData.push(mode);
            lastKnownMode = mode;
        } 
        
        else if (lastKnownMode !== null) {
            seriesData.push(lastKnownMode);
        } 
        
        else {
            seriesData.push(null);
        }
    });

    return seriesData;
}

function generateDailySum(sortedDates, buckets, key) {
    let seriesData = [];
    sortedDates.forEach(date => {
        let rows = buckets[date] || [];
        let sum = 0;
        rows.forEach(row => {
            sum += parseFloat(row[key]) || 0;
        });
        seriesData.push(sum);
    });
    return seriesData;
}

// ------------------------DASHBOARD INITIALIZATION---------------------------------

function initHistoricDashboard(rawHistoryData) {
    const pickerInput = document.getElementById('litepicker-range');
    const filterStats = document.getElementById('filter-stats');
    
    const filterSrc = document.getElementById('filter-src');
    const filterCcy = document.getElementById('filter-ccy');

    function createTrendLineChart(selector, title) {
        const options = {
            chart: { type: 'line', height: 400, toolbar: { show: true } },
            stroke: { width: 3, curve: 'straight' },
            colors: ['#206bc4', '#d63939', '#ff922b', '#fab005', '#2fb344'],
            series: [],
            noData: { text: 'Veri Bekleniyor...' },
            xaxis: { type: 'category', tickAmount: 10, labels: { rotate: -45, style: { fontSize: '11px' } } },
            yaxis: { 
                title: { text: 'Oran (%)' },
                labels: { formatter: (val) => val.toFixed(2) } 
            },
            
            title: { text: title, align: 'left' },
            grid: { strokeDashArray: 4 },
            legend: { position: 'top' },
            tooltip: {
                shared: true,
                intersect: false,
                inverseOrder: true,
                y: {
                    formatter: function (val) {
                        if (val === null || val === undefined || typeof val !== 'number') {
                            return val;
                        }
                        
                        return val.toFixed(2) + " %";
                    }
                }
            },
        };
        const chart = new ApexCharts(document.querySelector(selector), options);
        chart.render();
        return chart;
    }

    function createVolumeComboChart(selector, title) {
        const options = {
            chart: { 
                type: 'line',
                height: 400, 
                stacked: true, 
                toolbar: { show: true } 
            },
            stroke: { 
                width: [0, 0, 4],
                curve: 'smooth' 
            }, 
            plotOptions: { 
                bar: { columnWidth: '50%', borderRadius: 0 } 
            }, 
            colors: ['#001f5f', '#206bc4', '#f76707'], // Koyu Mavi (Current), Açık Mavi (Incoming), Kırmızı (Rate)
            series: [],
            noData: { text: 'Veri Bekleniyor...' },
            dataLabels: { enabled: false },
            xaxis: { 
                type: 'category', 
                labels: { rotate: -45, style: { fontSize: '11px' } },
                tooltip: { enabled: false }
            },
            yaxis: [
                { 
                    seriesName: 'Current Amount', 
                    title: { text: 'Hacim', style: { color: '#206bc4' } },
                    labels: { 
                        formatter: (val) => {
                            if (val >= 1000000) return (val/1000000).toFixed(1) + 'M';
                            if (val >= 1000) return (val/1000).toFixed(1) + 'K';
                            return val;
                        } 
                    } 
                },
                { 
                    seriesName: 'Current Amount',
                    show: false
                },
                { 
                    opposite: true, 
                    seriesName: 'Market Max', 
                    title: { text: 'Market Max (%)', style: { color: '#d63939' } },
                    labels: { formatter: (val) => val.toFixed(2) }
                }
            ],
            title: { text: title, align: 'left' },
            grid: { strokeDashArray: 4 },
            legend: { position: 'top' },
            tooltip: {
                shared: true,
                intersect: false,
                y: {
                    formatter: function (y, { seriesIndex, w }) {
                        if(typeof y !== "undefined") {
                             if (seriesIndex === 2) return y.toFixed(2) + " %";
                             return y.toLocaleString('tr-TR', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
                        }
                        return y;
                    }
                }
            }
        };
        const chart = new ApexCharts(document.querySelector(selector), options);
        chart.render();
        return chart;
    }

    const chartRates = createTrendLineChart("#chart-trend-rates", "Genel Oran Trendleri");
    const chartVolume = createVolumeComboChart("#chart-trend-volume", "Hacim ve Market Max");
    const chartAuth = createTrendLineChart("#chart-trend-auth", "Ekstrem Yetki vs Talep");
    
    chartAuth.updateOptions({

        colors: ['#d63939', '#2fb344', '#7D3C98', '#3498DB', '#E67E22'],
        
        stroke: {
            width: [3, 3, 3, 3, 3],
            dashArray: [0, 0, 3, 3, 3],
            curve: 'smooth'
        },
        
        tooltip: {
            shared: true,
            intersect: false,
            inverseOrder: true,
            y: {
                formatter: function (val) {
                    if (val === null || val === undefined || typeof val !== 'number') {
                        return val;
                    }

                    return val.toFixed(2) + " %";
                }
            }
        },

        yaxis: {
            title: { text: 'Oran (%)' },
            labels: { formatter: (val) => val.toFixed(2) }
        }
    });


    function processAndDisplay() {
        const picker = window.litePickerInstance;
        if (!picker) return;
        
        const start = picker.getStartDate();
        const end = picker.getEndDate();
        
        if (!start || !end) return;
        
        const startStr = start.format('YYYY-MM-DD');
        const endStr = end.format('YYYY-MM-DD');

        const srcVal = filterSrc.value;
        const ccyVal = filterCcy.value;
        
        const selectedRevize = Array.from(document.querySelectorAll('.filter-revize-check:checked')).map(cb => cb.value);
        const selectedBuckets = Array.from(document.querySelectorAll('.filter-bucket-check:checked')).map(cb => cb.value);
        const selectedVadeRanges = Array.from(document.querySelectorAll('.filter-vade-check:checked')).map(cb => cb.value);
        const selectedCust = Array.from(document.querySelectorAll('.filter-cust-check:checked')).map(cb => cb.value);

        const filteredRows = rawHistoryData.filter(row => {
            const rowDate = String(row.DATE_TIME_STR || "").split(' ')[0];
            if (rowDate < startStr || rowDate > endStr) return false;

            if (srcVal !== 'ALL' && row.DATA_SRC !== srcVal) return false;
            if (ccyVal !== row.CCY_CODE) return false;

            const rowBucketLabel = getAmountBucketLabel(row.RESERVATION_AMT || 0);
            if (!selectedBuckets.includes(rowBucketLabel)) return false;

            const rowVade = parseInt(row.VADE_BASLANGIC || 0);
            
            if (selectedVadeRanges.length === 0) return false;
            
            const isVadeInRange = selectedVadeRanges.some(rangeStr => {
                const [min, max] = rangeStr.split('-').map(Number);
                return rowVade >= min && rowVade <= max;
            });
        
            if (!isVadeInRange) return false;

            const custTp = String(row.CUST_TP || "").trim(); 
            if (!selectedCust.includes(custTp)) return false;

            if (selectedRevize.includes('ALL')) return true;
            if (selectedRevize.length > 0) {
                let isMatch = false;
                if (selectedRevize.includes('MAX')) {
                    if (row.IS_MAX_REVIZE === true || row.IS_MAX_REVIZE === 1) isMatch = true;
                }
                const rowRevNo = String(row.TALEP_REVIZE_NO);
                if (selectedRevize.includes(rowRevNo)) isMatch = true;
                if (!isMatch) return false;
            } else {
                return false; 
            }
            return true;
        });

        filterStats.textContent = `${filteredRows.length} işlem listeleniyor.
        (${startStr} - ${endStr})`;

        const chartData = calculateStats(filteredRows);
        updateChartsWithData(chartData);
    }

    function calculateStats(rows) {
        const buckets = groupRowsByDay(rows);
        const sortedDates = Object.keys(buckets).sort();

        // Grafik 1 Verileri
        const marketMax = getDailyFixedValue(sortedDates, buckets, 'MARKET_MAX_RT');
        const ekstrem = getDailyFixedValue(sortedDates, buckets, 'EKSTREM_YETKI');
        
        const offeredW = generateWeightedAvg(sortedDates, buckets, 'OFFERED_RATE', 'RESERVATION_AMT');
        const compW = generateWeightedAvg(sortedDates, buckets, 'PERCENTILE_COMPETITOR_RTS', 'RESERVATION_AMT');
        const demandW = generateWeightedAvg(sortedDates, buckets, 'PERCENTILE_DEMANDED_RTS', 'RESERVATION_AMT');

        // Grafik 2 Verileri
        const currAmt = generateDailySum(sortedDates, buckets, 'CURRENTAMOUNT');
        const incAmt = generateDailySum(sortedDates, buckets, 'INCOMING_AMT');
        
        const bucketsHour = groupRowsByHourFull(rows);
        const sortedHours = Object.keys(bucketsHour).sort();

        const hourlyEkstrem = getDailyFixedValue(sortedHours, bucketsHour, 'EKSTREM_YETKI');
        const hourlyDemandW = generateIntradayCumulativeWeightedAvg(sortedHours, bucketsHour, 'PERCENTILE_DEMANDED_RTS', 'RESERVATION_AMT');
        
        const hourlyP90 = generateSimpleCumulativePercentile(sortedHours, bucketsHour, 'PERCENTILE_DEMANDED_RTS', 0.90);
        const hourlyP75 = generateSimpleCumulativePercentile(sortedHours, bucketsHour, 'PERCENTILE_DEMANDED_RTS', 0.75);
        const hourlyP50 = generateSimpleCumulativePercentile(sortedHours, bucketsHour, 'PERCENTILE_DEMANDED_RTS', 0.50);
        
        const rezHourly = generateHourlyCount(sortedHours, bucketsHour);

        return {
            labels: sortedDates,
            labelsHourly: sortedHours,
            marketMax, ekstrem, offeredW, compW, demandW,
            currAmt, incAmt, hourlyEkstrem, hourlyDemandW,
            hourlyP90, hourlyP75, hourlyP50, rezHourly
        };
    }

    function updateChartsWithData(data) {
        
        

        /* const createStrictlySyncedCategorySeries = (labels, seriesList, allowZero = false) => {
            if (!labels || seriesList.length === 0) return { cleanLabels: [], syncedResults: seriesList.map(() => []) };
            let cleanLabels = [];
            let syncedResults = seriesList.map(() => []);

            labels.forEach((l, i) => {
                let currentValues = seriesList.map(arr => arr[i]);
                const isAllValid = currentValues.every(val => val !== null && val !== undefined && (allowZero || parseFloat(val) !== 0));

                if (isAllValid) {
                    let labelFormatted = l; 
                    if (l.includes(':')) {
                         const dt = new Date(l.replace(/-/g, "/"));
                         labelFormatted = dt.toLocaleDateString('tr-TR', { day: '2-digit', month: 'short' }) + " " + 
                                         String(dt.getHours()).padStart(2, '0') + ":00";
                    }
                    cleanLabels.push(labelFormatted);
                    currentValues.forEach((val, index) => syncedResults[index].push(parseFloat(val)));
                }
            });
             return { cleanLabels, syncedResults };
        }; */

        const formatDailyLabel = (str) => {
            if(!str) return "";
            const dt = new Date(str.replace(/-/g, "/"));
            return dt.toLocaleDateString('tr-TR', { day: '2-digit', month: 'short' });
        };

        // --- GRAFİK 1: TRENDLER ---
        const dailyLabels = data.labels.map(l => formatDailyLabel(l));
        
        chartRates.updateOptions({
            xaxis: { 
                type: 'category', 
                categories: dailyLabels,
                labels: { rotate: -45, style: { fontSize: '11px' } }
            },
            series: [
                { name: 'Market Max', data: data.marketMax },
                { name: 'Ekstrem Yetki', data: data.ekstrem },
                { name: 'Offered (W.Avg)', data: data.offeredW },
                { name: 'Competitor (W.Avg)', data: data.compW },
                { name: 'Demanded (W.Avg)', data: data.demandW }
            ],
        });

        // --- GRAFİK 2: HACİM ---
        chartVolume.updateOptions({
            xaxis: { 
                type: 'category', 
                categories: dailyLabels,
                labels: { rotate: -45, style: { fontSize: '11px' } }
            },
            series: [
                { name: 'Current Amount', type: 'column', data: data.currAmt },
                { name: 'Incoming Amount', type: 'column', data: data.incAmt },
                { name: 'Market Max', type: 'line', data: data.marketMax }
            ]
        });

        // --- GRAFİK 3: YETKİ VS ISTENEN ---
        let validLabels = [];
        let fEkstrem = [], fDemand = [], fP90 = [], fP75 = [], fP50 = [];

        data.labelsHourly.forEach((label, i) => {
            if (data.rezHourly[i] >= 10) {
                validLabels.push(label);
                fEkstrem.push(data.hourlyEkstrem[i]);
                fDemand.push(data.hourlyDemandW[i]);
                fP90.push(data.hourlyP90[i]);
                fP75.push(data.hourlyP75[i]);
                fP50.push(data.hourlyP50[i]);
            }
        });

        chartAuth.updateOptions({
            xaxis: { 
                type: 'category', 
                categories: validLabels,
                labels: { rotate: -45, style: { fontSize: '10px' } }
            },
            series: [
                { name: 'Ekstrem Yetki', data: fEkstrem },
                { name: 'Demanded (W.Avg)', data: fDemand },
                { name: 'P90 (Rate)', data: fP90 },
                { name: 'P75 (Rate)', data: fP75 },
                { name: 'P50 (Median)', data: fP50 }
            ]
        });
    }


    // --- EVENT LISTENERS & SETUP ---
    
    const picker = new Litepicker({
        element: pickerInput,
        singleMode: false,
        numberOfMonths: 2,
        numberOfColumns: 2,
        format: 'YYYY-MM-DD',
        setup: (picker) => {
            picker.on('selected', (date1, date2) => {
                processAndDisplay();
            });
        }
    });

    window.litePickerInstance = picker;

    const endDt = new Date();
    const startDt = new Date();
    startDt.setDate(endDt.getDate() - 7);
    picker.setDateRange(startDt, endDt);

    // filter listeners
    const filters = [filterSrc, filterCcy];
    filters.forEach(el => { if(el) el.addEventListener('change', processAndDisplay); });

    const checkboxSelectors = '.filter-bucket-check, .filter-revize-check, .filter-vade-check, .filter-cust-check';
    document.querySelectorAll(checkboxSelectors).forEach(cb => {
        cb.addEventListener('change', processAndDisplay);
    });

    document.querySelectorAll('.select-all-master').forEach(master => {
        const targetSelector = master.getAttribute('data-group');
        const children = document.querySelectorAll(targetSelector);
        if (children.length === 0) return;

        master.addEventListener('change', function(e) {
            const isChecked = e.target.checked;
            children.forEach(child => child.checked = isChecked);
            processAndDisplay();
        });

        children.forEach(child => {
            child.addEventListener('change', function() {
                const allChecked = Array.from(children).every(c => c.checked);
                master.checked = allChecked;
            });
        });
    });

    processAndDisplay();
}

document.addEventListener("DOMContentLoaded", function () {
    if (window.rawHistoryData) {
        initHistoricDashboard(window.rawHistoryData);
    }
});