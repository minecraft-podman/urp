import asyncio
import collections
import socket
import sys
import types

from .common import (
    MsgType, BaseUrpProtocol, UrpStreamMixin, UrpSubprocessMixin,
    make_stdio_binary, Disconnected
)

__all__ = (
    'errors', 'connect_tcp', 'connect_unix', 'connect_inherited_fd',
    'connect_stdio', 'connect_inherited_socket',
)


class ApplicationError(Exception):
    """
    Base exception for exceptions sent over the wire.
    """


class _ErrorCache(collections.defaultdict):
    def __missing__(self, key):
        self[key] = types.new_class(key, (ApplicationError,))
        return self[key]


errors = _ErrorCache()


def get_error(name, additional):
    """
    Gets an error instance for the given name and additional
    """
    if additional is None:
        return errors[name]()
    elif isinstance(additional, dict):
        err = errors[name](additional.pop('msg'))
        vars(err).update(additional)
        return err
    elif isinstance(additional, list):
        return errors[name](*additional)
    else:
        return errors[name](additional)


class ClientBaseProtocol(BaseUrpProtocol):
    def __getitem__(self, key):
        """
        Gets a method.

        Methods take keyword arguments and produce a sequence of returns and errors
        """
        async def call_method(**args):
            # TODO: Logging
            with self.urp_open_channel() as (send, queue):
                await send(MsgType.Call, key, args, 999)  # TODO (999 == log level)
                try:
                    while True:
                        msg = await queue.get()
                        if isinstance(msg, Exception):
                            # TODO: Raise or return?
                            raise msg
                        elif msg is None:
                            raise Disconnected
                        elif msg[0] == MsgType.Shoosh:
                            return
                        elif msg[0] == MsgType.Return:
                            yield msg[1]
                        elif msg[0] == MsgType.Error:
                            yield get_error(msg[1], msg[2])
                        elif msg[0] == MsgType.Log:
                            # TODO
                            ...
                except asyncio.CancelledError:
                    send(MsgType.Shoosh)

        return call_method

    async def urp_text_recv(self, txt):
        # TODO
        sys.stderr.write(txt)

    async def urp_new_channel(self, channel_id, args):
        # Don't do anything, we create channels
        pass


class ClientStreamProtocol(UrpStreamMixin, ClientBaseProtocol):
    pass


class ClientSubprocessProtocol(UrpSubprocessMixin, ClientBaseProtocol):
    def urp_stderr_recv(self, data):
        # TODO
        sys.stderr.buffer.write(data)


async def connect_tcp(host, port, **opts):
    """
    Connects to the given host/port.
    """
    loop = asyncio.get_running_loop()

    transpo, proto = await loop.create_connection(
        lambda: ClientStreamProtocol(),
        host, port, **opts)

    return proto


async def connect_unix(path, **opts):
    """
    Connects to the given Unix Domain Socket.
    """
    loop = asyncio.get_running_loop()

    transpo, proto = await loop.create_unix_connection(
        lambda: ClientStreamProtocol(),
        path, **opts)

    return proto


async def connect_inherited_fd(reader_fd, writer_fd):
    """
    Connect via reader and writer file descriptors.
    """
    raise NotImplementedError


async def connect_stdio():
    """
    Connect via our stdin and stdout.
    """
    fdin, fdout = make_stdio_binary()
    return await connect_inherited_fd(fdin, fdout)


async def connect_inherited_socket(sock_fd, **opts):
    """
    Connect via a connected socket file descriptor.
    """
    if isinstance(sock_fd, int):
        sock = socket.socket(fileno=sock_fd)
    else:
        sock = sock_fd
    loop = asyncio.get_running_loop()

    transpo, proto = await loop.connect_accepted_socket(
        lambda: ClientStreamProtocol(),
        sock=sock, **opts)

    return proto


# TODO: Run command
