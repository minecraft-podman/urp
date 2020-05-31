import asyncio
import inspect

from .common import MsgType, BaseUrpProtocol, UrpStreamMixin, UrpSubprocessMixin

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


async def wait_task_and_queue(task, queue):
    while True:
        qtask = asyncio.create_task(queue.get())
        done, pending = await asyncio.wait(
            [task, qtask], return_when=asyncio.FIRST_COMPLETED,
        )
        if qtask in pending:
            qtask.cancel()

        # Should we give these in a particular order?
        for t in done:
            yield await t

        if task.done():
            break


class ServerBaseProtocol(BaseUrpProtocol):
    def __init__(self, router=None):
        super().__init__()
        self.router = router if router is not None else {}

    async def urp_new_channel(self, channel_id, msg):
        with self.urp_open_channel(channel_id) as (send, queue):
            assert msg[0] == MsgType.Call

            # TODO: Logging
            # TODO: maybe redirect stdout/stderr?

            # Handles channel management and Shooshing
            task = asyncio.create_task(self._method_task(send, msg[1], msg[2]))
            async for msg in wait_task_and_queue(task, queue):
                if msg is None:  # Returned from task
                    await send(MsgType.Shoosh)
                    return
                # Got from the queue, so list
                elif msg[0] == MsgType.Shoosh:
                    task.cancel()
                    return
                # Anything else is a protocol error

    async def _method_task(self, send, name, kwargs):
        """
        Responsible for calling the actual method and producing returns
        """
        try:
            meth = self.router[name]
        except KeyError:
            await send(MsgType.Error, '.NotAMethod', None)
            return
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


class ServerStreamProtocol(UrpStreamMixin, ServerBaseProtocol):
    pass


class ServerSubprocessProtocol(UrpSubprocessMixin, ServerBaseProtocol):
    def urp_stderr_recv(self, data):
        # TODO
        sys.stderr.buffer.write(data)
