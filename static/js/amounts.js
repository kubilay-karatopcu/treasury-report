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

function getDistributionBucket(amt) {
    const val = parseFloat(amt || 0);
    if (val <= 5000000) return '0-5M';
    if (amt <= 10000000) return '5-10M';
    if (amt <= 25000000) return '10-25M';
    if (amt <= 100000000) return '25-100M'
    if (amt <= 200000000) return '100-200M'
    if (amt <= 500000000) return '200-500M'
    if (amt <= 1000000000) return '500-1000M'
    return '1000M+';
}

window.formatCurrency = function(value) {
    if (value >= 1000000000) return (value / 1000000000).toFixed(1) + "B";
    if (value >= 1000000) return (value / 1000000).toFixed(1) + "M";
    if (value >= 1000) return (value / 1000).toFixed(0) + "K";
    return value.toFixed(0);
};

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

function calculateDistributionStats(rows, colName) {
    let volMap = { '0-5M': 0, '5-10M': 0, '10-25M': 0, '25-100M': 0, '100-200M': 0, '200-500M': 0, '500-1000M': 0, '1000M+': 0 };
    let countMap = { '0-5M': 0, '5-10M': 0, '10-25M': 0, '25-100M': 0, '100-200M': 0, '200-500M': 0, '500-1000M': 0, '1000M+': 0 };

    rows.forEach(r => {
        let val = parseFloat(r[colName] || 0);
        if (val === 0) return; 

        let bucket = getDistributionBucket(val);
        volMap[bucket] += val;
        countMap[bucket] += 1;
    });

    const labels = ['0-5M', '5-10M', '10-25M', '25-100M', '100-200M', '200-500M', '500-1000M', '1000M+'];
    return { 
        labels, 
        volSeries: labels.map(l => volMap[l]), 
        countSeries: labels.map(l => countMap[l]) 
    };
}

function calculateHistogramStats(rows, colName) {
    let freqMap = {};
    const binSize = 500000;

    rows.forEach(r => {
        let val = parseFloat(r[colName] || 0);
        if (val === 0) return;

        let binIndex = Math.floor(val / binSize);
        let binLabel = formatCurrency(binIndex * binSize) + " - " + formatCurrency((binIndex + 1) * binSize);
        freqMap[binLabel] = (freqMap[binLabel] || 0) + 1;
    });

    // Sorting Logic
    let sortedEntries = Object.entries(freqMap).sort((a, b) => {
        let valA = parseCurrencyStr(a[0].split(" - ")[0]);
        let valB = parseCurrencyStr(b[0].split(" - ")[0]);
        return valA - valB;
    });

    return {
        categories: sortedEntries.map(e => e[0]),
        data: sortedEntries.map(e => e[1])
    };
}

function parseCurrencyStr(str) {
    if(str.includes("M")) return parseFloat(str) * 1000000;
    if(str.includes("K")) return parseFloat(str) * 1000;
    return parseFloat(str);
}


// ------------------------DASHBOARD INITIALIZATION---------------------------------

