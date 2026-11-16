import pytest

from firmware.provisioning import ap_portal, config_store, manager


class Button:
    """A BOOTSEL button that reads pressed from tick `press_at` for `hold` ticks."""

    def __init__(self, press_at=None, hold=None):
        self.calls = 0
        self.press_at = press_at
        self.hold = hold

    def __call__(self):
        i = self.calls
        self.calls += 1
        if self.press_at is None or i < self.press_at:
            return False
        return self.hold is None or i < self.press_at + self.hold


def check(button, **kwargs):
    sleeps, leds = [], []
    result = manager.bootsel_held_for_reset(
        read_button=button, sleep=sleeps.append, set_led=leds.append, **kwargs
    )
    return result, sleeps, leds


# --- BOOTSEL window/hold ----------------------------------------------------

def test_never_pressed_waits_out_the_window_and_returns_false():
    result, sleeps, leds = check(Button())
    assert result is False
    assert len(sleeps) == 30  # 3 s window at 0.1 s per tick
    assert leds == [True, False]


def test_pressed_and_held_for_five_seconds_triggers_reset():
    result, sleeps, leds = check(Button(press_at=5))
    assert result is True
    assert sleeps.count(0.1) == 5 + 50 + 2 * 3  # window + hold + confirmation blinks
    assert leds[0] is True and leds[-1] is False


def test_a_successful_hold_blinks_the_led_three_times():
    _, _, leds = check(Button(press_at=5))
    # on for the window, three off/on blinks, then off for good
    assert leds == [True, False, True, False, True, False, True, False]


@pytest.mark.parametrize("button", [
    Button(),                          # never pressed
    Button(press_at=30),               # pressed after the window
    Button(press_at=2, hold=1 + 49),   # released too early
])
def test_no_confirmation_blinks_unless_the_hold_succeeds(button):
    _, _, leds = check(button)
    assert leds == [True, False]


def test_press_just_inside_the_window_still_counts():
    assert check(Button(press_at=29))[0] is True


def test_press_after_the_window_is_ignored():
    result, sleeps, _ = check(Button(press_at=30))
    assert result is False
    assert len(sleeps) == 30


def test_released_before_five_seconds_does_not_reset():
    # button is read once to detect the press, then 49 more reads while holding
    assert check(Button(press_at=2, hold=1 + 49))[0] is False


def test_released_exactly_after_five_seconds_resets():
    assert check(Button(press_at=2, hold=1 + 50))[0] is True


def test_led_is_switched_off_even_if_reading_the_button_fails():
    leds = []

    def broken():
        raise OSError("boom")

    with pytest.raises(OSError):
        manager.bootsel_held_for_reset(read_button=broken, sleep=lambda s: None, set_led=leds.append)
    assert leds == [True, False]


def test_unreadable_button_disables_the_feature_without_delaying_boot(monkeypatch):
    monkeypatch.setattr(manager, "_bootsel_reader", lambda: None)
    slept = []
    monkeypatch.setattr(manager.time, "sleep", slept.append)
    assert manager.bootsel_held_for_reset() is False
    assert slept == []


def test_bootsel_reader_uses_rp2_when_available(monkeypatch):
    import sys
    import types

    fake = types.ModuleType("rp2")
    fake.bootsel_button = lambda: 1
    monkeypatch.setitem(sys.modules, "rp2", fake)
    assert manager._bootsel_reader() is fake.bootsel_button


def test_bootsel_reader_is_none_off_device():
    assert manager._bootsel_reader() is None


def test_led_setter_drives_the_board_led(fake_hardware):
    manager._led_setter()(True)
    machine = fake_hardware["machine"]
    machine.Pin.assert_called_with("LED", machine.Pin.OUT)
    machine.Pin.return_value.value.assert_called_with(1)


def test_led_setter_tolerates_a_missing_led():
    manager._led_setter()(True)  # machine isn't importable on the host


# --- start() state machine -------------------------------------------------

class CallLog(list):
    """Ordered record of calls, with the fakes' switches on `.state`."""


@pytest.fixture
def calls(monkeypatch):
    log = CallLog()
    state = {"held": False, "config": None}

    def fake_load():
        log.append("load")
        return state["config"]

    monkeypatch.setattr(manager, "bootsel_held_for_reset", lambda: state["held"])
    monkeypatch.setattr(config_store, "clear", lambda: log.append("clear"))
    monkeypatch.setattr(config_store, "load", fake_load)
    monkeypatch.setattr(ap_portal, "start_ap", lambda: log.append("start_ap"))
    monkeypatch.setattr(
        ap_portal, "serve_setup_portal",
        lambda on_config_submitted: log.append(("serve", on_config_submitted)),
    )
    monkeypatch.setattr(manager, "_restart", lambda: log.append("restart"))
    log.state = state
    return log


def test_valid_config_returns_straight_to_run_mode(calls):
    calls.state["config"] = {"ok": 1}
    assert manager.start() == {"ok": 1}
    assert calls == ["load"]


def test_no_config_enters_setup_then_restarts(calls):
    assert manager.start() is None
    assert calls == ["load", "start_ap", ("serve", config_store.save), "restart"]


def test_bootsel_clears_config_before_loading_and_forces_setup(calls):
    calls.state["held"] = True
    calls.state["config"] = None  # clear() removed it
    manager.start()
    assert calls[:3] == ["clear", "load", "start_ap"]


def test_bootsel_not_held_never_clears(calls):
    calls.state["config"] = {"ok": 1}
    manager.start()
    assert "clear" not in calls


def test_restart_waits_then_resets_the_board(fake_hardware, monkeypatch):
    order = []
    monkeypatch.setattr(manager.time, "sleep", lambda s: order.append(("sleep", s)))
    fake_hardware["machine"].reset.side_effect = lambda: order.append("reset")
    manager._restart()
    assert order == [("sleep", manager.RESTART_DELAY_S), "reset"]


# --- end to end with the real store ----------------------------------------

def test_setup_then_next_boot_runs_with_the_saved_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config_store, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config_store, "TMP_PATH", str(tmp_path / "config.json.tmp"))
    monkeypatch.setattr(manager, "bootsel_held_for_reset", lambda: False)
    monkeypatch.setattr(ap_portal, "start_ap", lambda: None)
    restarts = []
    monkeypatch.setattr(manager, "_restart", lambda: restarts.append(1))

    submitted = {
        "wifi": {"ssid": "home", "password": "hunter2hunter2"},
        "backend": {"type": "local_mqtt", "host": "broker.local", "use_tls": True},
        "sensors": [{"type": "dht11", "name": "lounge", "pin": 4}],
    }

    def portal(on_config_submitted):
        on_config_submitted(submitted)  # what the portal does on a valid form

    monkeypatch.setattr(ap_portal, "serve_setup_portal", portal)

    assert manager.start() is None  # first boot: setup
    assert restarts == [1]
    assert manager.start() == submitted  # second boot: run mode, no portal
    assert restarts == [1]


def test_corrupt_config_falls_back_to_setup(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("{ not json")
    monkeypatch.setattr(config_store, "CONFIG_PATH", str(path))
    monkeypatch.setattr(config_store, "TMP_PATH", str(tmp_path / "config.json.tmp"))
    monkeypatch.setattr(manager, "bootsel_held_for_reset", lambda: False)
    served = []
    monkeypatch.setattr(ap_portal, "start_ap", lambda: None)
    monkeypatch.setattr(ap_portal, "serve_setup_portal", lambda on_config_submitted: served.append(1))
    monkeypatch.setattr(manager, "_restart", lambda: None)
    manager.start()
    assert served == [1]
