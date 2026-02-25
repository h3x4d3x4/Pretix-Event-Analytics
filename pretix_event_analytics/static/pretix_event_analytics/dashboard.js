(function () {
    const palette = [
        '#3498db', '#2ecc71', '#e74c3c', '#f39c12', '#9b59b6',
        '#1abc9c', '#34495e', '#e67e22', '#95a5a6', '#16a085',
        '#d35400', '#2c3e50', '#27ae60', '#8e44ad', '#c0392b'
    ];

    function loadJson(id) {
        const el = document.getElementById(id);
        if (!el) return null;
        try {
            return JSON.parse(el.textContent);
        } catch (e) {
            console.error(`Failed to parse JSON for #${id}`, e);
            return null;
        }
    }

    const jsData = {
        pacing: loadJson('pacing-data'),
        country: loadJson('country-data'),
        age: loadJson('age-data'),
        sales: loadJson('daily-sales-data'),
        hourly: loadJson('hourly-heatmap-data'),
        prob: loadJson('prob-dist-data'),
        van: loadJson('van-length-data'),
        language: loadJson('language-data'),
        payment: loadJson('payment-data'),
        nameChange: loadJson('name-change-data'),
        nameChangeByEdition: loadJson('name-change-by-edition-data'),
        buyerStats: loadJson('buyer-stats') || { new_buyers: 0, repeat_count: 0 }
    };

    function makeBar(id, labels, data, label, color) {
        const ctx = document.getElementById(id);
        if (!ctx || !labels || !labels.length) return;
        new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{ label: label, data: data, backgroundColor: color || palette[0] }]
            },
            options: {
                responsive: true,
                plugins: { legend: { display: false } },
                scales: { y: { beginAtZero: true, ticks: { precision: 0 } } }
            }
        });
    }

    function makeHBar(id, labels, data, label, singleColor) {
        const ctx = document.getElementById(id);
        if (!ctx || !labels || !labels.length) return;
        new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{ label: label, data: data, backgroundColor: singleColor ? singleColor : palette }]
            },
            options: {
                indexAxis: 'y',
                responsive: true,
                plugins: { legend: { display: false } },
                scales: { x: { beginAtZero: true, ticks: { precision: 0 } } }
            }
        });
    }

    // Country — horizontal bar
    if (jsData.country) {
        makeHBar('countryChart', jsData.country.labels, jsData.country.data, 'Orders');
    }

    // Language - horizontal bar
    if (jsData.language) {
        makeHBar('languageChart', jsData.language.labels, jsData.language.data, 'Orders', palette[3]);
    }

    // Payment - doughnut
    if (jsData.payment) {
        const ctx = document.getElementById('paymentChart');
        if (ctx) {
            new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: jsData.payment.labels,
                    datasets: [{ data: jsData.payment.data, backgroundColor: palette }]
                },
                options: {
                    responsive: true,
                    plugins: { legend: { position: 'right' } }
                }
            });
        }
    }

    // Age — vertical bar
    if (jsData.age) {
        makeBar('ageChart', jsData.age.labels, jsData.age.data, 'Buyers', palette[1]);
    }

    // Daily sales — dual-axis line
    if (jsData.sales && jsData.sales.labels && jsData.sales.labels.length) {
        const ctx = document.getElementById('dailySalesChart');
        if (ctx) {
            new Chart(ctx, {
                data: {
                    labels: jsData.sales.labels,
                    datasets: [
                        {
                            type: 'line', label: 'Revenue', data: jsData.sales.revenue,
                            borderColor: palette[0], backgroundColor: 'rgba(52,152,219,.1)',
                            yAxisID: 'y', tension: 0.3, fill: true
                        },
                        {
                            type: 'bar', label: 'Orders', data: jsData.sales.orders,
                            backgroundColor: 'rgba(46,204,113,.5)', yAxisID: 'y1'
                        }
                    ]
                },
                options: {
                    responsive: true,
                    interaction: { mode: 'index', intersect: false },
                    plugins: { legend: { position: 'top' } },
                    scales: {
                        y: { type: 'linear', position: 'left', beginAtZero: true, title: { display: true, text: 'Revenue' } },
                        y1: { type: 'linear', position: 'right', beginAtZero: true, grid: { drawOnChartArea: false }, title: { display: true, text: 'Orders' } }
                    }
                }
            });
        }
    }

    // Sales Velocity Pacing — multi-line connected scatter
    if (jsData.pacing && jsData.pacing.length) {
        const ctx = document.getElementById('pacingChart');
        if (ctx) {
            const datasets = jsData.pacing.map((ds, index) => {
                const color = palette[index % palette.length];
                return {
                    label: ds.label,
                    data: ds.data, // [{x: -300, y: 500}, ...]
                    borderColor: color,
                    backgroundColor: color,
                    borderWidth: 2,
                    fill: false,
                    tension: 0.1,
                    pointRadius: 0,
                    pointHoverRadius: 5
                };
            });

            new Chart(ctx, {
                type: 'line',
                data: { datasets: datasets },
                options: {
                    responsive: true,
                    interaction: { mode: 'nearest', axis: 'x', intersect: false },
                    plugins: {
                        legend: { position: 'top' },
                        tooltip: {
                            callbacks: {
                                title: function (context) {
                                    if (!context.length) return '';
                                    const days = Math.abs(context[0].parsed.x);
                                    return days + ' days before event';
                                }
                            }
                        }
                    },
                    scales: {
                        x: {
                            type: 'linear',
                            title: { display: true, text: 'Days until event' },
                            ticks: {
                                callback: function (value) {
                                    return value === 0 ? 'Event Date' : Math.abs(value);
                                }
                            }
                        },
                        y: {
                            type: 'linear',
                            beginAtZero: true,
                            title: { display: true, text: 'Cumulative Revenue' }
                        }
                    }
                }
            });
        }
    }

    // Hourly heatmap — bar chart by hour
    if (jsData.hourly && jsData.hourly.length) {
        const ctx = document.getElementById('hourlyChart');
        if (ctx) {
            const hourCounts = new Array(24).fill(0);
            jsData.hourly.forEach(d => {
                const h = new Date(d.hour).getUTCHours();
                hourCounts[h] += d.orders;
            });
            const hourLabels = Array.from({ length: 24 }, (_, i) => `${String(i).padStart(2, '0')}:00`);
            new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: hourLabels,
                    datasets: [{ label: 'Orders', data: hourCounts, backgroundColor: palette[4] }]
                },
                options: {
                    responsive: true,
                    plugins: { legend: { display: false } },
                    scales: { y: { beginAtZero: true, ticks: { precision: 0 } } }
                }
            });
        }
    }

    // Repeat donut
    if (jsData.buyerStats) {
        const ctx = document.getElementById('repeatDonut');
        if (ctx) {
            new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: ['New buyers', 'Returning buyers'],
                    datasets: [{ data: [jsData.buyerStats.new_buyers, jsData.buyerStats.repeat_count], backgroundColor: [palette[0], palette[2]] }]
                },
                options: {
                    responsive: true,
                    plugins: { legend: { position: 'bottom' } }
                }
            });
        }
    }

    // Probability distribution
    if (jsData.prob && jsData.prob.labels && jsData.prob.labels.length) {
        const ctx = document.getElementById('probDistChart');
        if (ctx) {
            const bgColors = jsData.prob.labels.map(s => {
                if (s >= 60) return 'rgba(39,174,96,.7)';
                if (s >= 40) return 'rgba(52,152,219,.7)';
                return 'rgba(149,165,166,.7)';
            });
            new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: jsData.prob.labels,
                    datasets: [{ label: 'Buyers', data: jsData.prob.data, backgroundColor: bgColors }]
                },
                options: {
                    responsive: true,
                    plugins: { legend: { display: false } },
                    scales: { y: { beginAtZero: true, ticks: { precision: 0 } } }
                }
            });
        }
    }

    // Van length chart
    if (jsData.van && jsData.van.labels && jsData.van.labels.length) {
        const ctx = document.getElementById('vanLengthChart');
        if (ctx) {
            new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: jsData.van.labels,
                    datasets: [{ data: jsData.van.data, backgroundColor: [palette[5], palette[1], palette[2]] }]
                },
                options: {
                    responsive: true,
                    plugins: { legend: { position: 'bottom' } }
                }
            });
        }
    }

    // Name change chart (secondary market — current event, by month)
    if (jsData.nameChange && jsData.nameChange.labels && jsData.nameChange.labels.length) {
        makeBar('nameChangeChart', jsData.nameChange.labels, jsData.nameChange.data, 'Name Changes', '#e74c3c');
    }

    // Name change rate by edition (cross-year comparison)
    if (jsData.nameChangeByEdition && jsData.nameChangeByEdition.labels && jsData.nameChangeByEdition.labels.length) {
        const ctx = document.getElementById('nameChangeByEditionChart');
        if (ctx) {
            const rates = jsData.nameChangeByEdition.rates;
            const colors = rates.map(r => r > 10 ? 'rgba(231,76,60,.75)' : r > 5 ? 'rgba(243,156,18,.75)' : 'rgba(52,152,219,.75)');
            new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: jsData.nameChangeByEdition.labels,
                    datasets: [{
                        label: 'Resale Rate (%)',
                        data: rates,
                        backgroundColor: colors,
                    }]
                },
                options: {
                    responsive: true,
                    plugins: { legend: { display: false } },
                    scales: {
                        y: {
                            beginAtZero: true,
                            ticks: { callback: v => v + '%' }
                        }
                    }
                }
            });
        }
    }

    // Cohort matrix cell colors
    document.querySelectorAll('td.cohort-cell[data-bg-color]').forEach(function (el) {
        const bgColor = el.getAttribute('data-bg-color');
        const txtColor = el.getAttribute('data-color');
        if (bgColor) el.style.backgroundColor = bgColor;
        if (txtColor) el.style.color = txtColor;
    });

    // Handle confirms
    document.querySelectorAll('[data-confirm]').forEach(function (btn) {
        btn.addEventListener('click', function (e) {
            if (!confirm(btn.getAttribute('data-confirm'))) {
                e.preventDefault();
            }
        });
    });

    // Loading state for filter form
    const filterForm = document.querySelector('.analytics-filter-form');
    if (filterForm) {
        filterForm.addEventListener('submit', function () {
            const btn = filterForm.querySelector('.btn-filter-apply');
            if (btn) {
                btn.classList.add('disabled');
                btn.innerHTML = '<span class="fa fa-spinner fa-spin"></span> ' + btn.textContent.trim();
            }
        });
    }

    // Loading state for resync forms
    document.querySelectorAll('form.form-inline-action').forEach(function (form) {
        form.addEventListener('submit', function () {
            const btn = form.querySelector('button[type="submit"]');
            if (btn) {
                // If the user cancelled the confirm, do not show loading
                // Wait, the confirm click handler runs first. If default is prevented,
                // the submit event might still run? No, preventDefault on click
                // cancels the submit event. So we are safe here.
                btn.classList.add('disabled');
                btn.innerHTML = '<span class="fa fa-spinner fa-spin"></span> ' + btn.textContent.trim();
            }
        });
    });

})();
