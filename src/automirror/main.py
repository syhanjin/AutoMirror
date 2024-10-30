# -*- coding: utf-8 -*-
import argparse
import logging
import asyncio
from pathlib import Path
from automirror.configs import session, MirrorType

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


# TODO: 统一logging风格

async def check_target(target) -> list[dict[str, str]]:
    # 检查target是否存在
    resp = await session.target_client.get(f'{session.target_base_url}/orgs/{target}')
    assert resp.status_code in [200, 404], f'出了点小问题？{target=}, {resp.status_code=}'
    target_repos = []
    if resp.status_code == 404:
        # 创建org
        resp = await session.target_client.post(f'{session.target_base_url}/orgs/', json={'username': target})
        assert resp.status_code == 201, f'创建org失败, {resp.status_code=}'
    else:
        target_repos = await get_target_org_repos(target)
    return target_repos


async def get_target_org_repos(target):
    repos = []
    url = f'{session.target_base_url}/orgs/{target}/repos'
    while url:
        resp = await session.target_client.get(url)
        assert resp.status_code == 200, f'获取target_org失败, {resp.status_code=}'
        repos.extend(resp.json())
        if 'next' in resp.links:
            url = resp.links['next'].get('url')
        else:
            url = None
    return repos


async def get_origin_org_repos_iter(origin):
    url = f'{session.origin_base_url}/orgs/{origin}/repos'
    while url:
        resp = await session.origin_client.get(url)
        assert resp.status_code == 200, f'获取源org仓库失败, {resp.status_code=}'
        for repo in resp.json():
            yield repo
        if 'next' in resp.links:
            url = resp.links['next'].get('url')
        else:
            url = None


async def repo_migrate(clone_addr, repo_name, repo_owner):
    # 控制migrate并发数
    async with session.semaphore:
        resp = await session.target_client.post(
            f'{session.target_base_url}/repos/migrate/',
            json={
                'clone_addr': clone_addr,
                'mirror': True,
                'repo_name': repo_name,
                'repo_owner': repo_owner,
            }
        )
        if resp.status_code == 201:
            logging.info(f"Created - {repo_owner}/{repo_name}")
            return
        logging.error(f'CreateFailed - {repo_owner}/{repo_name} - {resp.status_code=}')
    if resp.status_code == 422:
        # migrate失败，删除库
        logging.error(f'Deleting - {repo_owner}/{repo_name} - {resp.status_code=}')
        await repo_delete(repo_name, repo_owner)


async def repo_delete(repo_name, repo_owner):
    resp = await session.target_client.delete(f'{session.target_base_url}/repos/{repo_owner}/{repo_name}/')
    if resp.status_code != 204:
        logging.error(f'DeleteFailed - {repo_owner}/{repo_name} - {resp.status_code=}')
        return
    logging.info(f"Deleted - {repo_owner}/{repo_name}")


async def update_org(mirror):
    get_origin_org_repos_exception = None
    try:
        target_repos = await check_target(mirror.target)
        target_repo_names = [x['name'] for x in target_repos]
    except Exception as e:
        logging.error(f'同步{mirror}失败：检查target时发生错误 {e}')
        return
    tg = asyncio.TaskGroup()
    try:
        async for repo in get_origin_org_repos_iter(mirror.origin):
            if repo['name'] in target_repo_names:
                target_repo_names.remove(repo['name'])
                logging.info(f"Existed - {mirror.target}/{repo['name']}")
            else:
                tg.create_task(repo_migrate(repo['clone_url'], repo['name'], mirror.target))
    except Exception as e:
        get_origin_org_repos_exception = e
    # 删除不存在的repo
    for repo_name in target_repo_names:
        tg.create_task(repo_delete(repo_name, mirror.target))
    if get_origin_org_repos_exception:
        logging.error(f'同步{mirror}不完全：获取origin_repos时发生错误 {get_origin_org_repos_exception}')
    else:
        logging.info(f'同步{mirror}成功！')


async def update_repo(mirror):
    try:
        target_repos = await check_target(mirror.target)
        target_repo_names = [x['name'] for x in target_repos]
    except Exception as e:
        logging.error(f'同步{mirror}失败：检查target时发生错误 {e}')
        return
    # TODO: 避免获取全部target_repo再进行检查
    if mirror.origin in target_repo_names:
        logging.info(f"Existed - {mirror.target}/{mirror.origin}")
    else:
        await repo_migrate(mirror.url, mirror.origin, mirror.target)
    logging.info(f'同步{mirror}成功！')


async def main(argv):
    # 关闭httpx的输出
    logging.getLogger("httpx").setLevel(logging.CRITICAL + 1)
    # 解析参数
    args = parser.parse_args(argv)
    session.load_config(args.config)
    await session.check_token()
    logging.info("开始同步")
    for mirror in session.mirrors:
        logging.info(f"开始同步{mirror}...")
        if mirror.type == MirrorType.REPO:
            await update_repo(mirror)
        elif mirror.type == MirrorType.ORG:
            await update_org(mirror)


if __name__ == '__main__':
    import sys

    asyncio.run(main(sys.argv[1:]))
