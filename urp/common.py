import asyncio
import contextlib
import enum

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
            while reqid not in self:
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

    # asyncio callbacks
    def connection_made(self, transport):
        self._transport = transport
        self._write_proxy.continue_calls()

    def connection_lost(self, exc):
        self._write_proxy.shutdown(exc)
        for q in self._response_queues.values():
            q.put_nowait(exc)

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
            asyncio.create_task(self.urp_new_channel(cid))
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
