import pytest

from firmware.adapters.base import DownstreamAdapter


def test_constructor_stores_config():
    adapter = DownstreamAdapter({"host": "broker.local"})
    assert adapter.config == {"host": "broker.local"}


@pytest.mark.parametrize("method,args", [
    ("connect", ()),
    ("publish", ("temperature", {"temperature_c": 21.4})),
    ("poll_commands", ()),
])
def test_required_methods_raise(method, args):
    adapter = DownstreamAdapter({})
    with pytest.raises(NotImplementedError):
        getattr(adapter, method)(*args)


def test_announce_is_a_noop_by_default():
    assert DownstreamAdapter({}).announce([]) is None
