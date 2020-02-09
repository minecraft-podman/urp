import collections
import sys
import types

from .common import MsgType, BaseUrpProtocol, UrpStreamMixin, UrpSubprocessMixin

__all__ = ('errors', 'ClientStreamProtocol', 'ClientSubprocessProtocol')


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
                await send(MsgType.Call, args, 999)  # TODO (999 == log level)
                async for msg in queue:
                    if msg[0] == MsgType.Shoosh:
                        return
                    elif msg[0] == MsgType.Return:
                        yield msg[1]
                    elif msg[0] == MsgType.Error:
                        yield get_error(msg[1], msg[2])
                    elif msg[0] == MsgType.Log:
                        # TODO
                        ...

        return call_method

    async def urp_text_recv(self, txt):
        # TODO
        sys.stderr.write(txt)

    async def urp_new_channel(self, channel_id):
        # Don't do anything, we create channels
        pass


class ClientStreamProtocol(UrpStreamMixin, ClientBaseProtocol):
    pass


class ClientSubprocessProtocol(UrpSubprocessMixin, ClientBaseProtocol):
    def urp_stderr_recv(self, data):
        # TODO
        sys.stderr.buffer.write(data)
