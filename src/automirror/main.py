# -*- coding: utf-8 -*-
import asyncio


async def main(argv):
    print(argv)


if __name__ == '__main__':
    import sys

    asyncio.run(main(sys.argv[1:]))
