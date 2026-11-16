import json
import os

import pytest

from firmware.provisioning import config_store
from firmware.runtime.config_schema import ConfigValidationError


def good(host="192.168.1.10"):
    return {
        "wifi": {"ssid": "home", "password": "hunter2hunter2"},
        "backend": {"type": "local_mqtt", "host": host},
        "sensors": [{"type": "dht11", "name": "lounge", "pin": 4}],
    }


@pytest.fixture(autouse=True)
def flash(tmp_path, monkeypatch):
    """Point the store at a temp directory standing in for the flash fs."""
    monkeypatch.setattr(config_store, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config_store, "TMP_PATH", str(tmp_path / "config.json.tmp"))
    return tmp_path


def write_raw(content):
    with open(config_store.CONFIG_PATH, "w") as f:
        f.write(content)


def test_nothing_saved_yet():
    assert config_store.exists() is False
    assert config_store.load() is None


def test_save_then_load_round_trips():
    config_store.save(good())
    assert config_store.exists() is True
    assert config_store.load() == good()


def test_save_leaves_no_tmp_file(flash):
    config_store.save(good())
    assert sorted(os.listdir(flash)) == ["config.json"]


def test_save_replaces_existing_config():
    config_store.save(good("a.local"))
    config_store.save(good("b.local"))
    assert config_store.load()["backend"]["host"] == "b.local"


def test_save_rejects_invalid_config_and_keeps_old_one():
    config_store.save(good())
    bad = good()
    del bad["wifi"]
    with pytest.raises(ConfigValidationError):
        config_store.save(bad)
    assert config_store.load() == good()


def test_invalid_save_writes_nothing(flash):
    with pytest.raises(ConfigValidationError):
        config_store.save({})
    assert os.listdir(flash) == []


def test_crash_while_writing_tmp_keeps_old_config(flash, monkeypatch):
    config_store.save(good("old.local"))

    def exploding_dump(obj, f):
        f.write('{"wifi": ')  # partial write, then power loss
        raise OSError("simulated power loss")

    monkeypatch.setattr(config_store.json, "dump", exploding_dump)
    with pytest.raises(OSError):
        config_store.save(good("new.local"))
    assert config_store.load()["backend"]["host"] == "old.local"
    assert sorted(os.listdir(flash)) == ["config.json"]


def test_crash_before_rename_keeps_old_config(flash, monkeypatch):
    config_store.save(good("old.local"))

    def exploding_rename(src, dst):
        raise OSError("simulated power loss")

    monkeypatch.setattr(config_store.os, "rename", exploding_rename)
    with pytest.raises(OSError):
        config_store.save(good("new.local"))
    assert config_store.load()["backend"]["host"] == "old.local"


def test_leftover_tmp_from_earlier_crash_is_ignored_and_overwritten(flash):
    (flash / "config.json.tmp").write_text('{"half": ')
    assert config_store.load() is None
    config_store.save(good())
    assert config_store.load() == good()
    assert sorted(os.listdir(flash)) == ["config.json"]


@pytest.mark.parametrize("content", [
    "",
    "not json",
    '{"wifi": ',
    "[]",
    "null",
    '"a string"',
    "{}",
    json.dumps({"wifi": {"ssid": "x", "password": ""}, "backend": {"type": "nope"}, "sensors": []}),
])
def test_unusable_config_loads_as_none(content):
    write_raw(content)
    assert config_store.load() is None


def test_binary_garbage_loads_as_none():
    with open(config_store.CONFIG_PATH, "wb") as f:
        f.write(b"\xff\xfe\x00\x80")
    assert config_store.load() is None


def test_config_path_being_a_directory_loads_as_none(flash):
    os.mkdir(config_store.CONFIG_PATH)
    assert config_store.load() is None


def test_clear_removes_config():
    config_store.save(good())
    config_store.clear()
    assert config_store.exists() is False
    assert config_store.load() is None


def test_clear_also_removes_leftover_tmp(flash):
    config_store.save(good())
    (flash / "config.json.tmp").write_text("junk")
    config_store.clear()
    assert os.listdir(flash) == []


def test_clear_with_nothing_saved_is_a_noop():
    config_store.clear()
    assert config_store.exists() is False
