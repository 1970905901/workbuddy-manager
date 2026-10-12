"""「设置 → 代理」的接口：命名线路表 + 全局出口代理。

两处配置分属不同的存放位置，接口把它们收在同一个页面下：

  · **命名线路表** → 上游 `config.json` 的 `proxies` 段。面板自己直连腾讯的请求
    （登录 / 签到 / 积分 / 模型）按账号绑定取用；读侧每次现读文件，所以改完立即
    生效、不必重启。聊天与上游后台任务由上游进程发出，取决于上游是否支持
    `proxies` + 账号 `proxy` 字段（见 docs/account-proxy-routes.md）。
  · **全局出口代理 / 更新代理** → 面板设置（SQLite），覆盖同名环境变量。环境变量
    仍作为兜底与「没设过」时的默认值。

凭据只进不出：写接口收完整地址，读接口一律回脱敏串（`***`），审计日志也只记脱敏串。
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from .. import config, db, security
from ..iputil import client_ip
from ..services import proxyroutes as routesvc
from . import accounts as accounts_router

router = APIRouter(prefix='/api', tags=['proxies'])


def _auth_dir(upstream_id: int | None):
    """当前分组的账号目录（绑定计数与改名/删除时的重绑都要按分组来）。

    复用账号路由里的 `_group` / `_group_dir`：分组语义只有那一份实现，
    在这里重写一遍迟早会和它漂移（多分组最容易出的错就是「忘了带分组」）。
    """
    group = accounts_router._group(upstream_id)          # noqa: SLF001 —— 同仓库内复用
    return accounts_router._group_dir(group)             # noqa: SLF001


def _wrap(exc: routesvc.RouteError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


def _masked(value: str) -> str:
    return routesvc.mask_url(value) if value else ''


@router.get('/proxy-routes')
def list_proxy_routes(upstream_id: int | None = Query(None),
                      user: dict = Depends(security.require_session_admin)) -> dict:
    """线路列表（凭据已脱敏）+ 每条线路的绑定账号数。

    仅会话管理员可读：地址虽然脱了敏，**用户名本身**往往是住宅代理的账号标识
    （例如 `api07…_c_CN_city_Shanghai_s_…`），只读令牌 / 只读账号不该看到它。
    账号页选线路走的仍是老的 `/api/proxies`（只有名字），不受这里影响。
    """
    auth_dir = _auth_dir(upstream_id)
    routes = routesvc.list_routes(auth_dir)
    healthy = routesvc.health()
    for item in routes:
        item['probe'] = healthy.get(item['name'])
        # 共享线路上挂太多账号是风控特征：界面据此给出提示（阈值 3，另见前端文案）。
        item['shared_warning'] = item['mode'] == 'shared' and item['bound'] >= 3
    return {
        'routes': routes,
        'default': config.DEFAULT_ACCOUNT_PROXY,
        'schemes': list(routesvc.SCHEMES),
        'bound_total': sum(item['bound'] for item in routes),
        'sticky_seconds': config.PROXY_STICKY_SECONDS,
    }


@router.post('/proxy-routes')
def create_proxy_route(request: Request, body: dict = Body(...),
                       user: dict = Depends(security.require_session_admin)) -> dict:
    try:
        item = routesvc.upsert(body.get('name'), body.get('url'), allow_overwrite=False)
    except routesvc.RouteError as exc:
        raise _wrap(exc) from exc
    security.audit(user, 'create_proxy_route', item['name'],
                   f'地址 {item["masked"]}；来源 {client_ip(request)}')
    return item


@router.put('/proxy-routes/{name}')
def update_proxy_route(name: str, request: Request, body: dict = Body(...),
                       upstream_id: int | None = Query(None),
                       user: dict = Depends(security.require_session_admin)) -> dict:
    """改地址，或改名（改名会把绑定它的账号一起改过去）。"""
    new_name = str(body.get('new_name') or '').strip()
    try:
        if new_name and new_name != name:
            result = routesvc.rename(name, new_name, auth_dir=_auth_dir(upstream_id))
            item = routesvc.describe(result['name'], routesvc.routes()[result['name']])
            item['bound'] = routesvc.bound_counts(_auth_dir(upstream_id)).get(result['name'], 0)
            security.audit(user, 'rename_proxy_route', f'{name} → {result["name"]}',
                           f'同步 {result["bound_updated"]} 台账号的绑定；'
                           f'来源 {client_ip(request)}')
            return {**item, 'bound_updated': result['bound_updated']}
        item = routesvc.upsert(name, body.get('url'), weight=body.get('weight'))
    except routesvc.RouteError as exc:
        raise _wrap(exc) from exc
    security.audit(user, 'update_proxy_route', item['name'],
                   f'地址 {item["masked"]}；来源 {client_ip(request)}')
    return item


@router.delete('/proxy-routes/{name}')
def delete_proxy_route(name: str, request: Request, force: bool = Query(False),
                       upstream_id: int | None = Query(None),
                       user: dict = Depends(security.require_session_admin)) -> dict:
    """删除线路。仍被账号绑着时要带 force（界面会先把账号数摆出来让人确认）。"""
    try:
        result = routesvc.delete(name, auth_dir=_auth_dir(upstream_id),
                                 clear_bindings=force)
    except routesvc.RouteError as exc:
        raise _wrap(exc) from exc
    security.audit(user, 'delete_proxy_route', name,
                   f'清掉 {result["bound_cleared"]} 台账号的绑定；来源 {client_ip(request)}')
    return result


@router.post('/proxy-routes/import')
def import_proxy_routes(request: Request, body: dict = Body(...),
                        user: dict = Depends(security.require_session_admin)) -> dict:
    """批量导入：每行 `名字=地址`；逐行报问题，能导的先导进去。"""
    text = str(body.get('text') or '')
    if not text.strip():
        raise HTTPException(status_code=400, detail='没有可导入的内容')
    try:
        result = routesvc.import_routes(text)
    except routesvc.RouteError as exc:
        raise _wrap(exc) from exc
    security.audit(user, 'import_proxy_routes', '',
                   f'导入 {result["imported"]} 条，问题 {len(result["problems"])} 条；'
                   f'来源 {client_ip(request)}')
    return result


@router.post('/proxy-routes/probe')
async def probe_proxy_route(body: dict = Body(...),
                            user: dict = Depends(security.require_session_admin)) -> dict:
    """经某条线路发一次轻请求，回报耗时、出口 IP 与归属地。

    探测是管理员主动发起的，所以失败原因原样返回（与上游探测同一口径）；
    出口 IP 也会显示出来 —— 配住宅代理时最该确认的就是「出口到底落在哪」。
    """
    try:
        result = await routesvc.probe_route(str(body.get('name') or ''))
    except routesvc.RouteError as exc:
        raise _wrap(exc) from exc
    routesvc.record_probe(str(body.get('name') or ''), result)
    return result


@router.get('/proxy-routes/{name}/accounts')
def route_accounts(name: str, upstream_id: int | None = Query(None),
                   user: dict = Depends(security.require_session_admin)) -> dict:
    """这条线路的绑定抽屉：全部账号 + 各自当前用的线路，勾选即绑定。

    池化意味着一个账号可以同时用多条线路（按权重分摊、失败切换），所以这里返回的是
    **每条线路各自的集合**，前端按「勾没勾」提交增量。
    """
    try:
        clean = routesvc.validate_name(name)
    except routesvc.RouteError as exc:
        raise _wrap(exc) from exc
    table = routesvc.routes()
    if clean not in table:
        raise HTTPException(status_code=404, detail=f'线路 {clean!r} 不存在')
    rows = routesvc.account_rows(_auth_dir(upstream_id))
    return {
        'name': clean,
        'mode': 'exclusive' if config.route_is_exclusive(table[clean]) else 'shared',
        'accounts': [
            {
                'file': row['file'],
                'uid': row['uid'],
                'nickname': row['nickname'],
                'routes': row['routes'],
                'session': row['session'],
                'session_preview': (row['session'][:4] + '*'
                                    if len(row['session']) > 4 else row['session']),
                'bound': clean in row['routes'],
            }
            for row in rows
        ],
    }


@router.post('/proxy-routes/{name}/accounts')
def bind_route_accounts(name: str, request: Request, body: dict = Body(...),
                        upstream_id: int | None = Query(None),
                        user: dict = Depends(security.require_session_admin)) -> dict:
    """批量绑定 / 解绑：`{"files": [...], "add": true|false}`。"""
    files = body.get('files')
    if not isinstance(files, list) or not files:
        raise HTTPException(status_code=400, detail='没有选择账号')
    add = bool(body.get('add', True))
    try:
        result = routesvc.set_bindings(name, [str(f) for f in files],
                                      auth_dir=_auth_dir(upstream_id), add=add)
    except routesvc.RouteError as exc:
        raise _wrap(exc) from exc
    security.audit(user, 'bind_proxy_route' if add else 'unbind_proxy_route', name,
                   f'{"绑定" if add else "解绑"} {result["updated"]} 台；'
                   f'来源 {client_ip(request)}')
    return result


@router.post('/proxy-routes/rotate')
def rotate_route_sessions(request: Request, body: dict = Body(...),
                          upstream_id: int | None = Query(None),
                          user: dict = Depends(security.require_session_admin)) -> dict:
    """换会话（= 换出口 IP）：住宅代理的会话名决定出口，换名字就是换 IP。

    做成**显式动作**而不是自动轮换：换 IP 在风控眼里是特征变化，什么时候换应该由
    人决定（换之前之后都能在列表里看到会话名）。
    """
    files = body.get('files')
    if not isinstance(files, list) or not files:
        raise HTTPException(status_code=400, detail='没有选择账号')
    result = routesvc.rotate_sessions([str(f) for f in files],
                                     auth_dir=_auth_dir(upstream_id))
    security.audit(user, 'rotate_proxy_session', '',
                   f'{result["updated"]} 台换会话；来源 {client_ip(request)}')
    return result


# ── 全局出口代理 ─────────────────────────────────────────────
# 存面板设置（key: http_proxy / update_proxy），空串 = 清除（回落到环境变量）。
@router.get('/settings/proxy')
def get_proxy_settings(user: dict = Depends(security.require_session_admin)) -> dict:
    """当前出口代理配置（脱敏）+ 生效来源。"""
    stored_http = config.stored_http_proxy()
    stored_update = str(db.get_setting('update_proxy', '') or '')
    effective = config.effective_http_proxy()
    source = 'panel' if stored_http else ('env' if config.HTTP_PROXY else 'none')
    return {
        'http_proxy': _masked(stored_http),
        'update_proxy': _masked(stored_update),
        'http_configured': bool(stored_http),
        'update_configured': bool(stored_update),
        'env_http_proxy': _masked(config.HTTP_PROXY),
        'env_update_proxy': _masked(str(os.environ.get('WB_UPDATE_PROXY') or '')),
        'effective': _masked(effective),
        'source': source,
        'probe_url': config.PROXY_PROBE_URL,
    }


@router.put('/settings/proxy')
def save_proxy_settings(request: Request, body: dict = Body(...),
                        user: dict = Depends(security.require_session_admin)) -> dict:
    """保存出口代理 / 更新代理。

    提交的地址里密码若是 `***`（界面回显的就是它）则沿用已存的密码；
    传空串表示清除（回落到环境变量）。
    """
    changed: list[str] = []
    for field, key in (('http_proxy', 'http_proxy'), ('update_proxy', 'update_proxy')):
        if field not in body:
            continue
        raw = str(body.get(field) or '').strip()
        if not raw:
            db.set_setting(key, '')
            changed.append(f'{field}=清除')
            continue
        stored = str(db.get_setting(key, '') or '')
        if routesvc.MASK in raw:
            if not stored:
                # 没存过密码时 `***` 无从还原：明确拒绝，而不是存进一个假地址
                raise HTTPException(status_code=400,
                                    detail=f'{field} 里还有 ***，请粘贴完整地址')
            raw = routesvc.keep_secret(raw, stored)
        try:
            value = routesvc.normalize_url(raw)
        except routesvc.RouteError as exc:
            raise HTTPException(status_code=400, detail=f'{field} 无效：{exc}') from exc
        db.set_setting(key, value)
        changed.append(f'{field}={_masked(value)}')
    if changed:
        security.audit(user, 'update_proxy_settings', '',
                       f'{"；".join(changed)}；来源 {client_ip(request)}')
    return get_proxy_settings(user)


@router.post('/settings/proxy/probe')
async def probe_global_proxy(user: dict = Depends(security.require_session_admin)) -> dict:
    """测当前生效的出口代理（没配就直接说清，而不是去连一个空地址）。"""
    effective = config.effective_http_proxy()
    if not effective:
        return {'ok': False, 'error': '当前没有配置出口代理（面板直连）', 'ms': 0}
    return await routesvc.probe_url(effective)
