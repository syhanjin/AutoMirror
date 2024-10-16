# -*- coding: utf-8 -*-

async def main(argv):
    print(argv)


if __name__ == '__main__':
    import sys, asyncio

    asyncio.run(main(sys.argv[1:]))
