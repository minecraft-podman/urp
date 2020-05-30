import asyncio
import socket

import pytest

from urp.client import client_from_inherited_socket, errors
from urp.framework import Service, method

from .utils import aenumerate

@pytest.fixture
def echo_service():
    serv = Service("urp-test")

    @serv.interface("example")
    class Example:
        @method("Echo")
        def ping(self, **args):
            return args

        @method
        def sync(self):
            return {"spam": "eggs"}

        @method("async")
        async def notsync(self):
            await asyncio.sleep(0.1)
            return {"spam": "eggs"}

        @method
        def gen(self):
            yield {"spam": "eggs"}
            yield {"foo": "bar"}

        @method
        async def async_gen(self):
            await asyncio.sleep(0.1)
            yield {"spam": "eggs"}
            await asyncio.sleep(0.1)
            yield {"foo": "bar"}

        @method
        def error(self, msg):
            raise Exception(msg)
    return serv


@pytest.fixture
async def linked_pair(echo_service):
    csock, ssock = socket.socketpair()
    server_task = asyncio.create_task(echo_service.serve_inherited_socket(ssock))
    client = await client_from_inherited_socket(csock)
    yield client, server_task
    server_task.cancel()


@pytest.mark.asyncio
async def test_basic_roundtrip(linked_pair):
    client, stask = linked_pair
    async with client:
        async for i, result in aenumerate(client['example.Echo'](spam='eggs')):
            assert i == 0
            assert result == {'spam': 'eggs'}


@pytest.mark.asyncio
async def test_shoosh(linked_pair):
    client, stask = linked_pair
    async with client:
        async def _():
            async for result in client['example.Echo'](spam='eggs'):
                pass
        t = asyncio.create_task(_())
        t.cancel()


@pytest.mark.asyncio
async def test_sync(linked_pair):
    client, stask = linked_pair
    async with client:
        async for i, result in aenumerate(client['example.sync']()):
            assert i == 0
            assert result == {'spam': 'eggs'}


@pytest.mark.asyncio
async def test_async(linked_pair):
    client, stask = linked_pair
    async with client:
        async for i, result in aenumerate(client['example.async']()):
            assert i == 0
            assert result == {'spam': 'eggs'}


@pytest.mark.asyncio
async def test_sync_gen(linked_pair):
    client, stask = linked_pair
    async with client:
        async for i, result in aenumerate(client['example.gen']()):
            assert i in (0, 1)
            if i == 0:
                assert result == {'spam': 'eggs'}
            elif i == 1:
                assert result == {'foo': 'bar'}
            else:
                assert False


@pytest.mark.asyncio
async def test_async_gen(linked_pair):
    client, stask = linked_pair
    async with client:
        async for i, result in aenumerate(client['example.async_gen']()):
            assert i in (0, 1)
            if i == 0:
                assert result == {'spam': 'eggs'}
            elif i == 1:
                assert result == {'foo': 'bar'}
            else:
                assert False


@pytest.mark.asyncio
async def test_errors(linked_pair):
    client, stask = linked_pair
    async with client:
        async for i, result in aenumerate(client['example.error'](msg="spam&eggs")):
            assert i == 0
            assert isinstance(result, errors['builtins.Exception'])
            assert str(result) == "spam&eggs"
