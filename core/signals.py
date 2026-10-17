"""
core/signals.py — Sinais (observer) do núcleo.

emit() chama os slots sincronamente, na thread que emitiu, pela ordem em que
foram ligados. Uma excepção num slot é registada e não impede os restantes.

Para entrega noutra thread usa-se core.loop.CoreLoop.post() explicitamente.

Nota: as ligações guardam referências fortes. Quem liga é responsável por
chamar disconnect() se o receptor tiver vida mais curta do que o emissor.
"""

import logging
import threading

logger = logging.getLogger("MeshDeck.signals")


class Signal:
    """Sinal com connect/disconnect/emit.

    Uso típico como atributo de classe não é possível sem metaclasse, por isso
    os sinais são criados por instância, no __init__:

        self.node_updated = Signal("node_updated")
        self.node_updated.connect(handler)
        self.node_updated.emit(node_id, data, packet)
    """

    __slots__ = ("name", "_slots", "_lock", "_blocked")

    def __init__(self, name: str = ""):
        self.name = name or "<anonymous>"
        self._slots: list = []
        self._lock = threading.RLock()
        self._blocked = False

    # -- ligação ----------------------------------------------------------
    def connect(self, slot):
        """Liga um callable. Devolve o slot, para poder ser usado como decorador."""
        if not callable(slot):
            raise TypeError(f"Signal({self.name}).connect espera um callable, recebeu {type(slot).__name__}")
        with self._lock:
            self._slots.append(slot)
        return slot

    def disconnect(self, slot=None):
        """Desliga um slot, ou todos se slot for None.

        Desligar um slot que não está ligado não é erro, para simplificar o
        teardown.
        """
        with self._lock:
            if slot is None:
                self._slots.clear()
            else:
                try:
                    self._slots.remove(slot)
                except ValueError:
                    pass

    # -- emissão ----------------------------------------------------------
    def emit(self, *args, **kwargs):
        """Chama todos os slots ligados, na ordem de ligação.

        Itera sobre uma cópia da lista porque um slot pode ligar ou desligar
        slots durante a emissão.
        """
        if self._blocked:
            return
        with self._lock:
            slots = list(self._slots)
        for slot in slots:
            try:
                slot(*args, **kwargs)
            except Exception:
                logger.exception("Signal(%s): slot %r levantou excepção", self.name, slot)

    # -- utilitários ------------------------------------------------------
    def block(self, blocked: bool = True):
        """Equivalente a QObject.blockSignals para este sinal."""
        self._blocked = bool(blocked)

    def is_blocked(self) -> bool:
        return self._blocked

    def __len__(self) -> int:
        with self._lock:
            return len(self._slots)

    def __bool__(self) -> bool:
        # Um Signal é sempre verdadeiro; sem isto, __len__ == 0 tornaria
        # `if self.some_signal:` falso, o que seria uma armadilha.
        return True

    def __repr__(self) -> str:
        return f"<Signal {self.name} slots={len(self)}>"
