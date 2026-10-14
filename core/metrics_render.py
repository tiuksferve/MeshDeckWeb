"""
core/metrics_render.py — MetricsRenderMixin: geração de HTML/JS para cada
secção de métricas (Chart.js, tabelas, KPIs).

Separado de tab_metrics.py para facilitar manutenção.
Combinado com MetricsDataMixin em core/metrics.py (MeshMetrics).
"""
import json
from i18n import tr
import math
import time
from datetime import datetime

from constants import (
    logger,
    DARK_BG, PANEL_BG, BORDER_COLOR, ACCENT_GREEN, ACCENT_BLUE,
    ACCENT_ORANGE, ACCENT_RED, TEXT_PRIMARY, TEXT_MUTED
)


class MetricsRenderMixin:
    """Mixin com toda a geração de HTML/JS para as secções de métricas.

    Depende dos atributos de dados definidos em MetricsDataMixin.
    """

    def _base_html(self, title: str, body: str) -> str:
        """Template HTML base com Chart.js e estilos.

        O Chart.js vem da cópia em web/vendor (servida pelo próprio servidor),
        para os gráficos funcionarem sem internet."""
        return f"""<!DOCTYPE html><html><head>
<meta charset="utf-8">
<script src="/vendor/chart.umd.min.js"></script>
<script>if(window.Chart){{Chart.defaults.animation=false;}}</script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0d1117; color: #e6edf3; font-family: 'Segoe UI', Arial, sans-serif;
         font-size: 13px; padding: 16px; overflow-y: auto; }}
  h2 {{ color: #39d353; font-size: 16px; margin-bottom: 4px; }}
  .subtitle {{ color: #8b949e; font-size: 11px; margin-bottom: 20px; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  .grid-3 {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin-bottom: 16px; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px;
           padding: 14px; }}
  .card h3 {{ color: #8b949e; font-size: 11px; font-weight: normal;
              text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }}
  .kpi {{ font-size: 32px; font-weight: bold; color: #e6edf3; }}
  .kpi-sub {{ font-size: 11px; color: #8b949e; margin-top: 2px; }}
  .kpi.green {{ color: #39d353; }}
  .kpi.orange {{ color: #f0883e; }}
  .kpi.red {{ color: #f85149; }}
  .kpi.blue {{ color: #58a6ff; }}
  .bar-wrap {{ margin-top: 6px; }}
  .bar-bg {{ background: #21262d; border-radius: 4px; height: 8px; overflow: hidden; }}
  .bar-fill {{ height: 8px; border-radius: 4px; transition: width 0.5s; }}
  .chart-wrap {{ position: relative; height: 220px; }}
  .chart-wrap-lg {{ position: relative; height: 280px; }}
  .full {{ grid-column: 1 / -1; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  th {{ color: #8b949e; text-align: left; padding: 6px 8px;
        border-bottom: 1px solid #30363d; font-weight: normal; font-size: 11px; }}
  td {{ padding: 6px 8px; border-bottom: 1px solid #21262d; }}
  tr:hover td {{ background: #21262d; }}
  .tag {{ display: inline-block; padding: 1px 6px; border-radius: 4px;
           font-size: 10px; font-weight: bold; }}
  .tag-green {{ background: #1a3d1a; color: #39d353; }}
  .tag-orange {{ background: #3d2a0a; color: #f0883e; }}
  .tag-red {{ background: #3d0a0a; color: #f85149; }}
  .tag-blue {{ background: #0a1f3d; color: #58a6ff; }}
  .tag-gray {{ background: #21262d; color: #8b949e; }}
  .no-data {{ color: #8b949e; text-align: center; padding: 40px 0; font-size: 13px; }}
  .updated {{ color: #8b949e; font-size: 10px; text-align: right; margin-top: 12px; }}
  /* Ecrã estreito (interface web no telemóvel). O desktop nunca chega a esta
     largura, por isso não é afectado. KPIs em duas colunas, gráficos e
     tabelas a toda a largura, e os valores grandes mais contidos. */
  @media (max-width: 600px) {{
    body {{ padding: 10px; }}
    h2 {{ font-size: 15px; }}
    .subtitle {{ margin-bottom: 12px; }}
    .grid, .grid-3 {{ grid-template-columns: 1fr 1fr; gap: 8px; }}
    .grid-3 {{ margin-bottom: 8px; }}
    .grid {{ margin-bottom: 8px; }}
    .grid-3 > :last-child:nth-child(odd), .grid > :last-child:nth-child(odd) {{ grid-column: 1 / -1; }}
    .grid > .card:has(canvas, table),
    .grid-3 > .card:has(canvas, table) {{ grid-column: 1 / -1; }}
    .card {{ padding: 10px; min-width: 0; overflow-x: auto; }}
    .card[style*="margin-top"] {{ margin-top: 8px !important; }}
    .card h3 {{ font-size: 10px; margin-bottom: 4px; }}
    .kpi, .kpi[style] {{ font-size: 22px !important; line-height: 1.15; min-width: 0 !important; }}
    .kpi-sub {{ font-size: 10px; }}
    .chart-wrap {{ height: 180px; }}
    .chart-wrap-lg {{ height: 220px; }}
    table {{ font-size: 11px; }}
    th, td {{ padding: 5px 4px; }}
  }}
</style>
</head><body>
<h2>{title}</h2>
{body}
</body></html>"""

    def _html_overview(self) -> str:
        now = time.time()
        total_pkts = len(self._packets)
        cutoff2h   = now - 7200
        active_nids = set(p[1] for p in self._packets if p[0] >= cutoff2h and p[1])
        n_active   = len(active_nids)

        # Pacotes último minuto
        ppm = len([p for p in self._packets if p[0] >= now - 60])

        snr_avg = round(sum(self._snr_values) / len(self._snr_values), 1) if self._snr_values else None
        hops_avg = round(sum(self._hops_values) / len(self._hops_values), 2) if self._hops_values else None

        ch_util_avg = round(sum(self._ch_util_active().values()) / len(self._ch_util_active()), 1) if self._ch_util_active() else None
        air_avg     = round(sum(self._air_tx_active().values()) / len(self._air_tx_active()), 1) if self._air_tx_active() else None

        # Taxa de entrega (só DMs: as mensagens de canal nunca têm ACK real)
        delivery = self._delivery()

        def kpi(val, unit, label, color="", kid="", note=""):
            v = f"{val}{unit}" if val is not None else "—"
            id_attr = f' id="{kid}"' if kid else ""
            note_html = f'<div class="kpi-sub" style="font-size:10px">{note}</div>' if note else ""
            return f'<div class="card"><h3>{label}</h3><div{id_attr} class="kpi {color}">{v}</div>{note_html}</div>'

        def ch_kpi(val):
            if val is None: return kpi(None, "", tr("Utiliz. Canal (avg)"))
            color = "green" if val < self.CH_UTIL_OK else ("orange" if val < self.CH_UTIL_WARN else "red")
            bar_color = "#39d353" if val < self.CH_UTIL_OK else ("#f0883e" if val < self.CH_UTIL_WARN else "#f85149")
            pct = min(int(val), 100)
            _ch_label = tr("Utiliz. Canal (avg)")
            _ch_sub   = tr("<25% óptimo · <50% aceitável · >50% crítico")
            return (f'''<div class="card"><h3>{_ch_label}</h3>
              <div id="ov-chutil" class="kpi {color}">{val}%</div>
              <div class="bar-wrap"><div class="bar-bg">
              <div class="bar-fill" style="width:{pct}%;background:{bar_color}"></div>
              </div></div>
              <div class="kpi-sub">{_ch_sub}</div>
            </div>''')

        # Tabela top nós por pacotes — FIX: 5 colunas alinhadas com JS update
        nid_counts = {}
        for p in self._packets:
            if p[1]: nid_counts[p[1]] = nid_counts.get(p[1], 0) + 1
        top = sorted(nid_counts.items(), key=lambda x: -x[1])[:8]
        rows = "".join(
            f"<tr><td>{nid}</td><td>{self._name(nid)}</td><td>{cnt}</td>"
            f"<td>{round(self._ch_val(nid), 1)}%</td>"
            f"<td>{'⚡' if self._battery.get(nid) == 101 else (str(self._battery.get(nid)) + '%' if self._battery.get(nid) is not None else '—')}</td></tr>"
            for nid, cnt in top
        ) or f"<tr><td colspan='5' class='no-data'>{tr('Sem dados ainda')}</td></tr>"

        body = f"""
<div class="subtitle">{tr('Resumo da sessão')}</div>
<div class="grid-3">
  {kpi(total_pkts, "", tr("Total Pacotes"), "blue", "ov-pkts")}
  {kpi(n_active, " " + tr("nós"), tr("Nós Activos (2h)"), "green", "ov-active")}
  {kpi(ppm, "/min", tr("Pacotes/min"), "", "ov-ppm")}
</div>
<div class="grid-3">
  {kpi(snr_avg, " dB", tr("SNR Médio"), "green" if snr_avg and snr_avg >= 0 else "orange", "ov-snr")}
  {kpi(hops_avg, " hops", tr("Hops Médio"), "", "ov-hops")}
  {kpi(delivery, "%", tr("Taxa Entrega"), "green" if delivery and delivery >= 80 else "orange", "ov-delivery", tr("DMs enviadas pelo nó local"))}
</div>
<div class="grid">
  {ch_kpi(ch_util_avg)}
  {kpi(air_avg, "%", tr("Airtime TX (avg)"), "green" if air_avg and air_avg < 10 else "orange", "ov-air")}
</div>
<div class="card" style="margin-top:16px">
  <h3>{tr("Top Nós por Pacotes")}</h3>
  <div style="margin-bottom:6px;font-size:11px;color:#8b949e">{tr("metrics_filter_hint")}</div>
  <table><tr><th>ID</th><th>{tr("Nome")}</th><th>{tr("Pacotes")}</th><th>Ch. Util.</th><th>{tr("Bateria")}</th></tr><tbody id="ov-node-tbody" data-filterable="1">{rows}</tbody></table>
</div>
<div class="updated" id="ov-updated">{tr('Sessão iniciada')} · {datetime.fromtimestamp(self._start_time).strftime('%H:%M:%S %d/%m/%Y')}</div>
<script>
window._metricsUpdateData = function(d) {{
  function kpiColor(val, thresholds) {{
    if(val===null||val===undefined) return '';
    for(var i=0;i<thresholds.length;i++) if(val<=thresholds[i][0]) return thresholds[i][1];
    return thresholds[thresholds.length-1][1];
  }}
  function setKpi(id, val, unit) {{
    var e=document.getElementById(id); if(!e) return;
    e.textContent = val!==null&&val!==undefined ? val+unit : '—';
  }}
  function setKpiClass(id, cls) {{
    var e=document.getElementById(id); if(e) e.className='kpi '+(cls||'')+' ov-kpi';
  }}
  setKpi('ov-pkts', d.total_pkts, '');
  setKpi('ov-active', d.n_active, d.unit_nos||' nodes');
  setKpi('ov-ppm', d.ppm, '/min');
  setKpi('ov-snr', d.snr_avg, ' dB');
  setKpiClass('ov-snr', d.snr_avg!==null&&d.snr_avg>=0?'green':'orange');
  setKpi('ov-hops', d.hops_avg, ' hops');
  setKpi('ov-delivery', d.delivery, '%');
  setKpiClass('ov-delivery', d.delivery!==null&&d.delivery>=80?'green':'orange');
  setKpi('ov-chutil', d.ch_avg, '%');
  setKpiClass('ov-chutil', d.ch_avg===null?'':d.ch_avg<25?'green':d.ch_avg<50?'orange':'red');
  setKpi('ov-air', d.air_avg, '%');
  setKpiClass('ov-air', d.air_avg!==null&&d.air_avg<10?'green':'orange');
  var tb=document.getElementById('ov-node-tbody');
  if(tb && d.table_rows && d.table_rows.length) {{
    var h='';
    d.table_rows.forEach(function(r) {{
      var nid=r[0],nm=r[1],cnt=r[2],ch=r[3],batt=r[4];
      var b=batt===101?'⚡':batt!==null&&batt!==undefined?batt+'%':'—';
      h+='<tr><td>'+nid+'</td><td>'+nm+'</td><td>'+cnt+'</td><td>'+ch+'%</td><td>'+b+'</td></tr>';
    }});
    tb.innerHTML=h;
    _applyFilter();
  }}
}};
var _currentFilter='';
function _applyFilter(){{
  var ft=_currentFilter;
  document.querySelectorAll('table tbody[data-filterable]').forEach(function(tbody){{
    var found=0;
    tbody.querySelectorAll('tr:not(.filter-no-match)').forEach(function(row){{
      var hay=Array.from(row.querySelectorAll('td')).map(function(c){{return c.textContent.toLowerCase();}}).join(' ');
      var ok=!ft||hay.indexOf(ft)!==-1;
      row.style.display=ok?'':'none';
      if(ok) found++;
    }});
    var noRow=tbody.querySelector('.filter-no-match');
    if(!noRow){{
      noRow=document.createElement('tr');noRow.className='filter-no-match';
      noRow.innerHTML='<td colspan="99" style="color:#f0883e;padding:8px;text-align:center;font-size:12px">🔍 '+(ft?'{tr("metrics_no_results")}'+' &quot;'+ft+'&quot;':'')+'</td>';
      tbody.appendChild(noRow);
    }}
    noRow.style.display=(ft&&found===0)?'':'none';
  }});
}}
window._metricsFilterTable=function(text){{_currentFilter=(text||'').toLowerCase().trim();_applyFilter();}};
</script>"""
        return self._base_html(tr("📊 Visão Geral"), body)

    # ── 2. Canal & Airtime ────────────────────────────────────────────────
    # Limites de duty cycle horário (EU_433 / EU_868 — ETSI EN300.220)
    DUTY_CYCLE_LIMIT_EU = 10.0   # 10%/hora — limite legal EU
    DUTY_CYCLE_WARN_EU  =  7.0   # 7%/hora — aviso preventivo

    def _html_channel(self) -> str:
        if not self._ch_util_active() and not self._ch_util_ts and not self._air_tx_active():
            body = f'<div class="no-data">{tr("⏳ Aguardando dados de telemetria (TELEMETRY_APP)...")}<br><br>{tr("Os nós devem ter o módulo de telemetria activado.")}</div>'
            return self._base_html(tr("📡 Canal & Airtime"), body)

        # Duty cycle horário = airUtilTx: o protobuf define-o como a % de
        # airtime de TX na última hora (não 10 min — multiplicar por 6
        # sobrestimava-o seis vezes). É uma métrica POR NÓ (TX daquele nó);
        # channelUtilization é da REDE (RX+TX de todos no canal).
        # Mostramos o pior nó como indicador de risco.
        # Fonte: ETSI EN300.220 — EU_433/EU_868 limite 10%/hora.
        duty_per_node = {nid: round(min(air, 100.0), 2)
                         for nid, air in self._air_tx_active().items()}

        # Nó com maior duty cycle (pior caso)
        worst_nid = max(duty_per_node, key=duty_per_node.get) if duty_per_node else None
        worst_dc  = duty_per_node[worst_nid] if worst_nid else None

        ts_labels = [self._ts_label(t) for t, _ in self._ch_util_ts]
        ts_vals   = [v for _, v in self._ch_util_ts]

        def duty_status(dc):
            if dc >= self.DUTY_CYCLE_LIMIT_EU: return "red",    tr("🚨 LIMITE EXCEDIDO")
            if dc >= self.DUTY_CYCLE_WARN_EU:  return "orange", tr("⚠ Próximo do limite")
            return "green", tr("lbl_dc_optimal")

        # Tabela por nó — usa apenas nós com TTL válido
        ch_active  = self._ch_util_active()
        air_active = self._air_tx_active()
        rows = ""
        all_nids = sorted(set(list(ch_active.keys()) + list(air_active.keys())))
        for nid in all_nids:
            ch  = ch_active.get(nid, 0)
            dc  = duty_per_node.get(nid, 0)
            ch_tag  = "green" if ch  < self.CH_UTIL_OK  else ("orange" if ch  < self.CH_UTIL_WARN else "red")
            dc_color, dc_label = duty_status(dc)
            dc_pct = min(int(dc / self.DUTY_CYCLE_LIMIT_EU * 100), 100)
            bar_c  = {"green": "#39d353", "orange": "#f0883e", "red": "#f85149"}[dc_color]
            worst_mark = " 🔺" if nid == worst_nid and worst_dc and worst_dc >= self.DUTY_CYCLE_WARN_EU else ""
            display_name = self._name(nid)
            # As mesmas 5 colunas que o cabeçalho e que a actualização em JS.
            rows += (
                f"<tr><td>{nid}</td><td>{display_name}{worst_mark}</td>"
                f"<td><span class='tag tag-{ch_tag}'>{ch:.1f}%</span></td>"
                f"<td><span class='tag tag-{dc_color}'>{dc:.2f}%</span>"
                f"  <div class='bar-bg' style='margin-top:3px'>"
                f"  <div class='bar-fill' style='width:{dc_pct}%;background:{bar_c}'></div></div></td>"
                f"<td><span class='tag tag-{dc_color}'>{dc_label}</span></td></tr>"
            )
        if not rows:
            rows = f"<tr><td colspan='5' class='no-data'>{tr('Sem dados')}</td></tr>"

        # KPI: pior nó (mais relevante para conformidade EU)
        # KPI principal: channelUtilization médio da rede
        # (cada nó reporta o que VÊ no canal — é a métrica da rede, não do nó)
        ch_net_avg = round(sum(ch_active.values()) / len(ch_active), 1) if ch_active else None
        if ch_net_avg is not None:
            ch_color = "green" if ch_net_avg < self.CH_UTIL_OK else ("orange" if ch_net_avg < self.CH_UTIL_WARN else "red")
            ch_bar_c = {"green": "#39d353", "orange": "#f0883e", "red": "#f85149"}[ch_color]
            ch_pct   = min(int(ch_net_avg), 100)
            ch_label = tr("✅ Óptimo (<25%)") if ch_net_avg < self.CH_UTIL_OK else (tr("⚠ Próximo do limite") if ch_net_avg < self.CH_UTIL_WARN else tr("🚨 LIMITE EXCEDIDO"))
            ch_kpi = (
                f'<div class="card"><h3>{tr("Channel Utilization da Rede")}</h3>'
                f'<div id="ch-net-val" class="kpi {ch_color}">{ch_net_avg}%</div>'
                f'<div class="bar-wrap"><div class="bar-bg">'
                f'<div class="bar-fill" style="width:{ch_pct}%;background:{ch_bar_c}"></div></div></div>'
                f'<div class="kpi-sub">{tr("Métrica da rede — airtime observado por cada nó (RX+TX de todos) · {ch_label}", ch_label=ch_label)}<br>'
                f'{tr("Firmware atrasa envios acima de 25% · Para GPS: limite 40%")}</div></div>'
            )
        else:
            ch_kpi = f'<div class="card"><h3>{tr("Channel Utilization da Rede")}</h3><div class="kpi" style="color:#8b949e">—</div><div class="kpi-sub">{tr("Aguardando dados de telemetria...")}</div></div>'

        # KPI secundário: duty cycle do pior nó (airUtilTx por nó — conformidade EU)
        if worst_dc is not None:
            worst_name = self._name(worst_nid)
            dc_color_w, dc_label_w = duty_status(worst_dc)
            dc_pct_w = min(int(worst_dc / self.DUTY_CYCLE_LIMIT_EU * 100), 100)
            bar_c_w  = {"green": "#39d353", "orange": "#f0883e", "red": "#f85149"}[dc_color_w]
            duty_kpi = (
                f'<div class="card"><h3>{tr("Duty Cycle/h — Pior Nó ({nome})", nome=worst_name)}</h3>'
                f'<div id="dc-avg-val" class="kpi {dc_color_w}">{worst_dc}%</div>'
                f'<div class="bar-wrap"><div class="bar-bg">'
                f'<div class="bar-fill" style="width:{dc_pct_w}%;background:{bar_c_w}"></div></div></div>'
                f'<div class="kpi-sub">{tr("Métrica por nó (TX daquele nó na última hora) · Limite EU: 10%/hora · {dc_label_w}", dc_label_w=dc_label_w)}</div></div>'
            )
        else:
            duty_kpi = f'<div class="card"><h3>{tr("Duty Cycle/h por Nó")}</h3><div class="kpi" style="color:#8b949e">—</div><div class="kpi-sub">{tr("Aguardando dados de airUtilTx...")}</div></div>'

        air_avg = round(sum(air_active.values()) / len(air_active), 2) if air_active else None
        air_color = "green" if air_avg and air_avg < 10 else ("orange" if air_avg else "")
        air_kpi = (
            f'<div class="card"><h3>{tr("Airtime TX/h (média)")}</h3>'
            f'<div id="air-avg-val" class="kpi {air_color}">{air_avg if air_avg is not None else "—"}'
            f'{"%" if air_avg is not None else ""}</div>'
            f'<div class="kpi-sub">{tr("Média do TX de todos os nós na última hora")}</div></div>'
        )

        n_ts = len(ts_vals) or 1
        body = f"""
<div class="subtitle">{tr("Canal LoRa · Airtime TX · Hourly Duty Cycle")}</div>
<div class="card" style="margin-bottom:16px;border-left:4px solid #39d353;padding:8px 14px">
  <span style="color:#39d353;font-size:12px;font-weight:bold">{tr("🌐 Métrica da Rede")}</span><span style="color:#8b949e;font-size:11px"> {tr("network_metric_desc")}</span>
</div>
<div class="grid-3">{ch_kpi}{duty_kpi}{air_kpi}</div>
<div class="card" style="margin-top:16px">
  <h3>{tr("Channel Utilization ao Longo do Tempo")}</h3>
  <div class="chart-wrap-lg"><canvas id="chChart"></canvas></div>
</div>
<div class="card" style="margin-top:16px">
  <h3>{tr("Por Nó — Ch. Util · Duty Cycle/h")}</h3>
  <div style="margin-bottom:6px;font-size:11px;color:#8b949e">{tr("metrics_filter_hint")}</div>
  <table><tr><th>ID</th><th>{tr("Nome")}</th><th>Ch. Util.</th><th>{tr("Duty Cycle/h (Airtime TX)")}</th><th>{tr("Estado")}</th></tr><tbody id="ch-node-tbody" data-filterable="1">{rows}</tbody></table>
  <div style="color:#8b949e;font-size:10px;margin-top:8px;padding-top:8px;border-top:1px solid #21262d">
    {tr("duty_cycle_note")}
  </div>
</div>
<script>
window._chChart = new Chart(document.getElementById('chChart'), {{
  type: 'line',
  data: {{
    labels: {json.dumps(ts_labels)},
    datasets: [
      {{ label: 'Ch. Utilization (%)', data: {json.dumps(ts_vals)},
         borderColor: '#39d353', backgroundColor: 'rgba(57,211,83,0.08)',
         fill: true, tension: 0.3, pointRadius: 2 }},
      {{ label: '{tr("Limite óptimo (25%)")}'  , data: Array({n_ts}).fill(25),
         borderColor: '#f0883e', borderDash: [4,4], pointRadius: 0, fill: false }},
      {{ label: '{tr("Limite crítico (50%)")}'  , data: Array({n_ts}).fill(50),
         borderColor: '#f85149', borderDash: [4,4], pointRadius: 0, fill: false }},
    ]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    scales: {{
      y: {{ min: 0, max: 100, grid: {{ color: '#21262d' }},
            ticks: {{ color: '#8b949e', callback: v => v + '%' }} }},
      x: {{ grid: {{ color: '#21262d' }}, ticks: {{ color: '#8b949e', maxTicksLimit: 10 }} }}
    }},
    plugins: {{ legend: {{ labels: {{ color: '#8b949e', boxWidth: 12 }} }},
                tooltip: {{ callbacks: {{ label: ctx => ctx.dataset.label + ': ' + ctx.parsed.y.toFixed(1) + '%' }} }} }}
  }}
}});
</script>
<script>
window._metricsUpdateData = function(d) {{
  function set(id, v) {{ var e=document.getElementById(id); if(e) e.textContent=v; }}
  set('ch-net-val', d.ch_net_avg !== null && d.ch_net_avg !== undefined ? d.ch_net_avg + '%' : '—');
  set('dc-avg-val', d.worst_dc !== null && d.worst_dc !== undefined ? d.worst_dc + '%' : '—');
  set('air-avg-val', d.air_avg !== null && d.air_avg !== undefined ? d.air_avg + '%' : '—');
  if(window._chChart && d.ts_vals && d.ts_vals.length > 0) {{
    window._chChart.data.labels = d.ts_labels;
    window._chChart.data.datasets[0].data = d.ts_vals;
    var n = d.ts_vals.length || 1;
    window._chChart.data.datasets[1].data = Array(n).fill(25);
    window._chChart.data.datasets[2].data = Array(n).fill(50);
    window._chChart.update('none');
  }}
  // Actualiza tabela por nó
  var tbody = document.getElementById('ch-node-tbody');
  if(tbody && d.rows && d.rows.length > 0) {{
    var DUTY_WARN=7, DUTY_LIMIT=10;
    var html='';
    d.rows.forEach(function(r) {{
      var nid=r[0], nm=r[1], ch=r[2], dc=r[4];
      var chTag = ch>=50?'red':ch>=25?'orange':'green';
      var dcColor = dc>=DUTY_LIMIT?'red':dc>=DUTY_WARN?'orange':'green';
      var dcPct = Math.min(Math.round(dc/DUTY_LIMIT*100),100);
      var barC = dcColor==='green'?'#39d353':dcColor==='orange'?'#f0883e':'#f85149';
      html += '<tr><td>'+nid+'</td><td>'+nm+'</td>'
            + '<td><span class="tag tag-'+chTag+'">'+ch+'%</span></td>'
            + '<td><span class="tag tag-'+dcColor+'">'+dc+'%</span>'
            + '<div class="bar-bg" style="margin-top:3px"><div class="bar-fill" style="width:'+dcPct+'%;background:'+barC+'"></div></div></td>'
            + '<td><span class="tag tag-'+dcColor+'">'
            + (dc>=DUTY_LIMIT?d.lbl_exceed:dc>=DUTY_WARN?d.lbl_warn:d.lbl_ok)
            + '</span></td></tr>';
    }});
    tbody.innerHTML = html;
    _applyFilter();
  }}
}};
var _currentFilter='';
function _applyFilter(){{
  var ft=_currentFilter;
  document.querySelectorAll('table tbody[data-filterable]').forEach(function(tbody){{
    var found=0;
    tbody.querySelectorAll('tr:not(.filter-no-match)').forEach(function(row){{
      var hay=Array.from(row.querySelectorAll('td')).map(function(c){{return c.textContent.toLowerCase();}}).join(' ');
      var ok=!ft||hay.indexOf(ft)!==-1;
      row.style.display=ok?'':'none';
      if(ok) found++;
    }});
    var noRow=tbody.querySelector('.filter-no-match');
    if(!noRow){{
      noRow=document.createElement('tr');noRow.className='filter-no-match';
      noRow.innerHTML='<td colspan="99" style="color:#f0883e;padding:8px;text-align:center;font-size:12px">🔍 '+(ft?'{tr("metrics_no_results")}'+' &quot;'+ft+'&quot;':'')+'</td>';
      tbody.appendChild(noRow);
    }}
    noRow.style.display=(ft&&found===0)?'':'none';
  }});
}}
window._metricsFilterTable=function(text){{_currentFilter=(text||'').toLowerCase().trim();_applyFilter();}};
</script>"""
        return self._base_html(tr("📡 Canal & Airtime"), body)

    # ── 3. Qualidade RF ───────────────────────────────────────────────────
    def _html_rf(self) -> str:
        if not self._snr_values and not self._hops_values:
            body = f'<div class="no-data">{tr("⏳ Aguardando pacotes RF...")}</div>'
            return self._base_html(tr("📶 Qualidade RF"), body)

        # Histograma SNR em buckets de 2dB
        def histogram(vals, bucket=2, mn=-20, mx=14):
            buckets = list(range(mn, mx + bucket, bucket))
            counts  = [0] * len(buckets)
            for v in vals:
                idx = min(int((v - mn) // bucket), len(counts) - 1)
                idx = max(0, idx)
                counts[idx] += 1
            labels = [f"{b}" for b in buckets]
            return labels, counts

        snr_labels, snr_counts = histogram(self._snr_values, 2, -20, 14)

        # Histograma hops
        max_hops = max(self._hops_values) if self._hops_values else 7
        hop_labels = [str(i) for i in range(0, min(max_hops + 2, 9))]
        hop_counts = [self._hops_values.count(i) for i in range(len(hop_labels))]

        # Stats SNR
        snr_sorted = sorted(self._snr_values)
        n = len(snr_sorted)
        snr_avg  = round(sum(snr_sorted) / n, 1) if n else None
        snr_med  = round(snr_sorted[n // 2], 1)  if n else None
        # FIX: formula correspondente ao _data_rf no metrics_data.py
        snr_p10  = round(snr_sorted[int(0.1 * (n - 1))], 1) if n else None

        # RSSI, hop limit esgotado e tabela por nó
        rd = self._data_rf_radio()
        rssi_avg = rd["rssi_avg"]
        rssi_color = ("" if rssi_avg is None else "green" if rssi_avg > -100
                      else "orange" if rssi_avg > -115 else "red")
        exh = rd["hop_exhausted_pct"]
        exh_color = ("" if exh is None else "green" if exh < self.HOP_EXHAUSTED_WARN
                     else "orange" if exh < self.HOP_EXHAUSTED_CRIT else "red")
        diag_tag = {"ok": "green", "noise": "orange", "weak": "red", "": "gray"}

        def node_row(r):
            nid, name, cnt, snr_v, rssi_v, hops_v, diag = r
            return (f"<tr><td>{nid}</td><td>{name}</td><td>{cnt}</td>"
                    f"<td>{'—' if snr_v is None else f'{snr_v:+.1f} dB'}</td>"
                    f"<td>{'—' if rssi_v is None else f'{rssi_v} dBm'}</td>"
                    f"<td>{'—' if hops_v is None else hops_v}</td>"
                    f"<td><span class='tag tag-{diag_tag[diag]}'>{rd['lbl_diag'][diag]}</span></td></tr>")
        rf_rows = "".join(node_row(r) for r in rd["node_rows"]) or \
            f"<tr><td colspan='7' class='no-data'>{tr('Sem dados ainda')}</td></tr>"

        body = f"""
<div class="subtitle" id="snr-n">{tr("Distribuição de SNR e hops · {n} amostras", n=len(self._snr_values))}</div>
<div class="card" style="margin-bottom:16px;border-left:4px solid #39d353;padding:8px 14px">
  <span style="color:#39d353;font-size:12px;font-weight:bold">{tr("🌐 Métrica da Rede")}</span><span style="color:#8b949e;font-size:11px"> {tr("network_metric_desc")}</span>
</div>
<div class="grid-3">
  <div class="card"><h3>{tr("SNR Médio")}</h3>
    <div id="snr-avg" class="kpi {'green' if snr_avg and snr_avg>=5 else 'orange' if snr_avg and snr_avg>=0 else 'red'}">{snr_avg if snr_avg is not None else '—'} dB</div></div>
  <div class="card"><h3>{tr("SNR Mediano")}</h3>
    <div id="snr-med" class="kpi">{snr_med if snr_med is not None else '—'} dB</div></div>
  <div class="card"><h3>{tr("SNR P10 (pior 10%)")}</h3>
    <div id="snr-p10" class="kpi red">{snr_p10 if snr_p10 is not None else '—'} dB</div></div>
</div>
<div class="grid-3">
  <div class="card"><h3>{tr("RSSI Médio")}</h3>
    <div id="rssi-avg" class="kpi {rssi_color}">{rssi_avg if rssi_avg is not None else '—'}{' dBm' if rssi_avg is not None else ''}</div>
    <div id="rssi-sub" class="kpi-sub">{tr("mediana")} {rd["rssi_med"] if rd["rssi_med"] is not None else '—'} dBm · {rd["n_rssi"]} {tr("amostras")}</div></div>
  <div class="card"><h3>{tr("Hop limit esgotado")}</h3>
    <div id="hop-exh" class="kpi {exh_color}">{exh if exh is not None else '—'}{'%' if exh is not None else ''}</div>
    <div id="hop-exh-sub" class="kpi-sub">{rd["lbl_exhausted_sub"]}<br>{tr("Acima de 5%: o hop limit pode ser curto para a rede")}</div></div>
  <div class="card"><h3>{tr("Amostras de rádio")}</h3>
    <div id="rf-n" class="kpi blue">{len(self._snr_values)}</div>
    <div id="rf-mqtt" class="kpi-sub">{rd["lbl_mqtt_excluded"]}<br>{tr("Só pacotes ouvidos pela rádio do nó local")}</div></div>
</div>
<div class="grid">
  <div class="card">
    <h3>{tr("Distribuição SNR (dB)")}</h3>
    <div class="chart-wrap"><canvas id="snrChart"></canvas></div>
  </div>
  <div class="card">
    <h3>{tr("Distribuição de Hops")}</h3>
    <div class="chart-wrap"><canvas id="hopsChart"></canvas></div>
  </div>
</div>
<div class="card" style="margin-top:16px">
  <h3>{tr("Por Nó — SNR × RSSI")}</h3>
  <div style="margin-bottom:6px;font-size:11px;color:#8b949e">{tr("rf_diag_hint")}</div>
  <table><tr><th>ID</th><th>{tr("Nome")}</th><th>{tr("Pacotes")}</th><th>SNR</th><th>RSSI</th><th>Hops</th><th>{tr("Diagnóstico")}</th></tr><tbody id="rf-node-tbody" data-filterable="1">{rf_rows}</tbody></table>
</div>
<div class="card" id="assessment-card" style="margin-top:16px;border-left:4px solid {
'#39d353' if snr_avg and snr_avg >= 5 else '#f0883e' if snr_avg and snr_avg >= 0 else '#f85149'
}">
  <h3>{tr("Avaliação da Qualidade RF")}</h3>
  <div id="rf-assessment" style="font-size:13px;line-height:1.7;color:#e6edf3">{self._rf_assessment(snr_avg, snr_med, snr_p10, self._hops_values)}</div>
</div>
<script>
window._snrChart = new Chart(document.getElementById('snrChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(snr_labels)},
    datasets: [{{ label: '{tr("Pacotes")}', data: {json.dumps(snr_counts)},
      backgroundColor: {json.dumps(
          ['rgba(248,81,73,0.7)' if i < 5 else 'rgba(240,136,62,0.7)' if i < 10 else 'rgba(57,211,83,0.7)'
           for i in range(len(snr_counts))])},
      borderRadius: 3 }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    scales: {{
      y: {{ grid: {{ color: '#21262d' }}, ticks: {{ color: '#8b949e' }} }},
      x: {{ grid: {{ color: '#21262d' }}, ticks: {{ color: '#8b949e' }},
             title: {{ display: true, text: 'SNR (dB)', color: '#8b949e' }} }}
    }},
    plugins: {{ legend: {{ display: false }} }}
  }}
}});
window._hopsChart = new Chart(document.getElementById('hopsChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(hop_labels)},
    datasets: [{{ label: '{tr("Pacotes")}', data: {json.dumps(hop_counts)},
      backgroundColor: 'rgba(88,166,255,0.7)', borderRadius: 3 }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    scales: {{
      y: {{ grid: {{ color: '#21262d' }}, ticks: {{ color: '#8b949e' }} }},
      x: {{ grid: {{ color: '#21262d' }}, ticks: {{ color: '#8b949e' }},
             title: {{ display: true, text: 'Hops', color: '#8b949e' }} }}
    }},
    plugins: {{ legend: {{ display: false }} }}
  }}
}});
</script>
<script>
window._metricsUpdateData = function(d) {{
  function set(id, v) {{ var e=document.getElementById(id); if(e) e.textContent=v; }}
  function setHtml(id, v) {{ var e=document.getElementById(id); if(e) e.innerHTML=v; }}
  set('snr-avg', d.snr_avg !== null ? d.snr_avg + ' dB' : '—');
  set('snr-med', d.snr_med !== null ? d.snr_med + ' dB' : '—');
  set('snr-p10', d.snr_p10 !== null ? d.snr_p10 + ' dB' : '—');
  set('snr-n', d.n + ' ' + (d.unit_amostras||'samples'));
  if(d.assessment) setHtml('rf-assessment', d.assessment);
  if(window._snrChart && d.snr_counts.length > 0) {{
    window._snrChart.data.labels = d.snr_labels;
    window._snrChart.data.datasets[0].data = d.snr_counts;
    window._snrChart.update('none');
  }}
  if(window._hopsChart && d.hop_counts.length > 0) {{
    window._hopsChart.data.labels = d.hop_labels;
    window._hopsChart.data.datasets[0].data = d.hop_counts;
    window._hopsChart.update('none');
  }}
  // RSSI, hop limit e tabela por nó
  function setClass(id, cls) {{ var e=document.getElementById(id); if(e) e.className='kpi '+cls; }}
  set('rssi-avg', d.rssi_avg !== null ? d.rssi_avg + ' dBm' : '—');
  setClass('rssi-avg', d.rssi_avg === null ? '' : d.rssi_avg > -100 ? 'green' : d.rssi_avg > -115 ? 'orange' : 'red');
  set('rssi-sub', (d.lbl_median||'median') + ' ' + (d.rssi_med !== null ? d.rssi_med : '—') + ' dBm · ' + d.n_rssi + ' ' + (d.unit_amostras||'samples'));
  set('hop-exh', d.hop_exhausted_pct !== null ? d.hop_exhausted_pct + '%' : '—');
  setClass('hop-exh', d.hop_exhausted_pct === null ? '' : d.hop_exhausted_pct < {self.HOP_EXHAUSTED_WARN} ? 'green' : d.hop_exhausted_pct < {self.HOP_EXHAUSTED_CRIT} ? 'orange' : 'red');
  set('rf-n', d.n);
  var mq=document.getElementById('rf-mqtt'); if(mq && mq.firstChild) mq.firstChild.textContent = d.lbl_mqtt_excluded;
  var ex=document.getElementById('hop-exh-sub'); if(ex && ex.firstChild) ex.firstChild.textContent = d.lbl_exhausted_sub;
  var tb=document.getElementById('rf-node-tbody');
  if(tb && d.node_rows && d.node_rows.length) {{
    var tag={{ok:'green',noise:'orange',weak:'red','':'gray'}}, h='';
    d.node_rows.forEach(function(r) {{
      var snr=r[3]===null?'—':(r[3]>0?'+':'')+r[3].toFixed(1)+' dB';
      var rssi=r[4]===null?'—':r[4]+' dBm';
      h+='<tr><td>'+r[0]+'</td><td>'+r[1]+'</td><td>'+r[2]+'</td><td>'+snr+'</td><td>'+rssi+'</td><td>'
        +(r[5]===null?'—':r[5])+'</td><td><span class="tag tag-'+tag[r[6]]+'">'+d.lbl_diag[r[6]]+'</span></td></tr>';
    }});
    tb.innerHTML=h; _applyFilter();
  }}
}};
var _currentFilter='';
function _applyFilter(){{
  var ft=_currentFilter;
  document.querySelectorAll('table tbody[data-filterable] tr').forEach(function(row){{
    var hay=Array.from(row.querySelectorAll('td')).map(function(c){{return c.textContent.toLowerCase();}}).join(' ');
    row.style.display=(!ft||hay.indexOf(ft)!==-1)?'':'none';
  }});
}}
window._metricsFilterTable=function(text){{_currentFilter=(text||'').toLowerCase().trim();_applyFilter();}};
</script>"""
        return self._base_html(tr("📶 Qualidade RF"), body)

    # ── 4. Tráfego ────────────────────────────────────────────────────────
    def _html_traffic(self) -> str:
        now = time.time()
        if not self._packets:
            body = f'<div class="no-data">{tr("⏳ Aguardando pacotes...")}</div>'
            return self._base_html(tr("📦 Tráfego de Rede"), body)

        label_map = {
            'TEXT_MESSAGE_APP':       tr('💬 Mensagem'),
            'NODEINFO_APP':           '🆔 NodeInfo',
            'POSITION_APP':           tr('📍 Posição'),
            'TELEMETRY_APP':          tr('📊 Telemetria'),
            'TRACEROUTE_APP':         '🔍 Traceroute',
            'ROUTING_APP':            '🔀 Routing',
            'NEIGHBORINFO_APP':       '🔗 NeighborInfo',
            'ADMIN_APP':              '⚙ Admin',
            'RANGE_TEST_APP':         '📏 Range Test',
            'STORE_AND_FORWARD_APP':  '📦 S&F',
        }
        bar_colors = ['#58a6ff','#39d353','#f0883e','#bc8cff','#f85149',
                      '#56d364','#ffa657','#79c0ff','#d2a8ff','#ff7b72']

        # ── Sessão inteira — barras ────────────────────────────────────
        counts = {}
        for pname, cnt in self._portnum_counts.items():
            lbl = label_map.get(pname, pname.replace('_APP','').replace('_',' ').title())
            counts[lbl] = counts.get(lbl, 0) + cnt
        sc     = sorted(counts.items(), key=lambda x: -x[1])
        labels = [k for k, _ in sc]
        values = [v for _, v in sc]

        # ── RF vs MQTT ─────────────────────────────────────────────────
        n_rf   = len([p for p in self._packets if not p[5]])
        n_mqtt = len([p for p in self._packets if p[5]])
        total  = n_rf + n_mqtt
        rf_pct   = round(n_rf   / total * 100) if total else 0
        mqtt_pct = round(n_mqtt / total * 100) if total else 0

        # ── Padrão de routing ──────────────────────────────────────────
        n_direct  = sum(1 for p in self._packets if p[4] == 0)
        n_1hop    = sum(1 for p in self._packets if p[4] == 1)
        n_multi   = sum(1 for p in self._packets if p[4] is not None and p[4] >= 2)
        n_unknown = sum(1 for p in self._packets if p[4] is None)
        total_hop = n_direct + n_1hop + n_multi + n_unknown
        d_pct = round(n_direct  / total_hop * 100) if total_hop else 0
        h_pct = round(n_1hop    / total_hop * 100) if total_hop else 0
        m_pct = round(n_multi   / total_hop * 100) if total_hop else 0
        u_pct = round(n_unknown / total_hop * 100) if total_hop else 0
        routing_vals   = [n_direct, n_1hop, n_multi, n_unknown] if total_hop else [1,0,0,0]
        routing_labels = [tr('🟢 Directo'), tr('🔵 1 Hop'), tr('🟠 Multi-hop'), tr('⚫ Desconhecido')]
        routing_colors = ['#39d353', '#58a6ff', '#f0883e', '#8b949e']

        # ── Série temporal ─────────────────────────────────────────────
        bins_60 = []
        for i in range(29, -1, -1):
            t0 = now - (i + 1) * 60
            t1 = now - i * 60
            cnt = len([p for p in self._packets if t0 <= p[0] < t1])
            bins_60.append((datetime.fromtimestamp(t1).strftime('%H:%M'), cnt))
        ppm_labels = [b[0] for b in bins_60]
        ppm_vals   = [b[1] for b in bins_60]

        body = f"""
<div class="subtitle">{tr("Distribuição de tráfego da sessão")}</div>

<!-- Linha 1: dois donuts em cima -->
<div class="grid">
  <div class="card">
    <h3>RF vs MQTT</h3>
    <div style="display:flex;gap:16px;align-items:center;min-height:160px">
      <canvas id="rfChart" width="150" height="150" style="flex-shrink:0"></canvas>
      <div style="font-size:12px;color:#8b949e;line-height:2.2">
        <div><span style="color:#2b7cd3;font-size:15px">■</span> RF &nbsp;<b style="color:#e6edf3">{n_rf}</b> ({rf_pct}%)</div>
        <div><span style="color:#f0883e;font-size:15px">■</span> MQTT &nbsp;<b style="color:#e6edf3">{n_mqtt}</b> ({mqtt_pct}%)</div>
      </div>
    </div>
  </div>
  <div class="card">
    <h3>{tr("Padrão de Routing")}</h3>
    <div style="display:flex;gap:16px;align-items:center;min-height:160px">
      <canvas id="routingChart" width="150" height="150" style="flex-shrink:0"></canvas>
      <div style="font-size:12px;color:#8b949e;line-height:2.2">
        <div id="rd-direct"><span style="color:#39d353;font-size:15px">■</span> {tr('🟢 Directo')} &nbsp;<b style="color:#e6edf3">{n_direct}</b> ({d_pct}%)</div>
        <div id="rd-1hop"><span style="color:#58a6ff;font-size:15px">■</span> {tr('🔵 1 Hop')} &nbsp;<b style="color:#e6edf3">{n_1hop}</b> ({h_pct}%)</div>
        <div id="rd-multi"><span style="color:#f0883e;font-size:15px">■</span> {tr('🟠 Multi-hop')} ≥2 &nbsp;<b style="color:#e6edf3">{n_multi}</b> ({m_pct}%)</div>
        <div id="rd-unknown"><span style="color:#8b949e;font-size:15px">■</span> {tr('⚫ Desconhecido')} &nbsp;<b style="color:#e6edf3">{n_unknown}</b> ({u_pct}%)</div>
      </div>
    </div>
  </div>
</div>

<!-- Linha 2: barras por tipo -->
<div class="card" style="margin-top:16px">
  <h3>{tr("Pacotes por Tipo — Sessão")}</h3>
  <div class="chart-wrap"><canvas id="typeChart"></canvas></div>
</div>

<!-- Linha 3: PPM -->
<div class="card" style="margin-top:16px">
  <h3>{tr("Pacotes por Minuto (últimos 30 min)")}</h3>
  <div class="chart-wrap-lg"><canvas id="ppmChart"></canvas></div>
</div>

<script>
window._rfChart = new Chart(document.getElementById('rfChart'), {{
  type: 'doughnut',
  data: {{
    labels: ['RF', 'MQTT'],
    datasets: [{{ data: [{n_rf}, {n_mqtt}],
      backgroundColor: ['#2b7cd3','#f0883e'], borderWidth: 0 }}]
  }},
  options: {{
    responsive: false,
    cutout: '60%',
    plugins: {{ legend: {{ display: false }} }}
  }}
}});
window._routingChart = new Chart(document.getElementById('routingChart'), {{
  type: 'doughnut',
  data: {{
    labels: {json.dumps(routing_labels)},
    datasets: [{{ data: {json.dumps(routing_vals)},
      backgroundColor: {json.dumps(routing_colors)}, borderWidth: 0 }}]
  }},
  options: {{
    responsive: false,
    cutout: '60%',
    plugins: {{ legend: {{ display: false }} }}
  }}
}});
window._typeChart = new Chart(document.getElementById('typeChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(labels)},
    datasets: [{{ data: {json.dumps(values)},
      backgroundColor: {json.dumps(bar_colors[:len(values)])}, borderRadius: 3 }}]
  }},
  options: {{
    indexAxis: 'y', responsive: true, maintainAspectRatio: false,
    scales: {{
      x: {{ grid: {{ color: '#21262d' }}, ticks: {{ color: '#8b949e' }} }},
      y: {{ grid: {{ display: false }}, ticks: {{ color: '#e6edf3', font: {{ size: 11 }} }} }}
    }},
    plugins: {{ legend: {{ display: false }} }}
  }}
}});
window._ppmChart = new Chart(document.getElementById('ppmChart'), {{
  type: 'line',
  data: {{
    labels: {json.dumps(ppm_labels)},
    datasets: [{{ label: '{tr("Pacotes/min")}', data: {json.dumps(ppm_vals)},
      borderColor: '#58a6ff', backgroundColor: 'rgba(88,166,255,0.1)',
      fill: true, tension: 0.3, pointRadius: 1 }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    scales: {{
      y: {{ min: 0, grid: {{ color: '#21262d' }}, ticks: {{ color: '#8b949e' }} }},
      x: {{ grid: {{ color: '#21262d' }}, ticks: {{ color: '#8b949e', maxTicksLimit: 10 }} }}
    }},
    plugins: {{ legend: {{ labels: {{ color: '#8b949e' }} }} }}
  }}
}});
</script>
<script>
window._metricsUpdateData = function(d) {{
  function setHtml(id, v) {{ var e=document.getElementById(id); if(e) e.innerHTML=v; }}
  // RF vs MQTT donut
  if(window._rfChart) {{
    var rfSum = d.n_rf + d.n_mqtt;
    if(rfSum > 0) {{
      window._rfChart.data.datasets[0].data = [d.n_rf, d.n_mqtt];
      window._rfChart.update('none');
    }}
  }}
  // Routing donut + legendas
  if(window._routingChart) {{
    var rs = d.n_direct + d.n_1hop + d.n_multi + d.n_unknown;
    if(rs > 0) {{
      window._routingChart.data.datasets[0].data = [d.n_direct, d.n_1hop, d.n_multi, d.n_unknown];
      window._routingChart.update('none');
    }}
    var tot = rs || 1;
    setHtml('rd-direct',  '<span style="color:#39d353;font-size:15px">■</span> '+d.lbl_direct+' &nbsp;<b style="color:#e6edf3">' + d.n_direct + '</b> (' + Math.round(d.n_direct/tot*100) + '%)');
    setHtml('rd-1hop',    '<span style="color:#58a6ff;font-size:15px">■</span> '+(d.lbl_1hop||'1 Hop')+' &nbsp;<b style="color:#e6edf3">' + d.n_1hop + '</b> (' + Math.round(d.n_1hop/tot*100) + '%)');
    setHtml('rd-multi',   '<span style="color:#f0883e;font-size:15px">■</span> '+(d.lbl_multi||'Multi-hop')+' ≥2 &nbsp;<b style="color:#e6edf3">' + d.n_multi + '</b> (' + Math.round(d.n_multi/tot*100) + '%)');
    setHtml('rd-unknown', '<span style="color:#8b949e;font-size:15px">■</span> '+d.lbl_unknown+' &nbsp;<b style="color:#e6edf3">' + d.n_unknown + '</b> (' + Math.round(d.n_unknown/tot*100) + '%)');
  }}
  // Barras por tipo
  if(window._typeChart && d.labels && d.labels.length > 0) {{
    window._typeChart.data.labels = d.labels;
    window._typeChart.data.datasets[0].data = d.values;
    window._typeChart.update('none');
  }}
  // PPM
  if(window._ppmChart) {{
    window._ppmChart.data.labels = d.ppm_labels;
    window._ppmChart.data.datasets[0].data = d.ppm_vals;
    window._ppmChart.update('none');
  }}
}};
</script>"""
        return self._base_html(tr("📦 Tráfego de Rede"), body)

    # ── 5. Nós & Bateria ──────────────────────────────────────────────────
    def _html_nodes(self) -> str:
        now      = time.time()
        cutoff2h = now - 7200

        active_nids = set(p[1] for p in self._packets if p[0] >= cutoff2h and p[1])
        n_active    = len(active_nids)

        # batteryLevel=101 → alimentação externa (Powered); 0 → sem dados
        batt_real  = {nid: v for nid, v in self._battery.items() if 1 <= v <= 100}
        batt_power = [nid for nid, v in self._battery.items() if v == 101]
        n_powered  = len(batt_power)
        n_battery  = len(batt_real)
        batt_avg   = round(sum(batt_real.values()) / len(batt_real), 0) if batt_real else None

        ts_labels = [self._ts_label(t) for t, _ in self._nodes_active_ts]
        ts_vals   = [v for _, v in self._nodes_active_ts]

        batt_bucket_labels = ['0–20%', '21–40%', '41–60%', '61–80%', '81–100%']
        batt_counts        = [0, 0, 0, 0, 0]
        for v in batt_real.values():
            batt_counts[min(int(v // 20), 4)] += 1

        # Hardware model distribution
        hw_model = self._hw_model
        hw_counts: dict = {}
        for hw in hw_model.values():
            hw_counts[hw] = hw_counts.get(hw, 0) + 1
        hw_sorted = sorted(hw_counts.items(), key=lambda x: -x[1])[:10]
        hw_labels = [h for h, _ in hw_sorted]
        hw_values = [c for _, c in hw_sorted]

        # Nós únicos com GPS — FIX: usa _node_pos (igual ao _data_nodes)
        n_gps_unique = len(self._node_pos)

        # Tabela de nós com bateria (tensão e uptime incluídos)
        batt_rows = ""
        for nid, batt in sorted(self._battery.items(), key=lambda x: x[1]):
            if batt == 101:
                disp = "<span class='tag tag-blue'>⚡ Powered</span>"
                bar  = ""
            elif batt == 0:
                disp = "<span class='tag tag-gray'>—</span>"
                bar  = ""
            else:
                color = "green" if batt > 60 else ("orange" if batt > 20 else "red")
                bar_c = {"green": "#39d353", "orange": "#f0883e", "red": "#f85149"}[color]
                disp  = f"<span class='tag tag-{color}'>{batt}%</span>"
                bar   = (f"<div class='bar-bg' style='margin-top:3px'>"
                         f"<div class='bar-fill' style='width:{batt}%;background:{bar_c}'>"
                         f"</div></div>")
            volt = self._voltage.get(nid)
            volt_str = f"{volt:.3f}V" if volt else "—"
            uptm = self._uptime.get(nid)
            if uptm:
                h, m = divmod(uptm // 60, 60)
                d2, h = divmod(h, 24)
                uptm_str = f"{d2}d{h:02d}h" if d2 else f"{h:02d}h{m:02d}m"
            else:
                uptm_str = "—"
            batt_rows += (
                f"<tr><td>{nid}</td><td>{self._name(nid)}</td><td>{disp}{bar}</td>"
                f"<td style='color:#8b949e'>{volt_str}</td>"
                f"<td style='color:#8b949e'>{uptm_str}</td></tr>"
            )
        if not batt_rows:
            batt_rows = f"<tr><td colspan='5' class='no-data'>{tr('Sem dados de bateria ainda')}</td></tr>"

        hw_chart_js = ""
        if hw_labels:
            hw_chart_js = f"""
window._hwChart = new Chart(document.getElementById('hwChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(hw_labels)},
    datasets: [{{ data: {json.dumps(hw_values)},
      backgroundColor: '#58a6ff', borderRadius: 3 }}]
  }},
  options: {{
    indexAxis: 'y', responsive: true, maintainAspectRatio: false,
    scales: {{
      x: {{ grid: {{ color: '#21262d' }}, ticks: {{ color: '#8b949e' }} }},
      y: {{ grid: {{ display: false }}, ticks: {{ color: '#8b949e' }} }}
    }},
    plugins: {{ legend: {{ display: false }} }}
  }}
}});"""

        _hw_card = (f'<div class="card" style="margin-top:16px"><h3>{tr("Hardware por Modelo ({n} nós)", n=len(hw_model))}</h3><div class="chart-wrap-lg"><canvas id="hwChart"></canvas></div></div>' if hw_labels else '')
        body = f"""
<div class="subtitle">{tr("Saúde dos nós, baterias e hardware")}</div>
<div class="card" style="margin-bottom:16px;border-left:4px solid #39d353;padding:8px 14px">
  <span style="color:#39d353;font-size:12px;font-weight:bold">{tr("🌐 Métrica da Rede")}</span><span style="color:#8b949e;font-size:11px"> {tr("network_metric_desc")}</span>
</div>
<div class="grid-3">
  <div class="card"><h3>{tr("Nós Activos (2h)")}</h3>
    <div class="kpi green" id="nodes-active">{n_active}</div></div>
  <div class="card"><h3>{tr("Bateria / Powered")}</h3>
    <div class="kpi blue" id="nodes-batt-count">{n_battery}</div>
    <div class="kpi-sub" id="nodes-powered">{tr("{n} com alimentação externa · 📍 {m} com GPS", n=n_powered, m=n_gps_unique)}</div></div>
  <div class="card"><h3>{tr("Bateria Média")}</h3>
    <div class="kpi {'green' if batt_avg and batt_avg>60 else 'orange'}" id="nodes-batt-avg">
      {f'{batt_avg:.0f}%' if batt_avg is not None else '—'}</div></div>
</div>
<div class="grid" style="margin-top:16px">
  <div class="card">
    <h3>{tr("Nós Activos ao Longo do Tempo")}</h3>
    <div class="chart-wrap"><canvas id="nodesChart"></canvas></div>
  </div>
  <div class="card">
    <h3>{tr("Distribuição de Bateria")}</h3>
    <div class="chart-wrap"><canvas id="battDistChart"></canvas></div>
  </div>
</div>
{_hw_card}
<div class="card" style="margin-top:16px">
  <h3>{tr("Bateria por Nó")}</h3>
  <table><tr><th>ID</th><th>{tr("Nome")}</th><th>{tr("Bateria")}</th><th>{tr("Tensão")}</th><th>Uptime</th></tr>{batt_rows}</table>
</div>
<script>
window._nodesChart = new Chart(document.getElementById('nodesChart'), {{
  type: 'line',
  data: {{
    labels: {json.dumps(ts_labels)},
    datasets: [{{ label: '{tr("Nós activos")}', data: {json.dumps(ts_vals)},
      borderColor: '#39d353', backgroundColor: 'rgba(57,211,83,0.1)',
      fill: true, tension: 0.3, pointRadius: 2 }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    scales: {{
      y: {{ min: 0, grid: {{ color: '#21262d' }}, ticks: {{ color: '#8b949e' }} }},
      x: {{ grid: {{ color: '#21262d' }}, ticks: {{ color: '#8b949e', maxTicksLimit: 8 }} }}
    }},
    plugins: {{ legend: {{ labels: {{ color: '#8b949e' }} }} }}
  }}
}});
window._battDistChart = new Chart(document.getElementById('battDistChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(batt_bucket_labels)},
    datasets: [{{ data: {json.dumps(batt_counts)},
      backgroundColor: ['#f85149','#f0883e','#ffa657','#56d364','#39d353'],
      borderRadius: 3 }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    scales: {{
      y: {{ grid: {{ color: '#21262d' }}, ticks: {{ color: '#8b949e' }} }},
      x: {{ grid: {{ display: false }}, ticks: {{ color: '#8b949e' }} }}
    }},
    plugins: {{ legend: {{ display: false }} }}
  }}
}});
{hw_chart_js}
</script>
<script>
window._metricsUpdateData = function(d) {{
  function set(id, v) {{ var e=document.getElementById(id); if(e) e.textContent=v; }}
  set('nodes-active',    d.n_active);
  set('nodes-batt-count', d.n_battery);
  set('nodes-batt-avg',  d.batt_avg !== null ? d.batt_avg + '%' : '—');
  set('nodes-powered',   d.lbl_powered || ('⚡ ' + (d.n_powered||0) + ' · 📍 ' + (d.n_gps_unique||0)));
  if(window._nodesChart && d.ts_vals && d.ts_vals.length > 0) {{
    window._nodesChart.data.labels = d.ts_labels;
    window._nodesChart.data.datasets[0].data = d.ts_vals;
    window._nodesChart.update('none');
  }}
  if(window._battDistChart && d.batt_buckets) {{
    window._battDistChart.data.datasets[0].data = d.batt_buckets;
    window._battDistChart.update('none');
  }}
}};
window._metricsFilterTable = function(text) {{
  var ft = (text || '').toLowerCase().trim();
  document.querySelectorAll('table tbody[data-filterable]').forEach(function(tbody) {{
    var found = 0;
    tbody.querySelectorAll('tr:not(.filter-no-match)').forEach(function(row) {{
      var hay = Array.from(row.querySelectorAll('td')).map(function(c){{ return c.textContent.toLowerCase(); }}).join(' ');
      var ok = !ft || hay.indexOf(ft) !== -1;
      row.style.display = ok ? '' : 'none';
      if (ok) found++;
    }});
    var noRow = tbody.querySelector('.filter-no-match');
    if (!noRow) {{
      noRow = document.createElement('tr'); noRow.className = 'filter-no-match';
      noRow.innerHTML = '<td colspan="99" style="color:#f0883e;padding:8px;text-align:center;font-size:12px">🔍 ' + (ft ? '{tr("metrics_no_results")}' + ' &quot;' + ft + '&quot;' : '') + '</td>';
      tbody.appendChild(noRow);
    }}
    noRow.style.display = (ft && found === 0) ? '' : 'none';
  }});
}};
</script>"""
        return self._base_html(tr("🔋 Nós & Bateria"), body)

    # ── 6. Fiabilidade ────────────────────────────────────────────────────
    def _html_environment(self) -> str:
        import html as _html
        d = self._data_environment()
        if not d["n_env"] and not d["n_power"]:
            body = (f'<div class="no-data">{tr("⏳ Ainda nenhum nó enviou telemetria de ambiente ou energia.")}<br><br>'
                    f'<span style="font-size:12px">{tr("environment_hint")}</span></div>')
            return self._base_html(tr("🌡 Ambiente & Energia"), body)

        esc = lambda v: _html.escape(str(v))

        def kpi(val, label, color, kid, sub="", sub_id=""):
            sid = f' id="{sub_id}"' if sub_id else ""
            return (f'<div class="card"><h3>{label}</h3><div id="{kid}" class="kpi {color}">{val}</div>'
                    f'<div{sid} class="kpi-sub">{sub}</div></div>')

        tmax, tmin = d["temp_max"], d["temp_min"]
        head = "".join(f"<th>{esc(label)}</th>" for label, _u in d["cols"])

        def env_row(r):
            nid, name, cells, ago, stale = r
            style = " style='opacity:.55'" if stale else ""
            return (f"<tr{style}><td>{esc(nid)}</td><td>{esc(name)}</td>"
                    + "".join(f"<td>{'—' if c is None else esc(c)}</td>" for c in cells)
                    + f"<td style='color:#8b949e'>{esc(ago)}</td></tr>")
        env_html = "".join(env_row(r) for r in d["env_rows"]) or \
            f"<tr><td colspan='99' class='no-data'>{tr('Sem dados ainda')}</td></tr>"
        power_html = "".join(
            f"<tr><td>{esc(nid)}</td><td>{esc(name)}</td><td>{esc(' · '.join(parts))}</td>"
            f"<td style='color:#8b949e'>{esc(ago)}</td></tr>"
            for nid, name, parts, ago in d["power_rows"])

        power_card = f"""
<div class="card" style="margin-top:16px">
  <h3>{tr("Energia (sensores INA)")}</h3>
  <table><tr><th>ID</th><th>{tr("Nome")}</th><th>{tr("Canais")}</th><th>{tr("Actualizado")}</th></tr>
  <tbody id="power-tbody" data-filterable="1">{power_html}</tbody></table>
</div>""" if d["power_rows"] else ""

        chart_card = f"""
<div class="card" style="margin-top:16px">
  <h3>{tr("Temperatura ao longo do tempo")}</h3>
  <div class="chart-wrap-lg"><canvas id="tempChart"></canvas></div>
</div>""" if d["temp_series"] else ""

        body = f"""
<div class="subtitle">{tr("Sensores de ambiente, qualidade do ar e energia dos nós")}</div>
<div class="grid-3">
  {kpi(d["n_env"], tr("Nós com sensores"), "blue", "env-n", tr("{n} com medição de energia", n=d["n_power"]), "env-n-sub")}
  {kpi("—" if tmax is None else f"{tmax[1]} °C", tr("Mais quente"), "orange", "env-max", "" if tmax is None else esc(tmax[0]), "env-max-sub")}
  {kpi("—" if tmin is None else f"{tmin[1]} °C", tr("Mais frio"), "blue", "env-min", "" if tmin is None else esc(tmin[0]) + (f" · {tr('média')} {d['temp_avg']} °C" if d['temp_avg'] is not None else ""), "env-min-sub")}
</div>
<div class="card" style="margin-top:16px">
  <h3>{tr("Últimas leituras por nó")}</h3>
  <div style="margin-bottom:6px;font-size:11px;color:#8b949e">{tr("metrics_filter_hint")} · {tr("Leituras com mais de 2 h aparecem esbatidas.")}</div>
  <table><tr><th>ID</th><th>{tr("Nome")}</th>{head}<th>{tr("Actualizado")}</th></tr>
  <tbody id="env-tbody" data-filterable="1">{env_html}</tbody></table>
</div>
{power_card}
{chart_card}
<script>
var _palette=['#f0883e','#58a6ff','#39d353','#bc8cff','#f85149','#e3b341'];
function _hm(v){{ var t=new Date(v); return String(t.getHours()).padStart(2,'0')+':'+String(t.getMinutes()).padStart(2,'0'); }}
function _datasets(series){{ return series.map(function(s,i){{ return {{ label:s.name, data:s.points,
  borderColor:_palette[i%6], backgroundColor:_palette[i%6], tension:0.3, pointRadius:2, fill:false }}; }}); }}
if(document.getElementById('tempChart')) {{
  window._tempChart = new Chart(document.getElementById('tempChart'), {{
    type:'line', data:{{ datasets:_datasets({json.dumps(d["temp_series"])}) }},
    options:{{ responsive:true, maintainAspectRatio:false, parsing:false,
      scales:{{ x:{{ type:'linear', grid:{{color:'#21262d'}}, ticks:{{color:'#8b949e', callback:_hm, maxTicksLimit:8}} }},
                y:{{ grid:{{color:'#21262d'}}, ticks:{{color:'#8b949e', callback:function(v){{return v+' °C';}}}} }} }},
      plugins:{{ legend:{{ labels:{{ color:'#8b949e', boxWidth:12 }} }},
                 tooltip:{{ callbacks:{{ title:function(c){{return _hm(c[0].parsed.x);}} }} }} }} }}
  }});
}}
var _currentFilter='';
function _applyFilter(){{
  var ft=_currentFilter;
  document.querySelectorAll('table tbody[data-filterable] tr').forEach(function(row){{
    var hay=Array.from(row.querySelectorAll('td')).map(function(c){{return c.textContent.toLowerCase();}}).join(' ');
    row.style.display=(!ft||hay.indexOf(ft)!==-1)?'':'none';
  }});
}}
window._metricsFilterTable=function(text){{_currentFilter=(text||'').toLowerCase().trim();_applyFilter();}};
function _esc(v){{ return String(v===null||v===undefined?'':v).replace(/[&<>"]/g,function(c){{return {{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c];}}); }}
window._metricsUpdateData=function(d){{
  function set(id,v){{ var e=document.getElementById(id); if(e) e.textContent=v; }}
  set('env-n', d.n_env);
  if(d.temp_max){{ set('env-max', d.temp_max[1]+' °C'); set('env-max-sub', d.temp_max[0]); }}
  if(d.temp_min){{ set('env-min', d.temp_min[1]+' °C'); }}
  var tb=document.getElementById('env-tbody');
  // Só se as colunas não mudaram; se apareceu um sensor novo, a página é recarregada abaixo.
  if(tb && d.env_rows.length && d.cols.length === {len(d["cols"])}){{
    tb.innerHTML=d.env_rows.map(function(r){{
      return '<tr'+(r[4]?' style="opacity:.55"':'')+'><td>'+_esc(r[0])+'</td><td>'+_esc(r[1])+'</td>'
        + r[2].map(function(c){{return '<td>'+(c===null?'—':_esc(c))+'</td>';}}).join('')
        + '<td style="color:#8b949e">'+_esc(r[3])+'</td></tr>'; }}).join('');
  }} else if(d.cols.length !== {len(d["cols"])}) {{ location.reload(); return; }}
  var pb=document.getElementById('power-tbody');
  if(pb && d.power_rows.length){{
    pb.innerHTML=d.power_rows.map(function(r){{ return '<tr><td>'+_esc(r[0])+'</td><td>'+_esc(r[1])+'</td><td>'
      +_esc(r[2].join(' · '))+'</td><td style="color:#8b949e">'+_esc(r[3])+'</td></tr>'; }}).join('');
  }}
  if(window._tempChart && d.temp_series.length){{ window._tempChart.data.datasets=_datasets(d.temp_series); window._tempChart.update('none'); }}
  _applyFilter();
}};
</script>"""
        return self._base_html(tr("🌡 Ambiente & Energia"), body)

    def _html_latency(self) -> str:
        d = self._data_latency()
        if d["n"] == 0:
            body = (f'<div class="no-data">{tr("⏳ Sem dados de latência ainda.")}<br><br>'
                    f'{tr("Envie mensagens com wantAck=True para medir o RTT")}<br>'
                    f'({tr("(tempo entre envio e ACK do destinatário).")})</div>')
            return self._base_html(tr("⏱ Latência (RTT)"), body)

        def kpi(val, unit, label, color="", kid="", note=""):
            v = f"{val}{unit}" if val is not None else "—"
            id_attr = f' id="{kid}"' if kid else ""
            note_html = f'<div class="kpi-sub" style="font-size:10px">{note}</div>' if note else ""
            return f'<div class="card"><h3>{label}</h3><div{id_attr} class="kpi {color}">{v}</div>{note_html}</div>'

        avg_color = ("green" if d["avg"] and d["avg"] < 10
                     else "orange" if d["avg"] and d["avg"] < 30 else "red")

        body = f"""
<div class="subtitle" id="rtt-n">{tr("RTT (Round-Trip Time) — tempo entre envio e ACK · {n} amostras", n=d['n'])}</div>
<div class="card" style="margin-bottom:16px;border-left:4px solid #f0883e;padding:8px 14px">
  <span style="color:#f0883e;font-size:12px;font-weight:bold">{tr("🏠 Métrica do Nó Local")}</span><span style="color:#8b949e;font-size:11px"> {tr("local_metric_desc")}</span>
</div>
<div class="grid-3">
  <div class="card"><h3>{tr('RTT Médio')}</h3><div id="rtt-avg" class="kpi {avg_color}">{d['avg']}s</div></div>
  <div class="card"><h3>{tr('RTT Mediana')}</h3><div id="rtt-med" class="kpi">{d['med']}s</div></div>
  <div class="card"><h3>{tr('RTT P90 (pior 10%)')}</h3><div id="rtt-p90" class="kpi orange">{d['p90']}s</div></div>
</div>
<div class="grid">
  <div class="card"><h3>{tr('RTT Mínimo')}</h3><div id="rtt-min" class="kpi green">{d['min']}s</div></div>
  <div class="card"><h3>{tr('RTT Máximo')}</h3><div id="rtt-max" class="kpi">{d['max']}s</div></div>
</div>
<div class="card" style="margin-top:16px">
  <h3>{tr("Distribuição de RTT")}</h3>
  <div class="chart-wrap-lg"><canvas id="rttChart"></canvas></div>
</div>
<div class="card" style="margin-top:16px">
  <h3>{tr("Interpretação")}</h3>
  <p style="color:#8b949e;font-size:12px;line-height:1.8">
    <b style="color:#e6edf3">RTT &lt; 5s:</b> {tr("RTT < 5s: Ligação directa excelente (0 hops).")}<br>
    <b style="color:#e6edf3">RTT 5–15s:</b> {tr("RTT 5–15s: Normal para 1–2 hops em LoRa.")}<br>
    <b style="color:#e6edf3">RTT 15–30s:</b> {tr("RTT 15–30s: Possível congestão ou 3+ hops.")}<br>
    <b style="color:#e6edf3">RTT &gt; 30s:</b> {tr("RTT > 30s: Rede congestionada ou rota longa.")}<br>
  </p>
</div>
<script>
window._rttMsgLabel = '{tr("Nº de mensagens")}';
window._rttChart = new Chart(document.getElementById('rttChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(d['hist_labels'])},
    datasets: [{{ label: '{tr("Nº de mensagens")}', data: {json.dumps(d['hist_counts'])},
      backgroundColor: '#58a6ff', borderRadius: 3 }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    scales: {{
      y: {{ min: 0, grid: {{ color: '#21262d' }}, ticks: {{ color: '#8b949e' }} }},
      x: {{ grid: {{ display: false }}, ticks: {{ color: '#8b949e' }} }}
    }},
    plugins: {{ legend: {{ display: false }},
                tooltip: {{ callbacks: {{ label: ctx => ctx.parsed.y + ' ' + (window._rttMsgLabel||'messages') }} }} }}
  }}
}});
</script>
<script>
window._metricsUpdateData = function(d) {{
  if (!d || d.n === undefined) return;
  function set(id, v) {{ var e=document.getElementById(id); if(e) e.textContent=v; }}
  function setClass(id, cls) {{ var e=document.getElementById(id); if(e) e.className='kpi '+cls; }}
  if (d.n === 0) return;
  set('rtt-avg', d.avg !== null ? d.avg + 's' : '—');
  set('rtt-med', d.med !== null ? d.med + 's' : '—');
  set('rtt-p90', d.p90 !== null ? d.p90 + 's' : '—');
  set('rtt-min', d.min !== null ? d.min + 's' : '—');
  set('rtt-max', d.max !== null ? d.max + 's' : '—');
  set('rtt-n',   d.n + ' ' + (d.unit_amostras || 'amostras'));
  var avgColor = d.avg === null ? '' : d.avg < 10 ? 'green' : d.avg < 30 ? 'orange' : 'red';
  setClass('rtt-avg', avgColor);
  if (window._rttChart && d.hist_counts && d.hist_counts.length > 0) {{
    window._rttChart.data.labels   = d.hist_labels;
    window._rttChart.data.datasets[0].data = d.hist_counts;
    window._rttChart.update('none');
  }}
}};
</script>"""
        return self._base_html(tr("⏱ Latência (RTT)"), body)

    def _html_reliability(self) -> str:
        # Fonte única de dados — elimina todos os acessos directos a atributos
        # removidos (_duplicates, _routing_acks/_naks).
        d = self._data_reliability()

        net_nak_rate    = d['net_nak_rate']
        p_col           = d['p_col']
        total_pkt       = d['total_pkt']
        active_senders  = d['active_senders']
        sent            = d['sent']
        acked           = d['acked']
        naked           = d['naked']
        ack_impl        = d['ack_implicit']
        pending         = d['pending']
        delivery        = d['delivery']
        nak_rate        = d['nak_rate']
        net_acks        = d['net_acks']
        net_naks        = d['net_naks']
        fw_errs         = d['fw_errs']

        nak_net_color = ("" if net_nak_rate is None else
                         "green" if net_nak_rate < 5 else
                         "orange" if net_nak_rate < 20 else "red")

        col_color = ("" if p_col is None else
                     "green"  if p_col < 5  else
                     "orange" if p_col < 15 else "red")
        col_label = (tr("Sem dados de Ch.Util.") if p_col is None else
                     tr("✅ Baixo")               if p_col < 5  else
                     tr("⚠ Próximo do limite")   if p_col < 15 else
                     tr("[!] Risco elevado"))

        dr_color = ("green"  if delivery and delivery >= 90 else
                    "orange" if delivery and delivery >= 70 else
                    "red"    if delivery else "")

        pie_net   = [net_acks, net_naks] if (net_acks or net_naks) else [1, 0]
        pie_local = [acked, naked, ack_impl, pending] if any([acked, naked, ack_impl, pending]) else [1, 0, 0, 0]

        no_net_data   = ("" if d['ever_seen'] else
                         f'<div class="no-data" style="margin-bottom:12px">{tr("⏳ Aguardando pacotes ROUTING_APP na rede…")}</div>')
        no_local_data = ("" if sent > 0 else
                         f'<div style="color:#8b949e;font-size:11px;margin-bottom:8px">{tr("⏳ Envie mensagens para ver métricas do nó local.")}</div>')

        body = f"""
<div class="subtitle">{tr("Fiabilidade da rede Meshtastic — observação passiva + nó local")}</div>

<h3 style="color:#58a6ff;font-size:13px;margin:0 0 10px 0">{tr("🌐 Fiabilidade da Rede (todos os nós)")}</h3>
{no_net_data}
<div class="grid" style="margin-bottom:12px">
  <div class="card">
    <h3>{tr("Pacotes únicos (5 min)")}</h3>
    <div id="rel-pkt" class="kpi blue">{total_pkt}</div>
    <div id="rel-pkt-sub" class="kpi-sub">{d['lbl_pkt_sub']}</div>
  </div>
  <div class="card">
    <h3>{tr("Colisões Estimadas (CAD)")}</h3>
    <div id="rel-col" class="kpi {col_color}">{p_col if p_col is not None else '—'}{'%' if p_col is not None else ''}</div>
    <div id="rel-col-label" class="kpi-sub">{col_label}</div>
  </div>
</div>
<div class="grid">
  <div class="card">
    <h3>{tr("NAK da Rede (ROUTING_APP)")}</h3>
    <div id="rel-net-nak" class="kpi {nak_net_color}">{net_nak_rate if net_nak_rate is not None else '—'}{'%' if net_nak_rate is not None else ''}</div>
    <div id="rel-net-sub" class="kpi-sub">ACK: {net_acks} · NAK entrega: {net_naks} · Erros FW: {fw_errs}<br>{tr("NAK = requestId + errorReason · Erros FW = sem requestId")}</div>
  </div>
  <div class="card">
    <h3>{tr("Duplicados na rede")}</h3>
    <div id="rel-ls-dupe" class="kpi {'' if d['ls_dupe_pct'] is None else 'green' if d['ls_dupe_pct'] < self.DUPE_WARN else 'orange' if d['ls_dupe_pct'] < self.DUPE_CRIT else 'red'}">{'—' if d['ls_dupe_pct'] is None else str(d['ls_dupe_pct']) + '%'}</div>
    <div id="rel-ls-dupe-sub" class="kpi-sub">{d['lbl_ls_dupe']}</div>
  </div>
</div>
<div class="grid" style="margin-top:14px">
  <div class="card">
    <h3>{tr("ACK vs NAK — Rede")}</h3>
    <div style="display:flex;align-items:center;justify-content:center;height:150px">
      <canvas id="relNetChart" width="150" height="150"></canvas>
    </div>
  </div>
  <div class="card">
    <h3>{tr("Referências")}</h3>
    <table>
      <tr><th>{tr("Métrica")}</th><th>{tr("Referência")}</th></tr>
      <tr><td>{tr("NAK da rede")}</td><td><span class='tag tag-green'>&lt;5% Normal</span> <span class='tag tag-orange'>5-20% {tr("5-20% Atenção")[4:]}</span> <span class='tag tag-red'>&gt;20% {tr(">20% Crítico")[4:]}</span></td></tr>
      <tr><td>{tr("Entrega local")}</td><td><span class='tag tag-green'>&ge;90% ACK real</span> <span class='tag tag-orange'>70-90%</span> <span class='tag tag-red'>&lt;70%</span></td></tr>
    </table>
  </div>
</div>

<h3 style="color:#58a6ff;font-size:13px;margin:16px 0 10px 0">{tr("📍 Nó Local (mensagens enviadas)")}</h3>
{no_local_data}
<div class="grid-3">
  <div class="card">
    <h3>{tr("Taxa de Entrega Real (DMs)")}</h3>
    <div id="rel-delivery" class="kpi {dr_color}">{delivery if delivery is not None else '—'}{'%' if delivery is not None else ''}</div>
    <div class="kpi-sub">{tr("ACK do destinatário ÷ (ACK+NAK)")}<br><i>{tr("Mensagens de canal não têm ACK do destino: contam como Relay quando alguém as retransmite.")}</i></div>
  </div>
  <div class="card">
    <h3>{tr("Taxa NAK Local")}</h3>
    <div id="rel-nak" class="kpi {'red' if nak_rate and nak_rate>20 else 'orange' if nak_rate else ''}">{nak_rate if nak_rate is not None else '—'}{'%' if nak_rate is not None else ''}</div>
    <div class="kpi-sub">{tr("Falhas definitivas com errorReason")}</div>
  </div>
  <div class="card">
    <h3>{tr("Mensagens Enviadas")}</h3>
    <div id="rel-sent" class="kpi blue">{sent}</div>
    <div id="rel-kind" class="kpi-sub">DM: {d['sent_dm']} · {tr("Canal")}: {d['sent_channel']}</div>
    <div id="rel-sub" class="kpi-sub">ACK: {acked} · NAK: {naked} · Relay: {ack_impl} · Pend.: {pending}</div>
  </div>
</div>
<div style="margin-top:12px">
  <div class="card">
    <h3>{tr("Distribuição — Nó Local")}</h3>
    <div style="display:flex;gap:16px;align-items:center;padding:8px 0">
      <canvas id="relChart" width="140" height="140"></canvas>
      <div style="font-size:11px;color:#8b949e;line-height:2">
        <div><span style="color:#39d353">■</span> ACK real ({acked}) — {tr("ACK do destinatário ÷ (ACK+NAK)")[:20]}…</div>
        <div><span style="color:#f85149">■</span> NAK ({naked}) — {tr("Falhas definitivas com errorReason")}</div>
        <div><span style="color:#f0883e">■</span> {tr("Relay local")} ({ack_impl})</div>
        <div><span style="color:#8b949e">■</span> {tr("Pendente")} ({pending})</div>
      </div>
    </div>
  </div>
</div>
<script>
window._relNetChart = new Chart(document.getElementById('relNetChart'), {{
  type: 'doughnut',
  data: {{
    labels: ['ACK \u2713', 'NAK \u2717'],
    datasets: [{{ data: {json.dumps(pie_net)},
      backgroundColor: ['#39d353','#f85149'], borderWidth: 0 }}]
  }},
  options: {{
    responsive: false,
    plugins: {{ legend: {{ position: 'bottom', labels: {{ color: '#8b949e', boxWidth: 12, padding: 8 }} }} }}
  }}
}});
window._relChart = new Chart(document.getElementById('relChart'), {{
  type: 'doughnut',
  data: {{
    labels: ['ACK \u2713', 'NAK \u2717', '{tr("Relay local")}', '{tr("Pendente")}'],
    datasets: [{{ data: {json.dumps(pie_local)},
      backgroundColor: ['#39d353','#f85149','#f0883e','#8b949e'], borderWidth: 0 }}]
  }},
  options: {{
    responsive: false,
    plugins: {{ legend: {{ display: false }} }}
  }}
}});
</script>
<script>
window._metricsUpdateData = function(d) {{
  function set(id, v) {{ var e=document.getElementById(id); if(e) e.textContent=v; }}
  function setClass(id, cls) {{ var e=document.getElementById(id); if(e) e.className='kpi '+cls; }}

  // Rede
  set('rel-col', d.p_col !== null && d.p_col !== undefined ? d.p_col + '%' : '\u2014');
  setClass('rel-col',  d.p_col === null ? '' : d.p_col < 5 ? 'green' : d.p_col < 15 ? 'orange' : 'red');
  set('rel-col-label', d.lbl_col + (d.ch_util_avg !== null ? ' \u00b7 Ch.Util: ' + d.ch_util_avg + '%' : ''));
  set('rel-net-nak',   d.net_nak_rate !== null ? d.net_nak_rate + '%' : '\u2014');
  setClass('rel-net-nak', d.net_nak_rate === null ? '' : d.net_nak_rate < 5 ? 'green' : d.net_nak_rate < 20 ? 'orange' : 'red');
  set('rel-net-sub',   'ACK: ' + d.net_acks + ' \u00b7 NAK entrega: ' + d.net_naks + ' \u00b7 Erros FW: ' + (d.fw_errs||0));
  set('rel-pkt',       d.total_pkt);
  set('rel-ls-dupe',   d.ls_dupe_pct !== null && d.ls_dupe_pct !== undefined ? d.ls_dupe_pct + '%' : '\u2014');
  setClass('rel-ls-dupe', d.ls_dupe_pct === null || d.ls_dupe_pct === undefined ? '' : d.ls_dupe_pct < {self.DUPE_WARN} ? 'green' : d.ls_dupe_pct < {self.DUPE_CRIT} ? 'orange' : 'red');
  set('rel-ls-dupe-sub', d.lbl_ls_dupe);
  set('rel-pkt-sub',   d.lbl_pkt_sub);

  // Nó local
  set('rel-delivery',  d.delivery !== null ? d.delivery + '%' : '\u2014');
  setClass('rel-delivery', d.delivery === null ? '' : d.delivery >= 90 ? 'green' : d.delivery >= 70 ? 'orange' : 'red');
  set('rel-nak',       d.nak_rate !== null ? d.nak_rate + '%' : '\u2014');
  setClass('rel-nak',  d.nak_rate === null ? '' : d.nak_rate > 20 ? 'red' : d.nak_rate > 0 ? 'orange' : 'green');
  set('rel-sent',      d.sent);
  set('rel-kind',      'DM: ' + d.sent_dm + ' \u00b7 ' + (d.lbl_channel || 'Canal') + ': ' + d.sent_channel);
  set('rel-sub',       'ACK: ' + d.acked + ' \u00b7 NAK: ' + d.naked + ' \u00b7 Relay: ' + d.ack_implicit + ' \u00b7 Pend.: ' + d.pending);

  // Charts
  if(window._relNetChart) {{
    var netVals = [d.net_acks, d.net_naks];
    if(netVals[0]+netVals[1] > 0) {{ window._relNetChart.data.datasets[0].data = netVals; window._relNetChart.update('none'); }}
  }}
  if(window._relChart) {{
    var lv = [d.acked, d.naked, d.ack_implicit, d.pending];
    if(lv.reduce(function(a,b){{return a+b;}},0) > 0) {{ window._relChart.data.datasets[0].data = lv; window._relChart.update('none'); }}
  }}
}};
</script>"""
        return self._base_html(tr("✅ Fiabilidade"), body)

    # ── 8. Vizinhança ─────────────────────────────────────────────────────
    def _html_neighbors(self) -> str:
        if not self._nb_links:
            body = (
                '<div style="padding:20px 24px;max-width:660px;margin:0 auto">'
                f'<p style="color:#8b949e;font-size:13px;margin-bottom:16px">{tr("⏳ Sem dados de NeighborInfo ainda.")}<br><br>'
                f'{tr("neighborinfo_card")}<br>'
                f'{tr("neighborinfo_appear")}</p>'
                '<hr style="border:none;border-top:1px solid #30363d;margin:16px 0">'
                f'<p style="font-size:13px;color:#e6edf3;font-weight:bold;margin-bottom:8px">{tr("⚙ Como activar o NeighborInfo")}</p>'
                f'<p style="color:#8b949e;font-size:12px;margin-bottom:8px">{tr("neighborinfo_intro")}</p>'
                '<ol style="color:#8b949e;font-size:12px;padding-left:20px;line-height:2.0">'
                f'<li>{tr("neighborinfo_li1")}</li>'
                f'<li>{tr("neighborinfo_li2")}</li>'
                f'<li>{tr("neighborinfo_li3")}</li>'
                f'<li>{tr("neighborinfo_li4_viz")}</li>'
                '</ol>'
                f'<p style="margin-top:10px;color:#8b949e;font-size:11px">{tr("neighborinfo_note1")}</p>'
                '</div>'
            )
            return self._base_html(tr("🔗 Vizinhança"), body)

        d = self._data_neighbors()
        rows_html = ""
        for from_id, from_n, nb_id, nb_n, snr_str, snr_val in d["rows"]:
            if snr_val is not None:
                color = "green" if snr_val >= 5 else ("orange" if snr_val >= 0 else "red")
            else:
                color = "gray"
            rows_html += (
                f"<tr><td>{from_id}</td><td>{from_n}</td>"
                f"<td style='color:#8b949e'>↔</td>"
                f"<td>{nb_id}</td><td>{nb_n}</td>"
                f"<td><span class='tag tag-{color}'>{snr_str} dB</span></td></tr>"
            )
        if not rows_html:
            rows_html = f"<tr><td colspan='6' class='no-data'>{tr('Sem pares')}</td></tr>"

        body = f"""
<div class="subtitle">{tr('Nós que se vêem mutuamente via LoRa · {n} nós reportaram · {m} pares únicos', n=d['n_nodes'], m=d['n_links'])}</div>
<div class="card" style="margin-bottom:16px;border-left:4px solid #8b949e;padding:8px 14px">
  <span style="color:#8b949e;font-size:11px">{tr('viz_metric_info')}</span>
</div>
<div class="card">
  <h3>{tr('Pares de Vizinhos Directos')}</h3>
  <div style="margin-bottom:6px;font-size:11px;color:#8b949e">{tr("metrics_filter_hint")}</div>
  <table>
    <tr><th>ID A</th><th>{tr('Nome A')}</th><th></th><th>ID B</th><th>{tr('Nome B')}</th><th>SNR</th></tr>
    <tbody data-filterable="1">{rows_html}</tbody>
  </table>
</div>
<script>
window._metricsUpdateData = function(d) {{
  // Tabela de vizinhos não tem update incremental — actualiza no próximo render
}};
window._metricsFilterTable = function(text) {{
  var ft = (text || '').toLowerCase().trim();
  document.querySelectorAll('table tbody[data-filterable]').forEach(function(tbody) {{
    var found = 0;
    tbody.querySelectorAll('tr:not(.filter-no-match)').forEach(function(row) {{
      var hay = Array.from(row.querySelectorAll('td')).map(function(c){{ return c.textContent.toLowerCase(); }}).join(' ');
      var ok = !ft || hay.indexOf(ft) !== -1;
      row.style.display = ok ? '' : 'none';
      if (ok) found++;
    }});
    var noRow = tbody.querySelector('.filter-no-match');
    if (!noRow) {{
      noRow = document.createElement('tr'); noRow.className = 'filter-no-match';
      noRow.innerHTML = '<td colspan="99" style="color:#f0883e;padding:8px;text-align:center;font-size:12px">🔍 ' + (ft ? '{tr("metrics_no_results")}' + ' &quot;' + ft + '&quot;' : '') + '</td>';
      tbody.appendChild(noRow);
    }}
    noRow.style.display = (ft && found === 0) ? '' : 'none';
  }});
}};
</script>"""
        return self._base_html(tr("🔗 Vizinhança"), body)

    # ── 9. Alcance & Links ────────────────────────────────────────────────
    def _neighborinfo_howto(self) -> str:
        return (
            f'<p style="font-size:13px;color:#e6edf3;font-weight:bold;margin-bottom:8px">{tr("⚙ Como activar o NeighborInfo")}</p>'
            f'<p style="color:#8b949e;font-size:12px;margin-bottom:8px">{tr("neighborinfo_intro")}</p>'
            '<ol style="color:#8b949e;font-size:12px;padding-left:20px;line-height:2.0">'
            f'<li>{tr("neighborinfo_li1")}</li>'
            f'<li>{tr("neighborinfo_li2")}</li>'
            f'<li>{tr("neighborinfo_li3")}</li>'
            f'<li>{tr("neighborinfo_li4_range")}</li>'
            '</ol>'
            f'<p style="margin-top:10px;color:#8b949e;font-size:11px">{tr("neighborinfo_note1")}<br>'
            f'{tr("neighborinfo_note2")}</p>'
            f'<p style="margin-top:8px;color:#8b949e;font-size:11px">{tr("range_haversine")}</p>'
        )

    def _html_range_links(self) -> str:
        d = self._data_range_links()
        if not (d["rows"] or d["direct_rows"] or d["relay_rows"]):
            body = (
                f'<div style="padding:20px 24px;max-width:660px;margin:0 auto">'
                f'<p style="color:#8b949e;font-size:13px;margin-bottom:16px">{tr("⏳ Sem dados de alcance ainda.")}<br><br>'
                f'{tr("range_waiting_sources")}<br>'
                f'{tr("Nós com GPS conhecidos até agora: {n}", n="<b>"+str(d["n_with_gps"])+"</b>")}</p>'
                '<hr style="border:none;border-top:1px solid #30363d;margin:16px 0">'
                f'{self._neighborinfo_howto()}</div>'
            )
            return self._base_html(tr("📏 Alcance & Links"), body)

        def kpi(val, label, color="", kid="", note="", note_id=""):
            id_attr = f' id="{kid}"' if kid else ""
            nid_attr = f' id="{note_id}"' if note_id else ""
            return (f'<div class="card"><h3>{label}</h3><div{id_attr} class="kpi {color}">{val}</div>'
                    f'<div{nid_attr} class="kpi-sub">{note}</div></div>')

        def snr_tag(v):
            if v is None:
                return "<span class='tag tag-gray'>—</span>"
            c = "green" if v >= 5 else "orange" if v >= -5 else "red"
            return f"<span class='tag tag-{c}'>{v:+.1f} dB</span>"

        def dist_tag(v):
            if v is None:
                return f"<span class='tag tag-gray'>{tr('sem GPS')}</span>"
            c = "green" if v < 2 else "orange" if v < 10 else "blue"
            return f"<span class='tag tag-{c}'>{v} km</span>"

        direct_html = "".join(
            f"<tr><td>{nid}</td><td>{name}</td><td>{dist_tag(dist)}</td><td>{snr_tag(best)}</td>"
            f"<td>{snr_tag(last)}</td><td>{n}</td></tr>"
            for nid, name, dist, best, last, n in d["direct_rows"]
        ) or f"<tr><td colspan='6' class='no-data'>{tr('Ainda nenhum pacote recebido directamente (0 hops).')}</td></tr>"

        def relay_row(r):
            byte, name, cnt, pct, n_cands = r
            label = name or f"<span style='color:#8b949e'>{d['lbl_unknown_relay']}</span>"
            if n_cands > 1:
                label += f" <span class='tag tag-orange'>{d['lbl_ambiguous']}</span>"
            return (f"<tr><td class='mono'>{byte}</td><td>{label}</td><td>{cnt}</td>"
                    f"<td><div class='bar-bg'><div class='bar-fill' style='width:{pct}%;background:#58a6ff'></div></div>"
                    f"<span style='font-size:10px;color:#8b949e'>{pct}%</span></td></tr>")
        relay_html = "".join(relay_row(r) for r in d["relay_rows"]) or \
            f"<tr><td colspan='4' class='no-data'>{tr('relay_waiting')}</td></tr>"

        nb_html = ""
        for from_id, from_n, nb_id, nb_n, dist, snr_str, snr_val in d["rows"]:
            nb_html += (f"<tr><td>{from_id}</td><td>{from_n}</td><td>{nb_id}</td><td>{nb_n}</td>"
                        f"<td>{dist_tag(dist)}</td><td>{snr_tag(snr_val)}</td></tr>")
        nb_card = (f"""
<div class="card" style="margin-top:16px">
  <h3>{tr('Links NeighborInfo por Alcance')}</h3>
  <table>
    <tr><th>ID A</th><th>{tr('Nome A')}</th><th>ID B</th><th>{tr('Nome B')}</th><th>{tr('Distância')}</th><th>SNR</th></tr>
    <tbody id="nb-range-tbody" data-filterable="1">{nb_html}</tbody>
  </table>
</div>""" if d["rows"] else f"""
<details class="card" style="margin-top:16px">
  <summary style="cursor:pointer;color:#8b949e;font-size:12px">{tr('Sem links NeighborInfo com GPS — como activar')}</summary>
  <div style="margin-top:10px">{self._neighborinfo_howto()}</div>
</details>""")

        far_note = (d["max_direct_name"] if d["max_direct"] is not None else
                    tr("Sem posição GPS do nó local") if not d["has_local_pos"] else tr("Nenhum nó directo com GPS"))
        nb_max = f"{d['max_range']:.2f} km" if d["max_range"] else "—"
        body = f"""
<div class="subtitle">{tr('range_subtitle')}</div>
<div class="grid-3" style="margin-bottom:16px">
  {kpi(f"{d['max_direct']} km" if d['max_direct'] is not None else "—", tr("Maior alcance directo"), "blue", "rl-direct", far_note, "rl-direct-sub")}
  {kpi(d['n_direct'], tr("Nós ouvidos directamente"), "green", "rl-ndirect", tr("0 hops, por rádio, nesta sessão"))}
  {kpi(nb_max, tr("Maior link NeighborInfo"), "", "rl-nb", f"{d['max_pair'][0]} ↔ {d['max_pair'][1]}" if d['max_range'] else tr("Requer NeighborInfo + GPS"))}
</div>
<div class="card">
  <h3>{tr('Alcance directo (0 hops)')}</h3>
  <div style="margin-bottom:6px;font-size:11px;color:#8b949e">{tr('direct_range_hint')}</div>
  <table>
    <tr><th>ID</th><th>{tr('Nome')}</th><th>{tr('Distância')}</th><th>{tr('Melhor SNR')}</th><th>{tr('Último SNR')}</th><th>{tr('Pacotes')}</th></tr>
    <tbody id="direct-tbody" data-filterable="1">{direct_html}</tbody>
  </table>
</div>
<div class="card" style="margin-top:16px">
  <h3>{tr('Quem mais retransmite para o nó local')}</h3>
  <div id="relay-sub" style="margin-bottom:6px;font-size:11px;color:#8b949e">{tr('relay_hint', n=d['relayed_total'])}</div>
  <table>
    <tr><th>{tr('Byte')}</th><th>{tr('Nó')}</th><th>{tr('Pacotes')}</th><th>%</th></tr>
    <tbody id="relay-tbody" data-filterable="1">{relay_html}</tbody>
  </table>
</div>
{nb_card}
<script>
var _currentFilter='';
function _applyFilter(){{
  var ft=_currentFilter;
  document.querySelectorAll('table tbody[data-filterable] tr').forEach(function(row){{
    var hay=Array.from(row.querySelectorAll('td')).map(function(c){{return c.textContent.toLowerCase();}}).join(' ');
    row.style.display=(!ft||hay.indexOf(ft)!==-1)?'':'none';
  }});
}}
window._metricsFilterTable=function(text){{_currentFilter=(text||'').toLowerCase().trim();_applyFilter();}};
function _esc(v){{ return String(v===null||v===undefined?'':v).replace(/[&<>"]/g,function(c){{return {{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c];}}); }}
function _snrTag(v){{ if(v===null||v===undefined) return "<span class='tag tag-gray'>—</span>";
  var c=v>=5?'green':v>=-5?'orange':'red'; return "<span class='tag tag-"+c+"'>"+(v>0?'+':'')+v.toFixed(1)+" dB</span>"; }}
function _distTag(v, noGps){{ if(v===null||v===undefined) return "<span class='tag tag-gray'>"+noGps+"</span>";
  var c=v<2?'green':v<10?'orange':'blue'; return "<span class='tag tag-"+c+"'>"+v+" km</span>"; }}
window._metricsUpdateData=function(d){{
  function set(id,v){{ var e=document.getElementById(id); if(e) e.textContent=v; }}
  set('rl-direct', d.max_direct!==null ? d.max_direct+' km' : '—');
  if(d.max_direct!==null) set('rl-direct-sub', d.max_direct_name);
  set('rl-ndirect', d.n_direct);
  var t=document.getElementById('direct-tbody');
  if(t && d.direct_rows.length){{
    t.innerHTML=d.direct_rows.map(function(r){{ return '<tr><td>'+_esc(r[0])+'</td><td>'+_esc(r[1])+'</td><td>'+_distTag(r[2], d.lbl_no_gps)
      +'</td><td>'+_snrTag(r[3])+'</td><td>'+_snrTag(r[4])+'</td><td>'+r[5]+'</td></tr>'; }}).join('');
  }}
  var rt=document.getElementById('relay-tbody');
  if(rt && d.relay_rows.length){{
    rt.innerHTML=d.relay_rows.map(function(r){{
      var lbl=r[1]?_esc(r[1]):"<span style='color:#8b949e'>"+_esc(d.lbl_unknown_relay)+"</span>";
      if(r[4]>1) lbl+=" <span class='tag tag-orange'>"+_esc(d.lbl_ambiguous)+"</span>";
      return "<tr><td class='mono'>"+_esc(r[0])+"</td><td>"+lbl+"</td><td>"+r[2]+"</td><td><div class='bar-bg'><div class='bar-fill' style='width:"
        +r[3]+"%;background:#58a6ff'></div></div><span style='font-size:10px;color:#8b949e'>"+r[3]+"%</span></td></tr>"; }}).join('');
    set('relay-sub', d.lbl_relay_hint);
  }}
  _applyFilter();
}};
</script>"""
        return self._base_html(tr("📏 Alcance & Links"), body)

    # ── 10. Intervalos entre pacotes ─────────────────────────────────────
    def _html_intervals(self) -> str:
        d = self._data_intervals()
        if not d["rows"]:
            body = (f'<div class="no-data">{tr("⏳ Sem dados de intervalos ainda.")}<br><br>'
                    f'{tr("Requer pelo menos 2 pacotes por nó para calcular o intervalo médio.")}</div>')
            return self._base_html(tr("⏰ Intervalos"), body)

        rows_html = ""
        for nid, nid_n, avg, mn, mx, count in d["rows"]:
            # Cores: verde <60s (activo), laranja 60-300s, vermelho >300s
            color = "green" if avg < 60 else ("orange" if avg < 300 else "red")
            freq_label = tr("Alta frequência") if avg < 30 else ("Normal" if avg < 180 else tr("Baixa frequência"))
            rows_html += (
                f"<tr><td>{nid}</td><td>{nid_n}</td>"
                f"<td><span class='tag tag-{color}'>{avg}s</span></td>"
                f"<td style='color:#8b949e'>{mn}s</td>"
                f"<td style='color:#8b949e'>{mx}s</td>"
                f"<td style='color:#8b949e'>{count}</td>"
                f"<td style='font-size:11px;color:#8b949e'>{freq_label}</td></tr>"
            )

        body = f"""
<div class="subtitle">{tr('Intervalo real entre pacotes recebidos de cada nó')}</div>
<div class="card" style="margin-bottom:16px;border-left:4px solid #8b949e;padding:8px 14px">
  <span style="color:#8b949e;font-size:11px">{tr('intervals_metric_info')}</span>
</div>
<div class="card">
  <h3>{tr('Intervalo Médio entre Pacotes por Nó')}</h3>
  <div style="margin-bottom:6px;font-size:11px;color:#8b949e">{tr("metrics_filter_hint")}</div>
  <table>
    <tr><th>ID</th><th>{tr('Nome')}</th><th>{tr('Média')}</th><th>{tr('Mín.')}</th><th>{tr('Máx.')}</th><th>{tr('Amostras')}</th><th>{tr('Frequência')}</th></tr>
    <tbody id="intervals-tbody" data-filterable="1">{rows_html}</tbody>
  </table>
  <div style="color:#8b949e;font-size:10px;margin-top:8px;padding-top:8px;border-top:1px solid #21262d">
    {tr('ℹ️ Intervalos <30s = alta frequência · 30–180s = normal · >180s = baixa frequência')}
  </div>
</div>
<script>
// Filtro activo — aplicado após cada update de dados
var _currentFilter = '';
window._metricsFilterTable = function(text) {{
  _currentFilter = (text || '').toLowerCase().trim();
  _applyFilter();
}};
function _applyFilter() {{
  var ft = _currentFilter;
  var tbody = document.getElementById('intervals-tbody');
  if (!tbody) return;
  var found = 0;
  tbody.querySelectorAll('tr:not(.filter-no-match)').forEach(function(row) {{
    var hay = Array.from(row.querySelectorAll('td')).map(function(c){{ return c.textContent.toLowerCase(); }}).join(' ');
    var ok = !ft || hay.indexOf(ft) !== -1;
    row.style.display = ok ? '' : 'none';
    if (ok) found++;
  }});
  var noRow = tbody.querySelector('.filter-no-match');
  if (!noRow) {{
    noRow = document.createElement('tr'); noRow.className = 'filter-no-match';
    noRow.innerHTML = '<td colspan="7" style="color:#f0883e;padding:8px;text-align:center;font-size:12px">🔍 ' + (ft ? '{tr("metrics_no_results")}' + ' &quot;' + ft + '&quot;' : '') + '</td>';
    tbody.appendChild(noRow);
  }}
  noRow.style.display = (ft && found === 0) ? '' : 'none';
}}
window._metricsUpdateData = function(d) {{
  if (!d || !d.rows) return;
  var tbody = document.getElementById('intervals-tbody');
  if (!tbody) return;
  var LABELS = d.lbl_freq || {{'high':'High frequency','normal':'Normal','low':'Low frequency'}};
  var html = '';
  d.rows.forEach(function(r) {{
    var nid=r[0], nm=r[1], avg=r[2], mn=r[3], mx=r[4], cnt=r[5];
    var color = avg < 60 ? 'green' : avg < 300 ? 'orange' : 'red';
    var freq  = avg < 30 ? LABELS.high : avg < 180 ? LABELS.normal : LABELS.low;
    html += '<tr><td>'+nid+'</td><td>'+nm+'</td>'
          + '<td><span class="tag tag-'+color+'">'+avg+'s</span></td>'
          + '<td style="color:#8b949e">'+mn+'s</td>'
          + '<td style="color:#8b949e">'+mx+'s</td>'
          + '<td style="color:#8b949e">'+cnt+'</td>'
          + '<td style="font-size:11px;color:#8b949e">'+freq+'</td></tr>';
  }});
  // Preserva a linha .filter-no-match ao reconstruir
  tbody.innerHTML = html || '<tr><td colspan="7" class="no-data">—</td></tr>';
  _applyFilter();   // reaplicar filtro após update dos dados
}};
</script>"""
        return self._base_html(tr("⏰ Intervalos"), body)


    # ── Nó Local ─────────────────────────────────────────────────────────
    def _html_radio_health(self) -> tuple:
        """Blocos "Saúde do rádio" (LocalStats) e "Máquina" (HostMetrics) do
        Nó Local. Devolve (html, js). A secção redesenha-se quando chega uma
        amostra nova (ls_ts/host_ts na assinatura), por isso não há update."""
        r = self._data_radio_health()

        def color(val, warn, crit):
            return "" if val is None else "green" if val < warn else "orange" if val < crit else "red"

        def kpi(title, value, cls, sub):
            return (f'<div class="card"><h3>{title}</h3><div class="kpi {cls}">{value}</div>'
                    f'<div class="kpi-sub">{sub}</div></div>')

        def fmt_pct(v):
            return "—" if v is None else f"{v}%"

        js = ""
        if not r["has_stats"]:
            radio = (f'<div class="card" style="margin-top:16px"><h3>{tr("📻 Saúde do Rádio (LocalStats)")}</h3>'
                     f'<div style="color:#8b949e;font-size:12px;padding:14px 0;text-align:center">'
                     f'{tr("⏳ O nó ainda não enviou LocalStats.")}<br>'
                     f'<span style="font-size:11px">{tr("localstats_hint")}</span></div></div>')
        else:
            dl = r["delta"]
            since = (tr("Últimos {m} min: +{rx} RX · +{tx} TX · +{d} repetidos · +{b} corrompidos",
                        m=dl["minutes"], rx=dl["numPacketsRx"], tx=dl["numPacketsTx"],
                        d=dl["numRxDupe"], b=dl["numPacketsRxBad"])
                     if dl else tr("A variação aparece a partir do segundo envio."))
            noise = r["noise"]
            noise_cls = ("" if noise is None else "green" if noise < self.NOISE_WARN
                         else "orange" if noise < self.NOISE_CRIT else "red")
            radio = f"""
<div class="card" style="margin-top:16px;border-left:4px solid #58a6ff">
  <h3>{tr("📻 Saúde do Rádio (LocalStats)")} · {r["stats_at"]}</h3>
  <div class="kpi-sub" style="margin-bottom:10px">{tr("Contadores do firmware desde o arranque do nó.")} {since}</div>
  <div class="grid-3">
    {kpi(tr("Repetidos recebidos"), fmt_pct(r["dupe_pct"]), color(r["dupe_pct"], self.DUPE_WARN, self.DUPE_CRIT),
         tr("{d} de {rx} pacotes · muitos = retransmissões desnecessárias (ex.: demasiados ROUTER)", d=r["dupe"], rx=r["rx"]))}
    {kpi(tr("Pacotes corrompidos"), fmt_pct(r["bad_pct"]), color(r["bad_pct"], self.BAD_WARN, self.BAD_CRIT),
         tr("{b} de {rx} · ruído ou colisões no canal", b=r["rx_bad"], rx=r["rx"]))}
    {kpi(tr("Ruído de fundo"), "—" if noise is None else f"{noise} dBm", noise_cls,
         tr("Noise floor medido pelo rádio · mais baixo é melhor"))}
  </div>
  <div class="grid-3" style="margin-top:12px">
    {kpi(tr("Relays"), r["relay"], "blue",
         tr("{c} cancelados ({p}) — outro nó retransmitiu primeiro", c=r["relay_canceled"], p=fmt_pct(r["relay_cancel_pct"])))}
    {kpi(tr("TX descartados"), r["dropped"], "red" if r["dropped"] else "green",
         tr("{p} dos envios · fila cheia ou duty cycle", p=fmt_pct(r["drop_pct"])))}
    {kpi(tr("Nós online"), f'{r["online"]} / {r["total"]}', "blue",
         tr("Na NodeDB do nó (últimas 2 h / total)"))}
  </div>
  {'<div class="chart-wrap" style="margin-top:12px"><canvas id="noiseChart"></canvas></div>' if len(r["noise_ts_vals"]) > 1 else ''}
</div>"""
            if len(r["noise_ts_vals"]) > 1:
                js = f"""
window._noiseChart = new Chart(document.getElementById('noiseChart'), {{
  type: 'line',
  data: {{ labels: {json.dumps(r["noise_ts_labels"])},
    datasets: [{{ label: '{tr("Ruído de fundo")} (dBm)', data: {json.dumps(r["noise_ts_vals"])},
      borderColor: '#58a6ff', backgroundColor: 'rgba(88,166,255,0.08)', fill: true, tension: 0.3, pointRadius: 2 }}] }},
  options: {{ responsive: true, maintainAspectRatio: false,
    scales: {{ y: {{ grid: {{ color: '#21262d' }}, ticks: {{ color: '#8b949e', callback: v => v + ' dBm' }} }},
               x: {{ grid: {{ color: '#21262d' }}, ticks: {{ color: '#8b949e', maxTicksLimit: 8 }} }} }},
    plugins: {{ legend: {{ labels: {{ color: '#8b949e', boxWidth: 12 }} }} }} }}
}});"""

        host = ""
        if r["has_host"]:
            load = r["host_load"]
            disks = " · ".join(f"{v} GB" for v in r["host_disk_gb"] if v is not None) or "—"
            up = r["host_uptime"]
            up_txt = "—" if not up else f"{up // 86400}d {up % 86400 // 3600:02d}h"
            host = f"""
<div class="card" style="margin-top:16px">
  <h3>{tr("🖥 Máquina do meshtasticd (HostMetrics)")} · {r["host_at"]}</h3>
  <div class="grid-3">
    {kpi(tr("Carga (1/5/15 min)"), f"{load[0]}", "green" if load[0] < 1 else "orange" if load[0] < 2 else "red",
         f"{load[0]} · {load[1]} · {load[2]}")}
    {kpi(tr("Memória livre"), "—" if r["host_mem_mb"] is None else f'{r["host_mem_mb"]} MB', "blue", tr("RAM disponível na máquina"))}
    {kpi(tr("Disco livre"), disks.split(" · ")[0], "blue", tr("/ e restantes discos: {d}", d=disks) + f" · uptime {up_txt}")}
  </div>
  {f'<div class="kpi-sub" style="margin-top:8px">{r["host_user"]}</div>' if r["host_user"] else ''}
</div>"""
        return radio + host, js

    def _html_local_node(self) -> str:
        # Ecrã de espera — _WAITING_SECTIONS + _refresh_current fazem a transição
        # automaticamente quando set_local_node_id é chamado.
        if not self._local_nid:
            body = (
                f'<div class="no-data" style="padding:40px 0">'
                f'<div style="font-size:32px;margin-bottom:16px">🔌</div>'
                f'<div style="font-size:15px;color:#e6edf3;margin-bottom:8px">'
                f'{tr("⏳ Aguardando identificação do nó local...")}</div>'
                f'<div style="font-size:12px;color:#8b949e">'
                f'{tr("A secção Nó Local aparece após a ligação estar estabelecida.")}</div>'
                f'</div>'
                f'<script>window._metricsUpdateData=function(d){{}};</script>'
            )
            return self._base_html(tr("🏠 Nó Local"), body)

        d = self._data_local_node()

        # Bateria
        batt = d['battery']
        if batt == 101:
            batt_color = "blue"
            batt_kpi   = "⚡"
        elif batt and 1 <= batt <= 100:
            batt_color = "green" if batt > 60 else ("orange" if batt > 20 else "red")
            batt_kpi   = f"{batt}%"
        else:
            batt_color = ""
            batt_kpi   = "—"

        volt_str = f"{d['voltage']:.3f} V" if d['voltage'] else "—"

        # Duty cycle
        dc       = d['dc_est']
        dc_color = "green" if dc < 7 else ("orange" if dc < 10 else "red")
        dc_label = (tr("lbl_dc_optimal") if dc < 7 else
                    tr("lbl_dc_warn")     if dc < 10 else
                    tr("lbl_dc_exceeded"))
        dc_pct   = min(int(dc / 10 * 100), 100)
        dc_bar_c = {"green": "#39d353", "orange": "#f0883e", "red": "#f85149"}[dc_color]

        # Ch Util
        ch       = d['ch_util']
        ch_color = "green" if ch < 25 else ("orange" if ch < 50 else "red")

        # SNR recebido
        snr_avg   = d['snr_rx_avg']
        snr_color = ("green"  if snr_avg and snr_avg >= 5 else
                     "orange" if snr_avg and snr_avg >= 0 else
                     "red"    if snr_avg else "")

        # Delivery
        delivery = d['delivery']
        dr_color = ("green"  if delivery and delivery >= 90 else
                    "orange" if delivery and delivery >= 70 else
                    "red"    if delivery else "")

        # GPS
        lat, lon = d['lat'], d['lon']
        gps_str  = (f"{lat:.6f}, {lon:.6f}" if lat is not None and lon is not None
                    else tr("Sem posição GPS"))

        # RTT sub-label
        rtt_sub = ""
        if d['rtt_avg'] is not None:
            rtt_sub = f"{tr('RTT médio')}: {d['rtt_avg']}s · {tr('mediana')}: {d['rtt_med']}s"

        radio_html, radio_js = self._html_radio_health()

        n_dc = len(self._local_dc_ts)
        dc_chart_html_note = tr("Valor do firmware (airUtilTx): % do tempo a transmitir na última hora.")

        if n_dc > 1:
            import json as _json
            ts_lbl = _json.dumps([self._ts_label(t) for t, _ in self._local_dc_ts])
            ts_val = _json.dumps([v for _, v in self._local_dc_ts])
            lbl_dc_hist   = tr("Duty Cycle/h (%)")
            dc_chart_js = f"""
window._dcChart = new Chart(document.getElementById('dcChart'), {{
  type: 'line',
  data: {{
    labels: {ts_lbl},
    datasets: [
      {{ label: '{lbl_dc_hist}', data: {ts_val},
         borderColor: '#f0883e', backgroundColor: 'rgba(240,136,62,0.08)',
         fill: true, tension: 0.3, pointRadius: 2 }},
      {{ label: 'EU limit (10%)', data: Array({n_dc}).fill(10),
         borderColor: '#f85149', borderDash: [4,4], pointRadius: 0, fill: false }},
      {{ label: 'Warning (7%)', data: Array({n_dc}).fill(7),
         borderColor: '#f0883e', borderDash: [4,4], pointRadius: 0, fill: false }},
    ]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    scales: {{
      y: {{ min: 0, max: 15, grid: {{ color: '#21262d' }},
            ticks: {{ color: '#8b949e', callback: v => v + '%' }} }},
      x: {{ grid: {{ color: '#21262d' }}, ticks: {{ color: '#8b949e', maxTicksLimit: 10 }} }}
    }},
    plugins: {{ legend: {{ labels: {{ color: '#8b949e', boxWidth: 12 }} }} }}
  }}
}});"""
            # Card sempre presente — só o canvas é escondido/mostrado
            dc_chart_html = f"""
<div class="card" style="margin-top:16px" id="dc-history-card">
  <h3>{tr("Duty Cycle/h — Histórico")}</h3>
  <div class="chart-wrap-lg"><canvas id="dcChart"></canvas></div>
  <div style="color:#8b949e;font-size:10px;margin-top:8px">{dc_chart_html_note}</div>
</div>"""
        else:
            dc_chart_js = ""
            # Card sempre presente mas com placeholder — evita flash de layout
            # quando os primeiros dados chegam e o card "aparece do nada"
            dc_chart_html = f"""
<div class="card" style="margin-top:16px" id="dc-history-card">
  <h3>{tr("Duty Cycle/h — Histórico")}</h3>
  <div style="color:#8b949e;font-size:12px;padding:20px 0;text-align:center">
    ⏳ {tr("Aguardando dados de telemetria...")}
  </div>
  <div style="color:#8b949e;font-size:10px;margin-top:4px">{dc_chart_html_note}</div>
</div>"""

        body = f"""
<div class="subtitle">{tr("Métricas e estado do nó local")}</div>

<!-- Identificação -->
<div class="card" style="margin-bottom:16px;border-left:4px solid #f0883e;padding:8px 14px;display:flex;gap:24px;flex-wrap:wrap">
  <div><span style="color:#8b949e;font-size:11px">ID</span><br>
       <span style="font-weight:bold;color:#e6edf3">{d['nid']}</span></div>
  <div><span style="color:#8b949e;font-size:11px">{tr("Nome")}</span><br>
       <span style="font-weight:bold;color:#e6edf3" id="ln-name">{d['name']}</span></div>
  <div><span style="color:#8b949e;font-size:11px">Hardware</span><br>
       <span id="ln-hw" style="color:#e6edf3">{d['hw_model']}</span></div>
  <div><span style="color:#8b949e;font-size:11px">Uptime</span><br>
       <span id="ln-uptime" style="color:#e6edf3">{d['uptime_fmt']}</span></div>
  <div><span style="color:#8b949e;font-size:11px">GPS</span><br>
       <span id="ln-gps" style="color:{'#39d353' if lat is not None else '#f0883e'};font-size:12px">{gps_str}</span></div>
</div>

<!-- KPIs linha 1: hardware/saúde -->
<div class="grid-3">
  <div class="card"><h3>{tr("Bateria")}</h3>
    <div id="ln-batt" class="kpi {batt_color}">{batt_kpi}</div>
    <div id="ln-volt" class="kpi-sub">{volt_str}</div></div>
  <div class="card"><h3>Ch. Util.</h3>
    <div id="ln-ch" class="kpi {ch_color}">{ch}%</div>
    <div class="kpi-sub">{tr("Canal observado pelo nó")}</div></div>
  <div class="card"><h3>{tr("Pacotes do nó local")}</h3>
    <div id="ln-air" class="kpi blue">{d['local_tx_pkts']}</div>
    <div class="kpi-sub">{tr("Pacotes deste nó vistos nesta sessão")}</div></div>
</div>

<!-- KPI duty cycle -->
<div class="card" style="margin-top:16px">
  <h3>{tr("Duty Cycle/h (airUtilTx)")} — <span id="ln-dc-label" style="color:{'#39d353' if dc_color=='green' else '#f0883e' if dc_color=='orange' else '#f85149'}">{dc_label}</span></h3>
  <div style="display:flex;align-items:center;gap:16px;margin-top:8px">
    <div id="ln-dc" class="kpi {dc_color}" style="font-size:36px;min-width:80px">{dc}%</div>
    <div style="flex:1">
      <div class="bar-bg"><div class="bar-fill" id="ln-dc-bar" style="width:{dc_pct}%;background:{dc_bar_c}"></div></div>
      <div style="color:#8b949e;font-size:11px;margin-top:6px">
        {tr("Limite EU_868/EU_433: 10%/hora · Aviso: 7%/hora")}<br>
        <i>{tr("Valor do firmware (airUtilTx): % do tempo a transmitir na última hora.")}</i>
      </div>
    </div>
  </div>
</div>

<!-- KPIs linha 2: RF e TX -->
<div class="grid-3" style="margin-top:16px">
  <div class="card"><h3>{tr("SNR Médio Recebido")}</h3>
    <div id="ln-snr" class="kpi {snr_color}">{snr_avg if snr_avg is not None else '—'}{' dB' if snr_avg is not None else ''}</div>
    <div id="ln-snr-sub" class="kpi-sub">min {d['snr_rx_min'] if d['snr_rx_min'] is not None else '—'} · max {d['snr_rx_max'] if d['snr_rx_max'] is not None else '—'}<br>{d['n_snr_rx']} {tr("pacotes")}</div></div>
  <div class="card"><h3>{tr("Msgs Enviadas")}</h3>
    <div id="ln-sent" class="kpi blue">{d['msgs_sent']}</div>
    <div id="ln-sent-sub" class="kpi-sub">ACK: {d['msgs_acked']} · NAK: {d['msgs_naked']} · Relay: {d['msgs_implicit']} · {tr("Pend.")}: {d['msgs_pending']}</div></div>
  <div class="card"><h3>{tr("Taxa Entrega (DMs)")}</h3>
    <div id="ln-delivery" class="kpi {dr_color}">{delivery if delivery is not None else '—'}{'%' if delivery is not None else ''}</div>
    <div id="ln-rtt-sub" class="kpi-sub">{rtt_sub}</div></div>
</div>

{radio_html}

{dc_chart_html}

<script>
{dc_chart_js}
{radio_js}
</script>
<script>
// Uptime live — actualizado a cada segundo independentemente do refresh Python
var _uptimeRaw = {d['uptime_raw']};   // segundos quando foi lido do firmware
var _uptimeTs  = {d['uptime_ts']};    // epoch Unix quando foi lido

function _fmtUptime(s) {{
  if (!s || s <= 0) return '\u2014';
  var d = Math.floor(s / 86400);
  var h = Math.floor((s % 86400) / 3600);
  var m = Math.floor((s % 3600) / 60);
  var sec = s % 60;
  if (d > 0) return d + 'd ' + String(h).padStart(2,'0') + 'h ' + String(m).padStart(2,'0') + 'm';
  if (h > 0) return h + 'h ' + String(m).padStart(2,'0') + 'm';
  return m + 'm ' + String(sec).padStart(2,'0') + 's';
}}

function _tickUptime() {{
  if (_uptimeRaw <= 0) return;
  var elapsed = Math.floor(Date.now() / 1000) - _uptimeTs;
  var current = _uptimeRaw + elapsed;
  var e = document.getElementById('ln-uptime');
  if (e) e.textContent = _fmtUptime(current);
}}
_tickUptime();
setInterval(_tickUptime, 1000);

window._metricsUpdateData = function(d) {{
  function set(id, v)       {{ var e=document.getElementById(id); if(e) e.textContent=v; }}
  function setClass(id,cls) {{ var e=document.getElementById(id); if(e) e.className='kpi '+cls; }}
  function setStyle(id,p,v) {{ var e=document.getElementById(id); if(e) e.style[p]=v; }}
  function setColor(id,c)   {{ var e=document.getElementById(id); if(e) e.style.color=c; }}

  // Actualiza uptime_raw para que o setInterval calcule a partir do valor mais recente
  if (d.uptime_raw > 0) {{
    _uptimeRaw = d.uptime_raw;
    _uptimeTs  = d.uptime_ts;
    _tickUptime();
  }}

  // Identificação
  set('ln-hw', d.hw_model || '\u2014');
  if (d.lat !== null && d.lat !== undefined && d.lon !== null) {{
    set('ln-gps', d.lat.toFixed(6) + ', ' + d.lon.toFixed(6));
    setColor('ln-gps', '#39d353');
  }}

  // Bateria
  var b  = d.battery;
  var bv = b===101?'\u26a1':b&&b>=1&&b<=100?b+'%':'\u2014';
  var bc = b===101?'blue':b>60?'green':b>20?'orange':'red';
  set('ln-batt', bv); setClass('ln-batt', bc);
  set('ln-volt', d.voltage ? d.voltage.toFixed(3)+' V' : '\u2014');

  // Ch/Air
  var ch = d.ch_util;
  set('ln-ch', ch !== undefined ? ch + '%' : '\u2014');
  setClass('ln-ch', ch<25?'green':ch<50?'orange':'red');
  set('ln-air', d.local_tx_pkts !== undefined ? d.local_tx_pkts : '\u2014');

  // Duty cycle
  var dc = d.dc_est;
  var dcColor = dc>=10?'red':dc>=7?'orange':'green';
  var dcLabel = dc>=10?(d.lbl_dc_exceeded||'\ud83d\udea8 LIMIT EXCEEDED'):
                dc>=7 ?(d.lbl_dc_warn||'\u26a0 Near limit'):
                        (d.lbl_dc_optimal||'\u2705 Optimal');
  set('ln-dc', dc !== undefined ? dc + '%' : '\u2014');
  setClass('ln-dc', dcColor);
  set('ln-dc-label', dcLabel);
  var barC = {{green:'#39d353',orange:'#f0883e',red:'#f85149'}}[dcColor];
  setStyle('ln-dc-bar', 'width',      Math.min(Math.round(dc/10*100),100)+'%');
  setStyle('ln-dc-bar', 'background', barC);

  // SNR
  var snr = d.snr_rx_avg;
  set('ln-snr', snr !== null && snr !== undefined ? snr + ' dB' : '\u2014');
  setClass('ln-snr', snr===null?'':snr>=5?'green':snr>=0?'orange':'red');
  var pkt = d.lbl_packets || 'packets';
  set('ln-snr-sub', 'min '+(d.snr_rx_min!==null?d.snr_rx_min:'\u2014')+' \u00b7 max '+(d.snr_rx_max!==null?d.snr_rx_max:'\u2014')+' \u00b7 '+d.n_snr_rx+' '+pkt);

  // TX/msgs
  set('ln-sent', d.msgs_sent);
  set('ln-sent-sub', 'ACK: '+d.msgs_acked+' \u00b7 NAK: '+d.msgs_naked+' \u00b7 Relay: '+d.msgs_implicit+' \u00b7 '+(d.lbl_pend||'Pend.')+': '+d.msgs_pending);
  var del = d.delivery;
  set('ln-delivery', del !== null ? del + '%' : '\u2014');
  setClass('ln-delivery', del===null?'':del>=90?'green':del>=70?'orange':'red');
  var rttSub = '';
  if (d.rtt_avg !== null && d.rtt_avg !== undefined) {{
    rttSub = (d.lbl_rtt_avg||'Avg RTT')+': '+d.rtt_avg+'s \u00b7 '+(d.lbl_median||'median')+': '+d.rtt_med+'s';
  }}
  set('ln-rtt-sub', rttSub);

  // DC chart
  if (window._dcChart && d.dc_ts_vals && d.dc_ts_vals.length > 1) {{
    window._dcChart.data.labels = d.dc_ts_labels;
    window._dcChart.data.datasets[0].data = d.dc_ts_vals;
    var n = d.dc_ts_vals.length || 1;
    window._dcChart.data.datasets[1].data = Array(n).fill(10);
    window._dcChart.data.datasets[2].data = Array(n).fill(7);
    window._dcChart.update('none');
  }}
}};
</script>

"""
        return self._base_html(tr("🏠 Nó Local"), body)
