"""Testes do CoreLoop e do Signal — a base de que depende todo o core sem Qt."""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core.loop import CoreLoop
from core.signals import Signal


@pytest.fixture
def loop():
    lp = CoreLoop(name="test-loop")
    lp.start()
    yield lp
    lp.stop()


def wait_until(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.005)
    return False


# ── post ─────────────────────────────────────────────────────────────────────

def test_post_runs_on_loop_thread(loop):
    seen = []
    loop.post(lambda: seen.append(threading.current_thread().name))
    assert wait_until(lambda: seen)
    assert seen[0] == "test-loop"


def test_post_preserves_order(loop):
    got = []
    for i in range(200):
        loop.post(got.append, i)
    assert wait_until(lambda: len(got) == 200)
    assert got == list(range(200))


def test_post_from_many_threads_is_serialised(loop):
    """Sem serialização, incrementos concorrentes perdiam contagens."""
    state = {"n": 0}

    def bump():
        state["n"] += 1

    threads = [threading.Thread(target=lambda: [loop.post(bump) for _ in range(100)])
               for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert wait_until(lambda: state["n"] == 800)


def test_post_from_loop_thread_defers(loop):
    """post() de dentro do loop enfileira, não corre inline (QueuedConnection)."""
    order = []

    def inner():
        order.append("inner")

    def outer():
        loop.post(inner)
        order.append("outer")

    loop.post(outer)
    assert wait_until(lambda: len(order) == 2)
    assert order == ["outer", "inner"]


def test_exception_in_callback_does_not_kill_loop(loop):
    got = []
    loop.post(lambda: (_ for _ in ()).throw(ValueError("boom")))
    loop.post(got.append, "depois")
    assert wait_until(lambda: got == ["depois"])
    assert loop.is_running


# ── single_shot ──────────────────────────────────────────────────────────────

def test_single_shot_fires_once(loop):
    got = []
    loop.single_shot(20, got.append, "x")
    assert wait_until(lambda: got == ["x"])
    time.sleep(0.1)
    assert got == ["x"]


def test_single_shot_zero_delay(loop):
    """QTimer.singleShot(0, ...) é usado no worker para ceder um ciclo ao loop."""
    got = []
    loop.single_shot(0, got.append, 1)
    assert wait_until(lambda: got == [1])


def test_single_shot_respects_delay(loop):
    stamps = []
    t0 = time.monotonic()
    loop.single_shot(120, lambda: stamps.append(time.monotonic() - t0))
    assert wait_until(lambda: stamps)
    assert stamps[0] >= 0.10


def test_single_shot_cancel(loop):
    got = []
    t = loop.single_shot(80, got.append, "nunca")
    t.stop()
    time.sleep(0.2)
    assert got == []


def test_timers_fire_in_due_order(loop):
    got = []
    loop.single_shot(90, got.append, "c")
    loop.single_shot(30, got.append, "a")
    loop.single_shot(60, got.append, "b")
    assert wait_until(lambda: len(got) == 3)
    assert got == ["a", "b", "c"]


# ── every ────────────────────────────────────────────────────────────────────

def test_every_repeats(loop):
    got = []
    t = loop.every(25, got.append, 1)
    assert wait_until(lambda: len(got) >= 4, timeout=4.0)
    t.stop()
    n = len(got)
    time.sleep(0.15)
    assert len(got) == n


def test_every_stop_from_inside_callback(loop):
    got = []
    holder = {}

    def cb():
        got.append(1)
        if len(got) == 3:
            holder["t"].stop()

    holder["t"] = loop.every(15, cb)
    assert wait_until(lambda: len(got) == 3, timeout=4.0)
    time.sleep(0.15)
    assert len(got) == 3
    assert not holder["t"].is_active


def test_every_slow_callback_does_not_accumulate(loop):
    """Reagendamento pós-callback evita uma avalanche de disparos em atraso."""
    got = []

    def slow():
        got.append(1)
        time.sleep(0.08)

    t = loop.every(10, slow)
    time.sleep(0.5)
    t.stop()
    # Com intervalo 10ms e callback de 80ms, ~5-6 disparos em 0.5s.
    # Sem a protecção, o heap acumularia disparos e dispararia dezenas.
    assert 3 <= len(got) <= 12, f"disparos: {len(got)}"


def test_timer_restart_resets_countdown(loop):
    got = []
    t = loop.timer(60, got.append, "x", repeat=False)
    t.start()
    time.sleep(0.03)
    t.start()           # reinicia a contagem
    time.sleep(0.04)
    assert got == []    # ainda não passaram 60ms desde o restart
    assert wait_until(lambda: got == ["x"])


def test_set_interval_while_active(loop):
    got = []
    t = loop.every(500, got.append, 1)
    t.set_interval(20)
    assert wait_until(lambda: len(got) >= 2, timeout=2.0)
    t.stop()


# ── ciclo de vida ────────────────────────────────────────────────────────────

def test_is_core_thread(loop):
    assert not loop.is_core_thread()
    got = []
    loop.post(lambda: got.append(loop.is_core_thread()))
    assert wait_until(lambda: got)
    assert got[0] is True


def test_stop_is_idempotent():
    lp = CoreLoop(name="idem")
    lp.start()
    lp.start()
    lp.stop()
    lp.stop()
    assert not lp.is_running


def test_post_after_stop_is_dropped():
    lp = CoreLoop(name="stopped")
    lp.start()
    lp.stop()
    lp.post(lambda: pytest.fail("não devia correr"))
    time.sleep(0.05)


# ── Signal ───────────────────────────────────────────────────────────────────

def test_signal_order_and_args():
    s = Signal("s")
    got = []
    s.connect(lambda a, b: got.append(("first", a, b)))
    s.connect(lambda a, b: got.append(("second", a, b)))
    s.emit(1, 2)
    assert got == [("first", 1, 2), ("second", 1, 2)]


def test_signal_slot_exception_does_not_stop_others():
    s = Signal("s")
    got = []

    def boom():
        raise RuntimeError("x")

    s.connect(boom)
    s.connect(lambda: got.append(1))
    s.emit()
    assert got == [1]


def test_signal_disconnect():
    s = Signal("s")
    got = []
    fn = s.connect(lambda: got.append(1))
    s.emit()
    s.disconnect(fn)
    s.emit()
    assert got == [1]
    s.disconnect(fn)        # desligar duas vezes não é erro


def test_signal_disconnect_all():
    s = Signal("s")
    got = []
    s.connect(lambda: got.append(1))
    s.connect(lambda: got.append(2))
    s.disconnect()
    s.emit()
    assert got == []


def test_signal_connect_during_emit_is_safe():
    """Um slot que liga outro slot não deve corromper a iteração."""
    s = Signal("s")
    got = []

    def adder():
        got.append("adder")
        s.connect(lambda: got.append("late"))

    s.connect(adder)
    s.emit()
    assert got == ["adder"]      # o novo slot só corre na emissão seguinte
    s.emit()
    assert got == ["adder", "adder", "late"]


def test_signal_block():
    s = Signal("s")
    got = []
    s.connect(lambda: got.append(1))
    s.block()
    s.emit()
    assert got == []
    s.block(False)
    s.emit()
    assert got == [1]


def test_signal_is_always_truthy():
    """__len__ == 0 não pode tornar `if sig:` falso."""
    assert bool(Signal("vazio")) is True


def test_signal_emit_from_threads_then_post_to_loop(loop):
    """Padrão real do worker: pubsub emite em threads da lib, o slot encaminha."""
    s = Signal("packet")
    seen = []
    s.connect(lambda pkt: loop.post(seen.append, pkt))

    def producer(base):
        for i in range(50):
            s.emit(base + i)

    threads = [threading.Thread(target=producer, args=(k * 1000,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert wait_until(lambda: len(seen) == 200)


def test_stop_drains_pending_callbacks():
    """Um sinal emitido imediatamente antes do stop() tem de ser entregue."""
    lp = CoreLoop(name="draining")
    lp.start()
    got = []
    for i in range(50):
        lp.post(got.append, i)
    lp.stop()
    assert got == list(range(50))


def test_stop_does_not_fire_pending_timers():
    lp = CoreLoop(name="draining-timers")
    lp.start()
    got = []
    lp.single_shot(0, got.append, "timer")
    lp.post(got.append, "call")
    lp.stop()
    assert "call" in got
    assert "timer" not in got


def test_callback_posting_during_drain_does_not_loop_forever():
    lp = CoreLoop(name="drain-reentrant")
    lp.start()
    got = []

    def reposter():
        got.append(1)
        lp.post(reposter)      # recusado durante a paragem

    lp.post(reposter)
    lp.stop()
    assert len(got) <= 2
