# -*- coding: utf-8 -*-
import argparse
import logging
import tomllib
import asyncio
from pathlib import Path

import httpx

__doc__ = """
AutoMirror v0.1.0

配置文件格式
--- config.toml ---
[config]
target_base_url = "your_target_base_url" # 镜像站api
origin_base_url = "your_origin_base_url" # 源站api Github: https://api.github.com
token = "your_token" # 镜像站api访问的access token

[[mirrors]]
type = "org" # 源为Org
origin = "your_origin_org_name" # Org的名称
# target = "your_target_org_name" # 镜像库同步的Org名称，不传入则默认与origin相同

[[mirrors]]
type = "repo" # 源为Repo
origin = "your_origin_repo_name" # 源的名称，镜像过来的库与源库同名
url = "https://github.com/syhanjin/Countdowner.git" # 源的git地址
target = "your_target_org_name" # 同步到镜像站后属于的Org
--- --- ---
""".strip()

parser = argparse.ArgumentParser(formatter_class=argparse.RawTextHelpFormatter, description=__doc__)
parser.add_argument('-c', '--config', type=Path, default=Path('./config.toml'), help='同步用配置文件路径')

config: Path = Path('./config.toml')
target_base_url = ''
origin_base_url = ''
target_client = httpx.AsyncClient(timeout=None)
origin_client = httpx.AsyncClient(timeout=None)


# TODO: 统一logging风格

async def check_target(target) -> list[dict[str, str]]:
    # 检查target是否存在
    resp = await target_client.get(f'{target_base_url}/orgs/{target}')
    assert resp.status_code in [200, 404], f'出了点小问题？{target=}, {resp.status_code=}'
    target_repos = []
    if resp.status_code == 404:
        # 创建org
        resp = await target_client.post(f'{target_base_url}/orgs/', json={'username': target})
        assert resp.status_code == 201, f'创建org失败, {resp.status_code=}'
    else:
        target_repos = await get_target_org_repos(target)
    return target_repos


async def get_target_org_repos(target):
    repos = []
    url = f'{target_base_url}/orgs/{target}/repos'
    while url:
        resp = await target_client.get(url)
        assert resp.status_code == 200, f'获取target_org失败, {resp.status_code=}'
        repos.extend(resp.json())
        if 'next' in resp.links:
            url = resp.links['next'].get('url')
        else:
            url = None
    return repos


async def get_origin_org_repos_iter(origin):
    url = f'{origin_base_url}/orgs/{origin}/repos'
    while url:
        resp = await origin_client.get(url)
        assert resp.status_code == 200, f'获取源org仓库失败, {resp.status_code=}'
        for repo in resp.json():
            yield repo
        if 'next' in resp.links:
            url = resp.links['next'].get('url')
        else:
            url = None


async def repo_migrate(clone_addr, repo_name, repo_owner):
    resp = await target_client.post(
        f'{target_base_url}/repos/migrate/',
        json={
            'clone_addr': clone_addr,
            'mirror': True,
            'repo_name': repo_name,
            'repo_owner': repo_owner,
        }
    )
    if resp.status_code != 201:
        logging.error(f'CreateFailed - {repo_owner}/{repo_name} - {resp.status_code=}')
    else:
        logging.info(f"Created - {repo_owner}/{repo_name}")


async def repo_delete(repo_name, repo_owner):
    resp = await target_client.delete(f'{target_base_url}/repos/{repo_owner}/{repo_name}/')
    if resp.status_code != 204:
        logging.error(f'DeleteFailed - {repo_owner}/{repo_name} - {resp.status_code=}')
        return
    logging.info(f"Deleted - {repo_owner}/{repo_name}")


async def update_org(origin, target):
    get_origin_org_repos_exception = None
    try:
        target_repos = await check_target(target)
        target_repo_names = [x['name'] for x in target_repos]
    except Exception as e:
        logging.error(f'同步失败({origin} -> {target})：检查target时发生错误 {e}')
        return
    async with asyncio.TaskGroup() as tg:
        try:
            async for repo in get_origin_org_repos_iter(origin):
                if repo['name'] in target_repo_names:
                    target_repo_names.remove(repo['name'])
                    logging.info(f"Existed - {target}/{repo['name']}")
                else:
                    tg.create_task(repo_migrate(repo['clone_url'], repo['name'], target))
        except Exception as e:
            get_origin_org_repos_exception = e
        # 删除不存在的repo
        for repo_name in target_repo_names:
            tg.create_task(repo_delete(repo_name, target))

    if get_origin_org_repos_exception:
        logging.error(f'同步不完全({origin} -> {target})：获取origin_repos时发生错误 {get_origin_org_repos_exception}')
    else:
        logging.info(f'同步成功({origin} -> {target})！')


async def update_repo(origin, origin_url, target):
    from urllib.parse import urlparse
    parsed = urlparse(origin_url)
    if not bool(parsed.scheme and parsed.netloc):
        logging.error(f'同步失败({origin} -> {target})：{origin_url=} 不是合法的url')
        return
    try:
        target_repos = await check_target(target)
        target_repo_names = [x['name'] for x in target_repos]
    except Exception as e:
        logging.error(f'同步失败({origin} -> {target})：检查target时发生错误 {e}')
        return
    # TODO: 避免获取全部target_repo再进行检查
    if origin in target_repo_names:
        logging.info(f"Existed - {target}/{origin}")
    else:
        await repo_migrate(origin_url, origin, target)


async def main(argv):
    # 关闭httpx的输出
    logging.getLogger("httpx").setLevel(logging.CRITICAL + 1)
    global config, target_base_url, origin_base_url, target_client
    # 解析参数
    args = parser.parse_args(argv)
    config = args.config
    # 保证config存在
    if not config.exists():
        raise FileNotFoundError("没有找到配置文件")
    with config.open('rb') as f:
        data = tomllib.load(f)
    assert data.get('config'), '配置文件中没有 config'
    target_base_url = data['config'].get('target_base_url')
    assert target_base_url, 'target_base_url未配置'
    origin_base_url = data['config'].get('origin_base_url')
    assert origin_base_url, 'origin_base_url未配置'
    token = data['config'].get('token')
    assert token, '认证token未配置'
    target_client = httpx.AsyncClient(timeout=None, headers={
        'Authorization': f'token {token}'
    })
    resp = await target_client.get(f"{target_base_url}/user")
    assert resp.status_code == 200, f'认证不成功, {resp.status_code=}'

    assert data.get('mirrors'), '没有配置镜像列表'
    # 以上是开始同步前可以检查出的配置问题，使用assert检查，检查不通过则直接终止
    logging.info("开始同步")
    for mirror in data['mirrors']:
        if not mirror.get('origin'):
            # 镜像源名字为空，直接跳过，不警告
            return
        if not mirror.get('target'):
            mirror['target'] = mirror['origin']
        if mirror.get('type') not in ['repo', 'org']:
            logging.error(
                f'同步失败({mirror["origin"]} -> {mirror["target"]})：类型必须为repo或者org，实际为：{mirror["type"]}')
            return
        if mirror['type'] == 'repo':
            logging.info(f'同步Repo({mirror["origin"]} -> {mirror["target"]})...')
            await update_repo(mirror['origin'], mirror.get('url'), mirror['target'])
            # print(result)
        elif mirror['type'] == 'org':
            logging.info(f'同步Org({mirror["origin"]} -> {mirror["target"]})...')
            await update_org(mirror['origin'], mirror['target'])


if __name__ == '__main__':
    import sys

    asyncio.run(main(sys.argv[1:]))
