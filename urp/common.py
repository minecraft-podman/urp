import asyncio
import contextlib
import enum
import os
import sys

import msgpack


class MsgType(enum.IntEnum):
    Shoosh = 0  # (Any): ()

    Call = 1  # (C2S): name, params, log
    Return = 2  # (S2C): value
    Error = 3  # (S2C): name, additional

    Log = 4  # (S2C): group, level, msg


class LogLevels(enum.IntEnum):
    Trace = 0
    Debug = 10
    Verbose = 20
    Info = 30
    Warning = 40
    Error = 50
    Critical = 60

# TODO: Write functions to go between python and urp log levels.


class Disconnected(Exception):
    """
    Not currently connected to the server
    """


class BackpressureManager:
    """
    Handles backpressure and gating access to a callable.

    Raises a BrokenPipeError if called while shutdown.

    NOTE: If a coroutine is passed, it will have to be double-awaited.
    """

    def __init__(self, func):
        self._func = func
        self._is_blocked = asyncio.Event()
        self._call_exception = None

        # Put us in a known state
        self.continue_calls()

    def pause_calls(self):
        """
        Pause calling temporarily.

        Does nothing if closed.
        """
        if self._call_exception is None:
            self._is_blocked.clear()

    def continue_calls(self):
        """
        Continue calling.

        Does nothing if closed.
        """
        if self._call_exception is None:
            self._is_blocked.set()

    def shutdown(self, exception=ConnectionError):
        """
        Causes calls to error.
        """
        self._call_exception = exception
        self._is_blocked.clear()

    async def __call__(self, *pargs, **kwargs):
        await self._is_blocked.wait()
        if self._call_exception is None:
            return self._func(*pargs, **kwargs)
        else:
            raise Disconnected from self._call_exception


# I'm worried that cleaning up channels immediately will cause problems if
# responses are in-flight.
class IdManager_Reusing(dict):
    @contextlib.contextmanager
    def generate(self):
        if self:
            reqid = max(self.keys()) + 1
        else:
            reqid = 0

        self[reqid] = asyncio.Queue()
        try:
            yield reqid, self[reqid]
        finally:
            del self[reqid]


class IdManager_Sequence(dict):
    _next_id = 0
    @contextlib.contextmanager
    def generate(self, reqid=None):
        if reqid is None:
            reqid = self._next_id
            while reqid in self:
                reqid = self._next_id
                self._next_id += 1

        self[reqid] = asyncio.Queue()
        try:
            yield reqid, self[reqid]
        finally:
            del self[reqid]


class BaseUrpProtocol(asyncio.BaseProtocol):
    def __init__(self):
        self._packer = msgpack.Packer(autoreset=True)
        self._unpacker = msgpack.Unpacker(raw=False)
        self._channels = IdManager_Sequence()
        self._write_proxy = BackpressureManager(self.urp_write_bytes)
        self._finished = asyncio.Event()
        self._tasks = []

    # asyncio callbacks
    def connection_made(self, transport):
        self._transport = transport
        self._write_proxy.continue_calls()

    def connection_lost(self, exc):
        self._write_proxy.shutdown(exc)
        for q in self._channels.values():
            q.put_nowait(exc)
        self._finished.set()

    def pause_writing(self):
        self._write_proxy.pause_calls()

    def resume_writing(self):
        self._write_proxy.continue_calls()

    # Our additions
    def urp_recv_bytes(self, data):
        """
        Call to feed data
        """
        self._unpacker.feed(data)
        for msg in self._unpacker:
            if isinstance(msg, str):
                asyncio.create_task(self.urp_text_recv(msg))
            else:
                self._urp_packet_recv(msg)

    def _urp_packet_recv(self, msg):
        """
        Called when a packet is received.
        """
        cid, *args = msg
        if cid not in self._channels:
            asyncio.create_task(self.urp_new_channel(cid, args))
        else:
            self._channels[cid].put_nowait(args)

    async def _urp_send_packet(self, packet):
        """
        Send a message. May block due to backpressure.

        Raises BrokenPipeError if unable to send due to closed connection.
        """
        data = self._packer.pack(packet)
        await self._write_proxy(data)

    @contextlib.contextmanager
    def urp_open_channel(self, channel_id=None):
        """
        Opens a channel defined by request ID.
        Returns a callable (accepting a type and payload to send) and a Queue (where responses go)
        """
        with self._channels.generate(channel_id) as (chanid, q):
            async def send(type, *args):
                await self._urp_send_packet([chanid, type, *args])

            yield send, q

    async def urp_send_text(self, txt):
        """
        Sends text over the unstructured/unassociated log stream.
        """
        await self._urp_send_packet(txt)

    async def urp_text_recv(self, txt):
        """
        Called when unassociated, unstructured log data is received.

        Override me.
        """
        raise NotImplementedError

    async def urp_new_channel(self, channel_id):
        """
        Called when we receive a packet for a channel we don't recognize

        Override me.
        """
        raise NotImplementedError

    def urp_write_bytes(self, data):
        """
        Called to send data. This is after serialization, back-pressure, and
        any other processes.

        Provided by mixin.
        """
        raise NotImplementedError

    async def finished(self):
        """
        Block until the transport has closed and all tasks have spun down.
        """
        # TODO: Track tasks
        await self._finished.wait()

    async def close(self):
        self._transport.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.close()
        await self.finished()