function initAmountsDashboard(initialDate) {
    const datePicker = document.getElementById('litepicker-date');
    const displayDate = document.getElementById('display-date');
    const filterStats = document.getElementById('filter-stats');
    
    const filterSrc = document.getElementById('filter-src');
    const filterCcy = document.getElementById('filter-ccy');

    let currentRawData = [];
    
    const clientCache = {};
    
    let chartTimeCum, chartTimeHour;
    let chartCurrPieVol, chartCurrPieCount, chartCurrHist;
    let chartIncPieVol, chartIncPieCount, chartIncHist;
    let chartPortPieVol, chartPortPieCount, chartPortHist;

    function createTimeSeriesChart(selector) {
        const options = {
            series: [],
            chart: {
                type: 'line', 
                height: 400,
                stacked: true, 
                toolbar: {
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
            },
                animations: { enabled: true }
            },
            colors: ['#001f5f', '#206bc4', '#f76707'],
            stroke: {
                width: [0, 0, 4], 
                curve: 'smooth'
            },
            plotOptions: {
                bar: { columnWidth: '70%', borderRadius: 0 }
            },
            dataLabels: { enabled: false },
            xaxis: {
                type: 'category',
                tooltip: { enabled: false }
            },
            tooltip: {
                shared: true,
                intersect: false,
                y: {
                    formatter: function (value, { seriesIndex, w }) {
                        return window.formatCurrency(value);
                    }
                }
            },
            yaxis: [
                {
                    seriesName: 'Roll Hacim',
                    title: { text: 'Hacim', style: { color: '#001f5f' } },
                    labels: { formatter: (val) => formatCurrency(val) }
                },
                {
                    seriesName: 'Yeni Hacim',
                    show: false
                },
                {
                    opposite: true,
                    seriesName: 'İşlem Adedi',
                    title: { text: 'Adet', style: { color: '#f76707' } },
                    labels: { formatter: (val) => val.toFixed(0) }
                }
            ],
            legend: { position: 'top' },
            noData: { text: 'Veri Bekleniyor...' }
        };
        const chart = new ApexCharts(document.querySelector(selector), options);
        chart.render();
        return chart;
    }

    function createPieChart(selector) {
        const options = {
            series: [],
            labels: [],
            chart: {
                type: 'pie',
                height: 500,
                animations: { enabled: true }
            },
            colors: [
                '#4263eb', // Blue
                '#2fb344', // Green
                '#f76707', // Orange
                '#d63939', // Red
                '#ae3ec9', // Purple
                '#17a2b8', // Cyan
                '#74b816', // Lime
                '#d6336c'  // Pink
            ],
            legend: { position: 'bottom' },
            dataLabels: {
                enabled: true,
                formatter: function (val, opts) {
                    return opts.w.globals.series[opts.seriesIndex] > 0 ? val.toFixed(1) + "%" : "";
                }
            },
            tooltip: {
                y: {
                    formatter: function(value) {
                        return value > 1000 ? formatCurrency(value) : value.toFixed(0);
                    }
                }
            },
            noData: { text: 'Veri Yok' }
        };
        const chart = new ApexCharts(document.querySelector(selector), options);
        chart.render();
        return chart;
    }

    function createHistogramChart(selector) {
        const options = {
            series: [],
            chart: {
                type: 'bar',
                height: 350,
                toolbar: {
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
            },
                animations: { enabled: true }
            },
            plotOptions: {
                bar: { borderRadius: 2, columnWidth: '90%' }
            },
            dataLabels: { enabled: false },
            colors: ['#6574cd'],
            xaxis: {
                type: 'category',
                labels: { rotate: -45, style: { fontSize: '10px' } },
                title: { text: 'Tutar Aralığı (500k)' }
            },
            yaxis: { title: { text: 'Frekans' } },
            tooltip: { y: { formatter: (val) => val + " kez" } },
            noData: { text: 'Veri Yok' }
        };
        const chart = new ApexCharts(document.querySelector(selector), options);
        chart.render();
        return chart;
    }

    chartTimeCum = createTimeSeriesChart("#chart-time-cumulative");
    chartTimeHour = createTimeSeriesChart("#chart-time-hourly");

    chartCurrPieVol = createPieChart("#chart-curr-pie-vol");
    chartCurrPieCount = createPieChart("#chart-curr-pie-count");
    chartCurrHist = createHistogramChart("#chart-curr-hist");

    chartIncPieVol = createPieChart("#chart-inc-pie-vol");
    chartIncPieCount = createPieChart("#chart-inc-pie-count");
    chartIncHist = createHistogramChart("#chart-inc-hist");

    chartPortPieVol = createPieChart("#chart-port-pie-vol");
    chartPortPieCount = createPieChart("#chart-port-pie-count");
    chartPortHist = createHistogramChart("#chart-port-hist");


    function processAndDisplay() {
        if (!currentRawData || currentRawData.length === 0) {
            if(filterStats) filterStats.textContent = "Veri yok.";
            return;
        }

        const srcVal = filterSrc ? filterSrc.value : 'ALL';
        const ccyVal = filterCcy ? filterCcy.value : 'MAX';
        
        const selectedRevize = Array.from(document.querySelectorAll('.filter-revize-check:checked')).map(cb => cb.value);
        const selectedBuckets = Array.from(document.querySelectorAll('.filter-bucket-check:checked')).map(cb => cb.value);
        const selectedVadeRanges = Array.from(document.querySelectorAll('.filter-vade-check:checked')).map(cb => cb.value);
        const selectedCust = Array.from(document.querySelectorAll('.filter-cust-check:checked')).map(cb => cb.value);

        // 2. Filtreleme
        const filteredRows = currentRawData.filter(row => {
            if (srcVal !== 'ALL' && row.DATA_SRC !== srcVal) return false;
            if (ccyVal !== row.CCY_CODE) return false;
            
            const rowBucket = getAmountBucketLabel(row.RESERVATION_AMT || 0);
            if (selectedBuckets.length > 0 && !selectedBuckets.includes(rowBucket)) return false;

            const rowVade = parseInt(row.VADE_BASLANGIC || 0);
            
            if (selectedVadeRanges.length === 0) return false;
            
            const isVadeInRange = selectedVadeRanges.some(rangeStr => {
                const [min, max] = rangeStr.split('-').map(Number);
                return rowVade >= min && rowVade <= max;
            });
        
            if (!isVadeInRange) return false;

            const custTp = String(row.CUST_TP || "").trim(); 
            if (!selectedCust.includes(custTp)) return false
            
            
            if (selectedRevize.includes('ALL')) return true;
            if (selectedRevize.length === 0) return false;

            const isMaxSelected = selectedRevize.includes('MAX');
            const rowIsMax = (row.IS_MAX_REVIZE == true || row.IS_MAX_REVIZE == 1);
            const matchesMax = isMaxSelected && rowIsMax;

            const rowRevNo = String(row.TALEP_REVIZE_NO);
            const matchesRevNo = selectedRevize.includes(rowRevNo);

            return matchesMax || matchesRevNo;

        });

        if(filterStats) filterStats.textContent = `${filteredRows.length} işlem listeleniyor.`;

        filteredRows.sort((a, b) => {
            const da = String(a.DATE_TIME_STR || "");
            const db = String(b.DATE_TIME_STR || "");
            return da.localeCompare(db);
        });

        const stats = calculateAllStats(filteredRows);

        updateTimeSeriesChart(chartTimeCum, stats.timeLabels, stats.cumRoll, stats.cumNew, stats.cumCount);
        updateTimeSeriesChart(chartTimeHour, stats.timeLabels, stats.hourlyRoll, stats.hourlyNew, stats.hourlyCount);

        updatePieChart(chartCurrPieVol, stats.distCurrent.labels, stats.distCurrent.volSeries);
        updatePieChart(chartCurrPieCount, stats.distCurrent.labels, stats.distCurrent.countSeries);
        updateHistogram(chartCurrHist, stats.histCurrent.categories, stats.histCurrent.data);

        updatePieChart(chartIncPieVol, stats.distIncoming.labels, stats.distIncoming.volSeries);
        updatePieChart(chartIncPieCount, stats.distIncoming.labels, stats.distIncoming.countSeries);
        updateHistogram(chartIncHist, stats.histIncoming.categories, stats.histIncoming.data);

        updatePieChart(chartPortPieVol, stats.distPortfolio.labels, stats.distPortfolio.volSeries);
        updatePieChart(chartPortPieCount, stats.distPortfolio.labels, stats.distPortfolio.countSeries);
        updateHistogram(chartPortHist, stats.histPortfolio.categories, stats.histPortfolio.data);
    }

    function calculateAllStats(rows) {
        const buckets = groupRowsByHour(rows);
        const sortedHours = Object.keys(buckets).sort();

        let cumRoll = [], cumNew = [], cumCount = [];
        let hourlyRoll = [], hourlyNew = [], hourlyCount = [];
        
        let runRoll = 0, runNew = 0, runCount = 0;

        sortedHours.forEach(h => {
            let hourRows = buckets[h];
            let hRoll = 0, hNew = 0, hCount = 0;

            hourRows.forEach(r => {
                hRoll += parseFloat(r.CURRENTAMOUNT || 0);
                hNew += parseFloat(r.INCOMING_AMT || 0);
                hCount++;
            });

            hourlyRoll.push(hRoll);
            hourlyNew.push(hNew);
            hourlyCount.push(hCount);

            runRoll += hRoll;
            runNew += hNew;
            runCount += hCount;
            cumRoll.push(runRoll);
            cumNew.push(runNew);
            cumCount.push(runCount);
        });

        const distCurrent = calculateDistributionStats(rows, 'CURRENTAMOUNT');
        const distIncoming = calculateDistributionStats(rows, 'INCOMING_AMT');
        const distPortfolio = calculateDistributionStats(rows, 'PORTFOLIO_AMT');

        const histCurrent = calculateHistogramStats(rows, 'CURRENTAMOUNT');
        const histIncoming = calculateHistogramStats(rows, 'INCOMING_AMT');
        const histPortfolio = calculateHistogramStats(rows, 'PORTFOLIO_AMT');

        return {
            timeLabels: sortedHours,
            cumRoll, cumNew, cumCount,
            hourlyRoll, hourlyNew, hourlyCount,
            distCurrent, distIncoming, distPortfolio,
            histCurrent, histIncoming, histPortfolio
        };
    }

    // --- UPDATE HELPERS ---
    
    function updateTimeSeriesChart(chart, labels, rollData, newData, countData) {
        let maxVol = 0;
        if (rollData.length > 0) {
            const totals = rollData.map((v, i) => v + (newData[i] || 0));
            maxVol = Math.max(...totals);
        }
        const yMax = maxVol > 0 ? maxVol * 1.1 : 100;

        chart.updateOptions({
            xaxis: { categories: labels },
            yaxis: [
                {
                    seriesName: 'Roll Hacim',
                    max: yMax,
                    title: { text: 'Hacim', style: { color: '#001f5f' } },
                    labels: { formatter: (val) => formatCurrency(val) }
                },
                {
                    seriesName: 'Yeni Hacim',
                    max: yMax,
                    show: false
                },
                {
                    opposite: true,
                    seriesName: 'İşlem Adedi',
                    title: { text: 'Adet', style: { color: '#f76707' } },
                    labels: { formatter: (val) => val.toFixed(0) }
                }
            ]
        });

        chart.updateSeries([
            { name: 'Roll Hacim', type: 'column', group: 'vol', data: rollData },
            { name: 'Yeni Hacim', type: 'column', group: 'vol', data: newData },
            { name: 'İşlem Adedi', type: 'line', data: countData }
        ]);
    }

    function updatePieChart(chart, labels, series) {
        chart.updateOptions({ labels: labels });
        chart.updateSeries(series);
    }

    function updateHistogram(chart, cats, data) {
        chart.updateOptions({ xaxis: { categories: cats } });
        chart.updateSeries([{ name: 'Frekans', data: data }]);
    }

    // --- EVENT LISTENERLAR ---
    
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
        const apiUrl = `/api/data/miktarlar/${dateStr}`; 

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

    [filterSrc, filterCcy].forEach(el => {
        if (el) el.addEventListener('change', processAndDisplay);
    });

    const allCheckboxes = document.querySelectorAll(
        '.filter-bucket-check, .filter-ccy-check, .filter-vade-check, .filter-cust-check, .filter-revize-check'
    );
    allCheckboxes.forEach(cb => {
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

    // --- INIT ---
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