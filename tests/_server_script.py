import asyncio

from urp import Service, method


serv = Service("urp-test")


@serv.interface("example")
class Example:
    @method("Echo")
    def ping(self, **args):
        return args

    @method
    def sync(self):
        return {"spam": "eggs"}

    @method("async")
    async def notsync(self):
        await asyncio.sleep(0.1)
        return {"spam": "eggs"}

    @method
    def gen(self):
        yield {"spam": "eggs"}
        yield {"foo": "bar"}

    @method
    async def async_gen(self):
        await asyncio.sleep(0.1)
        yield {"spam": "eggs"}
        await asyncio.sleep(0.1)
        yield {"foo": "bar"}

    @method
    def error(self, msg):
        raise Exception(msg)


asyncio.run(serv.serve_stdio())
