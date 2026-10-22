"""Локальний режим розробки: адмін-доступ лише за фактичною адресою з'єднання, не за заголовками."""

from unittest.mock import Mock

import pytest
from aiohttp.test_utils import make_mocked_request

import auth
import config


def _request(remote: str, headers: dict | None = None):
    transport = Mock()
    transport.get_extra_info.side_effect = lambda name, default=None: (remote, 12345) if name == "peername" else default
    return make_mocked_request("GET", "/api/orders", headers=headers or {}, transport=transport)


@pytest.fixture
def dev_mode(monkeypatch):
    monkeypatch.setattr(config, "LOCAL_DEV_MODE", True)
    monkeypatch.setattr(config, "ALLOW_LOCAL_NETWORK", True)
    monkeypatch.setattr(config, "ADMIN_BYPASS_TOKEN", "secret-token")


def test_spoofed_origin_or_host_is_not_admin(dev_mode):
    for headers in ({"Origin": "http://localhost"}, {"Host": "localhost"}, {"Origin": "http://127.0.0.1:8080"}):
        req = _request("93.184.216.34", headers)
        assert not auth.is_admin_authorized(req, None), headers


def test_tunnel_through_private_proxy_is_not_local(dev_mode):
    # ngrok у Docker: з'єднання з 172.x, але справжній клієнт — в інтернеті
    req = _request("172.18.0.3", {"X-Forwarded-For": "93.184.216.34", "Origin": "http://localhost"})
    assert not auth.is_local_network_request(req)
    assert not auth.is_local_dev_origin(req)
    assert not auth.is_admin_authorized(req, None)


def test_direct_local_requests_still_work(dev_mode):
    assert auth.is_local_dev_origin(_request("127.0.0.1"))
    assert auth.is_local_network_request(_request("192.168.1.20"))
    assert not auth.is_local_network_request(_request("8.8.8.8"))


def test_bypass_token_still_works_through_proxy(dev_mode):
    req = _request("172.18.0.3", {"X-Forwarded-For": "93.184.216.34", "X-Admin-Bypass-Token": "secret-token"})
    assert auth.is_admin_authorized(req, None)


def test_spoofed_user_id_rejected_from_internet(dev_mode, monkeypatch):
    monkeypatch.setattr(config, "VALIDATE_INIT_DATA", False)
    req = _request("172.18.0.3", {"X-Forwarded-For": "93.184.216.34"})
    user, err = auth.resolve_request_user(req, {"user_id": 42})
    assert user is None and err.status == 403


def test_production_mode_ignores_local_network(monkeypatch):
    monkeypatch.setattr(config, "LOCAL_DEV_MODE", False)
    monkeypatch.setattr(config, "ALLOW_LOCAL_NETWORK", False)
    assert not auth.is_admin_authorized(_request("127.0.0.1"), None)
