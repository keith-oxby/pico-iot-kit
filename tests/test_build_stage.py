"""build/stage.py: what the frozen image contains, and the setup page it carries."""

import os
import sys

import pytest

from build import stage
from firmware.provisioning import ap_portal

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE_FILE = os.path.join(REPO, stage.PAGE_SOURCE)


@pytest.fixture
def staged(tmp_path):
    target = tmp_path / "stage"
    return target, stage.stage(REPO, str(target))


def firmware_sources():
    found = []
    for root, dirs, files in os.walk(os.path.join(REPO, "firmware")):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        found += [os.path.relpath(os.path.join(root, n), REPO) for n in files if n.endswith(".py")]
    return found


def test_every_firmware_module_is_staged(staged):
    target, paths = staged
    for source in firmware_sources():
        expected = "main.py" if source == stage.MAIN else source
        assert expected in paths, "%s would be left out of the image" % source
        assert (target / expected).is_file()


def test_main_is_frozen_at_the_root_and_not_inside_the_package(staged):
    target, _ = staged
    assert (target / "main.py").read_text() == open(os.path.join(REPO, stage.MAIN)).read()
    assert not (target / "firmware" / "main.py").exists()


def test_only_python_files_and_the_generated_page_are_staged(staged):
    target, paths = staged
    assert all(p.endswith(".py") for p in paths)
    assert not list(target.rglob("*.html"))
    assert stage.PAGE_MODULE in paths


def test_the_generated_module_carries_the_page_byte_for_byte(staged):
    target, _ = staged
    namespace = {}
    exec((target / stage.PAGE_MODULE).read_text(encoding="utf-8"), namespace)
    with open(PAGE_FILE, encoding="utf-8", newline="") as f:
        assert namespace["PAGE"] == f.read()


@pytest.mark.parametrize("html", ["", "plain", 'quotes " \' and \\ backslash', "line\r\nbreaks\n", "naïve ° ✓"])
def test_page_module_source_round_trips_awkward_text(html):
    namespace = {}
    exec(stage.page_module_source(html), namespace)
    assert namespace["PAGE"] == html


def test_staging_refuses_a_non_empty_directory(tmp_path):
    (tmp_path / "leftover.py").write_text("x = 1")
    with pytest.raises(ValueError):
        stage.stage(REPO, str(tmp_path))


def test_the_page_is_read_from_the_file_when_there_is_no_frozen_copy(monkeypatch):
    monkeypatch.setattr(ap_portal, "SETUP_PAGE_PATH", PAGE_FILE)
    monkeypatch.delitem(sys.modules, "firmware.provisioning._setup_page", raising=False)
    with open(PAGE_FILE) as f:
        assert ap_portal.load_page() == f.read()


def test_the_frozen_copy_is_preferred_for_the_default_page(monkeypatch):
    class Frozen:
        PAGE = "<p>frozen</p> %s" % ap_portal.portal_http.ERROR_MARKER

    monkeypatch.setitem(sys.modules, "firmware.provisioning._setup_page", Frozen)
    monkeypatch.setattr(ap_portal, "SETUP_PAGE_PATH", "/does/not/exist.html")
    assert ap_portal.load_page() == Frozen.PAGE


def test_an_explicit_path_ignores_the_frozen_copy(monkeypatch, tmp_path):
    class Frozen:
        PAGE = "frozen %s" % ap_portal.portal_http.ERROR_MARKER

    monkeypatch.setitem(sys.modules, "firmware.provisioning._setup_page", Frozen)
    page = tmp_path / "p.html"
    page.write_text("file %s" % ap_portal.portal_http.ERROR_MARKER)
    assert ap_portal.load_page(str(page)).startswith("file")
