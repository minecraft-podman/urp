"""
Framework for defining servers.
"""
import asyncio
import collections.abc
import socket

from .common import make_stdio_binary
from .server import ServerStreamProtocol, ServerSubprocessProtocol


def method(name_or_func=None):
    """
    @method
    @method("Name")

    Define an URP method. Must be used on an interface class.
    """
    name = None

    def _(func):
        nonlocal name
        if name is None:
            name = func.__name__
        func.__urp_name__ = name
        return func

    if isinstance(name_or_func, str) or name_or_func is None:
        # @method("spam") or @method()
        name = name_or_func
        return _
    else:
        # @method
        return _(name_or_func)


class Service(collections.abc.Mapping):
    """
    Top-level class.

    Use @Service.interface() to add interfaces.
    """
    def __init__(self, name):
        self.name = name
        self._interfaces = {}
        self._method_index = None

    def _update_index(self):
        self._method_index = {}
        for iname, icls in self._interfaces.items():
            for mname in dir(icls):
                meth = getattr(icls, mname)
                if hasattr(meth, '__urp_name__'):
                    fullname = f"{iname}.{meth.__urp_name__}"
                    self._method_index[fullname] = icls, meth

    def interface(self, name):
        """
        @serv.interface("example.spam.egg")

        Adds an interface to the service
        """
        def _(icls):
            self._interfaces[name] = icls
            self._method_index = None
            return icls
        return _

    def __getitem__(self, key):
        if self._method_index is None:
            self._update_index()

        cls, meth = self._method_index[key]
        bound_meth = meth.__get__(cls())  # Very Py3 way
        return bound_meth

    def __iter__(self):
        if self._method_index is None:
            self._update_index()

        yield from self._method_index

    def __len__(self):
        if self._method_index is None:
            self._update_index()

        return len(self._method_index)

    async def listen_tcp(self, bind_host, bind_port, **opts):
        """
        Listen on TCP.

        Additional options are passed to create_server()
        """
        loop = asyncio.get_running_loop()

        server = await loop.create_server(
            lambda: ServerStreamProtocol(self),
            bind_host, bind_port, **opts)

        async with server:
            await server.serve_forever()

    async def listen_unix(self, socketpath, **opts):
        """
        Listen on a Unix Domain Socket.

        Additional options are passed to create_server()
        """
        loop = asyncio.get_running_loop()

        server = await loop.create_unix_server(
            lambda: ServerStreamProtocol(self),
            socketpath, **opts)

        async with server:
            await server.serve_forever()

    async def listen_inherited_socket(self, fd, **opts):
        """
        Listen on an listen socket given by a parent process (by file
        descriptor).

        opts are passed to either create_server() or create_unix_server(),
        depending on the socket family.
        """
        sock = socket.socket(fileno=fd)
        loop = asyncio.get_running_loop()

        if sock.family == socket.AF_UNIX:
            server = await loop.create_unix_server(
                lambda: ServerStreamProtocol(self),
                sock=sock, **opts)
        else:
            server = await loop.create_server(
                lambda: ServerStreamProtocol(self),
                sock=sock, **opts)

        async with server:
            await server.serve_forever()

    async def serve_inherited_socket(self, sock_fd, **opts):
        """
        Serve a client connected by inherited socket.

        opts are passed to connect_accepted_socket()
        """
        if isinstance(sock_fd, int):
            sock = socket.socket(fileno=sock_fd)
        else:
            sock = sock_fd
        loop = asyncio.get_running_loop()

        transpo, proto = await loop.connect_accepted_socket(
            lambda: ServerStreamProtocol(self),
            sock=sock, **opts)

        await proto.finished()

    async def serve_inherited_fd(self, fd_reader, fd_writer):
        """
        Serve a client connected by inherited file descriptor.

        Note that both file descriptors may be the same
        """
        raise NotImplementedError

    async def serve_stdio(self):
        """
        Serve a client connected by stdin/stdout
        """
        fdin, fdout = make_stdio_binary()
        # Ok, serve the stuff
        return await self.serve_inherited_fd(fdin, fdout)
