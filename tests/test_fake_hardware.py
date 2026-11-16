import sys


def test_fake_hardware_provides_modules(fake_hardware):
    import machine

    machine.unique_id.return_value = b"\xe6\x61\x64\x12"
    assert machine.unique_id() == b"\xe6\x61\x64\x12"
    assert sys.modules["machine"] is fake_hardware["machine"]


def test_fake_hardware_is_removed_after_test():
    assert "machine" not in sys.modules
    assert "network" not in sys.modules
