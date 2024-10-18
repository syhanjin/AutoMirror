# -*- coding: utf-8 -*-
import argparse
import logging
import tomllib
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


async def update_org(origin, target):
    results = []
    target_repos = await check_target(target)
    target_repo_names = [x['name'] for x in target_repos]
    async for repo in get_origin_org_repos_iter(origin):
        if repo['name'] in target_repo_names:
            target_repo_names.remove(repo['name'])
            logging.info(f"Existed - {target}/{repo['name']}")
            results.append({'code': 'existed', 'name': repo['name']})
            continue
        resp = await target_client.post(
            f'{target_base_url}/repos/migrate/',
            json={
                'clone_addr': repo['clone_url'],
                'mirror': True,
                'repo_name': repo['name'],
                'repo_owner': target,
            }
        )
        if resp.status_code == 201:
            logging.info(f"Created - {target}/{repo['name']}")
            results.append({'code': 'created', 'name': repo['name']})
        else:
            logging.error(f"CreateFailed - {target}/{repo['name']}: {resp.text}")
            results.append({'code': 'create-failed', 'name': repo['name'], 'message': resp.text})
    # 删除不存在的repo
    for repo_name in target_repo_names:
        resp = await target_client.delete(f'{target_base_url}/repos/{target}/{repo_name}/')
        if resp.status_code == 204:
            logging.info(f"Deleted - {target}/{repo_name}")
            results.append({'code': 'deleted', 'name': repo_name})
        else:
            logging.error(f"DeleteFailed - {target}/{repo_name}: {resp.text}")
            results.append({'code': 'delete-failed', 'name': repo_name, 'message': resp.text})
    return results


async def update_repo(origin, origin_url, target):
    from urllib.parse import urlparse
    parsed = urlparse(origin_url)
    assert bool(parsed.scheme and parsed.netloc), 'repo 的 origin_url 不是合法的 url'
    target_repos = await check_target(target)
    target_repo_names = [x['name'] for x in target_repos]
    if origin in target_repo_names:
        return {'code': 'exists', 'name': origin}
    resp = await target_client.post(
        f'{target_base_url}/repos/migrate/',
        json={
            'clone_addr': origin_url,
            'mirror': True,
            'repo_name': origin,
            'repo_owner': target,
        }
    )
    if resp.status_code == 201:
        logging.info(f"Created - {origin}")
        return {'code': 'created', 'name': origin}
    else:
        logging.info(f"CreateFailed - {origin}: {resp.text}")
        return {'code': 'create-failed', 'name': origin, 'message': resp.text}


async def update_mirror(_mirror):
    assert _mirror.get('type') in ['repo', 'org'], '类型必须为repo或者org'
    assert _mirror.get('origin'), '镜像源名字不为空'
    if not _mirror.get('target'):
        _mirror['target'] = _mirror['origin']
    # assert _mirror.get('target'), '镜像目标名字不为空'
    if _mirror['type'] == 'repo':
        logging.info(f'---更新Repo:{_mirror["origin"]}---')
        await update_repo(_mirror['origin'], _mirror.get('url'), _mirror['target'])
        # print(result)
    elif _mirror['type'] == 'org':
        logging.info(f'---更新Org:{_mirror["origin"]}---')
        await update_org(_mirror['origin'], _mirror['target'])


async def main(argv):
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
    logging.info("开始同步")
    for mirror in data['mirrors']:
        try:
            await update_mirror(mirror)
        except AssertionError as e:
            logging.error(f"origin: {mirror.get('origin')} 同步失败，{e=}")


if __name__ == '__main__':
    import sys, asyncio

    asyncio.run(main(sys.argv[1:]))
