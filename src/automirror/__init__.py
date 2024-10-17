import logging

from automirror.main import main


def entry():
    import asyncio, sys
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    try:
        asyncio.run(main(sys.argv[1:]))
    except Exception as e:
        logging.error(e)
