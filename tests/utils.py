async def aenumerate(iterable):
    count = 0
    async for val in iterable:
        yield count, val
        count += 1
