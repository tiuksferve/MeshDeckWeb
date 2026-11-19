#!/usr/bin/python3
"""
gpsmux — partilha um GPS série entre o meshtasticd e outras aplicações.

O GPS do uConsole (/dev/ttyAMA0) só pode ser lido por um programa. Este
serviço fica com ele e:

  * dá ao meshtasticd uma porta série virtual (pty) nos dois sentidos, em
    /run/meshdeck-gps/meshtasticd: o meshtasticd continua a detectar o GPS,
    a mandar-lhe comandos e a gerir a posição e as transmissões como estiver
    configurado;
  * publica o NMEA ao vivo em TCP (127.0.0.1:10110, só leitura), para a
    MeshDeckWeb e outras apps (PyGPSClient, OpenCPN…).

Os comandos de standby que o meshtasticd envia (para poupar energia entre
leituras) não chegam ao GPS: assim os dados continuam ao vivo para as outras
apps; o meshtasticd não nota diferença, recebe as frases quando as lê.
Cada tipo de comando que passa ou é travado fica no registo.

Só a biblioteca padrão (corre com /usr/bin/python3, sem venv).
"""
from __future__ import annotations

import argparse
import errno
import grp
import logging
import os
import select
import socket
import sys
import termios
import threading
import time
import tty

log = logging.getLogger("gpsmux")

BAUDS = (9600, 115200, 38400, 57600, 4800)
SPEEDS = {getattr(termios, f"B{b}"): b for b in (4800, 9600, 19200, 38400, 57600, 115200, 230400)}
# Standby/sleep: MTK (PMTK161/225), CASIC/L76K (PCAS12) e u-blox (UBX RXM-PMREQ).
DEFAULT_BLOCK = ("$PMTK161", "$PMTK225", "$PCAS12")
UBX_PMREQ = b"\xb5\x62\x02\x41"
# O meshtasticd liga só GGA e RMC no L76K ($PCAS03). Para as outras apps
# terem DOP e o céu, pede-se também GSA (cada fix) e GSV (de 3 em 3 fixes,
# para caber nos 9600 baud); o meshtasticd ignora as frases a mais.
PCAS03_WANT = {3: "1", 4: "3"}          # campo → frequência (1 = cada fix)
CLIENT_MAX_BUFFER = 64 * 1024
REOPEN_S = 3.0


def nmea_ok(line: bytes) -> bool:
    """Frase NMEA com checksum certo."""
    line = line.strip()
    if not line.startswith(b"$") or b"*" not in line:
        return False
    body, _, checksum = line[1:].rpartition(b"*")
    try:
        expected = int(checksum[:2], 16)
    except ValueError:
        return False
    calc = 0
    for byte in body:
        calc ^= byte
    return calc == expected


def with_checksum(body: str) -> bytes:
    calc = 0
    for ch in body:
        calc ^= ord(ch)
    return f"${body}*{calc:02X}\r\n".encode()


def adjust_pcas03(line: bytes) -> bytes:
    """$PCAS03 do meshtasticd com GSA/GSV ligados; o resto fica como veio.

    Só quando ele liga a posição (GGA ou RMC): o "tudo desligado" que manda
    ao procurar o GPS fica intacto, para a detecção não ver frases a mais.
    """
    try:
        body = line.strip()[1:].split(b"*", 1)[0].decode("ascii")
    except UnicodeDecodeError:
        return line
    fields = body.split(",")
    if len(fields) < 6 or (fields[1] in ("", "0") and fields[5] in ("", "0")):
        return line
    changed = False
    for index, value in PCAS03_WANT.items():
        if index < len(fields) and fields[index] in ("", "0"):
            fields[index] = value
            changed = True
    return with_checksum(",".join(fields)) if changed else line


def set_speed(fd: int, baud: int):
    attrs = termios.tcgetattr(fd)
    speed = getattr(termios, f"B{baud}")
    attrs[4] = attrs[5] = speed
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def open_serial(path: str) -> int:
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    tty.setraw(fd)
    attrs = termios.tcgetattr(fd)
    attrs[2] |= termios.CLOCAL | termios.CREAD
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    return fd


def detect_baud(fd: int, wait_s: float = 2.5) -> int | None:
    """Velocidade com que aparecem frases NMEA válidas."""
    for baud in BAUDS:
        set_speed(fd, baud)
        termios.tcflush(fd, termios.TCIFLUSH)
        buf = b""
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if not ready:
                continue
            try:
                buf += os.read(fd, 1024)
            except BlockingIOError:
                continue
            *lines, buf = buf.split(b"\n")
            if any(nmea_ok(line) for line in lines):
                return baud
    return None


