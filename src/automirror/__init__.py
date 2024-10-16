from automirror.main import main


def entry():
    import asyncio, sys
    asyncio.run(main(sys.argv[1:]))