class UrpStreamMixin(asyncio.Protocol):
    def data_received(self, data):
        self.urp_recv_bytes(data)

    def urp_write_bytes(self, data):
        """
        Called to send data. This is after serialization, back-pressure, and
        any other processes.

        Provided by mixin.
        """
        self._transport.write(data)


class UrpSubprocessMixin(asyncio.SubprocessProtocol):
    def process_exited(self):
        pass

    def pipe_connection_lost(self, fd, exc):
        if fd == 0:  # SSH's stdin
            self._write_proxy.shutdown(exc)

    def pipe_data_received(self, fd, data):
        if fd == 1:  # stdout
            self.urp_recv_bytes(data)
        elif fd == 2:  # stderr
            self.urp_stderr_recv(data)

    def urp_stderr_recv(self, data):
        """
        Called when we receive data from stderr.

        Override me.
        """
        raise NotImplementedError

    def urp_write_bytes(self, data):
        """
        Called to send data. This is after serialization, back-pressure, and
        any other processes.

        Provided by mixin.
        """
        self._transport.get_pipe_transport(0).write(data)


class StdioTransport(asyncio.Transport):
    """
    Acts as a stream transport for Protocols for inherited fds
    """

    protocol = None
    reader = None
    writer = None

    # Protocol methods
    def connection_made(self, transport):
        if isinstance(transport, asyncio.ReadTransport):
            self.reader = transport
        if isinstance(transport, asyncio.WriteTransport):
            self.writer = transport

        if self.reader is not None and self.writer is not None:
            self.protocol.connection_made(self)

    def connection_lost(self, exc):
        return self.protocol.connection_lost(exc)

    def pause_writing(self):
        return self.protocol.pause_writing()

    def resume_writing(self):
        return self.protocol.resume_writing()

    def data_received(self, data):
        return self.protocol.data_received(data)

    def eof_received(self):
        return self.protocol.eof_received()

    # Transport methods
    def is_closing(self):
        """Return True if the transport is closing or closed."""
        return self.reader.is_closing() or self.writer.is_closing()

    def close(self):
        self.reader.close()
        self.writer.close()

    def set_protocol(self, protocol):
        """Set a new protocol."""
        self.protocol = protocol

    def get_protocol(self):
        """Return the current protocol."""
        return self.protocol

    # ReadTransport methods
    def is_reading(self):
        return self.reader.is_reading()

    def pause_reading(self):
        return self.reader.pause_reading()

    def resume_reading(self):
        return self.reader.resume_reading()

    # WriteTransport methods
    def set_write_buffer_limits(self, high=None, low=None):
        return self.writer.set_write_buffer_limits(high, low)

    def get_write_buffer_size(self):
        return self.writer.get_write_buffer_size()

    def write(self, data):
        return self.writer.write(data)

    def write_eof(self):
        raise self.writer.write_eof()

    def can_write_eof(self):
        return self.writer.can_write_eof()

    def abort(self):
        return self.writer.abort()

def make_stdio_binary():
    """
    Does posixy things to prepare stdin/stdout for binary transport.
    """
    # Moves FD 0/1 to new FDs and copy better things over them.
    # Shuffle FDs under the nose of Python. The resulting state will _look_ the same.

    # STDIN becomes /dev/null
    with open(os.devnull, 'wt') as newin:
        fdin = os.dup(0)
        os.dup2(newin.fileno(), 0)

    # STDOUT becomes STDERR
    fdout = os.dup(1)
    os.dup2(2, 1)

    return fdin, fdout


async def connect_fd(protocol, readfd, writefd):
    """
    Connects the given protocol (by factory) to the given reader and writer (by
    file descriptor).
    """
    if isinstance(readfd, int):
        readfd = os.fdopen(readfd, 'rb')
    if isinstance(writefd, int):
        writefd = os.fdopen(writefd, 'wb')

    loop = asyncio.get_running_loop()

    proto = protocol()
    trans = StdioTransport()
    trans.set_protocol(proto)

    await asyncio.gather(
        loop.connect_read_pipe(lambda: trans, readfd),
        loop.connect_write_pipe(lambda: trans, writefd),
    )
    return trans, proto

async def connect_stdio(protocol):
    fdin, fdout = make_stdio_binary()
    return await connect_fd(protocol, fdin, fdout)
