import asyncio
import socket

import pytest

from urp.client import connect_inherited_socket
from urp.framework import Service, method


async def aenumerate(iterable):
    count = 0
    async for val in iterable:
        yield count, val


@pytest.fixture
def echo_service():
    serv = Service("urp-test")

    @serv.interface("example")
    class Example:
        @method("Echo")
        def ping(self, **args):
            return args

    return serv


@pytest.fixture
async def linked_pair(echo_service):
    csock, ssock = socket.socketpair()
    server_task = asyncio.create_task(echo_service.serve_inherited_socket(ssock))
    client = await connect_inherited_socket(csock)
    return client, server_task


@pytest.mark.asyncio
async def test_basic_roundtrip(linked_pair):
    client, stask = linked_pair
    try:
        async with client:
            async for i, result in aenumerate(client['example.Echo'](spam='eggs')):
                assert i == 0
                assert result == {'spam': 'eggs'}
    finally:
        stask.cancel()


@pytest.mark.asyncio
async def test_shoosh(linked_pair):
    client, stask = linked_pair
    try:
        async with client:
            async def _():
                async for result in client['example.Echo'](spam='eggs'):
                    pass
            t = asyncio.create_task(_())
            t.cancel()
    finally:
        stask.cancel()
