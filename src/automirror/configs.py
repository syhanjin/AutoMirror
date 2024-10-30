# -*- coding: utf-8 -*-
import asyncio
import dataclasses
import logging
import token
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse
from httpx import AsyncClient


class MirrorType(StrEnum):
    ORG = 'org'
    REPO = 'repo'


def check_url(url: str) -> bool:
    parsed = urlparse(url)
    return bool(parsed.scheme and parsed.netloc)


@dataclasses.dataclass
class Mirror:
    type: MirrorType
    origin: str
    target: str | None = None
    url: str | None = None

    def __repr__(self):
        if self.origin:
            return f'{self.type if isinstance(self.type, MirrorType) else ""}源({self.origin} -> {self.target})'
        else:
            return f'源({self.type=} {self.origin=} {self.target=} {self.url=})'

    def validate(self) -> bool:
        if not self.origin:
            logging.warning(f'{self}的名字为空，将跳过同步')
            return False
        if not self.target:
            self.target = self.origin
        if self.type not in MirrorType:
            logging.warning(f'{self}的类型不在可选范围，将跳过同步')
            return False
        self.type = MirrorType(self.type)
        if self.type == MirrorType.REPO:
            if not self.url:
                logging.warning(f'{self}的clone_url为空，将跳过同步')
                return False
            if not check_url(self.url):
                logging.warning(f'{self}的clone_url不合法，将跳过同步')
                return False
        return True


class Session:
    target_base_url: str
    origin_base_url: str
    token: str
    try_without_proxy: bool = True
    proxy_urls: list[str] = [""]

    earliest_update_time: datetime = datetime.fromisoformat("2021-01-01T00:00:00+08:00")

    mirrors: list[Mirror]

    target_client: AsyncClient
    origin_client: AsyncClient = AsyncClient(timeout=None)
    semaphore: asyncio.Semaphore

    def load_config(self, config_path: Path):
        if not config_path.is_file():
            raise FileNotFoundError(f"没有找到配置文件 {config_path}")
        with config_path.open('rb') as f:
            import tomllib
            raw_config = tomllib.load(f)
        assert raw_config.get('config'), '配置文件中没有 config 项'
        self.target_base_url = raw_config['config'].get('target_base_url')
        assert self.target_base_url, 'target_base_url未配置'
        self.origin_base_url = raw_config['config'].get('origin_base_url')
        assert self.origin_base_url, 'origin_base_url未配置'
        self.token = raw_config['config'].get('token')
        assert self.token, '认证token未配置'
        assert raw_config.get('mirrors'), '没有配置镜像列表'
        concurrency = raw_config['config'].get('concurrency', 3)
        assert concurrency >= 1, f'并发数至少为1, {concurrency=}'
        self.semaphore = asyncio.Semaphore(concurrency)
        # 识别代理url
        proxy_urls = raw_config['config'].get('proxy_urls', None)
        if proxy_urls:
            for proxy_url in proxy_urls:
                if check_url(proxy_url):
                    self.proxy_urls.append(proxy_url if proxy_url.endswith('/') else proxy_url + '/')
                else:
                    logging.warning(f'{proxy_url=}不是合法的url，将忽略')
        try_without_proxy = raw_config['config'].get('try_without_proxy', True)
        assert try_without_proxy or self.proxy_urls, f'{try_without_proxy=}，但是没有可用的代理'
        self.mirrors = []
        for mirror in raw_config['mirrors']:
            mirror_obj = Mirror(
                type=mirror.get('type'),
                origin=mirror.get('origin'),
                target=mirror.get('target'),
                url=mirror.get('url')
            )
            if mirror_obj.validate():
                self.mirrors.append(mirror_obj)

    async def check_token(self) -> bool:
        # f'认证不成功, {resp.status_code=}'
        if not token:
            return False
        self.target_client = AsyncClient(timeout=None, headers={
            'Authorization': f'token {self.token}'
        })
        resp = await self.target_client.get(f"{self.target_base_url}/user")
        if resp.status_code != 200:
            return False
        return True


session = Session()
