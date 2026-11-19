"""
core/gps_feed.py — GPS do uConsole ao vivo, só para leitura.

A MeshDeckWeb lê o NMEA e põe-no na interface e na API local (DashOS e
outras apps): posição, fix, DOP e o céu dos satélites. Não envia nada ao nó
nem à rede: a posição do nó e as transmissões continuam a ser as do
meshtasticd, como estiver configurado.

De onde vem o NMEA, por esta ordem:
  1. gpsmux (gpsmux/gpsmux.py), que partilha o GPS com o meshtasticd e o
     publica em tcp://127.0.0.1:10110;
  2. a porta série directamente, se ninguém (meshtasticd, gpsd) a estiver a
     usar;
  3. nada: o GPS é do meshtasticd e o gpsmux não está instalado.

Peças:
  - parse_sentence / NmeaState: NMEA → estado (puro, sem E/S).
  - gps_owner / detect_serial_path / mux_available: onde está o GPS.
  - TcpNmeaReader / SerialNmeaReader: threads que entregam linhas.
  - GpsFeed: liga tudo no CoreLoop e publica o estado.
"""
from __future__ import annotations

import os
import re
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from constants import logger

DEFAULT_PATH = "/dev/ttyAMA0"
MESHTASTICD_CONFIG = "/etc/meshtasticd/config.yaml"
MUX_ADDRESS = ("127.0.0.1", 10110)
OWNERS = ("meshtasticd", "gpsd")       # programas que, a correr, têm o GPS
BAUDS = (9600, 115200, 38400, 4800)
CHECK_MS = 5000                        # de quanto em quanto se revê a fonte
STATUS_MIN_S = 1.0                     # avisos ao browser, no máximo 1 por segundo
FIX_STALE_S = 10.0                     # sem posição há mais que isto: o fix perdeu-se
SKY_STALE_S = 10.0                     # satélite não reportado há mais que isto sai do céu
SERIAL_RETRY_S = 30.0

BUSY, NO_DEVICE, SEARCHING, FIX = "busy", "no_device", "searching", "fix"
SRC_MUX, SRC_SERIAL = "gpsmux", "serial"

# Talker NMEA → constelação.
SYSTEMS = {"GP": "GPS", "GL": "GLONASS", "GA": "Galileo", "GB": "BeiDou", "BD": "BeiDou",
           "GQ": "QZSS", "GI": "NavIC"}
# Sistema no fim do GSA (NMEA 4.1) → talker.
GSA_SYSTEM_ID = {"1": "GP", "2": "GL", "3": "GA", "4": "GB", "5": "GQ", "6": "GI"}


# ── NMEA ────────────────────────────────────────────────────────────────────
def parse_sentence(line: str):
    """'$GPGGA,...*hh' → ('GGA', 'GP', [campos]) ou None (checksum errado, lixo)."""
    line = (line or "").strip()
    if not line.startswith("$") or "*" not in line:
        return None
    body, _, checksum = line[1:].rpartition("*")
    try:
        expected = int(checksum[:2], 16)
    except ValueError:
        return None
    calc = 0
    for ch in body:
        calc ^= ord(ch)
    if calc != expected:
        return None
    fields = body.split(",")
    head = fields[0]
    if len(head) < 5:
        return None
    return head[-3:], head[:-3], fields[1:]


def _coord(value: str, hemi: str) -> Optional[float]:
    if not value or not hemi:
        return None
    try:
        dot = value.index(".")
        deg = float(value[:dot - 2])
        minutes = float(value[dot - 2:])
    except ValueError:
        return None
    out = deg + minutes / 60.0
    return -out if hemi in ("S", "W") else out


def _float(value: str) -> Optional[float]:
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _int(value: str) -> Optional[int]:
    try:
        return int(value) if value else None
    except ValueError:
        return None


def _utc(date: str, hms: str) -> Optional[datetime]:
    try:
        day, month, year = int(date[0:2]), int(date[2:4]), 2000 + int(date[4:6])
        hour, minute = int(hms[0:2]), int(hms[2:4])
        second = float(hms[4:])
        return datetime(year, month, day, hour, minute, int(second),
                        int((second % 1) * 1e6), tzinfo=timezone.utc)
    except (ValueError, IndexError):
        return None


