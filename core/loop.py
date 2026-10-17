"""
core/loop.py — Event loop de uma só thread para o núcleo.

A biblioteca meshtastic entrega callbacks pubsub em threads próprias; o
MeshtasticWorker encaminha-os para uma única thread para não haver acesso
concorrente ao estado interno. Esse desenho resolveu bugs reais de
concorrência.

    loop.single_shot(ms, fn)   corre fn uma vez, daqui a ms
    loop.every(ms, fn)         corre fn periodicamente
    loop.post(fn)              corre fn na thread do loop, logo que possível

Garantias:
  * Todos os callbacks correm na thread do loop, nunca em paralelo entre si.
  * post() a partir da própria thread do loop também enfileira, não corre
    inline.
  * Uma excepção num callback é registada e o loop continua.
  * Um timer periódico é reagendado só depois de o callback retornar, para
    um callback lento não acumular disparos em atraso.
"""

import functools
import heapq
import itertools
import logging
import threading
import time

logger = logging.getLogger("MeshDeck.loop")


def on_loop(method):
    """Garante que o método corre na thread do CoreLoop do objecto.

    Chamada da própria thread do loop corre inline; chamada de outra thread
    é enfileirada e retorna None imediatamente. O objecto tem de expor o loop em `self._loop`.

    Só se aplica a métodos sem valor de retorno útil — um comando
    enfileirado não tem resultado para devolver.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        loop = self._loop
        if loop.is_core_thread():
            return method(self, *args, **kwargs)
        loop.post(functools.partial(method, self, *args, **kwargs))
        return None

    wrapper.__wrapped_on_loop__ = True
    return wrapper


class Timer:
    """Handle de um temporizador (único ou periódico)."""

    __slots__ = ("_loop", "_interval", "_fn", "_args", "_repeat", "_active", "_seq", "name")

    def __init__(self, loop: "CoreLoop", interval_ms: float, fn, args, repeat: bool, name: str = ""):
        self._loop = loop
        self._interval = max(0.0, float(interval_ms)) / 1000.0
        self._fn = fn
        self._args = args
        self._repeat = repeat
        self._active = False
        self._seq = -1          # identifica a entrada válida no heap
        self.name = name or getattr(fn, "__name__", "timer")

    # -- controlo ---------------------------------------------------------
    @property
    def interval_ms(self) -> float:
        return self._interval * 1000.0

    def set_interval(self, interval_ms: float):
        """Altera o intervalo. Se activo, reagenda a partir de agora."""
        self._interval = max(0.0, float(interval_ms)) / 1000.0
        if self._active:
            self.start()

    def start(self, interval_ms: float = None):
        """(Re)arranca o timer. Chamar num timer já activo reinicia a contagem."""
        if interval_ms is not None:
            self._interval = max(0.0, float(interval_ms)) / 1000.0
        self._active = True
        self._loop._schedule(self)

    def stop(self):
        """Pára o timer. Seguro de chamar de dentro do próprio callback."""
        self._active = False
        self._seq = -1   # invalida qualquer entrada ainda no heap

    @property
    def is_active(self) -> bool:
        return self._active

    def __repr__(self) -> str:
        kind = "repeat" if self._repeat else "single"
        return f"<Timer {self.name} {self._interval * 1000:.0f}ms {kind} active={self._active}>"


class CoreLoop:
    """Loop de eventos de thread única: fila de callables + heap de timers."""

    def __init__(self, name: str = "meshdeck-core"):
        self._name = name
        self._cv = threading.Condition(threading.Lock())
        self._queue: list = []          # [(fn, args), ...]
        self._timers: list = []         # heap de (due, seq, timer)
        self._seq = itertools.count()
        self._thread: threading.Thread = None
        self._running = False
        self._stopping = False
        self._started_evt = threading.Event()

    # -- ciclo de vida ----------------------------------------------------
    def start(self):
        """Arranca a thread do loop. Idempotente."""
        with self._cv:
            if self._running:
                return
            self._running = True
            self._stopping = False
        self._started_evt.clear()
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()
        self._started_evt.wait(2.0)

    def stop(self, timeout: float = 5.0):
        """Pede a paragem e espera pela thread. Idempotente.

        Os callbacks já enfileirados correm antes de a thread sair, para que
        um sinal emitido imediatamente antes do stop() não se perca.
        """
        with self._cv:
            if not self._running:
                return
            self._stopping = True
            self._cv.notify_all()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout)
            if t.is_alive():
                logger.warning("CoreLoop(%s): thread não terminou em %.1fs", self._name, timeout)
        with self._cv:
            self._running = False
            self._queue.clear()
            self._timers.clear()
        self._thread = None

    @property
    def is_running(self) -> bool:
        return self._running

    def is_core_thread(self) -> bool:
        """True se o código que chama já está na thread do loop."""
        return self._thread is not None and threading.current_thread() is self._thread

    # -- agendamento ------------------------------------------------------
    def post(self, fn, *args):
        """Enfileira fn(*args) para correr na thread do loop.

        Chamável de qualquer thread, inclusive da própria thread do loop, caso
        em que o callback corre no ciclo seguinte e não inline.
        """
        with self._cv:
            if self._stopping:
                return
            self._queue.append((fn, args))
            self._cv.notify()

    def single_shot(self, delay_ms: float, fn, *args) -> Timer:
        """Corre fn(*args) uma vez, daqui a delay_ms. Devolve o Timer para poder cancelar."""
        t = Timer(self, delay_ms, fn, args, repeat=False)
        t.start()
        return t

    def every(self, interval_ms: float, fn, *args) -> Timer:
        """Timer periódico já arrancado."""
        t = Timer(self, interval_ms, fn, args, repeat=True)
        t.start()
        return t

    def timer(self, interval_ms: float, fn, *args, repeat: bool = True) -> Timer:
        """Cria um Timer parado, para arrancar mais tarde com .start()."""
        return Timer(self, interval_ms, fn, args, repeat=repeat)

    # -- interno ----------------------------------------------------------
    def _schedule(self, t: Timer):
        with self._cv:
            if self._stopping:
                return
            t._seq = next(self._seq)
            heapq.heappush(self._timers, (time.monotonic() + t._interval, t._seq, t))
            self._cv.notify()

    def _next_due_locked(self):
        """Tempo até o próximo timer válido, ou None se não houver nenhum."""
        while self._timers:
            due, seq, t = self._timers[0]
            if not t._active or t._seq != seq:
                heapq.heappop(self._timers)      # entrada obsoleta
                continue
            return due - time.monotonic()
        return None

    def _pop_due_locked(self) -> list:
        now = time.monotonic()
        ready = []
        while self._timers:
            due, seq, t = self._timers[0]
            if not t._active or t._seq != seq:
                heapq.heappop(self._timers)
                continue
            if due > now:
                break
            heapq.heappop(self._timers)
            ready.append((t, seq))
        return ready

    def _run(self):
        logger.debug("CoreLoop(%s) arrancou", self._name)
        self._started_evt.set()
        while True:
            with self._cv:
                while True:
                    if self._stopping or self._queue:
                        break
                    wait = self._next_due_locked()
                    if wait is not None and wait <= 0:
                        break
                    self._cv.wait(wait)   # wait(None) espera indefinidamente
                stopping = self._stopping
                # Ao parar, esvazia-se o que já está na fila mas não se
                # disparam timers: um sinal emitido imediatamente antes do
                # stop() ainda tem de chegar ao destinatário. post() já é
                # recusado nesta fase, por isso a drenagem é finita.
                calls = self._queue
                self._queue = []
                due = [] if stopping else self._pop_due_locked()

            for fn, args in calls:
                self._invoke(fn, args)

            if stopping:
                logger.debug("CoreLoop(%s) parou (%d callback(s) drenados)",
                             self._name, len(calls))
                return

            for t, seq in due:
                # stop() durante o processamento invalida o disparo.
                if not t._active or t._seq != seq:
                    continue
                if not t._repeat:
                    t._active = False
                self._invoke(t._fn, t._args)
                # Reagendar só agora: um callback lento não acumula atrasos.
                # Se o callback chamou stop() ou start(), _seq mudou e não se toca.
                if t._repeat and t._active and t._seq == seq:
                    self._schedule(t)

    @staticmethod
    def _invoke(fn, args):
        try:
            fn(*args)
        except Exception:
            logger.exception("CoreLoop: callback %r levantou excepção", fn)


# Loop partilhado do processo. O servidor usa este, em vez de criar
# instâncias próprias, para que haja uma só thread a tocar no estado.
_default_loop: CoreLoop = None
_default_lock = threading.Lock()


def default_loop() -> CoreLoop:
    """Devolve (criando na primeira chamada) o loop partilhado do processo."""
    global _default_loop
    with _default_lock:
        if _default_loop is None:
            _default_loop = CoreLoop()
        return _default_loop
