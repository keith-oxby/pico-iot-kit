import pytest

from firmware.runtime import wifi


@pytest.fixture
def wlan(fake_hardware):
    station = fake_hardware["network"].WLAN.return_value
    station.isconnected.return_value = False
    station.status.return_value = 1  # connecting
    return station


def test_returns_immediately_when_already_connected(wlan):
    wlan.isconnected.return_value = True
    wifi.connect("home", "hunter2hunter2")
    wlan.connect.assert_not_called()


def test_connects_and_waits_for_ip(wlan):
    wlan.isconnected.side_effect = [False, False, False, True]
    sleeps = []
    wifi.connect("home", "hunter2hunter2", sleep=sleeps.append)
    wlan.active.assert_called_with(True)
    wlan.connect.assert_called_once_with("home", "hunter2hunter2")
    assert sleeps == [1, 1]


def test_open_network_omits_password(wlan):
    wlan.isconnected.side_effect = [False, True]
    wifi.connect("cafe", "", sleep=lambda s: None)
    wlan.connect.assert_called_once_with("cafe")


def test_failure_status_raises_immediately(wlan):
    wlan.status.return_value = -3  # wrong password
    with pytest.raises(wifi.WifiError):
        wifi.connect("home", "wrongwrong", sleep=lambda s: None)


def test_timeout_raises(wlan):
    sleeps = []
    with pytest.raises(wifi.WifiError, match="timed out"):
        wifi.connect("home", "hunter2hunter2", timeout_s=3, sleep=sleeps.append)
    assert sleeps == [1, 1, 1]
