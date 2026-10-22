"""
core/metrics_history.py — Histórico das métricas em disco (SQLite).

As métricas vivem em memória e perdiam-se a cada reinício. Aqui guarda-se,
por nó local, o que entrou nelas nos últimos 7 dias: os eventos de ingestão
(pacotes, actualizações de nós, mensagens enviadas e confirmações), já
destilados — só os campos que as métricas usam, nunca o texto das mensagens.
No arranque, os eventos passam outra vez pelo mesmo código de ingestão, com a
hora original, e todas as secções voltam como estavam sem lógica duplicada.

A escrita é feita em lote (flush periódico), em modo WAL. Duas protecções de
disco: o ficheiro não passa de LIMIT_BYTES (perdem-se os eventos mais antigos)
e, com menos de MIN_FREE_BYTES livres no disco, deixa de se escrever — as
métricas continuam em memória como antes.
"""

import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time

logger = logging.getLogger("MeshDeck.history")

RETENTION_S    = 7 * 24 * 3600
LIMIT_BYTES    = 50 * 1024 * 1024
MIN_FREE_BYTES = 100 * 1024 * 1024
FLUSH_MS       = 60_000

# Tipos de evento: cada um corresponde a um método de ingestão das métricas.
PACKET   = "packet"     # ingest_raw_packet
NODE     = "node"       # ingest_packet(None, dados, nid): NodeDB, nó local
POSITION = "position"   # ingest_node_position
SENT     = "sent"       # ingest_message_sent
STATUS   = "status"     # ingest_message_status
NEIGHBOR = "neighbor"   # ingest_neighbor_info

# Campos de um pacote que as métricas lêem (core/metrics_data.py).
_PACKET_FIELDS = ("id", "fromId", "rxSnr", "rxRssi", "transportMechanism", "viaMqtt",
                  "shortName", "hopStart", "hopLimit", "hopsAway", "relayNode")
_NODE_FIELDS = ("short_name", "battery_level", "voltage", "uptime_seconds", "hw_model",
                "channel_utilization", "air_util_tx", "latitude", "longitude")


def history_dir(config_path: str) -> str:
    """Ao lado do server.json: ~/.config/meshdeck/metrics."""
    return os.path.join(os.path.dirname(config_path), "metrics")


_SKIP = object()


