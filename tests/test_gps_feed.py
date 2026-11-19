"""
Testes do GPS do uConsole ao vivo (core/gps_feed.py): NMEA e céu, onde está
o GPS, e o serviço que escolhe a fonte (gpsmux, série ou nenhuma).
"""

import socket
import threading
import time

import pytest

from core import gps_feed as g
from tests.conftest import REAL_DETECT_SERIAL_PATH, REAL_GPS_OWNER


def nmea(body: str) -> str:
    """Frase NMEA com o checksum certo."""
    calc = 0
    for ch in body:
        calc ^= ord(ch)
    return f"${body}*{calc:02X}\r\n"


GGA = nmea("GPGGA,123519.00,3841.6250,N,00917.8540,W,1,08,0.9,95.4,M,46.9,M,,")
RMC = nmea("GPRMC,123519.00,A,3841.6250,N,00917.8540,W,2.0,84.4,191126,,,A")
GSA_GP = nmea("GNGSA,A,3,04,05,09,12,,,,,,,,,1.8,0.9,1.5,1")
GSA_GL = nmea("GNGSA,A,3,65,66,,,,,,,,,,,1.8,0.9,1.5,2")
GSV_GP = nmea("GPGSV,2,1,05,04,40,083,46,05,17,308,41,09,07,344,39,12,50,215,45")
GSV_GP2 = nmea("GPGSV,2,2,05,25,10,100,")
GSV_GL = nmea("GLGSV,1,1,03,65,25,045,30,66,60,120,35,67,10,300,20")
GGA_NOFIX = nmea("GPGGA,123519.00,,,,,0,00,99.9,,M,,M,,")
RMC_VOID = nmea("GPRMC,123519.00,V,,,,,,,191126,,,N")
FIX_LINES = (GSV_GP, GSV_GP2, GSV_GL, GSA_GP, GSA_GL, GGA, RMC)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


# ── NMEA ─────────────────────────────────────────────────────────────────────
def test_checksum_is_required():
    assert g.parse_sentence(GGA)[0] == "GGA"
    assert g.parse_sentence(GGA.replace("3841", "3842")) is None, "checksum errado aceite"
    assert g.parse_sentence("lixo") is None


def test_fix_from_gga_rmc_gsa_gsv():
    state = g.NmeaState(Clock())
    for line in FIX_LINES:
        state.feed(line)
    snap = state.snapshot()
    assert snap["fix"] is True
    assert snap["lat"] == pytest.approx(38.693750)
    assert snap["lon"] == pytest.approx(-9.297567, abs=1e-6)
    assert snap["alt"] == 95.4
    assert snap["fix_type"] == 3 and snap["fix_quality"] == 1
    assert snap["sats_used"] == 8 and snap["sats_in_view"] == 8      # 5 GPS + 3 GLONASS
    assert (snap["pdop"], snap["hdop"], snap["vdop"]) == (1.8, 0.9, 1.5)
    assert snap["speed_mps"] == pytest.approx(1.029, abs=1e-3)
    assert snap["course"] == 84.4
    assert snap["time"] == pytest.approx(1795091719.0)              # 2026-11-19 12:35:19 UTC


def test_sky_lists_each_satellite_with_used_flag():
    state = g.NmeaState(Clock())
    for line in FIX_LINES:
        state.feed(line)
    sky = state.sky()
    by_key = {(s["system"], s["prn"]): s for s in sky}
    assert len(sky) == 8
    assert by_key[("GPS", 4)] == {"system": "GPS", "prn": 4, "el": 40, "az": 83, "snr": 46, "used": True}
    assert by_key[("GPS", 25)]["used"] is False and by_key[("GPS", 25)]["snr"] is None
    assert by_key[("GLONASS", 65)]["used"] is True and by_key[("GLONASS", 67)]["used"] is False
    assert [s["used"] for s in sky] == sorted([s["used"] for s in sky], reverse=True), "em uso primeiro"