class NmeaState:
    """Junta as frases de cada segundo num único fix, mais o céu.

    GGA: posição, qualidade, satélites usados, HDOP, altitude.
    RMC: validade, data/hora, velocidade e rumo.
    GSA: tipo de fix (2D/3D), DOP e os satélites em uso.
    GSV: satélites à vista, com elevação, azimute e SNR.
    """

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self.lat = self.lon = self.alt = None
        self.quality = 0
        self.sats_used = None
        self.hdop = self.pdop = self.vdop = None
        self.fix_type = None            # 1 sem fix, 2 = 2D, 3 = 3D
        self.speed_mps = None
        self.course = None
        self.utc: Optional[datetime] = None
        self.rmc_valid = False
        self._in_view: dict = {}        # talker → satélites à vista
        self._fix_types: dict = {}      # talker → tipo de fix
        self._used: dict = {}           # sistema (ou "GN") → PRN em uso (GSA)
        self._sky: dict = {}            # (talker, prn) → {el, az, snr, t}
        self.last_sentence = None       # relógio monotónico da última frase válida
        self.last_position = None       # idem, da última posição

    def feed(self, line: str) -> bool:
        """Processa uma linha. True quando a posição mudou (GGA ou RMC)."""
        parsed = parse_sentence(line)
        if parsed is None:
            return False
        kind, talker, f = parsed
        now = self._clock()
        self.last_sentence = now
        if kind == "GGA" and len(f) >= 9:
            self.quality = _int(f[5]) or 0
            self.sats_used = _int(f[6])
            self.hdop = _float(f[7]) or self.hdop
            self.alt = _float(f[8])
            lat, lon = _coord(f[1], f[2]), _coord(f[3], f[4])
            if self.quality > 0 and lat is not None and lon is not None:
                self.lat, self.lon, self.last_position = lat, lon, now
                return True
            return False
        if kind == "RMC" and len(f) >= 9:
            self.rmc_valid = f[1] == "A"
            knots = _float(f[6])
            self.speed_mps = knots * 0.514444 if knots is not None else None
            self.course = _float(f[7])
            self.utc = _utc(f[8], f[0])
            lat, lon = _coord(f[2], f[3]), _coord(f[4], f[5])
            if self.rmc_valid and lat is not None and lon is not None:
                self.lat, self.lon, self.last_position = lat, lon, now
                return True
            return False
        if kind == "GSA" and len(f) >= 17:
            key = GSA_SYSTEM_ID.get(f[17], talker) if len(f) > 17 and f[17] else talker
            self._fix_types[key] = _int(f[1]) or 1
            self.fix_type = max(self._fix_types.values())
            # Em uso, pelo nome do sistema: o BeiDou vem como "BD" nas GSV e
            # como 4 (→ "GB") nas GSA; o nome junta os dois.
            used_key = SYSTEMS.get(key, key) if key != "GN" else "GN"
            self._used[used_key] = {int(p) for p in f[2:14] if p.isdigit()}
            self.pdop = _float(f[14]) or self.pdop
            self.hdop = _float(f[15]) or self.hdop
            self.vdop = _float(f[16]) or self.vdop
            return False
        if kind == "GSV" and len(f) >= 3:
            total = _int(f[2])
            if total is not None:
                self._in_view[talker] = total
            for i in range(3, len(f) - 3, 4):
                prn = _int(f[i])
                if prn is None:
                    continue
                self._sky[(talker, prn)] = {"el": _int(f[i + 1]), "az": _int(f[i + 2]),
                                            "snr": _int(f[i + 3]), "t": now}
            return False
        return False

    @property
    def sats_in_view(self) -> Optional[int]:
        if not self._in_view:
            return None
        # GN é a soma já feita pelo receptor; não se soma outra vez.
        if "GN" in self._in_view and len(self._in_view) == 1:
            return self._in_view["GN"]
        return sum(v for k, v in self._in_view.items() if k != "GN")

    def sky(self) -> list:
        """Satélites reportados há menos de SKY_STALE_S, os em uso primeiro."""
        now = self._clock()
        mixed = self._used.get("GN", set())      # GSA sem sistema: vale para todos
        out = []
        for (talker, prn), d in self._sky.items():
            if now - d["t"] > SKY_STALE_S:
                continue
            system = SYSTEMS.get(talker, talker)
            used = prn in self._used.get(system, set()) or prn in mixed
            out.append({"system": system, "prn": prn, "el": d["el"],
                        "az": d["az"], "snr": d["snr"], "used": used})
        out.sort(key=lambda s: (not s["used"], -(s["snr"] or 0)))
        return out

    def has_fix(self) -> bool:
        if self.last_position is None or self.lat is None:
            return False
        if self._clock() - self.last_position > FIX_STALE_S:
            return False
        return self.quality > 0 or self.rmc_valid

    def snapshot(self) -> dict:
        fix = self.has_fix()
        return {
            "fix": fix,
            "lat": self.lat if fix else None,
            "lon": self.lon if fix else None,
            "alt": self.alt if fix else None,
            "fix_type": self.fix_type if fix else (1 if self.last_sentence else None),
            "fix_quality": self.quality,
            "sats_used": self.sats_used,
            "sats_in_view": self.sats_in_view,
            "hdop": self.hdop, "pdop": self.pdop, "vdop": self.vdop,
            "speed_mps": self.speed_mps if fix else None,
            "course": self.course if fix else None,
            "time": self.utc.timestamp() if self.utc else None,
        }


