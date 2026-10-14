"""
core/node_store.py — Estado dos nós da rede, sem Qt.

Substitui NodeTableModel. O modelo Qt guardava os nós numa lista porque a
vista precisava de números de linha; aqui a chave é o id_string e a ordenação
e filtragem passam para o cliente, que é quem tem o contexto da coluna
escolhida pelo utilizador.

_safe_update vem de models.py sem alterações: é a regra de fusão que impede
uma actualização parcial de apagar campos válidos, e um lastHeard=0 do NodeDB
de sobrescrever um last_heard real.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from constants import logger
from core.serialize import to_epoch, to_jsonable


# Campos que todo o registo enviado ao cliente tem de ter.
#
# _safe_update ignora valores None nos campos protegidos, o que é correcto para
# a fusão mas deixa a chave simplesmente ausente num registo novo. Sem esta
# normalização o servidor enviava dezenas de formas diferentes — um nó sem SNR
# não tinha o campo, em vez de o ter a null — e o cliente teria de testar a
# existência de cada campo. Os campos de telemetria opcional (env_*, pwr_*,
# health_*) continuam a aparecer só quando existem: são dezenas e nem fazem
# sentido para a maioria dos nós.
NODE_PAYLOAD_FIELDS = (
    "id_string", "id_num", "long_name", "short_name", "last_heard",
    "snr", "hops_away", "via_mqtt", "latitude", "longitude", "altitude",
    "battery_level", "voltage", "uptime_seconds",
    "channel_utilization", "air_util_tx",
    "hw_model", "public_key", "last_packet_type",
)


def _safe_update(target: dict, source: dict) -> None:
    """
    Aplica source a target com as seguintes regras:
    - last_heard: só actualiza se o novo valor é um datetime mais recente
      que o existente; nunca sobrescreve datetime com None, 0 ou outro tipo
    - Campos numéricos (snr, hops_away, battery_level, latitude, longitude,
      altitude): só actualiza se o novo valor não é None
    - Campos string (long_name, short_name, hw_model, public_key): só
      actualiza se o novo valor é não-vazio
    - Campos bool e outros campos simples: actualiza sempre
    - Campos técnicos internos (id_string, last_packet): actualiza sempre

    Esta função resolve o problema de node_record_from_nodedb sobrescrever
    last_heard válido com lastHeard=0 do NodeDB, e de dict.update() apagar
    campos reais com None de actualizações parciais.
    """
    # Campos que nunca devem ser sobrescritos por None ou valor falsy
    _no_overwrite_none = {
        'snr', 'hops_away', 'battery_level',
        'latitude', 'longitude', 'altitude',
        'voltage', 'uptime_seconds', 'channel_utilization', 'air_util_tx',
    }
    # Campos string que não devem ser sobrescritos por strings vazias
    _no_overwrite_empty_str = {'long_name', 'short_name', 'hw_model', 'public_key'}

    for key, val in source.items():
        if key == 'last_heard':
            # Só actualiza last_heard se o novo valor é um datetime
            # e é mais recente que o existente (ou não há valor existente)
            existing = target.get('last_heard')
            if isinstance(val, datetime):
                if not isinstance(existing, datetime) or val > existing:
                    target['last_heard'] = val
            # Se val não é datetime (ex: 0, None, int) — ignora completamente
        elif key in _no_overwrite_none:
            if val is not None:
                target[key] = val
        elif key in _no_overwrite_empty_str:
            if val:  # ignora strings vazias
                target[key] = val
        else:
            target[key] = val


def node_record_from_nodedb(num, node: dict) -> Optional[tuple]:
    """Converte uma entrada do nodesByNum no registo usado pelo store.

    Devolve (id_string, dados) ou None se não for possível determinar um ID.
    Esta conversão estava duplicada entre o _emit_node do worker e o
    _on_nodes_batch da janela principal; é a mesma em ambos os sítios.
    """
    if not isinstance(node, dict):
        return None
    user = node.get('user', {}) or {}
    nid = user.get('id')
    if not nid:
        if num is None:
            return None
        try:
            nid = f"!{int(num) & 0xffffffff:08x}"
        except (TypeError, ValueError):
            return None
    nid = nid.lower()

    # lastHeard=0 significa "desconhecido" — converte para None
    last_heard = None
    last_heard_raw = node.get('lastHeard')
    if last_heard_raw:
        try:
            last_heard = datetime.fromtimestamp(int(last_heard_raw))
        except (TypeError, ValueError, OSError, OverflowError):
            pass

    pos = node.get('position', {}) or {}
    lat_i = pos.get('latitudeI')
    lon_i = pos.get('longitudeI')

    return nid, {
        "id_num":        num,
        "long_name":     user.get('longName', ''),
        "short_name":    user.get('shortName', ''),
        "last_heard":    last_heard,
        "snr":           node.get('snr'),
        "hops_away":     node.get('hopsAway'),
        "via_mqtt":      node.get('viaMqtt'),
        "latitude":      lat_i / 1e7 if lat_i is not None else None,
        "longitude":     lon_i / 1e7 if lon_i is not None else None,
        "altitude":      pos.get('altitude'),
        "battery_level": (node.get('deviceMetrics', {}) or {}).get('batteryLevel'),
        "hw_model":      user.get('hwModel', ''),
        "public_key":    user.get('publicKey', ''),
    }


class NodeStore:
    """Registo dos nós vistos na rede, indexado por id_string em minúsculas."""

    ONLINE_WINDOW = timedelta(hours=2)

    def __init__(self):
        self._nodes: Dict[str, Dict[str, Any]] = {}
        self._local_id:  Optional[str] = None   # ID canónico !hex, minúsculas
        self._local_num: Optional[int] = None   # nodeNum, bloqueado cedo

    # -- nó local ---------------------------------------------------------
    def set_local_node(self, node_id: Optional[str], node_num: Optional[int] = None):
        """Registar isto antes do primeiro batch evita um nó local duplicado."""
        self._local_id = node_id.lower() if node_id else None
        if node_num is not None:
            try:
                self._local_num = int(node_num) & 0xFFFFFFFF
            except (TypeError, ValueError):
                self._local_num = None

    @property
    def local_id(self) -> Optional[str]:
        return self._local_id

    def is_local(self, node_id: str, node_num=None) -> bool:
        if self._local_num is not None and node_num is not None:
            try:
                if (int(node_num) & 0xFFFFFFFF) == self._local_num:
                    return True
            except (TypeError, ValueError):
                pass
        return bool(self._local_id and node_id
                    and node_id.lower() == self._local_id)

    # -- escrita ----------------------------------------------------------
    def update(self, node_id: str, data: Dict[str, Any], packet=None) -> bool:
        """Funde `data` no registo do nó. Devolve True se o nó é novo."""
        nid = node_id.lower() if node_id else ""
        if not nid:
            return False

        existing = self._nodes.get(nid)
        if existing is None:
            record = {"id_string": nid}
            _safe_update(record, data)
            if packet is not None:
                record["last_packet"] = packet
            self._nodes[nid] = record
            logger.info("NodeStore: novo nó %s%s",
                        nid, "  [LOCAL]" if self.is_local(nid, data.get("id_num")) else "")
            return True

        _safe_update(existing, data)
        if packet is not None:
            existing["last_packet"] = packet
        return False

    def apply_batch(self, batch: list) -> List[str]:
        """Aplica um nodes_batch do worker. Devolve os ids dos nós novos."""
        inserted = []
        for num, node in batch:
            parsed = node_record_from_nodedb(num, node)
            if parsed is None:
                continue
            nid, data = parsed
            if self.update(nid, data):
                inserted.append(nid)
        return inserted

    def clear(self):
        self._nodes.clear()

    # -- leitura ----------------------------------------------------------
    def get(self, node_id: str) -> Optional[Dict[str, Any]]:
        return self._nodes.get(node_id.lower()) if node_id else None

    def all(self) -> List[Dict[str, Any]]:
        return list(self._nodes.values())

    def ids(self) -> Set[str]:
        return set(self._nodes)

    def count(self) -> int:
        return len(self._nodes)

    def contacted_count(self) -> int:
        """Nós já contactados (com last_heard), excluindo o nó local.

        É o total que a barra de estado mostra; é independente da pesquisa.
        """
        return sum(1 for n in self._nodes.values()
                   if isinstance(n.get("last_heard"), datetime)
                   and not self._is_local_record(n))

    def online_count(self, window: timedelta = None) -> int:
        """Nós activos na janela (2 horas por omissão), excluindo o nó local."""
        cutoff = datetime.now() - (window or self.ONLINE_WINDOW)
        return sum(1 for n in self._nodes.values()
                   if isinstance(n.get("last_heard"), datetime)
                   and n["last_heard"] >= cutoff
                   and not self._is_local_record(n))

    def choices(self) -> List[tuple]:
        """(id, nome longo, nome curto) para os selectores de destino."""
        return [(n.get("id_string", ""), n.get("long_name", ""), n.get("short_name", ""))
                for n in self._nodes.values()]

    def with_position(self) -> List[Dict[str, Any]]:
        return [n for n in self._nodes.values()
                if n.get("latitude") is not None and n.get("longitude") is not None]

    def _is_local_record(self, record: dict) -> bool:
        return self.is_local(record.get("id_string", ""), record.get("id_num"))

    # -- snapshot para o browser ------------------------------------------
    def payload(self, record: dict, include_packet: bool = False) -> Dict[str, Any]:
        """Um registo em forma estável e serializável para JSON.

        Todos os campos de NODE_PAYLOAD_FIELDS estão presentes, a None quando
        desconhecidos. last_heard sai em segundos desde a época, que é o que o
        JS precisa. O pacote bruto fica de fora por omissão: são centenas e o
        cliente só precisa de um quando o utilizador abre o detalhe.
        """
        item = {field: None for field in NODE_PAYLOAD_FIELDS}
        item.update({k: v for k, v in record.items() if k != "last_packet"})
        item["last_heard"] = to_epoch(record.get("last_heard"))
        item["is_local"] = self._is_local_record(record)
        if include_packet and "last_packet" in record:
            item["last_packet"] = to_jsonable(record["last_packet"])
        return to_jsonable(item)

    def snapshot(self, include_packets: bool = False) -> List[Dict[str, Any]]:
        """Todos os nós em forma estável e serializável para JSON."""
        return [self.payload(record, include_packets)
                for record in self._nodes.values()]

    def packet_snapshot(self, node_id: str) -> Optional[Dict[str, Any]]:
        """O último pacote de um nó, serializável — para o diálogo de detalhe."""
        record = self.get(node_id)
        if not record or "last_packet" not in record:
            return None
        return to_jsonable(record["last_packet"])
