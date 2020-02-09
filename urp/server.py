import asyncio
import inspect

from .common import MsgType, BaseUrpProtocol

__all__ = ()


def _fqn(cls):
    fullname = ""
    if cls.__module__:
        fullname = cls.__module__ + "."
    if hasattr(cls, '__qualname__'):
        fullname += cls.__qualname__
    else:
        fullname += cls.__name__
    return fullname


class ServerBaseProtocol(BaseUrpProtocol):
    async def urp_new_channel(self, channel_id):
        with self.urp_open_channel(channel_id) as (send, queue):
            msg = await queue.get()
            assert msg[0] == MsgType.Call

            # TODO: Logging
            # TODO: maybe redirect stdout/stderr?
            task = asyncio.create_task(self._method_task(send, msg[1], msg[2]))
            while True:
                msg = asyncio.gather(task, queue.get)
                if msg is None:  # Returned from task
                    return
                # Got from the queue, so list
                elif msg[0] == MsgType.Shoosh:
                    task.cancel()
                    return
                # Anything else is a protocol error

    async def _method_task(self, send, name, kwargs):
        meth = ...(name)  # TODO: function resolution
        try:
            methval = meth(**kwargs)
            if inspect.isasyncgenfunction(meth):
                async for val in methval:
                    await send(MsgType.Return, val)
            elif inspect.iscoroutinefunction(meth):
                await send(MsgType.Return, await methval)
            elif inspect.isgeneratorfunction(meth):
                for val in methval:
                    await send(MsgType.Return, val)
            else:
                await send(MsgType.Return, methval)
        except Exception as exc:
            additional = {
                'args': exc.args,
                'msg': str(exc),
            }
            additional.update(vars(exc))
            await send(MsgType.Error, _fqn(type(exc)), additional)
