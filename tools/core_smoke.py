#!/usr/bin/env python3
"""
tools/core_smoke.py — Verificação do núcleo contra um nó real, sem interface.

Liga-se ao nó, imprime cada sinal à medida que chega e no fim mostra um
resumo. Serve para confirmar que core/ funciona sozinho, antes de existir
servidor ou browser.

Exemplos:
    python3 tools/core_smoke.py                          # TCP localhost:4403
    python3 tools/core_smoke.py --host 192.168.1.50
    python3 tools/core_smoke.py --serial /dev/ttyACM0
    python3 tools/core_smoke.py --seconds 120 --traceroute '!1234abcd'
    python3 tools/core_smoke.py --quiet                  # só o resumo
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.loop import CoreLoop
from core.mesh_worker import MeshtasticWorker
from core.node_store import NodeStore
from core.signals import Signal

# Sinais cujo payload é grande ou muito frequente: contam-se, não se imprimem
# na íntegra, senão o terminal fica ilegível numa rede movimentada.
VOLUMINOUS = {"raw_packet_received", "nodes_batch", "interface_ready"}


def human(value, limit=110):
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "…"


class Smoke:
    def __init__(self, worker: MeshtasticWorker, store: NodeStore, quiet: bool):
        self.worker = worker
        self.store = store
        self.quiet = quiet
        self.counts = {}
        self.started = time.monotonic()
        self.connected = False

        for name, sig in vars(worker).items():
            if isinstance(sig, Signal):
                self.counts[name] = 0
                sig.connect(self._make_logger(name))

        # Os sinais que alimentam o estado ligam-se também ao store, para o
        # resumo final refletir o que o servidor verá.
        worker.my_node_id_ready.connect(lambda nid: store.set_local_node(nid))
        worker.nodes_batch.connect(store.apply_batch)
        worker.node_updated.connect(
            lambda nid, data, pkt: store.update(nid, data, pkt))
        worker.connection_changed.connect(self._on_connection)

    def _make_logger(self, name):
        def handler(*args):
            self.counts[name] += 1
            if self.quiet:
                return
            stamp = datetime.now().strftime("%H:%M:%S")
            if name in VOLUMINOUS:
                detail = f"{len(args[0])} itens" if args and hasattr(args[0], "__len__") \
                    else type(args[0]).__name__
                print(f"{stamp}  {name:<24} {detail}  (total {self.counts[name]})")
            else:
                print(f"{stamp}  {name:<24} {human(args if len(args) != 1 else args[0])}")
        return handler

    def _on_connection(self, connected):
        self.connected = connected

    def summary(self):
        elapsed = time.monotonic() - self.started
        print("\n" + "=" * 68)
        print(f"Resumo após {elapsed:.0f}s")
        print("=" * 68)
        print(f"  ligado no fim:        {'sim' if self.connected else 'NÃO'}")
        print(f"  nó local:             {self.store.local_id or '—'}")
        print(f"  nós conhecidos:       {self.store.count()}")
        print(f"  já contactados:       {self.store.contacted_count()}")
        print(f"  activos (<2h):        {self.store.online_count()}")
        print(f"  com posição GPS:      {len(self.store.with_position())}")
        print("\n  sinais recebidos:")
        for name in sorted(self.counts):
            if self.counts[name]:
                print(f"    {name:<24} {self.counts[name]}")
        silent = [n for n in sorted(self.counts) if not self.counts[n]]
        if silent:
            print(f"\n  sem emissões: {', '.join(silent)}")

        import json
        try:
            json.dumps(self.store.snapshot())
            print("\n  snapshot serializa para JSON: sim")
        except (TypeError, ValueError) as e:
            print(f"\n  snapshot NÃO serializa: {e}")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="localhost", help="host do meshtasticd (default: localhost)")
    p.add_argument("--port", type=int, default=4403, help="porta TCP (default: 4403)")
    p.add_argument("--serial", metavar="DEVICE",
                   help="usar SerialInterface neste dispositivo em vez de TCP")
    p.add_argument("--seconds", type=float, default=45.0,
                   help="quanto tempo observar antes do resumo (default: 45)")
    p.add_argument("--traceroute", metavar="NODE_ID",
                   help="enviar um traceroute para este nó 10s após ligar")
    p.add_argument("--message", metavar="TEXT",
                   help="enviar esta mensagem no canal 0 após ligar")
    p.add_argument("--quiet", action="store_true", help="só imprimir o resumo")
    p.add_argument("--debug", action="store_true", help="logging em DEBUG")
    return p.parse_args()


def main():
    args = parse_args()
    logging.getLogger().setLevel(logging.DEBUG if args.debug else logging.INFO)
    logging.basicConfig(format="%(levelname)s %(name)s: %(message)s")

    target = args.serial or f"{args.host}:{args.port}"
    print(f"MeshDeck core smoke — alvo {target}, a observar {args.seconds:.0f}s")
    print("Ctrl+C para terminar mais cedo.\n")

    loop = CoreLoop(name="smoke-core")
    loop.start()

    store = NodeStore()
    worker = MeshtasticWorker(hostname=args.host, port=args.port,
                              serial_port=args.serial, loop=loop)
    smoke = Smoke(worker, store, args.quiet)

    worker.start()

    if args.traceroute:
        loop.single_shot(10_000, worker.send_traceroute, args.traceroute)
    if args.message:
        loop.single_shot(8_000, worker.send_message, 0, args.message)

    try:
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\ninterrompido")

    smoke.summary()
    worker.stop()
    loop.stop()
    return 0 if smoke.connected else 1


if __name__ == "__main__":
    sys.exit(main())
