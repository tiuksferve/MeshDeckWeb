"""
core/metrics_data.py — MetricsDataMixin: estruturas de dados, ingestão de
pacotes e cálculo de métricas para a aba de métricas em tempo real.

CORRECÇÕES (v1.0.3):
  - BUG: dupla ingestão de TELEMETRY_APP eliminada
  - BUG: _node_pos inicializado em _reset_data()
  - BUG: _duplicates windowed (mesma janela de 5 min que _pkt_ids)
  - IMPRECISÃO: P10 corrigido para int(0.1*(n-1))
  - IMPRECISÃO: _ch_util/_air_tx expiram com TTL de 30 min
  - IMPRECISÃO: NAK da rede separado (entrega vs erros de firmware)
  - IMPRECISÃO: n_gps_unique usa _node_pos (coords validadas)
  - MELHORIA: nova secção "Nó Local"
"""
import logging
import math
import time
from datetime import datetime
from i18n import tr

logger = logging.getLogger("MeshDeck.metrics")

_CH_TTL_S = 1800   # 30 min sem actualização → expirar valor de ch_util/air_tx

# Campos de LocalStats e HostMetrics (telemetry.proto), em camelCase como a
# biblioteca os entrega. Os uint64 chegam como texto (JSON do protobuf).
_LOCAL_STATS_FIELDS = (
    "uptimeSeconds", "channelUtilization", "airUtilTx", "numPacketsTx", "numPacketsRx",
    "numPacketsRxBad", "numOnlineNodes", "numTotalNodes", "numRxDupe", "numTxRelay",
    "numTxRelayCanceled", "heapTotalBytes", "heapFreeBytes", "numTxDropped", "noiseFloor",
)
# Ambiente e qualidade do ar: (campo camelCase, etiqueta por traduzir,
# unidade, casas decimais). Só os campos que os sensores comuns enviam.
ENV_FIELDS = (
    ("temperature",        "Temperatura",      "°C",    1),
    ("relativeHumidity",   "Humidade",         "%",     0),
    ("barometricPressure", "Pressão",          "hPa",   0),
    ("iaq",                "IAQ",              "",      0),
    ("gasResistance",      "Gás",              "MΩ",    2),
    ("co2",                "CO₂",              "ppm",   0),
    ("pm25Standard",       "PM2.5",            "µg/m³", 0),
    ("lux",                "Luz",              "lx",    0),
    ("uvLux",              "UV",               "lx",    1),
    ("windSpeed",          "Vento",            "m/s",   1),
    ("windDirection",      "Direcção do vento", "°",    0),
    ("rainfall1h",         "Chuva 1h",         "mm",    1),
    ("rainfall24h",        "Chuva 24h",        "mm",    1),
    ("soilMoisture",       "Humidade do solo", "%",     0),
    ("soilTemperature",    "Temp. do solo",    "°C",    1),
    ("radiation",          "Radiação",         "µR/h",  1),
    ("voltage",            "Tensão (sensor)",  "V",     2),
    ("current",            "Corrente (sensor)", "mA",   0),
)
_ENV_KEYS = {f[0] for f in ENV_FIELDS}

_HOST_FIELDS = ("uptimeSeconds", "freememBytes", "diskfree1Bytes", "diskfree2Bytes",
                "diskfree3Bytes", "load1", "load5", "load15")


# SNR mínimo que o LoRa ainda descodifica, por spreading factor (datasheets
# Semtech SX126x/SX127x): −7,5 dB em SF7 e menos 2,5 dB por cada SF acima.
# As cores do SNR medem a margem até esse limite, por isso dependem do preset:
# −8 dB é confortável em LongFast (SF11) e quase inaudível em ShortFast (SF7).
PRESET_SPREADING_FACTOR = {
    "SHORT_TURBO": 7, "SHORT_FAST": 7, "SHORT_SLOW": 8, "MEDIUM_FAST": 9,
    "MEDIUM_SLOW": 10, "LONG_TURBO": 11, "LONG_FAST": 11, "LONG_MODERATE": 11,
    "LONG_SLOW": 12, "VERY_LONG_SLOW": 12,
}
DEFAULT_SPREADING_FACTOR = 11       # LONG_FAST, o preset de fábrica
SNR_MARGIN_EXCELLENT_DB = 15.0      # margem acima do limite de descodificação
SNR_MARGIN_GOOD_DB = 10.0
SNR_MARGIN_FAIR_DB = 5.0            # abaixo disto: perto de perder pacotes


def snr_floor_db(spreading_factor: int) -> float:
    return -7.5 - 2.5 * (spreading_factor - 7)


def spreading_factor_of(lora) -> int:
    """SF em uso a partir do LoRaConfig (protobuf ou dict com os mesmos nomes).
    Com preset, o preset manda; o campo spread_factor só vale sem preset ou
    para presets que esta tabela ainda não conhece."""
    def field(name):
        return lora.get(name) if isinstance(lora, dict) else getattr(lora, name, None)
    if lora is None:
        return DEFAULT_SPREADING_FACTOR
    preset = field("modem_preset")
    if not isinstance(preset, str) and preset is not None:
        try:
            from meshtastic.protobuf import config_pb2
            preset = config_pb2.Config.LoRaConfig.ModemPreset.Name(int(preset))
        except (ImportError, ValueError):
            preset = None
    use_preset = field("use_preset")
    if use_preset is not False and preset in PRESET_SPREADING_FACTOR:
        return PRESET_SPREADING_FACTOR[preset]
    sf = field("spread_factor")
    if isinstance(sf, int) and 7 <= sf <= 12:
        return sf
    return DEFAULT_SPREADING_FACTOR


