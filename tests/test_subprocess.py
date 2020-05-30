from pathlib import Path
import sys

import pytest

from urp.client import spawn_server

from .utils import aenumerate

@pytest.fixture
async def stdio_client():
    this = Path(__file__).absolute()
    path = this.parent / "_server_script.py"
    client = await spawn_server(sys.executable, path)
    yield client
    await client.close()


@pytest.mark.asyncio
async def test_subprocess(stdio_client):
    async with stdio_client:
        async for i, result in aenumerate(stdio_client['example.async']()):
            assert i == 0
            assert result == {'spam': 'eggs'}