# ── onde está o GPS ────────────────────────────────────────────────────────
def gps_owner(proc: str = "/proc") -> Optional[str]:
    """Nome do programa que tem o GPS (meshtasticd, gpsd) ou None.

    Lê só /proc/<pid>/comm, legível para todos; os descritores de outros
    utilizadores não se podem ver sem root.
    """
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return None
    for pid in pids:
        try:
            with open(os.path.join(proc, pid, "comm"), encoding="utf-8") as fh:
                name = fh.read().strip()
        except OSError:
            continue
        if name in OWNERS:
            return name
    return None


def detect_serial_path(config_path: str = MESHTASTICD_CONFIG) -> str:
    """SerialPath do bloco GPS do config.yaml do meshtasticd; senão /dev/ttyAMA0."""
    try:
        with open(config_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return DEFAULT_PATH
    in_gps = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not raw.startswith((" ", "\t")):
            in_gps = line.strip() == "GPS:"
            continue
        match = re.match(r"\s+SerialPath:\s*(\S+)", line)
        if in_gps and match:
            return match.group(1).strip("'\"")
    return DEFAULT_PATH


def mux_available(address=MUX_ADDRESS, timeout: float = 0.3) -> bool:
    """O gpsmux está a publicar NMEA?"""
    try:
        with socket.create_connection(address, timeout=timeout):
            return True
    except OSError:
        return False


# ── leitores ───────────────────────────────────────────────────────────────
class _Reader:
    kind = "reader"

    def __init__(self, on_line: Callable[[str], None], on_error: Callable[[str], None]):
        self._on_line = on_line
        self._on_error = on_error
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._guard, daemon=True, name=f"gps-{self.kind}")

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def _guard(self):
        try:
            self._run()
        except Exception as e:
            if not self._stop.is_set():
                self._on_error(str(e))

    def _run(self):
        raise NotImplementedError


class TcpNmeaReader(_Reader):
    """Linhas NMEA do gpsmux (ou de outro servidor NMEA por TCP)."""

    kind = SRC_MUX

    def __init__(self, address, on_line, on_error):
        self.address = address
        super().__init__(on_line, on_error)

    def _run(self):
        with socket.create_connection(self.address, timeout=3) as sock:
            sock.settimeout(1)
            logger.info("GPS: a ler NMEA de tcp://%s:%s (gpsmux)", *self.address)
            buf = b""
            while not self._stop.is_set():
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    raise ConnectionError("gpsmux fechou a ligação")
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for raw in lines:
                    self._on_line(raw.decode("ascii", errors="ignore"))


class SerialNmeaReader(_Reader):
    """Linhas NMEA da porta série, com a velocidade detectada."""

    kind = SRC_SERIAL

    def __init__(self, path, on_line, on_error):
        self.path = path
        super().__init__(on_line, on_error)

    def _run(self):
        import serial  # pyserial, já vem com a biblioteca meshtastic
        port = self._open(serial)
        if port is None:
            raise IOError("no NMEA")
        logger.info("GPS: a ler NMEA de %s a %s baud", self.path, port.baudrate)
        try:
            while not self._stop.is_set():
                raw = port.readline()
                if raw:
                    self._on_line(raw.decode("ascii", errors="ignore"))
        finally:
            port.close()

    def _open(self, serial):
        for baud in BAUDS:
            if self._stop.is_set():
                return None
            port = serial.Serial(self.path, baud, timeout=1)
            deadline = time.monotonic() + 2.5
            while time.monotonic() < deadline and not self._stop.is_set():
                if parse_sentence(port.readline().decode("ascii", errors="ignore")):
                    return port
            port.close()
        return None