class MetricsDataMixin:
    spreading_factor = DEFAULT_SPREADING_FACTOR

    def set_spreading_factor(self, spreading_factor: int) -> bool:
        """Muda a escala do SNR; devolve True se mudou."""
        sf = spreading_factor if 7 <= int(spreading_factor) <= 12 else DEFAULT_SPREADING_FACTOR
        changed = sf != self.spreading_factor
        self.spreading_factor = sf
        return changed

    @property
    def snr_levels(self) -> dict:
        """Limiares de SNR (dB) para o preset actual: verde ≥ good, laranja ≥ fair."""
        floor = snr_floor_db(self.spreading_factor)
        return {"floor": floor, "excellent": floor + SNR_MARGIN_EXCELLENT_DB,
                "good": floor + SNR_MARGIN_GOOD_DB, "fair": floor + SNR_MARGIN_FAIR_DB,
                "spreading_factor": self.spreading_factor}

    def snr_color(self, snr) -> str:
        """'green', 'orange', 'red' ou '' (sem valor)."""
        if snr is None:
            return ""
        levels = self.snr_levels
        return "green" if snr >= levels["good"] else "orange" if snr >= levels["fair"] else "red"


    def _reset_data(self):
        self._start_time = time.time()
        self._node_short: dict = {}
        self._packets: list = []

        # ch_util/air_tx agora guardam {'val': float, 'ts': float} para TTL
        self._ch_util: dict  = {}
        self._air_tx: dict   = {}
        self._ch_util_ts: list = []

        self._snr_values: list = []     # só pacotes recebidos por rádio
        self._hops_values: list = []
        self._rssi_values: list = []
        # Por nó (rádio): últimas amostras de SNR e RSSI, hops do último pacote.
        self._node_radio: dict = {}
        # Pacotes que chegaram sem hops restantes (hop_limit esgotado), sobre
        # os que trazem hop_start (firmware ≥ 2.3) — o denominador.
        self._hop_exhausted: int = 0
        self._hop_measured: int  = 0
        self._mqtt_excluded: int = 0    # pacotes MQTT fora das estatísticas de rádio
        # Nós ouvidos directamente (0 hops, por rádio): {nid: {'n','best','snr','ts'}}
        self._direct: dict = {}
        # Retransmissores: o firmware ≥ 2.6 diz só o ÚLTIMO BYTE do nó que
        # retransmitiu (relay_node). {byte: contagem} e o total com essa info.
        self._relay_counts: dict = {}
        self._relayed_total: int = 0
        self._portnum_counts: dict = {}

        self._battery: dict = {}
        self._voltage: dict = {}
        self._uptime:  dict = {}

        self._sent_ts: dict   = {}
        self._rtt_values: list = []
        self._hw_model: dict  = {}

        self._msgs_sent         = 0
        self._msgs_sent_channel = 0   # broadcast: nunca recebe ACK real do destino
        self._msgs_acked        = 0
        self._msgs_ack_implicit = 0   # confirmadas só por retransmissão ouvida
        self._msgs_naked        = 0
        self._dm_acked          = 0   # a taxa de entrega só faz sentido em DMs
        self._dm_naked          = 0
        # Estado de cada mensagem enviada: 'pending' → 'implicit' → 'ack'/'nak'.
        # Cada mensagem conta num só estado, por isso pendentes = enviadas −
        # confirmadas − falhadas − implícitas, sem nunca crescer sem fim.
        self._sent_state: dict = {}
        self._sent_kind: dict  = {}

        self._pkt_ids: dict    = {}
        self._pkt_ids_ever: int = 0

        # Contadores separados para fiabilidade de rede
        self._routing_acks: int     = 0   # ACK de entrega (requestId, sem erro)
        self._routing_naks: int     = 0   # NAK de entrega (requestId + errorReason)
        self._routing_fw_errs: int  = 0   # Erros internos firmware (sem requestId)

        self._nodes_active_ts: list = []
        self._nb_links: dict  = {}
        self._pkt_intervals: dict = {}

        # FIX: inicializado aqui — antes era lazy-init e não era limpo em reset
        self._node_pos: dict = {}

        # Métricas do nó local
        self._local_nid: str        = ''
        self._local_ch_util: float  = 0.0
        self._local_air_tx: float   = 0.0
        self._local_battery: int    = 0
        self._local_voltage: float  = 0.0
        self._local_uptime: int     = 0
        self._local_hw_model: str   = ''
        self._local_snr_rx: list    = []
        self._local_dc_ts: list     = []

        # LocalStats: contadores do firmware desde o arranque do nó. Guarda-se
        # a amostra anterior para mostrar o que mudou entre dois envios.
        self._local_stats: dict      = {}
        self._local_stats_prev: dict = {}
        self._local_stats_ts: float  = 0.0
        self._noise_ts: list         = []    # (ts, noise floor dBm)
        # HostMetrics: a máquina onde corre o meshtasticd.
        self._host: dict             = {}
        self._host_ts: float         = 0.0

        # Ambiente/qualidade do ar e energia (INA), por nó: últimos valores.
        self._env: dict    = {}    # {nid: {'ts': float, 'values': {campo: valor}}}
        self._env_temp_ts: dict = {}   # {nid: [(ts, °C)]} para o gráfico
        self._power: dict  = {}    # {nid: {'ts': float, 'channels': {n: [V, mA]}}}

    # ── helpers TTL ──────────────────────────────────────────────────────
    def _ch_val(self, nid: str) -> float:
        e = self._ch_util.get(nid)
        if e is None or time.time() - e['ts'] > _CH_TTL_S:
            return 0.0
        return e['val']

    def _air_val(self, nid: str) -> float:
        e = self._air_tx.get(nid)
        if e is None or time.time() - e['ts'] > _CH_TTL_S:
            return 0.0
        return e['val']

    def _ch_util_active(self) -> dict:
        now = time.time()
        return {nid: e['val'] for nid, e in self._ch_util.items()
                if now - e['ts'] <= _CH_TTL_S}

    def _air_tx_active(self) -> dict:
        now = time.time()
        return {nid: e['val'] for nid, e in self._air_tx.items()
                if now - e['ts'] <= _CH_TTL_S}

    def _count_duplicates(self) -> int:
        """Duplicados na janela actual de _pkt_ids (windowed, não cumulativo)."""
        return sum(max(v['count'] - 1, 0) for v in self._pkt_ids.values())

    # ── configuração nó local ─────────────────────────────────────────────
    def set_local_node_id(self, nid: str):
        if nid:
            self._local_nid = nid
            # Invalida o hash para que o próximo tick do timer force setHtml.
            # Não manipula _was_waiting — local_node não está em _WAITING_SECTIONS.
            if hasattr(self, '_local_node_sig'):
                self._local_node_sig = None

    # ── ingestão ─────────────────────────────────────────────────────────
    # ── ingestão ─────────────────────────────────────────────────────────
    def ingest_packet(self, packet: dict, node_data: dict, nid: str = ''):
        """
        Actualização de um nó vinda do worker.

        Sem pacote (lote inicial da NodeDB, leitura do nó local): só os campos
        do nó, identificado por `nid` — sem ele não há a quem os atribuir.
        Com pacote: só para quem não liga raw_packet_received; quem liga
        (desktop e servidor) não deve passar o pacote aqui, ou conta-o duas vezes.
        """
        if not packet:
            self._update_from_node_data(nid, node_data)
            return

        # Garante que o pacote tem o node_data fundido se necessário
        p = dict(packet)
        if node_data:
            if not p.get('shortName'): p['shortName'] = node_data.get('short_name')
            if p.get('hopsAway') is None: p['hopsAway'] = node_data.get('hops_away')

        self.ingest_raw_packet(p)

    def _update_from_node_data(self, nid: str, node_data: dict):
        """Fallback para actualização de métricas sem recepção de pacote real."""
        if not nid or not node_data: return
        ts = time.time()
        if node_data.get('short_name'):
            self._node_short[nid] = node_data['short_name']
        for field, target in [('battery_level', '_battery'), ('voltage', '_voltage'),
                             ('uptime_seconds', '_uptime'), ('hw_model', '_hw_model')]:
            val = node_data.get(field)
            if val is not None and val != '':   # hw_model vazio = ainda desconhecido
                if field == 'voltage': val = round(float(val), 3)
                getattr(self, target)[nid] = val

        # Telemetria com TTL
        ch_util = node_data.get('channel_utilization')
        air_tx  = node_data.get('air_util_tx')
        if ch_util is not None: self._ch_util[nid] = {'val': float(ch_util), 'ts': ts}
        if air_tx  is not None: self._air_tx[nid]  = {'val': float(air_tx),  'ts': ts}

        if self._local_nid and nid == self._local_nid:
            if ch_util is not None: self._local_ch_util = float(ch_util)
            if air_tx is not None:  self._local_air_tx = float(air_tx)
            if node_data.get('battery_level') is not None: self._local_battery = int(node_data['battery_level'])
            if node_data.get('voltage') is not None:       self._local_voltage = round(float(node_data['voltage']), 3)
            if node_data.get('uptime_seconds') is not None: self._local_uptime = int(node_data['uptime_seconds'])
            if node_data.get('hw_model'): self._local_hw_model = node_data['hw_model']

        # Posição
        lat = node_data.get('latitude')
        lon = node_data.get('longitude')
        if lat is not None and lon is not None:
            self.ingest_node_position(nid, lat, lon)

    def ingest_raw_packet(self, packet: dict):
        """
        Master Ingester: processa todos os pacotes recebidos pela rede (RF e MQTT).
        Unifica a detecção de duplicados, contagem de tráfego, telemetria e fiabilidade.
        Garante que loopbacks do nó local são processados para estatísticas de rede.
        """
        ts      = time.time()
        pkt_id  = packet.get('id')
        nid     = packet.get('fromId', '')
        decoded = packet.get('decoded') or {}
        portnum = decoded.get('portnum', 'UNKNOWN_APP')
        snr     = packet.get('rxSnr')
        rssi    = packet.get('rxRssi')
        transport = str(packet.get('transportMechanism') or '')
        via_mqtt = bool(packet.get('viaMqtt')) or 'MQTT' in transport

        # Contexto resolvido pelo worker
        short_name = packet.get('shortName')

        if nid and short_name:
            self._node_short[nid] = short_name

        # Só os pacotes ouvidos pela rádio do nó local medem a rádio: os que
        # chegam por MQTT não têm SNR/RSSI reais (vêm a 0 ou ausentes) e os
        # ecos do próprio nó não foram recebidos de ninguém.
        is_local = bool(self._local_nid) and nid == self._local_nid
        radio = not via_mqtt and not is_local
        if via_mqtt:
            self._mqtt_excluded += 1

        # Hops deste pacote: hop_start − hop_limit (firmware ≥ 2.3). A
        # biblioteca omite campos a 0, por isso hopLimit ausente com hopStart
        # presente quer dizer 0 hops restantes. Sem hopStart, cai no valor
        # guardado na NodeDB (hopsAway), menos fiel mas melhor que nada.
        hops = None
        hop_start = packet.get('hopStart') or 0
        if radio:
            if hop_start:
                hop_limit = packet.get('hopLimit') or 0
                if 0 <= hop_limit <= hop_start:
                    hops = hop_start - hop_limit
                    self._hop_measured += 1
                    if hop_limit == 0 and hop_start > 0:
                        self._hop_exhausted += 1
            if hops is None and packet.get('hopsAway') is not None:
                hops = int(packet.get('hopsAway'))

        # 1. Timeline e Contadores (RF Qual, Tráfego)
        # -------------------------------------------
        self._packets.append((ts, nid, portnum, snr if radio else None, hops, via_mqtt))
        if len(self._packets) > 5000:
            self._packets = self._packets[-4000:]

        if radio and snr is not None:
            val_snr = float(snr)
            self._snr_values.append(val_snr)
            if len(self._snr_values) > 2000: self._snr_values = self._snr_values[-1500:]
            # Perspectiva do nó local (sinal de entrada)
            self._local_snr_rx.append(val_snr)
            if len(self._local_snr_rx) > 500: self._local_snr_rx = self._local_snr_rx[-400:]

        if radio and rssi:          # 0/ausente = sem medição
            self._rssi_values.append(int(rssi))
            if len(self._rssi_values) > 2000: self._rssi_values = self._rssi_values[-1500:]

        if hops is not None:
            self._hops_values.append(int(hops))
            if len(self._hops_values) > 2000: self._hops_values = self._hops_values[-1500:]

        if radio and nid and hops == 0:
            d = self._direct.setdefault(nid, {'n': 0, 'best': None, 'snr': None, 'ts': ts})
            d['n'] += 1
            d['ts'] = ts
            if snr is not None:
                d['snr'] = float(snr)
                d['best'] = float(snr) if d['best'] is None else max(d['best'], float(snr))

        # Num pacote com hops > 0, relay_node é o último salto antes de nós.
        # Num pacote directo seria o próprio emissor, por isso não conta.
        relay = packet.get('relayNode')
        if radio and relay and hops:
            byte = int(relay) & 0xff
            self._relay_counts[byte] = self._relay_counts.get(byte, 0) + 1
            self._relayed_total += 1

        if radio and nid and (snr is not None or rssi):
            entry = self._node_radio.setdefault(nid, {'snr': [], 'rssi': [], 'hops': None, 'n': 0})
            entry['n'] += 1
            if snr is not None: entry['snr'] = (entry['snr'] + [float(snr)])[-20:]
            if rssi:            entry['rssi'] = (entry['rssi'] + [int(rssi)])[-20:]
            if hops is not None: entry['hops'] = hops

        self._portnum_counts[portnum] = self._portnum_counts.get(portnum, 0) + 1

        # 2. Duplicados — FIX: _count_duplicates() recalcula dinamicamente
        # ---------------------------------------------------------------
        if pkt_id and nid:
            if pkt_id in self._pkt_ids:
                self._pkt_ids[pkt_id]['count'] += 1
                self._pkt_ids[pkt_id]['ts'] = ts
            else:
                self._pkt_ids[pkt_id] = {'from': nid, 'ts': ts, 'count': 1}
                self._pkt_ids_ever += 1
            cutoff_ids = ts - 300
            self._pkt_ids = {k: v for k, v in self._pkt_ids.items() if v['ts'] >= cutoff_ids}

        # 3. Telemetria e Saúde (TELEMETRY_APP)
        # -------------------------------------
        if portnum == 'TELEMETRY_APP':
            tel = decoded.get('telemetry', {})
            if tel.get('localStats'):
                self._ingest_local_stats(nid, tel['localStats'], ts)
            if tel.get('hostMetrics'):
                self._ingest_host_metrics(nid, tel['hostMetrics'], ts)
            if nid and (tel.get('environmentMetrics') or tel.get('airQualityMetrics')):
                self._ingest_environment(nid, {**(tel.get('airQualityMetrics') or {}),
                                               **(tel.get('environmentMetrics') or {})}, ts)
            if nid and tel.get('powerMetrics'):
                self._ingest_power(nid, tel['powerMetrics'], ts)
            dm  = tel.get('deviceMetrics', {})
            if dm and nid:
                batt    = dm.get('batteryLevel')
                ch_util = dm.get('channelUtilization')
                air_tx  = dm.get('airUtilTx')
                volt    = dm.get('voltage')
                uptm    = dm.get('uptimeSeconds')

                if batt    is not None: self._battery[nid] = int(batt)
                if volt    is not None: self._voltage[nid] = round(float(volt), 3)
                if uptm    is not None: self._uptime[nid]  = int(uptm)
                if ch_util is not None: self._ch_util[nid] = {'val': float(ch_util), 'ts': ts}
                if air_tx  is not None: self._air_tx[nid]  = {'val': float(air_tx),  'ts': ts}

                # Série temporal ch_util (média activa)
                active_ch = self._ch_util_active()
                if active_ch:
                    avg = sum(active_ch.values()) / len(active_ch)
                    self._ch_util_ts.append((ts, round(avg, 1)))
                    if len(self._ch_util_ts) > 120: self._ch_util_ts = self._ch_util_ts[-120:]

                # Métricas específicas do nó local
                if self._local_nid and nid == self._local_nid:
                    if ch_util is not None: self._local_ch_util = float(ch_util)
                    if air_tx is not None:
                        self._local_air_tx = float(air_tx)
                        # airUtilTx já é a % de TX na última hora (protobuf):
                        # é directamente o duty cycle horário.
                        dc = round(min(float(air_tx), 100.0), 2)
                        self._local_dc_ts.append((ts, dc))
                        if len(self._local_dc_ts) > 120: self._local_dc_ts = self._local_dc_ts[-120:]
                    if batt is not None: self._local_battery = int(batt)
                    if volt is not None: self._local_voltage = round(float(volt), 3)
                    if uptm is not None: self._local_uptime  = int(uptm)

        # 4. Posição (POSITION_APP e NODEINFO_APP)
        # ----------------------------------------
        pos = decoded.get('position', {})
        if pos and nid:
            lat_i = pos.get('latitudeI') or pos.get('latitude_i')
            lon_i = pos.get('longitudeI') or pos.get('longitude_i')
            if lat_i is not None and lon_i is not None:
                lat, lon = lat_i / 1e7, lon_i / 1e7
                if abs(lat) > 0.001 or abs(lon) > 0.001:
                    self._node_pos[nid] = (lat, lon)

        # 5. Vizinhança (NEIGHBORINFO_APP)
        # --------------------------------
        if portnum == 'NEIGHBORINFO_APP':
            ni = decoded.get('neighborinfo', {}) or decoded.get('neighborInfo', {})
            nbs = ni.get('neighbors', [])
            if nid and nbs:
                parsed = []
                for nb in nbs:
                    nb_num = nb.get('nodeId') or nb.get('node_id')
                    if nb_num:
                        parsed.append((f"!{int(nb_num) & 0xffffffff:08x}".lower(), float(nb.get('snr', 0.0))))
                if parsed: self._nb_links[nid] = parsed

        # 6. Fiabilidade de Entrega (ROUTING_APP)
        # ---------------------------------------
        if portnum == 'ROUTING_APP':
            routing    = decoded.get('routing', {}) or {}
            err        = (routing.get('errorReason', 'NONE') or 'NONE').upper()
            request_id = decoded.get('requestId', 0)
            has_err    = (err != 'NONE' and err != '')

            if has_err:
                if request_id: self._routing_naks += 1     # NAK de entrega
                else:          self._routing_fw_errs += 1  # Erro interno
            elif request_id:
                self._routing_acks += 1                    # ACK de entrega

        # 7. Hardware Model (NODEINFO_APP)
        # --------------------------------
        if portnum == 'NODEINFO_APP' and nid:
            hw = (decoded.get('user', {}) or {}).get('hwModel', '')
            if hw:
                self._hw_model[nid] = hw
                if self._local_nid and nid == self._local_nid:
                    self._local_hw_model = hw

        # 8. Intervalo entre pacotes por nó
        # ---------------------------------
        if nid:
            entry = self._pkt_intervals.setdefault(nid, {'last': None, 'vals': []})
            if entry['last'] is not None:
                interval = ts - entry['last']
                if 1 < interval < 3600:
                    entry['vals'].append(round(interval, 1))
                    if len(entry['vals']) > 100: entry['vals'] = entry['vals'][-80:]
            entry['last'] = ts

        # 9. Histórico de Nós Activos (a cada 60s)
        # ----------------------------------------
        if not self._nodes_active_ts or ts - self._nodes_active_ts[-1][0] >= 60:
            cutoff = ts - 7200
            active = len(set(p[1] for p in self._packets if p[0] >= cutoff))
            self._nodes_active_ts.append((ts, active))
            if len(self._nodes_active_ts) > 120: self._nodes_active_ts = self._nodes_active_ts[-120:]

    @staticmethod
    def _numbers(raw: dict, fields) -> dict:
        out = {}
        for key in fields:
            val = raw.get(key)
            if val is None:
                continue
            try:
                out[key] = float(val) if key in ("channelUtilization", "airUtilTx") else int(val)
            except (TypeError, ValueError):
                pass
        return out

    def _ingest_local_stats(self, nid: str, raw: dict, ts: float):
        """LocalStats só sai do nó local para a app (nunca vai para a rede)."""
        stats = self._numbers(raw, _LOCAL_STATS_FIELDS)
        # A biblioteca omite os campos a 0: um contador ausente vale 0.
        for key in _LOCAL_STATS_FIELDS:
            if key not in ("channelUtilization", "airUtilTx", "noiseFloor"):
                stats.setdefault(key, 0)
        logger.debug("LocalStats de %s: rx=%s tx=%s rx_bad=%s dupe=%s relay=%s/%s dropped=%s "
                     "noise=%s online=%s/%s", nid, stats["numPacketsRx"], stats["numPacketsTx"],
                     stats["numPacketsRxBad"], stats["numRxDupe"], stats["numTxRelay"],
                     stats["numTxRelayCanceled"], stats["numTxDropped"], stats.get("noiseFloor"),
                     stats["numOnlineNodes"], stats["numTotalNodes"])
        # Reinício do nó (uptime a recuar): os contadores recomeçam, não há delta.
        prev = self._local_stats
        restarted = bool(prev) and stats["uptimeSeconds"] < prev.get("uptimeSeconds", 0)
        self._local_stats_prev = {} if restarted else prev
        self._local_stats = stats
        self._local_stats_ts = ts
        if stats.get("noiseFloor"):
            self._noise_ts.append((ts, stats["noiseFloor"]))
            if len(self._noise_ts) > 120: self._noise_ts = self._noise_ts[-120:]
        # Também trazem utilização do canal, airtime e uptime do nó local.
        if not self._local_nid or nid == self._local_nid:
            if "channelUtilization" in stats: self._local_ch_util = stats["channelUtilization"]
            if "airUtilTx" in stats:
                self._local_air_tx = stats["airUtilTx"]
                self._local_dc_ts.append((ts, round(min(stats["airUtilTx"], 100.0), 2)))
                if len(self._local_dc_ts) > 120: self._local_dc_ts = self._local_dc_ts[-120:]
            if stats["uptimeSeconds"]: self._local_uptime = stats["uptimeSeconds"]

    def _ingest_host_metrics(self, nid: str, raw: dict, ts: float):
        host = self._numbers(raw, _HOST_FIELDS)
        if raw.get("userString"):
            host["userString"] = str(raw["userString"])
        logger.debug("HostMetrics de %s: load1=%s freemem=%s disk1=%s uptime=%s", nid,
                     host.get("load1"), host.get("freememBytes"), host.get("diskfree1Bytes"),
                     host.get("uptimeSeconds"))
        self._host = host
        self._host_ts = ts

    def _ingest_environment(self, nid: str, raw: dict, ts: float):
        values = {}
        for key in _ENV_KEYS:
            val = raw.get(key)
            if isinstance(val, (int, float)) and not (isinstance(val, float) and math.isnan(val)):
                values[key] = float(val)
        if not values:
            return
        entry = self._env.setdefault(nid, {'ts': ts, 'values': {}})
        entry['ts'] = ts
        entry['values'].update(values)        # um envio pode trazer só parte
        if 'temperature' in values:
            series = self._env_temp_ts.setdefault(nid, [])
            series.append((ts, round(values['temperature'], 1)))
            if len(series) > 120: del series[:-120]

    def _ingest_power(self, nid: str, raw: dict, ts: float):
        channels = {}
        for ch in range(1, 9):
            v, i = raw.get(f"ch{ch}Voltage"), raw.get(f"ch{ch}Current")
            if v is not None or i is not None:
                channels[ch] = [round(float(v), 2) if v is not None else None,
                                round(float(i)) if i is not None else None]
        if channels:
            entry = self._power.setdefault(nid, {'ts': ts, 'channels': {}})
            entry['ts'] = ts
            entry['channels'].update(channels)

    def _data_environment(self) -> dict:
        """Últimos valores de ambiente e energia por nó, e a temperatura no tempo."""
        now = time.time()
        def ago(ts):
            m = int((now - ts) // 60)
            return tr("agora") if m < 1 else tr("há {n} min", n=m) if m < 60 else tr("há {n} h", n=m // 60)
        cols = [f for f in ENV_FIELDS if any(f[0] in e['values'] for e in self._env.values())]
        env_rows = []
        for nid, e in sorted(self._env.items(), key=lambda x: self._name(x[0]).lower()):
            cells = []
            for key, _label, unit, dec in cols:
                v = e['values'].get(key)
                cells.append(None if v is None else f"{v:.{dec}f}{' ' + unit if unit else ''}")
            env_rows.append([nid, self._name(nid), cells, ago(e['ts']), now - e['ts'] > 7200])
        power_rows = []
        for nid, p in sorted(self._power.items(), key=lambda x: self._name(x[0]).lower()):
            parts = []
            for ch, (v, i) in sorted(p['channels'].items()):
                txt = " · ".join(x for x in (f"{v} V" if v is not None else "", f"{i} mA" if i is not None else "") if x)
                parts.append(f"ch{ch}: {txt}")
            power_rows.append([nid, self._name(nid), parts, ago(p['ts'])])
        temps = {nid: e['values']['temperature'] for nid, e in self._env.items() if 'temperature' in e['values']}
        hottest = max(temps, key=temps.get) if temps else None
        coldest = min(temps, key=temps.get) if temps else None
        # Gráfico: os 6 nós com mais amostras, cada um na sua linha.
        series = sorted(self._env_temp_ts.items(), key=lambda x: -len(x[1]))[:6]
        return {
            "n_env": len(self._env), "n_power": len(self._power),
            "cols": [[tr(label), unit] for _k, label, unit, _d in cols],
            "env_rows": env_rows, "power_rows": power_rows,
            "temp_avg": round(sum(temps.values()) / len(temps), 1) if temps else None,
            "temp_max": [self._name(hottest), round(temps[hottest], 1)] if hottest else None,
            "temp_min": [self._name(coldest), round(temps[coldest], 1)] if coldest else None,
            "temp_series": [{"name": self._name(nid),
                             "points": [{"x": round(t * 1000), "y": v} for t, v in pts]}
                            for nid, pts in series],
            "now": self._now_str(),
        }

    # Limiares indicativos da saúde do rádio (LocalStats), revistos com 22 h
    # de dados reais (rede de mais de 200 nós, LongFast, nó mal posicionado).
    # Repetidos: numa rede densa cada pacote chega por vários retransmissores
    # (66 % ≈ 3 cópias) e uma antena melhor ouve ainda mais cópias, por isso
    # só 80 % (5 cópias) é aviso. Corrompidos e ruído dependem do local da
    # antena; ficam exigentes para continuarem a apontar uma má posição.
    DUPE_WARN, DUPE_CRIT = 80.0, 90.0     # % de RX que eram repetições
    BAD_WARN, BAD_CRIT   = 5.0, 15.0      # % de RX corrompidos (ruído/colisões)
    NOISE_WARN, NOISE_CRIT = -105, -95    # dBm: acima disto o ruído de fundo é alto

    def _data_radio_health(self) -> dict:
        """Saúde do rádio local (LocalStats) e da máquina (HostMetrics)."""
        s = self._local_stats
        if not s:
            return {"has_stats": False, "has_host": bool(self._host), **self._data_host()}
        rx, tx = s["numPacketsRx"], s["numPacketsTx"]
        relays = s["numTxRelay"] + s["numTxRelayCanceled"]
        pct = lambda a, b: round(a / b * 100, 1) if b else None
        prev = self._local_stats_prev
        delta = {}
        if prev:
            minutes = max((s["uptimeSeconds"] - prev.get("uptimeSeconds", 0)) / 60, 0)
            for key in ("numPacketsRx", "numPacketsTx", "numRxDupe", "numPacketsRxBad",
                        "numTxRelay", "numTxDropped"):
                delta[key] = max(s[key] - prev.get(key, 0), 0)
            delta["minutes"] = round(minutes, 1)
        return {
            "has_stats": True, "has_host": bool(self._host),
            "rx": rx, "tx": tx, "rx_bad": s["numPacketsRxBad"], "dupe": s["numRxDupe"],
            "relay": s["numTxRelay"], "relay_canceled": s["numTxRelayCanceled"],
            "dropped": s["numTxDropped"],
            "dupe_pct": pct(s["numRxDupe"], rx),
            "bad_pct": pct(s["numPacketsRxBad"], rx),
            "relay_cancel_pct": pct(s["numTxRelayCanceled"], relays),
            "drop_pct": pct(s["numTxDropped"], tx + s["numTxDropped"]),
            "noise": s.get("noiseFloor"),
            "online": s["numOnlineNodes"], "total": s["numTotalNodes"],
            "heap_free": s["heapFreeBytes"], "heap_total": s["heapTotalBytes"],
            "uptime": s["uptimeSeconds"],
            "delta": delta,
            "stats_at": datetime.fromtimestamp(self._local_stats_ts).strftime('%H:%M:%S'),
            "noise_ts_labels": [self._ts_label(t) for t, _ in self._noise_ts],
            "noise_ts_vals": [v for _, v in self._noise_ts],
            **self._data_host(),
        }

    def _data_host(self) -> dict:
        h = self._host
        if not h:
            return {}
        gb = lambda b: round(b / 1e9, 1) if b else None
        return {
            "host_load": [round(h.get(k, 0) / 100, 2) for k in ("load1", "load5", "load15")],
            "host_mem_mb": round(h["freememBytes"] / 1e6) if h.get("freememBytes") else None,
            "host_disk_gb": [gb(h.get(k)) for k in ("diskfree1Bytes", "diskfree2Bytes", "diskfree3Bytes")],
            "host_uptime": h.get("uptimeSeconds"),
            "host_user": h.get("userString", ""),
            "host_at": datetime.fromtimestamp(self._host_ts).strftime('%H:%M:%S'),
        }

    def ingest_node_position(self, nid: str, lat: float, lon: float):
        """FIX: _node_pos inicializado em _reset_data; filtra coords (0,0)."""
        if nid and lat is not None and lon is not None:
            if abs(lat) > 0.001 or abs(lon) > 0.001:
                self._node_pos[nid] = (lat, lon)

    def ingest_message_status(self, req_id: int, status: str):
        """ACK real, NAK ou ACK implícito (o nó ouviu alguém retransmitir).

        Uma mensagem passa por pending → implicit → ack/nak; cada transição
        tira-a de um contador e põe-na noutro. Uma mensagem de canal fica em
        'implicit': em broadcast não há destinatário que devolva ACK.
        """
        prev = self._sent_state.get(req_id)
        if prev not in ('pending', 'implicit'):
            return                      # desconhecida ou já resolvida
        is_dm = self._sent_kind.get(req_id) == 'dm'
        if status in ('ack', 'nak') and prev == 'implicit':
            self._msgs_ack_implicit -= 1
        if status == 'nak':
            self._msgs_naked += 1
            if is_dm: self._dm_naked += 1
            self._sent_state[req_id] = 'nak'
            self._sent_ts.pop(req_id, None)
        elif status == 'ack':
            self._msgs_acked += 1
            if is_dm: self._dm_acked += 1
            self._sent_state[req_id] = 'ack'
            sent_at = self._sent_ts.pop(req_id, None)
            if sent_at is not None:
                rtt = round(time.time() - sent_at, 2)
                if 0 < rtt < 300:
                    self._rtt_values.append(rtt)
                    if len(self._rtt_values) > 200:
                        self._rtt_values = self._rtt_values[-150:]
        elif status == 'ack_implicit' and prev == 'pending':
            self._msgs_ack_implicit += 1
            self._sent_state[req_id] = 'implicit'
        self._refresh_if_reliability()

    def ingest_message_sent(self, packet_id: int, kind: str = 'dm'):
        """kind: 'dm' (espera ACK do destinatário) ou 'channel' (broadcast)."""
        self._msgs_sent += 1
        if kind == 'channel':
            self._msgs_sent_channel += 1
        if packet_id:
            self._sent_state[packet_id] = 'pending'
            self._sent_kind[packet_id] = kind
            self._sent_ts[packet_id] = time.time()
            # Limita a memória numa sessão longa; os contadores mantêm-se.
            if len(self._sent_state) > 2000:
                for old in list(self._sent_state)[:500]:
                    self._sent_state.pop(old, None)
                    self._sent_kind.pop(old, None)
                    self._sent_ts.pop(old, None)
        self._refresh_if_reliability()

    def _pending(self) -> int:
        return max(self._msgs_sent - self._msgs_acked - self._msgs_naked
                   - self._msgs_ack_implicit, 0)

    def _delivery(self):
        """Taxa de entrega das DMs: ACK do destinatário ÷ (ACK + NAK).
        As mensagens de canal não entram — nunca têm ACK real."""
        total = self._dm_acked + self._dm_naked
        return round(self._dm_acked / total * 100, 1) if total > 0 else None

    def _refresh_if_reliability(self):
        if getattr(self, '_current_key', None) in ('reliability', 'local_node'):
            self._refresh_current()

    def ingest_neighbor_info(self, from_id: str, neighbors: list):
        """Wrapper para compatibilidade se chamado manualmente."""
        if from_id and neighbors:
            self._nb_links[from_id] = neighbors

    # ── helpers ──────────────────────────────────────────────────────────
    def _ts_label(self, ts: float) -> str:
        return datetime.fromtimestamp(ts).strftime('%H:%M')

    def _now_str(self) -> str:
        return datetime.now().strftime('%H:%M:%S')

    def _name(self, nid: str) -> str:
        return self._node_short.get(nid, nid) if nid else nid

    # ── data functions ────────────────────────────────────────────────────
    def _data_overview(self) -> dict:
        now        = time.time()
        total_pkts = len(self._packets)
        active_nids = set(p[1] for p in self._packets if p[0] >= now-7200 and p[1])
        ppm        = len([p for p in self._packets if p[0] >= now-60])
        snr_avg    = round(sum(self._snr_values)/len(self._snr_values), 1) if self._snr_values else None
        hops_avg   = round(sum(self._hops_values)/len(self._hops_values), 2) if self._hops_values else None
        ch_active  = self._ch_util_active()
        air_active = self._air_tx_active()
        ch_avg     = round(sum(ch_active.values())/len(ch_active), 1) if ch_active else None
        air_avg    = round(sum(air_active.values())/len(air_active), 1) if air_active else None
        delivery   = self._delivery()
        nid_counts = {}
        for p in self._packets:
            if p[1]: nid_counts[p[1]] = nid_counts.get(p[1], 0) + 1
        top = sorted(nid_counts.items(), key=lambda x: -x[1])[:8]
        # FIX: 5 colunas — nid, nome, pacotes, ch_util, bateria
        table_rows = [
            [nid, self._name(nid), cnt, round(self._ch_val(nid), 1), self._battery.get(nid)]
            for nid, cnt in top
        ]
        return {"total_pkts":total_pkts, "n_active":len(active_nids), "ppm":ppm,
                "snr_avg":snr_avg, "hops_avg":hops_avg, "ch_avg":ch_avg, "air_avg":air_avg,
                "delivery":delivery, "table_rows":table_rows, "now":self._now_str(),
                "unit_nos": " " + tr("nós"), "now_label": tr("Actualizado:")}

    def _data_channel(self) -> dict:
        ch_active  = self._ch_util_active()
        air_active = self._air_tx_active()
        duty       = {nid: round(min(a, 100.0), 2) for nid, a in air_active.items()}
        duty_avg   = round(sum(duty.values())/len(duty), 2) if duty else None
        worst_nid  = max(duty, key=duty.get) if duty else None
        worst_dc   = duty[worst_nid] if worst_nid else None
        worst_name = self._name(worst_nid) if worst_nid else "—"
        air_avg    = round(sum(air_active.values())/len(air_active), 2) if air_active else None
        ch_net_avg = round(sum(ch_active.values())/len(ch_active), 1) if ch_active else None
        ts_labels  = [self._ts_label(t) for t,_ in self._ch_util_ts]
        ts_vals    = [v for _,v in self._ch_util_ts]
        rows = []
        all_nids = sorted(set(list(ch_active.keys()) + list(air_active.keys())))
        for nid in all_nids:
            ch  = ch_active.get(nid, 0)
            air = air_active.get(nid, 0)
            dc  = duty.get(nid, round(min(air, 100.0), 2))
            rows.append([nid, self._name(nid), round(ch,1), round(air,2), dc])
        return {"duty_avg":duty_avg, "worst_dc":worst_dc, "worst_name":worst_name,
                "air_avg":air_avg, "ch_net_avg":ch_net_avg,
                "ts_labels":ts_labels, "ts_vals":ts_vals, "rows":rows, "now":self._now_str(),
                "lbl_exceed": tr("🚨 LIMITE EXCEDIDO"), "lbl_warn": tr("⚠ Próximo do limite"),
                "lbl_ok": tr("lbl_dc_optimal")}

    def _rf_assessment(self, snr_avg, snr_med, snr_p10, hops_values) -> str:
        if snr_avg is None or not self._snr_values:
            return tr("⏳ Aguardando dados suficientes para avaliação...")
        n = len(self._snr_values)
        lv = self.snr_levels
        exc, good, fair = lv["excellent"], lv["good"], lv["fair"]
        pct_exc  = round(sum(1 for v in self._snr_values if v >= exc)         / n * 100)
        pct_good = round(sum(1 for v in self._snr_values if good <= v < exc)  / n * 100)
        pct_marg = round(sum(1 for v in self._snr_values if fair <= v < good) / n * 100)
        pct_weak = round(sum(1 for v in self._snr_values if v < fair)         / n * 100)
        pct_ok   = pct_exc + pct_good
        scale_note = tr("Escala para SF{sf}: o rádio ainda descodifica até {floor} dB de SNR.",
                        sf=lv["spreading_factor"], floor=f"{lv['floor']:g}")
        lines = []
        lines.append(
            f"<b>{tr('Distribuição de qualidade em {n} pacotes:', n=n)}</b> "
            f"<span style='color:#39d353'>{pct_exc}% {tr('excelente')} (≥{exc:g} dB)</span> · "
            f"<span style='color:#56d364'>{pct_good}% {tr('bom')} (≥{good:g} dB)</span> · "
            f"<span style='color:#f0883e'>{pct_marg}% {tr('marginal')} (≥{fair:g} dB)</span> · "
            f"<span style='color:#f85149'>{pct_weak}% {tr('fraco')} (&lt;{fair:g} dB)</span>"
            f"<br><span style='color:#8b949e;font-size:11px'>"
            f"{scale_note}"
            f"</span>"
        )
        if pct_ok >= 80:
            lines.append(tr("✅ Rede em excelentes condições RF. A grande maioria dos pacotes chega com sinal forte."))
        elif pct_ok >= 60:
            lines.append(tr("✅ Qualidade RF boa. A maioria das ligações é estável, com algumas margens."))
        elif pct_ok >= 40:
            lines.append(tr("⚠️ Qualidade RF moderada."))
        else:
            lines.append(tr("🚨 Qualidade RF fraca."))
        if snr_p10 is not None:
            if snr_p10 < fair:   lines.append(tr("⚠️ Pior decil SNR fraco", snr_p10=snr_p10))
            elif snr_p10 < good: lines.append(tr("ℹ️ Pior decil SNR marginal", snr_p10=snr_p10))
            else:             lines.append(tr("✅ Pior decil SNR ok", snr_p10=snr_p10))
        if hops_values:
            avg_hops   = sum(hops_values) / len(hops_values)
            pct_direct = round(hops_values.count(0) / len(hops_values) * 100)
            pct_1hop   = round(hops_values.count(1) / len(hops_values) * 100)
            max_hops   = max(hops_values)
            lines.append(tr("topologia", pct_direct=f"{pct_direct:.0f}", pct_1hop=f"{pct_1hop:.0f}",
                            avg_hops=avg_hops, max_hops=max_hops))
            if avg_hops > 2.5: lines.append(tr("⚠️ Média de hops elevada"))
            if self._hop_measured >= 20:
                exh = self._hop_exhausted / self._hop_measured * 100
                if exh >= self.HOP_EXHAUSTED_WARN:
                    lines.append(tr("⚠️ {pct}% dos pacotes chegaram com o hop limit esgotado: o nó está na periferia da rede para muitos emissores. Melhorar a posição da antena ajuda mais do que aumentar o hop limit.",
                                    pct=f"{exh:.0f}"))
            if max_hops >= 6:  lines.append(tr("⚠️ Máximo de hops", max_hops=max_hops))
        if pct_ok >= 70 and (not hops_values or sum(hops_values)/len(hops_values) < 2):
            lines.append(tr("conclusao_verde"))
        elif pct_ok >= 50:
            lines.append(tr("conclusao_amarela"))
        else:
            lines.append(tr("conclusao_vermelha"))
        return "<br>".join(lines)

    def _data_rf(self) -> dict:
        def histogram(vals, bucket=2, mn=-20, mx=14):
            buckets = list(range(mn, mx+bucket, bucket))
            counts  = [0]*len(buckets)
            for v in vals:
                idx = min(int((v-mn)//bucket), len(counts)-1)
                idx = max(0, idx)
                counts[idx] += 1
            return [str(b) for b in buckets], counts
        snr_labels, snr_counts = histogram(self._snr_values) if self._snr_values else ([], [])
        max_hops  = max(self._hops_values) if self._hops_values else 7
        hop_labels = [str(i) for i in range(0, min(max_hops+2, 9))]
        hop_counts = [self._hops_values.count(i) for i in range(len(hop_labels))]
        n          = len(self._snr_values)
        snr_sorted = sorted(self._snr_values)
        snr_avg    = round(sum(snr_sorted)/n, 1) if n else None
        snr_med    = round(snr_sorted[n//2], 1)  if n else None
        # FIX: percentil correcto — int(0.1*(n-1)) em vez de n//10
        snr_p10    = round(snr_sorted[int(0.1*(n-1))], 1) if n else None
        assessment = self._rf_assessment(snr_avg, snr_med, snr_p10, self._hops_values)
        return {**self._data_rf_radio(),
                "n":n, "snr_avg":snr_avg, "snr_med":snr_med, "snr_p10":snr_p10,
                "snr_labels":snr_labels, "snr_counts":snr_counts,
                "hop_labels":hop_labels, "hop_counts":hop_counts,
                "assessment": assessment, "unit_amostras": tr("amostras"),
                "lbl_median": tr("mediana"),
                "p10_note": tr("(mínimo se n<10)") if n and n < 10 else ""}

    # Limiares do diagnóstico por nó. Em LoRa o RSSI mede tudo o que chega
    # (sinal + ruído) e o SNR quanto o sinal sobressai do ruído: SNR baixo
    # com RSSI forte aponta para ruído/interferência; com RSSI fraco, para
    # distância ou obstáculos. São indicativos, não um diagnóstico certo.
    # O SNR só merece diagnóstico abaixo do patamar "laranja" da escala do
    # preset (snr_levels["fair"], a 5 dB do limite de descodificação).
    RSSI_STRONG_DBM = -100
    # % de pacotes sem hops restantes. Mede sobretudo a posição do nó na rede
    # (na periferia, muitos pacotes chegam no fim do percurso): ficam abaixo
    # dos 43 % medidos com a antena mal posicionada, para os continuar a mostrar.
    HOP_EXHAUSTED_WARN = 20.0
    HOP_EXHAUSTED_CRIT = 40.0

    def radio_diagnosis(self, snr, rssi) -> str:
        """'ok', 'noise', 'weak' ou '' (sem dados)."""
        if snr is None:
            return ''
        if snr >= self.snr_levels["fair"]:
            return 'ok'
        if rssi is not None and rssi > self.RSSI_STRONG_DBM:
            return 'noise'
        return 'weak'

    def _data_rf_radio(self) -> dict:
        """RSSI, hop limit esgotado e a tabela SNR × RSSI por nó."""
        rssi = sorted(self._rssi_values)
        n_rssi = len(rssi)
        exhausted_pct = (round(self._hop_exhausted / self._hop_measured * 100, 1)
                         if self._hop_measured else None)
        node_rows = []
        for nid, e in self._node_radio.items():
            snr_avg = round(sum(e['snr']) / len(e['snr']), 1) if e['snr'] else None
            rssi_avg = round(sum(e['rssi']) / len(e['rssi'])) if e['rssi'] else None
            node_rows.append([nid, self._name(nid), e['n'], snr_avg, rssi_avg, e['hops'],
                              self.radio_diagnosis(snr_avg, rssi_avg)])
        # Os piores primeiro: é a tabela onde se procura o problema.
        node_rows.sort(key=lambda r: (r[3] is None, r[3] if r[3] is not None else 0))
        return {
            "rssi_avg": round(sum(rssi) / n_rssi) if n_rssi else None,
            "rssi_med": rssi[n_rssi // 2] if n_rssi else None,
            "n_rssi": n_rssi,
            "hop_exhausted": self._hop_exhausted, "hop_measured": self._hop_measured,
            "hop_exhausted_pct": exhausted_pct,
            "mqtt_excluded": self._mqtt_excluded,
            "node_rows": node_rows,
            "lbl_diag": {"ok": tr("✅ Boa"), "noise": tr("⚡ Ruído/interferência provável"),
                         "weak": tr("📉 Sinal fraco (distância/obstáculos)"), "": "—"},
            "lbl_exhausted_sub": tr("{n} de {m} pacotes chegaram sem hops restantes",
                                    n=self._hop_exhausted, m=self._hop_measured),
            "lbl_mqtt_excluded": tr("{n} pacotes MQTT excluídos", n=self._mqtt_excluded),
        }

    def _data_traffic(self) -> dict:
        now = time.time()
        label_map = {
            "TEXT_MESSAGE_APP":      tr("💬 Mensagem"),
            "NODEINFO_APP":          "🆔 NodeInfo",
            "POSITION_APP":          tr("📍 Posição"),
            "TELEMETRY_APP":         tr("📊 Telemetria"),
            "TRACEROUTE_APP":        "🔍 Traceroute",
            "ROUTING_APP":           "🔀 Routing",
            "NEIGHBORINFO_APP":      "🔗 NeighborInfo",
            "ADMIN_APP":             "⚙ Admin",
            "RANGE_TEST_APP":        "📏 Range Test",
            "STORE_AND_FORWARD_APP": "📦 S&F",
        }
        counts = {}
        for pname, cnt in self._portnum_counts.items():
            lbl = label_map.get(pname, pname.replace("_APP","").replace("_"," ").title())
            counts[lbl] = counts.get(lbl, 0) + cnt
        sc     = sorted(counts.items(), key=lambda x: -x[1])
        labels = [k for k,_ in sc]
        values = [v for _,v in sc]
        n_direct  = sum(1 for p in self._packets if p[4] == 0)
        n_1hop    = sum(1 for p in self._packets if p[4] == 1)
        n_multi   = sum(1 for p in self._packets if p[4] is not None and p[4] >= 2)
        n_unknown = sum(1 for p in self._packets if p[4] is None)
        n_rf   = len([p for p in self._packets if not p[5]])
        n_mqtt = len([p for p in self._packets if p[5]])
        bins = []
        for i in range(29, -1, -1):
            t0 = now-(i+1)*60; t1 = now-i*60
            cnt = len([p for p in self._packets if t0 <= p[0] < t1])
            bins.append((datetime.fromtimestamp(t1).strftime("%H:%M"), cnt))
        return {"labels":labels, "values":values,
                "n_direct":n_direct, "n_1hop":n_1hop, "n_multi":n_multi, "n_unknown":n_unknown,
                "n_rf":n_rf, "n_mqtt":n_mqtt,
                "ppm_labels":[b[0] for b in bins], "ppm_vals":[b[1] for b in bins],
                "lbl_direct": tr("🟢 Directo"), "lbl_1hop": tr("🔵 1 Hop"),
                "lbl_multi":  tr("🟠 Multi-hop"), "lbl_unknown": tr("⚫ Desconhecido")}

    def _data_nodes(self) -> dict:
        now      = time.time()
        cutoff2h = now - 7200
        n_active = len(set(p[1] for p in self._packets if p[0] >= cutoff2h and p[1]))
        batt_real  = {nid: v for nid, v in self._battery.items() if 1 <= v <= 100}
        batt_power = {nid for nid, v in self._battery.items() if v == 101}
        batt_buckets = [0, 0, 0, 0, 0]
        for v in batt_real.values():
            batt_buckets[min(int(v // 20), 4)] += 1
        batt_avg = round(sum(batt_real.values()) / len(batt_real), 0) if batt_real else None
        batt_rows = []
        for nid, v in sorted(self._battery.items(), key=lambda x: x[1]):
            volt = self._voltage.get(nid)
            uptm = self._uptime.get(nid)
            batt_rows.append([nid, v, volt, uptm])
        hw_counts: dict = {}
        for nid, hw in self._hw_model.items():
            hw_counts[hw] = hw_counts.get(hw, 0) + 1
        hw_sorted = sorted(hw_counts.items(), key=lambda x: -x[1])
        # FIX: usa _node_pos (coords validadas) em vez de contar POSITION_APP packets
        n_gps_unique = len(self._node_pos)
        ts_labels = [self._ts_label(t) for t, _ in self._nodes_active_ts]
        ts_vals   = [v for _, v in self._nodes_active_ts]
        return {
            "n_active": n_active,
            "n_battery": len(batt_real), "n_powered": len(batt_power),
            "batt_avg": batt_avg,
            "ts_labels": ts_labels, "ts_vals": ts_vals,
            "batt_buckets": batt_buckets, "batt_rows": batt_rows,
            "hw_labels": [h for h, _ in hw_sorted],
            "hw_values": [c for _, c in hw_sorted],
            "n_gps_unique": n_gps_unique,
            "lbl_powered": tr("{n} com alimentação externa · 📍 {m} com GPS",
                              n=len(batt_power), m=n_gps_unique),
        }

    def _data_reliability(self) -> dict:
        total_resp = self._msgs_acked + self._msgs_naked
        delivery   = self._delivery()
        nak_rate   = round(self._msgs_naked / total_resp * 100, 1) if total_resp > 0 else None
        pending    = self._pending()
        rtt_avg    = round(sum(self._rtt_values)/len(self._rtt_values), 1)      if self._rtt_values else None
        rtt_min    = round(min(self._rtt_values), 1)                             if self._rtt_values else None
        rtt_max    = round(max(self._rtt_values), 1)                             if self._rtt_values else None
        rtt_med    = round(sorted(self._rtt_values)[len(self._rtt_values)//2], 1) if self._rtt_values else None
        total_pkt  = len(self._pkt_ids)
        # FIX: windowed
        duplicates = self._count_duplicates()
        dup_rate   = round(duplicates / max(total_pkt, 1) * 100, 1) if total_pkt > 0 else None
        # FIX: NAK separados
        net_ack_total = self._routing_acks + self._routing_naks
        net_nak_rate  = round(self._routing_naks / net_ack_total * 100, 1) if net_ack_total > 0 else None
        active_senders = len(set(v["from"] for v in self._pkt_ids.values()))
        ch_active  = self._ch_util_active()
        ch_util_avg = (sum(ch_active.values()) / len(ch_active) if ch_active else None)
        p_col = round((1 - math.exp(-ch_util_avg / 100.0)) * 100 * 0.5, 1) if ch_util_avg is not None else None
        return {
            "sent": self._msgs_sent, "acked": self._msgs_acked,
            "ack_implicit": self._msgs_ack_implicit,
            "naked": self._msgs_naked, "pending": pending,
            "delivery": delivery, "nak_rate": nak_rate,
            "rtt_avg": rtt_avg, "rtt_min": rtt_min, "rtt_max": rtt_max, "rtt_med": rtt_med,
            "n_rtt": len(self._rtt_values),
            "total_pkt": total_pkt, "duplicates": duplicates, "dup_rate": dup_rate,
            "net_acks": self._routing_acks, "net_naks": self._routing_naks,
            "fw_errs": self._routing_fw_errs,
            "net_nak_rate": net_nak_rate, "active_senders": active_senders,
            "p_col": p_col,
            "ch_util_avg": round(ch_util_avg, 1) if ch_util_avg is not None else None,
            "lbl_col": (
                tr("Sem dados de Ch.Util.") if p_col is None else
                tr("✅ Baixo")               if p_col < 5  else
                tr("⚠ Próximo do limite")   if p_col < 15 else
                tr("[!] Risco elevado")
            ),
            # O firmware filtra as repetições antes de entregar os pacotes à
            # app: os duplicados vistos aqui não medem o flooding da rede.
            "lbl_pkt_sub": tr("{n} nós emissores", n=active_senders),
            # Duplicados reais: contados pelo firmware do nó local (LocalStats).
            "ls_dupe_pct": (round(self._local_stats["numRxDupe"] / self._local_stats["numPacketsRx"] * 100, 1)
                            if self._local_stats.get("numPacketsRx") else None),
            "lbl_ls_dupe": (tr("{d} repetições em {r} pacotes recebidos (LocalStats, desde o arranque do nó)",
                               d=self._local_stats["numRxDupe"], r=self._local_stats["numPacketsRx"])
                            if self._local_stats else tr("⏳ Aguardando LocalStats do nó local")),
            "sent_dm": self._msgs_sent - self._msgs_sent_channel,
            "sent_channel": self._msgs_sent_channel,
            "lbl_channel": tr("Canal"),
            "lbl_nak_delivery": tr("NAK entrega"),
            "lbl_fw_errors": tr("Erros FW"),
            "ever_seen": self._pkt_ids_ever > 0,
        }

    def _data_latency(self) -> dict:
        n = len(self._rtt_values)
        if n == 0:
            return {"n": 0, "avg": None, "med": None,
                    "min": None, "max": None, "p90": None,
                    "hist_labels": [], "hist_counts": [], "now": self._now_str()}
        s   = sorted(self._rtt_values)
        avg = round(sum(s) / n, 1)
        med = round(s[n // 2], 1)
        mn  = round(s[0], 1)
        mx  = round(s[-1], 1)
        p90 = round(s[int(n * 0.9)], 1)
        buckets = list(range(0, 65, 5))
        counts  = [0] * len(buckets)
        over    = 0
        for v in s:
            if v >= 60:
                over += 1
            else:
                idx = min(int(v // 5), len(counts) - 1)
                counts[idx] += 1
        hist_labels = [f"{b}–{b+5}s" for b in buckets]
        if over:
            hist_labels.append(">60s")
            counts.append(over)
        return {"n": n, "avg": avg, "med": med, "min": mn, "max": mx, "p90": p90,
                "hist_labels": hist_labels, "hist_counts": counts, "now": self._now_str()}

    def _data_neighbors(self) -> dict:
        rows = []
        seen = set()
        for from_id, neighbors in self._nb_links.items():
            for nb_id, snr in neighbors:
                key = tuple(sorted([from_id.lower(), nb_id.lower()]))
                if key in seen:
                    continue
                seen.add(key)
                snr_str = f"{snr:+.1f}" if snr else "—"
                rows.append([from_id, self._name(from_id), nb_id, self._name(nb_id),
                              snr_str, float(snr) if snr else None])
        rows.sort(key=lambda r: r[2], reverse=True)
        return {"rows": rows, "n_links": len(rows), "n_nodes": len(self._nb_links),
                "now": self._now_str()}

    def _data_range_links(self) -> dict:
        import math as _m
        def haversine(lat1, lon1, lat2, lon2):
            R    = 6371.0
            dlat = _m.radians(lat2 - lat1)
            dlon = _m.radians(lon2 - lon1)
            a    = (_m.sin(dlat/2)**2 +
                    _m.cos(_m.radians(lat1)) * _m.cos(_m.radians(lat2)) * _m.sin(dlon/2)**2)
            return R * 2 * _m.atan2(_m.sqrt(a), _m.sqrt(1-a))
        node_pos  = self._node_pos   # FIX: atributo inicializado em _reset_data
        rows      = []
        max_range = None
        max_pair  = ("—", "—")
        seen      = set()
        for from_id, neighbors in self._nb_links.items():
            pos_a = node_pos.get(from_id)
            if not pos_a:
                continue
            for nb_id, snr in neighbors:
                key = tuple(sorted([from_id.lower(), nb_id.lower()]))
                if key in seen:
                    continue
                seen.add(key)
                pos_b = node_pos.get(nb_id)
                if not pos_b:
                    continue
                dist    = round(haversine(pos_a[0], pos_a[1], pos_b[0], pos_b[1]), 3)
                snr_str = f"{snr:+.1f}" if snr else "—"
                rows.append([from_id, self._name(from_id), nb_id, self._name(nb_id),
                              dist, snr_str, snr])
                if max_range is None or dist > max_range:
                    max_range = dist
                    max_pair  = (self._name(from_id), self._name(nb_id))
        rows.sort(key=lambda r: r[4], reverse=True)
        direct = self._data_direct_range(haversine)
        relayers = self._data_relayers()
        return {"rows": rows, "max_range": max_range, "max_pair": max_pair,
                "n_with_gps": len(node_pos), "now": self._now_str(),
                **direct, **relayers}

    def _data_direct_range(self, haversine) -> dict:
        """Distância do nó local a cada nó ouvido directamente (0 hops, rádio)."""
        local = self._node_pos.get(self._local_nid) if self._local_nid else None
        rows = []
        for nid, d in self._direct.items():
            pos = self._node_pos.get(nid)
            dist = round(haversine(local[0], local[1], pos[0], pos[1]), 2) if local and pos else None
            rows.append([nid, self._name(nid), dist, d['best'], d['snr'], d['n']])
        # Com distância primeiro, da maior para a menor; sem GPS no fim.
        rows.sort(key=lambda r: (r[2] is None, -(r[2] or 0)))
        far = next((r for r in rows if r[2] is not None), None)
        return {"direct_rows": rows, "n_direct": len(rows),
                "max_direct": far[2] if far else None, "max_direct_name": far[1] if far else "—",
                "has_local_pos": local is not None}

    def _relay_candidates(self, byte: int) -> list:
        known = set(self._node_short) | set(self._pkt_intervals) | set(self._node_pos)
        out = []
        for nid in known:
            try:
                if int(nid.lstrip('!'), 16) & 0xff == byte:
                    out.append(nid)
            except ValueError:
                continue
        return sorted(out)

    def _data_relayers(self) -> dict:
        """Quem mais retransmite para o nó local. Só se conhece o último byte
        do ID: se vários nós conhecidos o partilham, a linha diz quais."""
        rows = []
        for byte, cnt in sorted(self._relay_counts.items(), key=lambda x: -x[1])[:12]:
            cands = self._relay_candidates(byte)
            name = (self._name(cands[0]) if len(cands) == 1 else
                    " / ".join(self._name(c) for c in cands[:3]) if cands else "")
            rows.append([f"…{byte:02x}", name, cnt,
                         round(cnt / self._relayed_total * 100, 1), len(cands)])
        return {"relay_rows": rows, "relayed_total": self._relayed_total,
                "lbl_unknown_relay": tr("Nó desconhecido"),
                "lbl_relay_hint": tr("relay_hint", n=self._relayed_total),
                "lbl_no_gps": tr("sem GPS"),
                "lbl_ambiguous": tr("ambíguo")}

    # Intervalo médio entre pacotes de um nó. Os firmwares de fábrica enviam
    # telemetria e posição de 15 em 15 ou de 30 em 30 min (mediana real:
    # 24 min); só intervalos curtos pesam no canal e merecem cor de aviso.
    INTERVAL_HIGH_S = 60          # abaixo: vermelho (congestiona o canal)
    INTERVAL_FREQUENT_S = 300     # abaixo: laranja

    def interval_band(self, avg: float) -> tuple:
        """(cor, chave da etiqueta) para um intervalo médio em segundos."""
        if avg < self.INTERVAL_HIGH_S:
            return "red", "high"
        if avg < self.INTERVAL_FREQUENT_S:
            return "orange", "frequent"
        return "green", "normal"

    def _data_intervals(self) -> dict:
        rows = []
        for nid, entry in self._pkt_intervals.items():
            vals = entry['vals']
            if len(vals) < 2:
                continue
            avg = round(sum(vals) / len(vals), 1)
            mn  = round(min(vals), 1)
            mx  = round(max(vals), 1)
            rows.append([nid, self._name(nid), avg, mn, mx, len(vals)])
        rows.sort(key=lambda r: r[1])
        return {"rows": rows, "now": self._now_str(),
                "lbl_freq": {"high": tr("Alta frequência"), "frequent": tr("Frequente"),
                             "normal": "Normal"}}

    def _data_local_node(self) -> dict:
        """Dados da secção dedicada ao nó local."""
        dc_est  = round(min(self._local_air_tx, 100.0), 2)
        snr_avg = (round(sum(self._local_snr_rx)/len(self._local_snr_rx), 1)
                   if self._local_snr_rx else None)
        snr_min = round(min(self._local_snr_rx), 1) if self._local_snr_rx else None
        snr_max = round(max(self._local_snr_rx), 1) if self._local_snr_rx else None

        def fmt_uptime(s):
            if not s: return "—"
            d, rem = divmod(int(s), 86400)
            h, rem = divmod(rem, 3600)
            m, sec = divmod(rem, 60)
            if d: return f"{d}d {h:02d}h {m:02d}m"
            if h: return f"{h}h {m:02d}m"
            return f"{m}m {sec:02d}s"

        total_resp = self._msgs_acked + self._msgs_naked
        delivery   = self._delivery()
        nak_rate   = round(self._msgs_naked / total_resp * 100, 1) if total_resp > 0 else None
        pending    = self._pending()
        rtt_avg    = (round(sum(self._rtt_values)/len(self._rtt_values), 1)
                     if self._rtt_values else None)
        rtt_med    = (round(sorted(self._rtt_values)[len(self._rtt_values)//2], 1)
                     if self._rtt_values else None)

        local_pos    = self._node_pos.get(self._local_nid) if self._local_nid else None
        dc_ts_labels = [self._ts_label(t) for t, _ in self._local_dc_ts]
        dc_ts_vals   = [v for _, v in self._local_dc_ts]

        # Pacotes do nó local observados na rede (loopback pode filtrar alguns)
        local_tx_pkts = (sum(1 for p in self._packets if p[1] == self._local_nid)
                         if self._local_nid else 0)

        return {
            "nid":           self._local_nid or "—",
            "name":          self._name(self._local_nid) if self._local_nid else "—",
            "hw_model":      self._local_hw_model or "—",
            "battery":       self._local_battery,
            "voltage":       self._local_voltage,
            "uptime_fmt":    fmt_uptime(self._local_uptime),
            "uptime_raw":    self._local_uptime,   # segundos — JS incrementa dinamicamente
            "uptime_ts":     round(time.time()),   # quando o uptime_raw foi lido
            "ch_util":       round(self._local_ch_util, 1),
            "air_tx":        round(self._local_air_tx, 2),
            "dc_est":        dc_est,
            "dc_ts_labels":  dc_ts_labels,
            "dc_ts_vals":    dc_ts_vals,
            "snr_rx_avg":    snr_avg,
            "snr_rx_min":    snr_min,
            "snr_rx_max":    snr_max,
            "n_snr_rx":      len(self._local_snr_rx),
            "msgs_sent":     self._msgs_sent,
            "msgs_acked":    self._msgs_acked,
            "msgs_naked":    self._msgs_naked,
            "msgs_implicit": self._msgs_ack_implicit,
            "msgs_pending":  pending,
            "delivery":      delivery,
            "nak_rate":      nak_rate,
            "rtt_avg":       rtt_avg,
            "rtt_med":       rtt_med,
            "n_rtt":         len(self._rtt_values),
            "lat":           local_pos[0] if local_pos else None,
            "lon":           local_pos[1] if local_pos else None,
            "local_tx_pkts": local_tx_pkts,
            "ls_ts":         self._local_stats_ts,
            "host_ts":       self._host_ts,
            "now":           self._now_str(),
            # Labels traduzidos para uso no JS (update sem reload)
            "lbl_dc_optimal":  tr("lbl_dc_optimal"),
            "lbl_dc_warn":     tr("lbl_dc_warn"),
            "lbl_dc_exceeded": tr("lbl_dc_exceeded"),
            "lbl_packets":     tr("pacotes"),
            "lbl_rtt_avg":     tr("RTT médio"),
            "lbl_median":      tr("mediana"),
            "lbl_pend":        tr("Pend."),
        }
