import asyncio
from .main import main


def entry() -> int:
    asyncio.run(main())
    return 0