class CommandFilter:
    """Comandos do meshtasticd para o GPS: deixa passar, excepto standby.

    O que chega do pty pode ser NMEA (linhas $...) ou binário (UBX). As
    linhas juntam-se até ao \\n; o binário passa tal como vem, salvo o
    pedido de energia do u-blox.
    """

    def __init__(self, block: tuple[str, ...], adjust: bool = True):
        self.block = tuple(b.encode() for b in block)
        self.adjust = adjust
        self._partial = b""
        self.seen: set[bytes] = set()

    def feed(self, data: bytes) -> bytes:
        out = b""
        data = self._partial + data
        self._partial = b""
        while data:
            if data.startswith(b"$"):
                end = data.find(b"\n")
                if end < 0:
                    self._partial = data
                    break
                line, data = data[:end + 1], data[end + 1:]
                head = line.split(b",", 1)[0].split(b"*", 1)[0].strip()
                blocked = any(line.startswith(b) for b in self.block)
                if not blocked and head == b"$PCAS03" and self.adjust:
                    adjusted = adjust_pcas03(line)
                    if adjusted != line:
                        self._note_once(b"PCAS03 " + line.strip(), "meshtasticd → GPS: %s ajustado para %s "
                                        "(GSA/GSV ligados para o céu)" % (line.strip().decode(errors="replace"),
                                                                           adjusted.strip().decode()))
                        line = adjusted
                self._note(head, blocked)
                if not blocked:
                    out += line
            elif data.startswith(UBX_PMREQ[:2]):
                if len(data) < 6:
                    self._partial = data
                    break
                length = data[4] | (data[5] << 8)
                total = 6 + length + 2
                if len(data) < total:
                    self._partial = data
                    break
                frame, data = data[:total], data[total:]
                blocked = frame.startswith(UBX_PMREQ)
                self._note(b"UBX %02x-%02x" % (frame[2], frame[3]), blocked)
                if not blocked:
                    out += frame
            else:
                # Outro binário: até ao próximo início conhecido.
                cut = min([i for i in (data.find(b"$", 1), data.find(UBX_PMREQ[:2], 1)) if i > 0]
                          or [len(data)])
                out += data[:cut]
                data = data[cut:]
        return out

    def _note_once(self, key: bytes, text: str):
        if key not in self.seen:
            self.seen.add(key)
            log.info(text)

    def _note(self, head: bytes, blocked: bool):
        key = head + (b" (travado)" if blocked else b"")
        if key not in self.seen:
            self.seen.add(key)
            log.info("meshtasticd → GPS: %s%s", head.decode(errors="replace"),
                     " — travado (standby), o GPS fica acordado" if blocked else "")


