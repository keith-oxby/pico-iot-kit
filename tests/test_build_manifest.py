"""build/manifest.py must freeze files in a fixed order, whatever the filesystem does."""

import os

import pytest

MANIFEST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build", "manifest.py")


def run_manifest(stage, monkeypatch):
    calls = {"freeze": [], "include": [], "require": []}
    monkeypatch.setenv("PICOKIT_STAGE", str(stage))
    env = {
        "freeze": lambda *a, **k: calls["freeze"].append((a, k)),
        "include": lambda *a, **k: calls["include"].append(a),
        "require": lambda *a, **k: calls["require"].append(a),
    }
    with open(MANIFEST) as f:
        exec(compile(f.read(), MANIFEST, "exec"), env)
    return calls


@pytest.fixture
def stage(tmp_path):
    # created in a scrambled order, as a filesystem might later list them
    for rel in ("main.py", "firmware/zeta.py", "firmware/sensors/b.py", "firmware/alpha.py",
                "firmware/sensors/a.py", "firmware/notes.txt", "firmware/sensors/__init__.py"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n")
    return tmp_path


def test_files_are_frozen_by_name_in_sorted_order(stage, monkeypatch):
    calls = run_manifest(stage, monkeypatch)
    ((args, _),) = calls["freeze"]
    assert args[0] == str(stage)
    assert args[1] == (
        "main.py",
        os.path.join("firmware", "alpha.py"),
        os.path.join("firmware", "zeta.py"),
        os.path.join("firmware", "sensors", "__init__.py"),
        os.path.join("firmware", "sensors", "a.py"),
        os.path.join("firmware", "sensors", "b.py"),
    )


def test_only_python_files_are_frozen(stage, monkeypatch):
    calls = run_manifest(stage, monkeypatch)
    assert all(p.endswith(".py") for p in calls["freeze"][0][0][1])


def test_the_board_manifest_and_the_mqtt_client_are_pulled_in(stage, monkeypatch):
    calls = run_manifest(stage, monkeypatch)
    assert calls["include"] == [("$(BOARD_DIR)/manifest.py",)]
    assert ("umqtt.simple",) in calls["require"]
