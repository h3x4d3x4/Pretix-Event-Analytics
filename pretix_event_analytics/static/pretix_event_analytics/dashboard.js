/*
 * Pretix Event Analytics — chart renderer.
 *
 * Every chart is described server-side as a spec (services/reports/charts.py)
 * and rendered here. Each chart gets hover tooltips, an HTML legend when it
 * has more than one series, and a table view (the accessible fallback).
 * Colours come from CSS tokens on .pa-root, by fixed slot.
 */
(function () {
    'use strict';

    const root = document.querySelector('.pa-root');
    if (!root || typeof Chart === 'undefined') return;

    let specs = {};
    try {
        specs = JSON.parse(document.getElementById('pa-charts').textContent || '{}');
    } catch (e) {
        console.error('analytics: could not parse chart data', e);
    }

    const css = getComputedStyle(root);
    const tok = (name) => css.getPropertyValue(name).trim();
    const SLOTS = [1, 2, 3, 4, 5, 6, 7].map((i) => tok('--pa-s' + i));
    const OTHER = tok('--pa-other');
    const GRID = tok('--pa-grid');
    const TEXT2 = tok('--pa-text-2');
    const TEXT3 = tok('--pa-text-3');
    const SURFACE = tok('--pa-surface');
    const locale = document.documentElement.lang || navigator.language || 'en';
    const currency = root.dataset.currency || 'EUR';

    const colorOf = (slot) => (slot === 'other' ? OTHER : SLOTS[(slot || 0) % SLOTS.length]);

    const nf0 = new Intl.NumberFormat(locale, { maximumFractionDigits: 0 });
    const nf1 = new Intl.NumberFormat(locale, { maximumFractionDigits: 1 });
    let cf0, cf2;
    try {
        cf0 = new Intl.NumberFormat(locale, { style: 'currency', currency, maximumFractionDigits: 0 });
        cf2 = new Intl.NumberFormat(locale, { style: 'currency', currency });
    } catch (e) {
        cf0 = cf2 = { format: (v) => currency + ' ' + nf0.format(v) };
    }
    const formatters = {
        number: { axis: (v) => nf0.format(v), exact: (v) => nf1.format(v) },
        currency: { axis: (v) => cf0.format(v), exact: (v) => cf2.format(v) },
        percent: { axis: (v) => nf0.format(v) + '%', exact: (v) => nf1.format(v) + '%' },
    };

    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Chart.defaults.font.size = 12;
    Chart.defaults.color = TEXT3;
    Chart.defaults.animation = false;

    const instances = {};

    function cumulate(data) {
        let acc = 0;
        return data.map((v) => (v === null ? null : (acc += v)));
    }

    // Vertical marker lines (e.g. price tier changes) at category labels.
    const markerPlugin = {
        id: 'paMarkers',
        afterDatasetsDraw(chart, _args, opts) {
            const markers = opts && opts.markers;
            if (!markers || !markers.length) return;
            const x = chart.scales.x;
            const { top, bottom } = chart.chartArea;
            const ctx = chart.ctx;
            ctx.save();
            let lastLabelX = -Infinity;
            markers.forEach((m, i) => {
                const px = x.type === 'linear'
                    ? x.getPixelForValue(m.x)
                    : x.getPixelForValue(chart.data.labels.indexOf(m.x));
                if (!isFinite(px) || px < chart.chartArea.left || px > chart.chartArea.right) return;
                // Organiser notes: solid accent line; automatic tier changes: dashed grey.
                ctx.strokeStyle = m.kind === 'note' ? SLOTS[1] : TEXT3;
                ctx.setLineDash(m.kind === 'note' ? [] : [3, 3]);
                ctx.lineWidth = 1;
                ctx.beginPath();
                ctx.moveTo(px, top);
                ctx.lineTo(px, bottom);
                ctx.stroke();
                ctx.setLineDash([]);
                // Numbers that would collide are skipped; the list under the
                // chart still names every marker.
                if (px - lastLabelX >= 16) {
                    ctx.fillStyle = TEXT2;
                    ctx.font = '600 10px ' + Chart.defaults.font.family;
                    ctx.textAlign = 'center';
                    ctx.fillText(String(i + 1), px, top - 4);
                    lastLabelX = px;
                }
            });
            ctx.restore();
        },
    };

    function buildDatasets(spec, cumulative) {
        const kind = cumulative ? 'line' : spec.kind;
        return spec.series.map((s, i) => {
            const c = colorOf(s.slot);
            const emphasised = spec.highlight === undefined || spec.highlight === i;
            let data = cumulative ? cumulate(s.data) : s.data;
            if (spec.x === 'linear') {
                data = data.map((y, j) => ({ x: spec.labels[j], y }));
            }
            const base = {
                label: s.name,
                data,
                borderColor: c,
                backgroundColor: kind === 'line' ? c : c,
            };
            if (kind === 'line') {
                return Object.assign(base, {
                    type: 'line',
                    borderWidth: emphasised ? 2.5 : 1.5,
                    borderColor: emphasised ? c : c + '99',
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    pointHoverBorderColor: SURFACE,
                    pointHoverBorderWidth: 2,
                    tension: 0,
                    spanGaps: false,
                    fill: false,
                    order: emphasised ? 0 : 1,
                });
            }
            return Object.assign(base, {
                type: 'bar',
                borderRadius: spec.stacked ? 0 : 4,
                borderSkipped: 'start',
                borderWidth: spec.stacked ? { top: 1 } : 0,
                borderColor: SURFACE,
                maxBarThickness: 36,
                categoryPercentage: 0.8,
                barPercentage: 0.9,
            });
        });
    }

    function render(el) {
        const spec = specs[el.dataset.chart];
        if (!spec) return;
        const canvas = el.querySelector('canvas');
        const hasData = spec.series.some((s) => s.data.some((v) => v !== null && v !== 0));
        if (!hasData) {
            el.innerHTML = '<div class="pa-empty">' + (root.dataset.noData || 'No data yet') + '</div>';
            return;
        }
        const cumulative = el.dataset.cumulative === '1';
        const f = formatters[spec.format] || formatters.number;
        const horizontal = !!spec.horizontal;
        const valueAxis = horizontal ? 'x' : 'y';
        const catAxis = horizontal ? 'y' : 'x';

        const scales = {};
        scales[catAxis] = {
            type: spec.x === 'linear' ? 'linear' : 'category',
            stacked: !!spec.stacked && !cumulative,
            grid: { display: false },
            border: { color: GRID },
            ticks: {
                color: TEXT3,
                autoSkip: true,
                maxRotation: 0,
                callback: spec.x === 'linear'
                    ? (v) => (v === 0 ? (root.dataset.eventDay || 'Event') : nf0.format(Math.abs(v)))
                    : function (v) {
                        const l = this.getLabelForValue(v);
                        return typeof l === 'string' && l.length > 22 ? l.slice(0, 21) + '…' : l;
                    },
            },
            title: { display: !!spec.xTitle, text: spec.xTitle, color: TEXT3 },
        };
        scales[valueAxis] = {
            stacked: !!spec.stacked && !cumulative,
            beginAtZero: true,
            grid: { color: GRID, drawTicks: false },
            border: { display: false },
            ticks: { color: TEXT3, callback: (v) => f.axis(v), padding: 6, precision: 0 },
            title: { display: !!spec.yTitle, text: spec.yTitle, color: TEXT3 },
        };

        if (instances[el.dataset.chart]) instances[el.dataset.chart].destroy();
        instances[el.dataset.chart] = new Chart(canvas, {
            type: cumulative ? 'line' : spec.kind,
            data: { labels: spec.x === 'linear' ? undefined : spec.labels, datasets: buildDatasets(spec, cumulative) },
            options: {
                maintainAspectRatio: false,
                indexAxis: horizontal ? 'y' : 'x',
                interaction: { mode: 'index', intersect: false, axis: catAxis },
                layout: { padding: { top: spec.markers ? 14 : 4 } },
                scales,
                plugins: {
                    legend: { display: false },
                    paMarkers: { markers: spec.markers },
                    tooltip: {
                        backgroundColor: '#1d1d1b',
                        padding: 10,
                        boxPadding: 4,
                        usePointStyle: true,
                        filter: (item) => item.raw !== null && (typeof item.raw !== 'object' || item.raw.y !== null),
                        callbacks: {
                            title: (items) => {
                                if (!items.length) return '';
                                if (spec.x === 'linear') {
                                    const d = Math.abs(items[0].parsed.x);
                                    return d === 0 ? (root.dataset.eventDay || 'Event day')
                                        : nf0.format(d) + ' ' + (root.dataset.daysBefore || 'days before');
                                }
                                return items[0].label;
                            },
                            label: (item) => ' ' + item.dataset.label + ': ' + f.exact(horizontal ? item.parsed.x : item.parsed.y),
                            footer: (items) => {
                                if (!spec.stacked || cumulative || items.length < 2) return '';
                                const sum = items.reduce((a, i) => a + (horizontal ? i.parsed.x : i.parsed.y), 0);
                                return (root.dataset.total || 'Total') + ': ' + f.exact(sum);
                            },
                        },
                    },
                },
            },
            plugins: [markerPlugin],
        });

        renderLegend(el, spec);
        renderMarkerList(el, spec, cumulative);
        renderTable(el, spec);
    }

    function renderLegend(el, spec) {
        const holder = el.parentNode.querySelector('.pa-legend[data-for="' + el.dataset.chart + '"]');
        if (!holder) return;
        holder.innerHTML = '';
        if (spec.series.length < 2) return;
        spec.series.forEach((s, i) => {
            const span = document.createElement('span');
            const sw = document.createElement('i');
            sw.style.background = colorOf(s.slot);
            if (spec.highlight !== undefined && spec.highlight !== i) sw.style.opacity = '0.6';
            span.appendChild(sw);
            span.appendChild(document.createTextNode(s.name));
            if (spec.highlight === i) span.style.fontWeight = '600';
            holder.appendChild(span);
        });
    }

    function renderMarkerList(el, spec, cumulative) {
        const holder = el.parentNode.querySelector('.pa-markers[data-for="' + el.dataset.chart + '"]');
        if (!holder) return;
        holder.innerHTML = '';
        if (!spec.markers || !spec.markers.length) return;
        spec.markers.forEach((m, i) => {
            const d = document.createElement('div');
            d.textContent = (i + 1) + ' · ' + (m.label || m.x) + ' — ' + m.text;
            if (m.kind === 'note') d.className = 'pa-note';
            holder.appendChild(d);
        });
    }

    function renderTable(el, spec) {
        const holder = el.parentNode.querySelector('.pa-table-view[data-for="' + el.dataset.chart + '"]');
        if (!holder) return;
        const f = formatters[spec.format] || formatters.number;
        const table = document.createElement('table');
        table.className = 'table table-condensed pa-table';
        const head = table.createTHead().insertRow();
        const th0 = document.createElement('th');
        th0.textContent = spec.xTitle || '';
        head.appendChild(th0);
        spec.series.forEach((s) => {
            const th = document.createElement('th');
            th.className = 'num';
            th.textContent = s.name;
            head.appendChild(th);
        });
        const body = table.createTBody();
        spec.labels.forEach((label, i) => {
            if (spec.series.every((s) => s.data[i] === null || s.data[i] === 0) && spec.x === 'linear') return;
            const row = body.insertRow();
            row.insertCell().textContent = spec.x === 'linear' ? nf0.format(Math.abs(label)) : label;
            spec.series.forEach((s) => {
                const c = row.insertCell();
                c.className = 'num';
                c.textContent = s.data[i] === null ? '–' : f.exact(s.data[i]);
            });
        });
        holder.innerHTML = '';
        holder.appendChild(table);
    }

    // ── Wire up ──────────────────────────────────────────────────────────────
    // Pretix's CSP forbids style attributes in markup; widths are applied
    // through the CSSOM instead, which the policy allows.
    document.querySelectorAll('[data-bar]').forEach((el) => {
        const minus = parseFloat(el.dataset.barMinus) || 0;  // stacked segment (capacity: pending after paid)
        const v = Math.max(0, Math.min(100, (parseFloat(el.dataset.bar) || 0) - minus));
        el.style.width = v + '%';
    });
    document.querySelectorAll('[data-chart]').forEach(render);

    document.addEventListener('click', (ev) => {
        const tableBtn = ev.target.closest('[data-table-toggle]');
        if (tableBtn) {
            const view = root.querySelector('.pa-table-view[data-for="' + tableBtn.dataset.tableToggle + '"]');
            if (view) {
                view.classList.toggle('open');
                tableBtn.classList.toggle('active', view.classList.contains('open'));
            }
            return;
        }
        const cumBtn = ev.target.closest('[data-cumulative-toggle]');
        if (cumBtn) {
            const el = root.querySelector('[data-chart="' + cumBtn.dataset.cumulativeToggle + '"]');
            if (el) {
                el.dataset.cumulative = el.dataset.cumulative === '1' ? '0' : '1';
                cumBtn.classList.toggle('active', el.dataset.cumulative === '1');
                render(el);
            }
            return;
        }
        const sw = ev.target.closest('[data-switch]');
        if (sw) {
            const group = sw.closest('[data-switch-group]');
            const name = group.dataset.switchGroup;
            group.querySelectorAll('[data-switch]').forEach((b) => b.classList.toggle('active', b === sw));
            root.querySelectorAll('[data-switch-pane="' + name + '"]').forEach((pane) => {
                const show = pane.dataset.pane === sw.dataset.switch;
                pane.hidden = !show;
                if (show) pane.querySelectorAll('[data-chart]').forEach((c) => {
                    const inst = instances[c.dataset.chart];
                    if (inst) inst.resize(); else render(c);
                });
            });
        }
    });

    // Sync cumulative toggles across the tickets/revenue pair on the Sales page
    document.querySelectorAll('[data-cumulative-all]').forEach((btn) => {
        btn.addEventListener('click', () => {
            const on = !btn.classList.contains('active');
            btn.classList.toggle('active', on);
            root.querySelectorAll('[data-chart][data-cumulative-group="' + btn.dataset.cumulativeAll + '"]').forEach((el) => {
                el.dataset.cumulative = on ? '1' : '0';
                render(el);
            });
        });
    });

    // Edition picker: submit on change
    document.querySelectorAll('form[data-autosubmit] input').forEach((inp) => {
        inp.addEventListener('change', () => inp.form.submit());
    });

    const toTop = document.getElementById('paBackToTop');
    if (toTop) {
        window.addEventListener('scroll', () => toTop.classList.toggle('visible', window.scrollY > 600), { passive: true });
        toTop.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
    }
})();
