"""gzip-стиснення й заголовки безпеки HTTP API."""

import asyncio

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from routes.setup import compression_middleware, security_headers_middleware


def _run(scenario):
    async def big(_request):
        return web.json_response([{"name": "Товар", "id": i} for i in range(200)])

    async def small(_request):
        return web.json_response({"ok": True})

    async def go():
        app = web.Application(middlewares=[security_headers_middleware, compression_middleware])
        app.router.add_get("/big", big)
        app.router.add_get("/small", small)
        async with TestClient(TestServer(app)) as client:
            return await scenario(client)

    return asyncio.run(go())


def test_large_json_is_gzipped():
    async def scenario(client):
        resp = await client.get("/big", headers={"Accept-Encoding": "gzip"}, auto_decompress=False)
        assert resp.headers.get("Content-Encoding") == "gzip"
        raw = await resp.read()
        resp2 = await client.get("/big", headers={"Accept-Encoding": "gzip"})
        data = await resp2.json()
        assert len(data) == 200
        assert len(raw) < 2000

    _run(scenario)


def test_small_or_unaccepted_responses_are_not_compressed():
    async def scenario(client):
        resp = await client.get("/small", headers={"Accept-Encoding": "gzip"}, auto_decompress=False)
        assert "Content-Encoding" not in resp.headers
        resp = await client.get("/big", headers={"Accept-Encoding": "identity"}, auto_decompress=False)
        assert "Content-Encoding" not in resp.headers

    _run(scenario)


def test_security_headers_present():
    async def scenario(client):
        resp = await client.get("/small")
        assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert "camera=()" in resp.headers["Permissions-Policy"]

    _run(scenario)
