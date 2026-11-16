import sys

import pytest

from firmware import app


class Recorder:
    def __init__(self, config=None, start_error=None, run_error=None):
        self.config = config
        self.start_error = start_error
        self.run_error = run_error
        self.calls = []

    def start(self):
        self.calls.append("start")
        if self.start_error:
            raise self.start_error
        return self.config

    def run(self, config):
        self.calls.append(("run", config))
        if self.run_error:
            raise self.run_error

    def main(self):
        sleeps, resets = [], []
        app.main(
            start_provisioning=self.start,
            run=self.run,
            sleep=sleeps.append,
            reset=lambda: resets.append(True),
            start_watchdog=lambda: self.calls.append("watchdog"),
        )
        return sleeps, resets


CONFIG = {"ok": 1}


def test_provisioning_runs_once_then_the_runtime_gets_the_config():
    rec = Recorder(config=CONFIG)
    sleeps, resets = rec.main()
    assert rec.calls == ["watchdog", "start", ("run", CONFIG)]  # the watchdog covers boot too
    assert sleeps == [] and resets == []


def test_a_provisioning_crash_waits_then_resets_without_running():
    rec = Recorder(start_error=OSError("AP failed"))
    sleeps, resets = rec.main()
    assert rec.calls == ["watchdog", "start"]
    assert sleeps == [app.CRASH_RESTART_DELAY_S] and resets == [True]


def test_a_runtime_crash_waits_then_resets():
    rec = Recorder(config=CONFIG, run_error=ValueError("boom"))
    sleeps, resets = rec.main()
    assert sleeps == [app.CRASH_RESTART_DELAY_S] and resets == [True]


def test_no_config_is_treated_as_a_crash():
    rec = Recorder(config=None)
    sleeps, resets = rec.main()
    assert "run" not in [c[0] for c in rec.calls if isinstance(c, tuple)]
    assert sleeps == [app.CRASH_RESTART_DELAY_S] and resets == [True]


def test_the_crash_is_logged_with_its_traceback(capsys):
    Recorder(config=CONFIG, run_error=ValueError("distinctive failure")).main()
    out = capsys.readouterr()
    assert "crashed, restarting" in out.out
    assert "distinctive failure" in out.err + out.out


@pytest.mark.parametrize("where", ["start", "run"])
@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_ctrl_c_and_exit_are_never_swallowed(where, interrupt):
    kwargs = {"config": CONFIG}
    kwargs["start_error" if where == "start" else "run_error"] = interrupt()
    rec = Recorder(**kwargs)
    sleeps, resets = [], []
    with pytest.raises(interrupt):
        app.main(start_provisioning=rec.start, run=rec.run,
                 sleep=sleeps.append, reset=lambda: resets.append(True))
    assert sleeps == [] and resets == []


def test_the_restart_delay_is_a_floor_against_reset_loops():
    assert app.CRASH_RESTART_DELAY_S >= 10


def test_default_reset_resets_the_board(fake_hardware):
    app._reset()
    fake_hardware["machine"].reset.assert_called_once_with()


def test_defaults_use_the_real_provisioning_and_runtime(monkeypatch, fake_hardware):
    import firmware.provisioning
    import firmware.runtime.device

    seen = []
    monkeypatch.setattr(firmware.provisioning, "start", lambda: CONFIG)
    monkeypatch.setattr(firmware.runtime.device, "run", lambda config: seen.append(config))
    app.main(sleep=lambda s: None, reset=lambda: None)
    assert seen == [CONFIG]


def test_print_exception_works_with_and_without_the_micropython_helper(monkeypatch, capsys):
    app._print_exception(ValueError("on cpython"))
    assert "on cpython" in capsys.readouterr().err
    printed = []
    monkeypatch.setattr(sys, "print_exception", printed.append, raising=False)
    app._print_exception(ValueError("on micropython"))
    assert len(printed) == 1


# --- wiring ------------------------------------------------------------------

def test_there_is_no_boot_py_to_run_twice():
    import os

    assert not os.path.exists("firmware/boot.py")


def test_main_py_is_a_thin_entry_point_that_does_not_import_boot():
    source = open("firmware/main.py").read()
    assert "import boot" not in source
    assert "from firmware.app import main" in source
    assert source.rstrip().endswith("main()")