# ── serviço ────────────────────────────────────────────────────────────────
class GpsFeed:
    """Escolhe a fonte, junta o NMEA e publica o estado (on_status).

    Corre no CoreLoop. Só lê: nada daqui vai para o nó.
    """

    def __init__(self, loop, on_status: Callable[[dict], None], *,
                 owner_fn=gps_owner, path_fn=detect_serial_path, mux_fn=mux_available,
                 tcp_factory=None, serial_factory=None, clock=time.monotonic):
        self._loop = loop
        self._on_status = on_status
        self._owner_fn = owner_fn
        self._path_fn = path_fn
        self._mux_fn = mux_fn
        self._tcp_factory = tcp_factory or (lambda on_line, on_error:
                                            TcpNmeaReader(MUX_ADDRESS, on_line, on_error))
        self._serial_factory = serial_factory or SerialNmeaReader
        self._clock = clock
        self._reader = None
        self._source = ""
        self._gen = 0                   # leitor actual; linhas de um antigo ignoram-se
        self._state = NmeaState(clock)
        self._owner = None
        self._path = ""
        self._error = ""
        self._serial_retry_after = 0.0
        self._last_fix_at = None        # time.time() da última posição com fix
        self._last_status_at = 0.0
        self._last_status = None
        self._timer = None

    # -- ciclo -----------------------------------------------------------
    def start(self):
        self._timer = self._loop.every(CHECK_MS, self.check)
        self._loop.post(self.check)

    def stop(self):
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._stop_reader()

    def check(self):
        """Revê a fonte: gpsmux primeiro; a série só se ninguém a tiver."""
        reader_ok = self._reader is not None and self._reader.alive
        if reader_ok and self._source == SRC_MUX:
            self._publish()
            return
        if self._mux_fn():
            self._stop_reader()
            self._start(SRC_MUX)
            self._publish()
            return
        self._owner = self._owner_fn()
        if self._owner:
            self._stop_reader()
        elif not reader_ok and self._clock() >= self._serial_retry_after:
            self._start(SRC_SERIAL)
        self._publish()

    def _start(self, source: str):
        self._error = ""
        self._owner = None
        self._state = NmeaState(self._clock)
        self._gen += 1
        gen = self._gen
        on_line = lambda line: self._loop.post(self._on_line, line, gen)          # noqa: E731
        on_error = lambda err: self._loop.post(self._on_reader_error, err, gen)   # noqa: E731
        if source == SRC_MUX:
            self._path = "tcp://%s:%s" % MUX_ADDRESS
            self._reader = self._tcp_factory(on_line, on_error)
        else:
            self._path = self._path_fn()
            self._reader = self._serial_factory(self._path, on_line, on_error)
        self._source = source
        self._reader.start()

    def _stop_reader(self):
        if self._reader is not None:
            self._reader.stop()
            self._reader = None
            self._gen += 1
            self._state = NmeaState(self._clock)
        self._source = ""

    def _on_reader_error(self, err: str, gen: int = None):
        if gen is not None and gen != self._gen:
            return
        logger.info("GPS: sem leitura de %s (%s)", self._path, err)
        if self._source == SRC_SERIAL:
            self._serial_retry_after = self._clock() + SERIAL_RETRY_S
        self._error = err
        self._reader = None
        self._source = ""
        self._gen += 1
        self._publish(force=True)

    def _on_line(self, line: str, gen: int = None):
        if self._reader is None or (gen is not None and gen != self._gen):
            return
        if self._state.feed(line) and self._state.has_fix():
            self._last_fix_at = time.time()
        self._publish()

    # -- estado ----------------------------------------------------------
    def status(self) -> dict:
        """O que a interface e a API local mostram."""
        if self._reader is not None:
            state = FIX if self._state.has_fix() else SEARCHING
        elif self._owner:
            state = BUSY
        else:
            state = NO_DEVICE
        live = state in (FIX, SEARCHING)
        return {
            "state": state, "source": self._source if live else "",
            "owner": self._owner or "", "path": self._path or "", "error": self._error,
            "fix": self._state.snapshot() if live else None,
            "sky": self._state.sky() if live else [],
            "last_fix": self._last_fix_at,
        }

    def _publish(self, force: bool = False):
        status = self.status()
        if status == self._last_status:
            return
        # O conteúdo muda a cada frase; mudanças de estado vão logo, o resto
        # no máximo uma vez por segundo.
        now = self._clock()
        state_changed = (self._last_status or {}).get("state") != status["state"]
        if not force and not state_changed and now - self._last_status_at < STATUS_MIN_S:
            return
        self._last_status = status
        self._last_status_at = now
        self._on_status(status)