def _plain(value):
    """Só tipos JSON; a biblioteca meshtastic junta protobufs ('raw') e bytes."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            item = _plain(item) if key != "raw" else _SKIP
            if item is not _SKIP:
                out[key] = item
        return out
    if isinstance(value, (list, tuple)):
        return [item for item in map(_plain, value) if item is not _SKIP]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return _SKIP


def distill_packet(packet: dict) -> dict:
    """O pacote reduzido ao que a ingestão usa. Sem texto nem payload."""
    out = {k: packet[k] for k in _PACKET_FIELDS if packet.get(k) is not None}
    decoded = packet.get("decoded") or {}
    dec = {}
    for key in ("portnum", "requestId"):
        if decoded.get(key) is not None:
            dec[key] = decoded[key]
    if decoded.get("telemetry"):
        dec["telemetry"] = _plain(decoded["telemetry"])
    pos = decoded.get("position") or {}
    pos = {k: pos[k] for k in ("latitudeI", "longitudeI", "latitude_i", "longitude_i")
           if pos.get(k) is not None}
    if pos:
        dec["position"] = pos
    ni = decoded.get("neighborinfo") or decoded.get("neighborInfo") or {}
    if ni.get("neighbors"):
        dec["neighborinfo"] = {"neighbors": [
            {"nodeId": nb.get("nodeId") or nb.get("node_id"), "snr": nb.get("snr", 0.0)}
            for nb in ni["neighbors"]]}
    routing = decoded.get("routing") or {}
    if routing.get("errorReason"):
        dec["routing"] = {"errorReason": routing["errorReason"]}
    hw = (decoded.get("user") or {}).get("hwModel")
    if hw:
        dec["user"] = {"hwModel": hw}
    if dec:
        out["decoded"] = _plain(dec)
    return _plain(out)


def distill_node(data: dict) -> dict:
    return _plain({k: data[k] for k in _NODE_FIELDS if data.get(k) not in (None, "")})


def _file_name(node_id: str) -> str:
    """'!a1b2c3d4' → 'a1b2c3d4.sqlite'; nunca sai do directório."""
    return (re.sub(r"[^0-9A-Za-z_-]", "", node_id) or "node") + ".sqlite"


class MetricsHistory:
    """Eventos de um nó local num ficheiro SQLite. Thread-safe."""

    def __init__(self, directory: str, node_id: str, clock=time.time):
        self.node_id = node_id
        self.path = os.path.join(directory, _file_name(node_id))
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: list = []
        self._paused_reason = ""          # porque é que a escrita está parada
        os.makedirs(directory, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS events "
                         "(ts REAL NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL)")
        self._db.execute("CREATE INDEX IF NOT EXISTS events_ts ON events (ts)")
        self._db.commit()

    # -- escrita ------------------------------------------------------------
    def record(self, kind: str, ts: float, data):
        with self._lock:
            self._pending.append((ts, kind, json.dumps(data, separators=(",", ":"))))

    def take_pending(self) -> list:
        """Os eventos ainda por gravar, que deixam de o ser. Usado ao trocar
        de nó: os eventos recebidos antes de se saber o nó passam ao novo."""
        with self._lock:
            pending, self._pending = self._pending, []
        return pending

    def put_pending(self, rows: list):
        with self._lock:
            self._pending.extend(rows)

    def flush(self):
        """Grava o que está pendente e aplica a retenção e o limite."""
        with self._lock:
            pending, self._pending = self._pending, []
            if not self._can_write():
                return
            try:
                if pending:
                    self._db.executemany("INSERT INTO events VALUES (?, ?, ?)", pending)
                self._db.execute("DELETE FROM events WHERE ts < ?",
                                 (self._clock() - RETENTION_S,))
                self._enforce_limit()
                self._db.commit()
            except sqlite3.Error as e:
                logger.warning("histórico de métricas: não foi possível gravar (%s)", e)

    def _can_write(self) -> bool:
        try:
            free = shutil.disk_usage(os.path.dirname(self.path)).free
        except OSError:
            free = MIN_FREE_BYTES
        if free < MIN_FREE_BYTES:
            if not self._paused_reason:
                logger.warning("histórico de métricas em pausa: só %d MB livres no disco "
                               "(mínimo %d MB); as métricas continuam em memória",
                               free // 2**20, MIN_FREE_BYTES // 2**20)
            self._paused_reason = "disk"
            return False
        if self._paused_reason:
            logger.info("histórico de métricas retomado")
            self._paused_reason = ""
        return True

    def _used_bytes(self) -> int:
        page_size = self._db.execute("PRAGMA page_size").fetchone()[0]
        pages = self._db.execute("PRAGMA page_count").fetchone()[0]
        free_pages = self._db.execute("PRAGMA freelist_count").fetchone()[0]
        return (pages - free_pages) * page_size

    def _enforce_limit(self):
        """Acima do limite, apaga os 10% mais antigos até caber. O SQLite
        reutiliza as páginas libertadas, por isso o ficheiro não cresce mais."""
        for _ in range(20):
            if self._used_bytes() <= LIMIT_BYTES:
                return
            count = self._db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            if not count:
                return
            self._db.execute(
                "DELETE FROM events WHERE rowid IN "
                "(SELECT rowid FROM events ORDER BY ts LIMIT ?)", (max(count // 10, 1),))
            logger.info("histórico de métricas acima de %d MB: apagados os eventos mais antigos",
                        LIMIT_BYTES // 2**20)

    # -- leitura ------------------------------------------------------------
    def events(self):
        """Eventos dos últimos 7 dias, por ordem: (ts, kind, data)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, kind, data FROM events WHERE ts >= ? ORDER BY ts, rowid",
                (self._clock() - RETENTION_S,)).fetchall()
        for ts, kind, data in rows:
            try:
                yield ts, kind, json.loads(data)
            except ValueError:
                continue

    def clear(self):
        with self._lock:
            self._pending = []
            try:
                self._db.execute("DELETE FROM events")
                self._db.commit()
            except sqlite3.Error as e:
                logger.warning("histórico de métricas: não foi possível limpar (%s)", e)

    def close(self):
        self.flush()
        with self._lock:
            try:
                self._db.close()
            except sqlite3.Error:
                pass
