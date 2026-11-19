"""
Testes do gpsmux (gpsmux/gpsmux.py) com um GPS falso: um pty faz de
/dev/ttyAMA0, outro lado faz de meshtasticd, e um cliente TCP de MeshDeckWeb.
"""

import os
import select
import socket
import sys
import termios
import threading
import time
import tty

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gpsmux"))
import gpsmux  # noqa: E402


def nmea(body: str) -> bytes:
    calc = 0
    for ch in body:
        calc ^= ord(ch)
    return f"${body}*{calc:02X}\r\n".encode()


GGA = nmea("GPGGA,123519.00,3841.6250,N,00917.8540,W,1,08,0.9,95.4,M,46.9,M,,")


def read_until(fd_or_sock, needle: bytes, timeout=5.0) -> bytes:
    buf = b""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and needle not in buf:
        ready, _, _ = select.select([fd_or_sock], [], [], 0.1)
        if not ready:
            continue
        try:
            chunk = fd_or_sock.recv(4096) if isinstance(fd_or_sock, socket.socket) else os.read(fd_or_sock, 4096)
        except (BlockingIOError, OSError):
            continue
        buf += chunk
    return buf


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def mux(tmp_path):
    gps_master, gps_slave = os.openpty()          # o "GPS": escreve-se no master
    tty.setraw(gps_master)
    device = os.ttyname(gps_slave)
    port = free_port()
    link = str(tmp_path / "run" / "meshtasticd")
    m = gpsmux.Mux(device, link, ("127.0.0.1", port), group="nonexistent-group",
                   block=gpsmux.DEFAULT_BLOCK)
    # Detecção de velocidade: o GPS falso tem de estar a falar.
    stop = threading.Event()

    def talk():
        while not stop.is_set():
            try:
                os.write(gps_master, GGA)
            except OSError:
                pass
            time.sleep(0.1)

    threading.Thread(target=talk, daemon=True).start()
    threading.Thread(target=m.run, daemon=True).start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and (not os.path.exists(link) or m.serial_fd is None):
        time.sleep(0.05)
    assert os.path.islink(link) and m.serial_fd is not None, "o mux não arrancou"
    yield m, gps_master, link, port
    stop.set()
    m.stop()


def test_nmea_reaches_meshtasticd_and_tcp_clients(mux):
    m, _gps, link, port = mux
    mesh = os.open(link, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    client = socket.create_connection(("127.0.0.1", port))
    try:
        assert GGA in read_until(mesh, GGA), "o meshtasticd não recebeu o NMEA"
        assert GGA in read_until(client, GGA), "o cliente TCP não recebeu o NMEA"
    finally:
        os.close(mesh)
        client.close()


def test_commands_pass_but_standby_is_blocked(mux):
    m, gps, link, _port = mux
    mesh = os.open(link, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        os.write(mesh, b"$PCAS12,60*28\r\n")          # standby: não chega ao GPS
        os.write(mesh, b"$PCAS03,1,0,0,0,1,0,0,0*02\r\n")
        got = read_until(gps, b"$PCAS03")
        assert b"$PCAS03" in got and b"$PCAS12" not in got
        assert b"$PCAS12 (travado)" in m.filter.seen
    finally:
        os.close(mesh)


def test_ubx_power_request_is_blocked_other_ubx_passes():
    f = gpsmux.CommandFilter(gpsmux.DEFAULT_BLOCK)
    pmreq = b"\xb5\x62\x02\x41\x08\x00" + bytes(8) + b"\x4d\x3b"
    cfg = b"\xb5\x62\x06\x01\x03\x00\xf0\x01\x00\xfb\x11"
    assert f.feed(pmreq + cfg) == cfg
    # Uma frase partida em dois pedaços junta-se antes de decidir.
    assert f.feed(b"$PMTK16") == b""
    assert f.feed(b"1,0*28\r\n$PMTK220,1000*1F\r\n") == b"$PMTK220,1000*1F\r\n"


def test_speed_change_on_the_pty_is_mirrored_to_the_gps(mux):
    m, _gps, link, _port = mux
    mesh = os.open(link, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        attrs = termios.tcgetattr(mesh)
        attrs[4] = attrs[5] = termios.B115200
        termios.tcsetattr(mesh, termios.TCSANOW, attrs)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and m.serial_baud != 115200:
            time.sleep(0.05)
        assert m.serial_baud == 115200
    finally:
        os.close(mesh)


def test_pcas03_from_meshtasticd_gets_gsa_and_gsv():
    """O meshtasticd liga só GGA e RMC no L76K; o céu precisa de GSA e GSV."""
    f = gpsmux.CommandFilter(gpsmux.DEFAULT_BLOCK)
    out = f.feed(b"$PCAS03,1,0,0,0,1,0,0,0,0,0,,,0,0*02\r\n")
    assert out == gpsmux.with_checksum("PCAS03,1,0,1,3,1,0,0,0,0,0,,,0,0")
    assert gpsmux.nmea_ok(out)
    already = gpsmux.with_checksum("PCAS03,1,0,1,1,1,0,0,0,0,0,,,0,0")
    assert f.feed(already) == already, "não se mexe no que já pede GSA/GSV"
    silence = b"$PCAS03,0,0,0,0,0,0,0,0,0,0,,,0,0*02\r\n"
    assert f.feed(silence) == silence, "o silêncio da detecção do GPS fica intacto"
    assert gpsmux.CommandFilter((), adjust=False).feed(b"$PCAS03,1,0,0,0,1*00\r\n") == b"$PCAS03,1,0,0,0,1*00\r\n"


def test_nmea_checksum():
    assert gpsmux.nmea_ok(GGA)
    assert not gpsmux.nmea_ok(GGA.replace(b"3841", b"3842"))
    assert not gpsmux.nmea_ok(b"lixo")
