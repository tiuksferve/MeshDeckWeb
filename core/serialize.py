"""
core/serialize.py — Conversão para JSON do que vem da biblioteca meshtastic.

Os dicionários de pacotes e de nós trazem datetime, bytes (PSK, chaves
públicas, endereços MAC) e às vezes objectos protobuf. Nenhum deles sobrevive
a json.dumps, e um único campo inesperado chegaria ao browser como erro 500.
to_jsonable() nunca levanta excepção: o que não souber converter vira texto.
"""

import base64
import logging
from datetime import date, datetime

logger = logging.getLogger("MeshDeck.serialize")

MAX_DEPTH = 12   # travão contra estruturas cíclicas ou absurdamente aninhadas


def to_epoch(value):
    """datetime -> segundos desde a época. Devolve None para o resto.

    Segundos (e não milissegundos) por coerência com rxTime/lastHeard do
    protocolo Meshtastic; o browser multiplica por 1000.
    """
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day).timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    return None


def to_jsonable(value, _depth: int = 0):
    """Devolve uma estrutura composta só por tipos que o json aceita."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    if _depth >= MAX_DEPTH:
        return repr(value)[:200]

    if isinstance(value, (datetime, date)):
        return to_epoch(value)

    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")

    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            # As chaves de JSON têm de ser strings; nodesByNum usa ints.
            out[k if isinstance(k, str) else str(k)] = to_jsonable(v, _depth + 1)
        return out

    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(v, _depth + 1) for v in value]

    # Protobuf, enums e tudo o mais: representação textual em vez de erro.
    try:
        return str(value)
    except Exception:
        logger.debug("to_jsonable: %s não é representável", type(value).__name__)
        return None


def safe_utf8_text(value: str) -> str:
    """Garante que uma string é codificável em UTF-8 estrito.

    core.mesh_worker._clean_text já sanitiza long_name/short_name assim que
    entram no sistema, mas uma secção de métricas compõe texto vindo de vários
    nós e seria frágil depender de rastrear cada campo individualmente — seria
    só mais um local esquecido como este. Esta é a rede de segurança final, à
    saída: nenhuma resposta HTTP pode rebentar com UnicodeEncodeError por um
    surrogate solto, venha ele de onde vier.
    """
    if not isinstance(value, str):
        return value
    return value.encode("utf-8", errors="replace").decode("utf-8")
