#!/usr/bin/env python3
import asyncio
from softioc import softioc, builder

async def main():
    prefix = "DUMMY:"
    builder.SetDeviceName(prefix)

    # A single PV
    pv_value = builder.aOut(prefix + "VALUE", initial_value=42.0, always_update=True)

    # Publish database
    builder.LoadDatabase()

    # Use asyncio loop for softIOC
    loop = asyncio.get_running_loop()
    def asyncio_dispatcher(func, *args, **kwargs):
        loop.call_soon(func, *args)

    # Init IOC with dispatcher
    softioc.iocInit(dispatcher=asyncio_dispatcher)

    print("IOC started with PV:", prefix + "VALUE")

    # Keep running
    await softioc.interactive_ioc(globals())

if __name__ == "__main__":
    asyncio.run(main())