class Mux:
    def __init__(self, device: str, link: str, tcp: tuple[str, int], group: str,
                 block: tuple[str, ...], adjust: bool = True):
        self.device = device
        self.link = link
        self.tcp = tcp
        self.group = group
        self.filter = CommandFilter(block, adjust)
        self.serial_fd: int | None = None
        self.serial_baud: int | None = None
        self.next_open = 0.0
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave)
        self.pty_speed = self._pty_speed()
        self.clients: dict[socket.socket, bytearray] = {}
        self.server = None
        self.stopped = threading.Event()

    def stop(self):
        self.stopped.set()

    # -- montagem --------------------------------------------------------
    def setup(self):
        os.set_blocking(self.master, False)
        slave_path = os.ttyname(self.slave)
        try:
            gid = grp.getgrnam(self.group).gr_gid
            os.chown(slave_path, -1, gid)
        except (KeyError, PermissionError) as e:
            log.warning("pty: não foi possível passar ao grupo %s: %s", self.group, e)
        os.chmod(slave_path, 0o660)
        os.makedirs(os.path.dirname(self.link), exist_ok=True)
        tmp = self.link + ".new"
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        os.symlink(slave_path, tmp)
        os.replace(tmp, self.link)
        log.info("porta virtual para o meshtasticd: %s → %s", self.link, slave_path)

        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(self.tcp)
        self.server.listen(8)
        self.server.setblocking(False)
        log.info("NMEA ao vivo em tcp://%s:%s", *self.tcp)

    def _pty_speed(self):
        # As definições do pty que o meshtasticd muda são as do lado escravo,
        # que este processo mantém aberto (assim o pty não fecha entre usos).
        try:
            return termios.tcgetattr(self.slave)[4]
        except termios.error:
            return None

    # -- GPS real ----------------------------------------------------------
    def ensure_serial(self):
        if self.serial_fd is not None or time.monotonic() < self.next_open:
            return
        try:
            fd = open_serial(self.device)
        except OSError as e:
            log.warning("GPS: não abre %s (%s); nova tentativa em %.0f s", self.device, e, REOPEN_S)
            self.next_open = time.monotonic() + REOPEN_S
            return
        baud = self.serial_baud or detect_baud(fd)
        if baud is None:
            log.warning("GPS: sem NMEA em %s; nova tentativa em %.0f s", self.device, REOPEN_S)
            os.close(fd)
            self.next_open = time.monotonic() + REOPEN_S
            return
        set_speed(fd, baud)
        self.serial_fd, self.serial_baud = fd, baud
        log.info("GPS: %s a %d baud", self.device, baud)

    def close_serial(self, why: str):
        if self.serial_fd is not None:
            log.warning("GPS: %s fechado (%s)", self.device, why)
            os.close(self.serial_fd)
        self.serial_fd = None
        self.next_open = time.monotonic() + REOPEN_S

    def mirror_speed(self):
        """O meshtasticd muda a velocidade do pty ao procurar o GPS: o real acompanha."""
        speed = self._pty_speed()
        if speed is None or speed == self.pty_speed:
            return
        self.pty_speed = speed
        baud = SPEEDS.get(speed)
        if baud and self.serial_fd is not None and baud != self.serial_baud:
            log.info("meshtasticd pediu %d baud: o GPS real acompanha", baud)
            set_speed(self.serial_fd, baud)
            self.serial_baud = baud

    # -- ciclo -----------------------------------------------------------
    def from_gps(self, data: bytes):
        try:
            os.write(self.master, data)
        except BlockingIOError:
            # O meshtasticd não está a ler (a dormir): descarta o que ficou
            # para trás, para ele receber frases frescas quando voltar.
            termios.tcflush(self.slave, termios.TCIFLUSH)
            try:
                os.write(self.master, data)
            except OSError:
                pass
        except OSError:
            pass
        for client, buf in list(self.clients.items()):
            buf += data
            if len(buf) > CLIENT_MAX_BUFFER:
                log.info("cliente TCP lento, desligado")
                self.drop(client)

    def drop(self, client):
        self.clients.pop(client, None)
        try:
            client.close()
        except OSError:
            pass

    def run(self):
        self.setup()
        last_mirror = 0.0
        while not self.stopped.is_set():
            self.ensure_serial()
            readers = [self.master, self.server, *self.clients]
            if self.serial_fd is not None:
                readers.append(self.serial_fd)
            writers = [c for c, buf in self.clients.items() if buf]
            ready_r, ready_w, _ = select.select(readers, writers, [], 0.5)

            for fd in ready_r:
                if fd is self.server:
                    try:
                        client, addr = self.server.accept()
                        client.setblocking(False)
                        self.clients[client] = bytearray()
                        log.info("cliente NMEA ligado: %s:%s", *addr)
                    except OSError:
                        pass
                elif fd == self.serial_fd:
                    try:
                        data = os.read(self.serial_fd, 4096)
                    except BlockingIOError:
                        continue
                    except OSError as e:
                        self.close_serial(str(e))
                        continue
                    if not data:
                        self.close_serial("EOF")
                        continue
                    self.from_gps(data)
                elif fd == self.master:
                    try:
                        data = os.read(self.master, 4096)
                    except (BlockingIOError, OSError):
                        continue
                    out = self.filter.feed(data)
                    if out and self.serial_fd is not None:
                        try:
                            os.write(self.serial_fd, out)
                        except OSError:
                            pass
                elif isinstance(fd, socket.socket):
                    try:
                        if not fd.recv(1024):          # clientes só lêem; dados deles ignoram-se
                            self.drop(fd)
                    except OSError:
                        self.drop(fd)

            for client in ready_w:
                buf = self.clients.get(client)
                if not buf:
                    continue
                try:
                    sent = client.send(buf)
                    del buf[:sent]
                except BlockingIOError:
                    pass
                except OSError:
                    self.drop(client)

            now = time.monotonic()
            if now - last_mirror > 1.0:
                last_mirror = now
                self.mirror_speed()
        self.close()

    def close(self):
        for client in list(self.clients):
            self.drop(client)
        for fd in (self.serial_fd, self.master, self.slave):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self.serial_fd = None
        if self.server is not None:
            self.server.close()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--device", default="/dev/ttyAMA0", help="porta série do GPS")
    p.add_argument("--link", default="/run/meshdeck-gps/meshtasticd",
                   help="caminho da porta virtual para o meshtasticd (GPS: SerialPath)")
    p.add_argument("--tcp", default="127.0.0.1:10110", help="onde publicar o NMEA (host:porta)")
    p.add_argument("--group", default="dialout", help="grupo com acesso à porta virtual")
    p.add_argument("--no-block", action="store_true",
                   help="deixar passar também os comandos de standby (o GPS pode dormir)")
    p.add_argument("--no-sky", action="store_true",
                   help="não pedir GSA/GSV ao GPS (sem céu nem DOP para as outras apps)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s", stream=sys.stdout)
    host, _, port = args.tcp.rpartition(":")
    mux = Mux(args.device, args.link, (host or "127.0.0.1", int(port)), args.group,
              () if args.no_block else DEFAULT_BLOCK, adjust=not args.no_sky)
    try:
        mux.run()
    except KeyboardInterrupt:
        pass
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            log.error("a porta %s já está em uso", args.tcp)
            return 1
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
