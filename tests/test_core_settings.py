"""Testes do Settings — substituto do QSettings."""

import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core.settings import Settings, config_dir


@pytest.fixture
def st(tmp_path):
    return Settings(filename="settings.json", directory=str(tmp_path))


def test_defaults_when_missing(st):
    assert st.value("nao_existe") is None
    assert st.value("nao_existe", "fallback") == "fallback"
    assert not st.contains("nao_existe")


def test_roundtrip_preserves_types(st, tmp_path):
    st.set_value("language", "pt")
    st.set_value("sound_enabled", False)
    st.set_value("port", 4403)
    st.set_value("themes", ["dark", "light"])

    again = Settings(filename="settings.json", directory=str(tmp_path))
    assert again.value("language") == "pt"
    assert again.value("sound_enabled") is False      # bool, não "false"
    assert again.value("port") == 4403                # int, não "4403"
    assert again.value("themes") == ["dark", "light"]


def test_false_is_not_treated_as_missing(st):
    """O bug clássico: `if value:` confundir False gravado com ausência."""
    st.set_value("sound_enabled", False)
    assert st.contains("sound_enabled")
    assert st.value("sound_enabled", True) is False


def test_type_coercion_of_hand_written_values(st, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"sound_enabled": "false", "port": "4403"}))
    s = Settings(filename="settings.json", directory=str(tmp_path))
    assert s.value("sound_enabled", type=bool) is False
    assert s.value("port", type=int) == 4403


def test_bad_coercion_returns_raw_value(st):
    st.set_value("port", "nao-e-numero")
    assert st.value("port", type=int) == "nao-e-numero"


def test_remove_and_clear(st):
    st.set_value("a", 1)
    st.set_value("b", 2)
    st.remove("a")
    assert not st.contains("a")
    assert st.contains("b")
    st.clear()
    assert st.all() == {}


def test_update_writes_once(st, tmp_path):
    st.update({"language": "en", "sound_enabled": True})
    again = Settings(filename="settings.json", directory=str(tmp_path))
    assert again.value("language") == "en"
    assert again.value("sound_enabled") is True


def test_corrupt_file_falls_back_to_defaults_and_is_kept(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{isto nao e json")
    s = Settings(filename="settings.json", directory=str(tmp_path))
    assert s.value("language", "en") == "en"
    # O ficheiro original não é apagado, para o utilizador poder recuperá-lo.
    assert path.read_text() == "{isto nao e json"


def test_non_object_json_is_ignored(tmp_path):
    (tmp_path / "settings.json").write_text("[1, 2, 3]")
    s = Settings(filename="settings.json", directory=str(tmp_path))
    assert s.all() == {}


def test_write_is_atomic_no_tmp_left_behind(st, tmp_path):
    st.set_value("a", 1)
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []



def test_concurrent_writes_do_not_corrupt_file(st, tmp_path):
    def writer(n):
        for i in range(30):
            st.set_value(f"k{n}", i)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # O ficheiro tem de continuar a ser JSON válido com as 6 chaves.
    data = json.loads((tmp_path / "settings.json").read_text())
    assert sorted(data) == [f"k{n}" for n in range(6)]


def test_config_dir_respects_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config_dir() == str(tmp_path / "meshdeck")
    assert os.path.isdir(config_dir())


def test_separate_files_are_independent(tmp_path):
    a = Settings(filename="settings.json", directory=str(tmp_path))
    b = Settings(filename="server.json", directory=str(tmp_path))
    a.set_value("x", 1)
    b.set_value("x", 2)
    assert Settings(filename="settings.json", directory=str(tmp_path)).value("x") == 1
    assert Settings(filename="server.json", directory=str(tmp_path)).value("x") == 2
