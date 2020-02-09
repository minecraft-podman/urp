"""
Framework for defining servers.
"""
import asyncio
import collections.abc
import os
import socket
import sys

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
                    self._method_index[fullname] = meth

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

        return self._method_index[key]

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

    async def serve_inherited_socket(self, fd, **opts):
        """
        Serve a client connected by inherited socket.

        opts are passed to connect_accepted_socket()
        """
        sock = socket.socket(fileno=fd)
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
        fdin, fdout = _make_stdio_binary()
        # Ok, serve the stuff
        return self.serve_inherited_fd(fdin, fdout)


def _make_stdio_binary():
    """
    Does posixy and pythony things to prepare stdin/stdout for binary transport.
    """
    # Dissassemble all the connections, so that remaining references don't cause problems
    if hasattr(sys.stdin, 'detach'):
        # Detach text io, returning buffer; detach buffer, returning raw
        sys.stdin.detach().detach()

    if hasattr(sys.stdout, 'detach'):
        sys.stdout.detach().detach()

    # Mock in substitutes for users
    sys.stdin = open(os.devnull, 'wt')
    sys.stdout = sys.stderr

    # Do the same thing on a fd level
    # Get the old stdin/out out of the way
    fdin = os.dup(0)
    fdout = os.dup(1)

    # Get the new stdio in place
    os.dup2(sys.stdin.fileno(), 0)
    os.dup2(sys.stdout.fileno(), 1)

    return fdin, fdout
