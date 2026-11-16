import os
import subprocess
import sys

import pytest

MODULES = [
    "firmware.app",
    "firmware.adapters",
    "firmware.adapters.tls",
    "firmware.adapters.local_mqtt",
    "firmware.adapters.home_assistant",
    "firmware.sensors",
    "firmware.provisioning",
    "firmware.provisioning.ap_portal",
    "firmware.provisioning.config_store",
    "firmware.runtime",
    "firmware.runtime.clock",
    "firmware.runtime.ca_store",
    "firmware.runtime.config_schema",
    "firmware.runtime.device",
    "firmware.runtime.wifi",
]


def run_fresh(code):
    """Run `code` in a new isolated interpreter with the repo root importable."""
    prelude = "import sys; sys.path.insert(0, %r); " % os.getcwd()
    return subprocess.run(
        [sys.executable, "-I", "-c", prelude + code],
        capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("module", MODULES)
def test_each_module_imports_first_in_a_fresh_interpreter(module):
    """Import order must not matter: no cycles between the layers."""
    result = run_fresh("import %s" % module)
    assert result.returncode == 0, result.stderr


def test_the_harness_itself_can_fail():
    assert run_fresh("import firmware.does_not_exist").returncode != 0


def test_importing_the_config_schema_does_not_load_the_runtime_or_adapters():
    result = run_fresh(
        "import firmware.runtime.config_schema, sys;"
        "bad = [m for m in sys.modules if m.startswith(('firmware.adapters',"
        " 'firmware.sensors', 'firmware.runtime.device'))];"
        "assert not bad, bad"
    )
    assert result.returncode == 0, result.stderr
