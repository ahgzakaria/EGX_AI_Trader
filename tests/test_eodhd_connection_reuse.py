"""The EODHD client must keep its connection open, and survive losing it.

Every request used to be `urlopen`, which opens a socket and completes a TLS
handshake each time. From here to EODHD that handshake is two or three round
trips and it dominated the call: five symbols over fresh connections cost
1.36-2.54s each, the same five over one reused connection cost 0.27-0.46s. A
241-symbol daily scan spent roughly 6.6 minutes waiting on handshakes it did
not need, which is most of why the scan felt slow.

These tests pin the reuse and, more importantly, the recovery: a kept-open
socket that the server has since closed fails on the next write, and a client
that cannot reconnect from that is worse than one that never reused anything.
"""

from __future__ import annotations

import http.client

import pytest

from providers.eodhd_client import EODHDClient


class _FakeResponse:
    def __init__(self, status=200, body=b'{"ok": true}'):
        self.status = status
        self.reason = "OK" if status == 200 else "Error"
        self.headers = {}
        self._body = body

    def read(self):
        return self._body


class _RecordingConnection:
    """Counts how many connections were opened, and how many requests each saw."""

    opened = 0

    def __init__(self, host, timeout=None):
        type(self).opened += 1
        self.host = host
        self.timeout = timeout
        self.requests = 0
        self.closed = False
        self.fail_next = False

    def request(self, method, target, headers=None):
        if self.fail_next:
            self.fail_next = False
            raise http.client.RemoteDisconnected("server closed the socket")
        self.requests += 1

    def getresponse(self):
        return _FakeResponse()

    def close(self):
        self.closed = True


@pytest.fixture
def client(monkeypatch):
    _RecordingConnection.opened = 0
    monkeypatch.setattr(http.client, "HTTPSConnection", _RecordingConnection)
    return EODHDClient()


def test_repeated_requests_open_only_one_connection(client):
    for _ in range(5):
        client._fetch("https://eodhd.com/api/eod/X.EGX?fmt=json", 10)

    assert _RecordingConnection.opened == 1, (
        "each request opened its own connection; the handshake is being paid "
        "once per call again"
    )
    assert client._connection.requests == 5


def test_a_dropped_connection_is_replaced_and_the_request_still_succeeds(client):
    client._fetch("https://eodhd.com/api/eod/X.EGX?fmt=json", 10)
    first = client._connection
    # A server that closed an idle socket fails on the next write, not before.
    first.fail_next = True

    body = client._fetch("https://eodhd.com/api/eod/Y.EGX?fmt=json", 10)

    assert body == '{"ok": true}'
    assert _RecordingConnection.opened == 2
    assert first.closed, "the dead connection must be closed, not leaked"


def test_a_persistently_dead_connection_gives_up_rather_than_looping(client, monkeypatch):
    class _AlwaysFails(_RecordingConnection):
        def request(self, method, target, headers=None):
            raise http.client.RemoteDisconnected("gone")

    monkeypatch.setattr(http.client, "HTTPSConnection", _AlwaysFails)

    with pytest.raises(Exception):
        client._fetch("https://eodhd.com/api/eod/X.EGX?fmt=json", 10)


def test_an_http_error_is_raised_not_retried_as_a_transport_failure(client):
    """A 404 must reach the caller's own retry logic as an HTTPError, which is
    what maps it to the same exception class it mapped to before this change.
    Swallowing it as a transport error would silently change the error type
    every caller sees."""

    from urllib import error as urlerror

    class _NotFound(_RecordingConnection):
        def getresponse(self):
            return _FakeResponse(status=404, body=b"missing")

    _RecordingConnection.opened = 0
    import providers.eodhd_client as module
    original = http.client.HTTPSConnection
    http.client.HTTPSConnection = _NotFound
    try:
        with pytest.raises(urlerror.HTTPError) as caught:
            client._fetch("https://eodhd.com/api/eod/NOPE.EGX?fmt=json", 10)
        assert caught.value.code == 404
    finally:
        http.client.HTTPSConnection = original


def test_close_is_idempotent_and_leaves_the_client_usable(client):
    client._fetch("https://eodhd.com/api/eod/X.EGX?fmt=json", 10)
    client.close()
    client.close()
    assert client._connection is None

    client._fetch("https://eodhd.com/api/eod/X.EGX?fmt=json", 10)
    assert _RecordingConnection.opened == 2


def test_the_context_manager_releases_the_connection(monkeypatch):
    _RecordingConnection.opened = 0
    monkeypatch.setattr(http.client, "HTTPSConnection", _RecordingConnection)

    with EODHDClient() as client:
        client._fetch("https://eodhd.com/api/eod/X.EGX?fmt=json", 10)
        held = client._connection

    assert held.closed
    assert client._connection is None