def test_beidou_used_flag_matches_across_talkers():
    """L76K: BeiDou é "BD" nas GSV e sistema 4 nas GSA (NMEA 4.1, com o signal ID no fim)."""
    state = g.NmeaState(Clock())
    for line in (nmea("BDGSV,1,1,02,27,19,104,37,28,13,060,43,0"),
                 nmea("GNGSA,A,3,27,,,,,,,,,,,,2.7,1.9,2.0,4"), GGA):
        state.feed(line)
    sky = {s["prn"]: s for s in state.sky()}
    assert sky[27]["system"] == "BeiDou" and sky[27]["used"] is True
    assert sky[28]["used"] is False and sky[28]["snr"] == 43


def test_no_fix_while_searching_and_old_data_goes_stale():
    clock = Clock()
    state = g.NmeaState(clock)
    for line in (GSV_GP, GGA_NOFIX, RMC_VOID):
        state.feed(line)
    assert state.has_fix() is False
    assert state.snapshot()["sats_in_view"] == 5 and len(state.sky()) == 4
    state.feed(GGA)
    assert state.has_fix() is True
    clock.now += g.FIX_STALE_S + 1
    assert state.has_fix() is False, "o fix devia perder-se sem frases novas"
    assert state.sky() == [], "satélites antigos deviam sair do céu"


# ── onde está o GPS ──────────────────────────────────────────────────────────
def fake_proc(tmp_path, names):
    for pid, name in enumerate(names, start=100):
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "comm").write_text(name + "\n")
    (tmp_path / "self").mkdir()
    return str(tmp_path)


def test_owner_is_meshtasticd_when_running(tmp_path):
    assert REAL_GPS_OWNER(fake_proc(tmp_path, ["bash", "meshtasticd"])) == "meshtasticd"


def test_owner_none_when_nobody_has_it(tmp_path):
    assert REAL_GPS_OWNER(fake_proc(tmp_path, ["bash", "python3"])) is None


