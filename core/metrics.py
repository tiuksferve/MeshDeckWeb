"""
core/metrics.py — Métricas da rede, livres de interface.

MetricsDataMixin (core/metrics_data.py) agrega os pacotes; MetricsRenderMixin
(core/metrics_render.py) devolve o HTML de cada secção, com Chart.js. A
MeshMetrics junta os dois, define os limites do canal e despacha as 12
secções que o servidor serve ao browser.
"""

import hashlib

from core.metrics_data import MetricsDataMixin
from core.metrics_render import MetricsRenderMixin

# (chave, etiqueta por traduzir). A ordem é a que a interface apresenta.
SECTIONS = (
    ("overview",    "📊 Visão Geral"),
    ("local_node",  "🏠 Nó Local"),
    ("channel",     "📡 Canal & Airtime"),
    ("rf",          "📶 Qualidade RF"),
    ("traffic",     "📦 Tráfego"),
    ("nodes",       "🔋 Nós & Bateria"),
    ("environment", "🌡 Ambiente & Energia"),
    ("reliability", "✅ Fiabilidade"),
    ("latency",     "⏱ Latência"),
    ("neighbors",   "🔗 Vizinhança"),
    ("range_links", "📏 Alcance & Links"),
    ("intervals",   "⏰ Intervalos"),
)

SECTION_KEYS = tuple(key for key, _label in SECTIONS)

# Secções que mostram ecrã de espera enquanto não há dados suficientes.
# local_node fica de fora: é gerido pelo hash guard da aba.
WAITING_SECTIONS = frozenset(
    {"intervals", "neighbors", "range_links", "rf", "channel", "nodes", "environment"})

# Campos do nó local cuja mudança obriga a redesenhar a secção: a página
# local_node não tem actualização incremental, por isso só se recarrega
# quando um destes muda.
LOCAL_NODE_SIGNATURE_FIELDS = (
    "ch_util", "air_tx", "dc_est", "battery", "msgs_sent", "msgs_acked",
    "snr_rx_avg", "delivery", "ls_ts", "host_ts",
)


class MeshMetrics(MetricsDataMixin, MetricsRenderMixin):
    """Agregador de métricas utilizável sem interface.

    Alimenta-se com ingest_raw_packet, ingest_packet, ingest_message_sent,
    ingest_message_status, ingest_neighbor_info e ingest_node_position, e
    lê-se com html_for() ou data_for().
    """

    # Limites do canal, da documentação oficial Meshtastic. Viviam na
    # MetricsTab mas é o render que os consome, por isso pertencem aqui.
    CH_UTIL_OK   = 25.0   # abaixo = verde
    CH_UTIL_WARN = 50.0   # abaixo = laranja; acima = vermelho

    def __init__(self):
        super().__init__()
        self._reset_data()

    # -- despacho das secções --------------------------------------------
    def html_for(self, key: str) -> str:
        """HTML completo de uma secção, com Chart.js e estilos incluídos."""
        fn = getattr(self, f"_html_{key}", None)
        if fn is None:
            raise KeyError(f"secção de métricas desconhecida: {key!r}")
        return fn()

    def data_for(self, key: str) -> dict:
        """Dados em bruto de uma secção, para actualização incremental.

        overview não tem _data_overview com a mesma forma das outras; todas
        as secções têm um _data_* excepto local_node, que é renderizado por
        inteiro. Devolve {} quando não existe.
        """
        fn = getattr(self, f"_data_{key}", None)
        return fn() if fn is not None else {}

    def refresh_state(self, key: str) -> dict:
        """O que um cliente precisa para actualizar a secção sem a recarregar.

        has_data: se False → True numa secção com ecrã de espera, o cliente
        recarrega o HTML (o ecrã de espera não tem gráficos para actualizar).
        reload_key: só local_node; muda quando a secção tem de ser redesenhada.
        data: o que vai para window._metricsUpdateData nas restantes.
        """
        if key not in SECTION_KEYS:
            raise KeyError(f"secção de métricas desconhecida: {key!r}")
        state = {"has_data": self.has_data(key) if key in WAITING_SECTIONS else True,
                 "reload_key": "", "data": {}}
        if key == "local_node":
            if not self._local_nid:
                state["reload_key"] = "_waiting"
            else:
                d = self._data_local_node()
                sig = str(tuple(d.get(f) for f in LOCAL_NODE_SIGNATURE_FIELDS))
                state["reload_key"] = hashlib.md5(sig.encode()).hexdigest()
        else:
            state["data"] = self.data_for(key)
        return state

    def has_data(self, key: str) -> bool:
        """Se a secção já tem dados para sair do ecrã de espera."""
        if key == "intervals":
            return bool(self._data_intervals()["rows"])
        if key == "neighbors":
            return bool(self._nb_links)
        if key == "range_links":
            d = self._data_range_links()
            return bool(d["rows"] or d["direct_rows"] or d["relay_rows"])
        if key == "rf":
            return bool(self._snr_values or self._hops_values)
        if key == "channel":
            return bool(self._ch_util_active() or self._air_tx_active() or self._ch_util_ts)
        if key == "nodes":
            return bool(self._battery or self._packets)
        if key == "environment":
            return bool(self._env or self._power)
        return True

    def clear(self):
        """Esquece tudo o que foi recolhido. Usado pelo botão de limpar."""
        self._reset_data()