def test_serial_path_from_meshtasticd_config(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("Lora:\n  Module: sx1262\n  SerialPath: /dev/nope\n"
                   "GPS:\n#  SerialPath: /dev/ttyS0\n  SerialPath: /dev/ttyAMA0\n"
                   "I2C:\n  I2CDevice: /dev/i2c-1\n")
    assert REAL_DETECT_SERIAL_PATH(str(cfg)) == "/dev/ttyAMA0"
    cfg.write_text("GPS:\n#  SerialPath: /dev/ttyS0\n")
    assert REAL_DETECT_SERIAL_PATH(str(cfg)) == g.DEFAULT_PATH
    assert REAL_DETECT_SERIAL_PATH(str(tmp_path / "missing.yaml")) == g.DEFAULT_PATH


def test_tcp_reader_delivers_lines_from_a_nmea_server():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def serve():
        conn, _ = server.accept()
        conn.sendall((GGA + RMC).encode())
        time.sleep(0.5)
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    lines, errors = [], []
    reader = g.TcpNmeaReader(("127.0.0.1", port), lines.append, errors.append)
    reader.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not errors:
        time.sleep(0.05)
    server.close()
    assert [g.parse_sentence(line)[0] for line in lines] == ["GGA", "RMC"]
    assert errors, "o fim da ligação devia ser um erro (para se voltar a ligar)"


# ── serviço ──────────────────────────────────────────────────────────────────
class Loop:
    """CoreLoop síncrono: post corre já; every guarda o callback."""

    def __init__(self):
        self.timers = []

    def post(self, fn, *args):
        fn(*args)

    def every(self, _ms, fn, *args):
        loop = self

        class T:
            def stop(self):
                loop.timers.remove(self)

        t = T()
        self.timers.append(t)
        return t


class Reader:
    def __init__(self, kind, path, on_line, on_error):
        self.kind, self.path, self.on_line, self.on_error = kind, path, on_line, on_error
        self.alive = True
        self.stopped = False

    def start(self):
        pass

    def stop(self):
        self.stopped = True
        self.alive = False


@pytest.fixture
def feed():
    clock = Clock()
    world = {"owner": None, "mux": False}
    readers, statuses = [], []

    def tcp(on_line, on_error):
        readers.append(Reader("tcp", "mux", on_line, on_error))
        return readers[-1]

    def serial(path, on_line, on_error):
        readers.append(Reader("serial", path, on_line, on_error))
        return readers[-1]

    f = g.GpsFeed(Loop(), statuses.append, owner_fn=lambda: world["owner"],
                  path_fn=lambda: "/dev/ttyAMA0", mux_fn=lambda: world["mux"],
                  tcp_factory=tcp, serial_factory=serial, clock=clock)
    return f, clock, world, readers, statuses


def feed_fix(reader):
    for line in FIX_LINES:
        reader.on_line(line)


def test_reads_from_gpsmux_when_it_is_running(feed):
    f, _clock, world, readers, statuses = feed
    world["mux"], world["owner"] = True, "meshtasticd"     # o meshtasticd usa o pty do gpsmux
    f.start()
    assert [r.kind for r in readers] == ["tcp"]
    feed_fix(readers[0])
    status = statuses[-1]
    assert status["state"] == g.FIX and status["source"] == g.SRC_MUX
    assert status["fix"]["sats_used"] == 8 and len(status["sky"]) == 8
    assert status["path"] == "tcp://127.0.0.1:10110" and status["last_fix"]


def test_busy_without_gpsmux_while_meshtasticd_has_the_gps(feed):
    f, _clock, world, readers, statuses = feed
    world["owner"] = "meshtasticd"
    f.start()
    assert readers == []
    assert statuses[-1]["state"] == g.BUSY and statuses[-1]["owner"] == "meshtasticd"
    assert statuses[-1]["fix"] is None and statuses[-1]["sky"] == []


def test_reads_serial_when_nobody_has_it_and_lets_go(feed):
    f, _clock, world, readers, statuses = feed
    f.start()
    assert [(r.kind, r.path) for r in readers] == [("serial", "/dev/ttyAMA0")]
    feed_fix(readers[0])
    assert statuses[-1]["source"] == g.SRC_SERIAL and statuses[-1]["state"] == g.FIX
    world["owner"] = "meshtasticd"
    f.check()
    assert readers[0].stopped and statuses[-1]["state"] == g.BUSY
    feed_fix(readers[0])                                    # linhas atrasadas do leitor parado
    assert statuses[-1]["state"] == g.BUSY


def test_switches_from_serial_to_gpsmux_when_it_appears(feed):
    f, _clock, world, readers, _statuses = feed
    f.start()
    world["mux"] = True
    f.check()
    assert readers[0].stopped and readers[-1].kind == "tcp"


def test_gpsmux_drop_reconnects_and_serial_failure_waits(feed):
    f, clock, world, readers, statuses = feed
    world["mux"] = True
    f.start()
    readers[0].on_error("gpsmux fechou a ligação")
    assert statuses[-1]["state"] == g.NO_DEVICE
    f.check()
    assert len(readers) == 2 and readers[1].kind == "tcp", "não voltou a ligar ao gpsmux"
    world["mux"] = False
    readers[1].on_error("closed")
    f.check()
    assert readers[-1].kind == "serial"
    readers[-1].on_error("[Errno 2] No such file")
    count = len(readers)
    f.check()
    assert len(readers) == count, "tentou a série outra vez logo a seguir"
    clock.now += g.SERIAL_RETRY_S + 1
    f.check()
    assert len(readers) == count + 1


def test_status_is_throttled_but_state_changes_go_at_once(feed):
    f, clock, world, readers, statuses = feed
    world["mux"] = True
    f.start()
    feed_fix(readers[0])                    # passa a FIX: vai logo
    assert statuses[-1]["state"] == g.FIX
    sent = len(statuses)
    readers[0].on_line(GGA)                 # mesmo segundo: não vai
    readers[0].on_line(GSV_GL)
    assert len(statuses) == sent
    clock.now += g.STATUS_MIN_S
    readers[0].on_line(nmea("GPGGA,123520.00,3841.6260,N,00917.8540,W,1,08,0.9,95.4,M,46.9,M,,"))
    assert len(statuses) == sent + 1
