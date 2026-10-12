"""命名代理线路表：读写上游 `config.json` 里的 `proxies` 段。

为什么要有这个模块（而不是让用户手改 JSON）：线路表是「账号 → 出口」的配置，
面板里早就有「出口线路」下拉，但下拉里的名字只能靠手工编辑上游配置文件才能新增
—— 界面上看得见、改不了。这里把那份读写收成一处，供 API 与页面使用。

三条必须守住的性质：

1. **只动 `proxies` 段**：上游 config.json 里还有 api_key、schedule、prompt 等，
   写线路时一个键都不能碰（照旧保留未知键），否则用户的口令 / 调度配置会被抹掉。
2. **凭据不出网**：代理地址通常带用户名密码，列表一律回脱敏串（`***`）；编辑时
   若提交的仍是脱敏串，就沿用原来那份密码 —— 为了改个域名不必重新输密码。
3. **改名 / 删除要管绑定**：线路被改名或删除后，仍绑着它的账号会**直接失败**
   （按设计不回退到默认出口），所以这两条路径要把绑定一并改掉 / 清掉，并回报
   影响了几台账号。
"""
from __future__ import annotations

import ipaddress
import json
import re
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from .. import config
from . import wb2api
from .tencent import _atomic_write_json

# 线路名会出现在账号文件与界面上：限字母数字与 . _ -（1~32 位），免得引号 / 换行
# 拼出误导性的名字（也会让日志列宽失控）。
NAME_RE = re.compile(r'^[A-Za-z0-9._-]{1,32}$')

SCHEMES = ('http', 'https', 'socks5', 'socks5h')
MASK = '***'


class RouteError(ValueError):
    """线路表相关的**用户可见**错误（路由层翻成对应状态码）。"""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _config_path() -> Path:
    return config.UPSTREAM_CONFIG


def _load() -> dict:
    path = _config_path()
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise RouteError(
            f'未找到上游配置文件 {path}。多分组部署请确认该分组的配置文件已配置，'
            f'容器部署请确认它已挂载。', 409) from exc
    except (OSError, ValueError) as exc:
        raise RouteError(f'上游配置文件无法读取：{exc}') from exc
    if not isinstance(raw, dict):
        raise RouteError('上游配置文件格式异常（顶层不是对象）')
    return raw


def _save(cfg: dict) -> None:
    if not cfg.get('proxies'):
        cfg.pop('proxies', None)      # 空表就把键删掉，保持文件干净（读侧两种都认）
    _atomic_write_json(_config_path(), cfg)


def routes() -> dict[str, str]:
    """线路名 → 原始地址（**含凭据**，只供服务端使用）。"""
    return config.proxy_routes()


def validate_name(name: object) -> str:
    value = str(name or '').strip()
    if not NAME_RE.match(value):
        raise RouteError('线路名只能是字母、数字与 . _ -（1~32 位）')
    return value


def normalize_url(url: object) -> str:
    """校验并规范化代理地址（只校验形状，不探测可达性）。"""
    value = str(url or '').strip()
    if not value:
        raise RouteError('代理地址不能为空')
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise RouteError('代理地址格式不对') from exc
    if parts.scheme not in SCHEMES:
        raise RouteError('代理地址要以 http:// https:// socks5:// 或 socks5h:// 开头')
    if not parts.hostname:
        raise RouteError('代理地址缺少主机名')
    if not port:
        raise RouteError('代理地址缺少端口')
    netloc = f'{_quote_userinfo(parts)}@{parts.hostname}' if parts.username is not None \
        else parts.hostname
    if port != {'http': 80, 'https': 443}.get(parts.scheme):
        netloc = f'{netloc}:{port}'
    return urlunsplit((parts.scheme, netloc, '', '', ''))


def _quote_userinfo(parts) -> str:  # noqa: ANN001 —— urlsplit 结果
    """用户名 / 密码按 URL 规则转义：不转义的话特殊字符会让拼回去的地址变样。"""
    user = quote(unquote(parts.username or ''), safe='')
    if not parts.password:
        return user
    return f'{user}:{quote(unquote(parts.password), safe="")}'


def mask_url(url: str) -> str:
    """脱敏：保留协议、用户名、主机与端口，密码换成 `***`。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return MASK
    if not parts.password:
        return url
    user = quote(unquote(parts.username or ''), safe='')
    netloc = f'{user}:{MASK}@{parts.hostname}'
    if parts.port:
        netloc = f'{netloc}:{parts.port}'
    return urlunsplit((parts.scheme, netloc, '', '', ''))


def describe(name: str, url: str) -> dict:
    """给界面用的一条线路（凭据已脱敏）。"""
    try:
        parts = urlsplit(url)
        scheme, host, port = parts.scheme, parts.hostname or '', parts.port
        has_auth = bool(parts.password)
    except ValueError:
        scheme, host, port, has_auth = '', '', None, False
    return {
        'name': name,
        'scheme': scheme,
        'host': host,
        'port': port,
        'has_auth': has_auth,
        'masked': mask_url(url),
        # 内网 / 本机地址当出口通常只在特殊部署里有意义（例如同机房的代理），
        # 标注一下，免得用户以为是「写错了」而反复改。
        'local': _is_local(host),
    }


def _is_local(host: str) -> bool:
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return host == 'localhost' or host.endswith(('.local', '.internal', '.lan'))
    return bool(addr.is_private or addr.is_loopback or addr.is_link_local)


def keep_secret(url: str, stored: str) -> str:
    """把 `***` 占位换回已知的那份密码（改地址不必重输密码）。

    传入的地址里密码是 `***` 或空、而 `stored`（已存的那份完整地址）有密码时，
    沿用后者；否则原样返回。线路表与面板设置两处都用它。
    """
    try:
        incoming = urlsplit(url)
    except ValueError:
        return url
    if incoming.password and incoming.password != MASK:
        return url
    if not stored:
        # 没存过：`***` 无从还原，直接当作没有密码。
        return url.replace(f':{MASK}@', '@')
    try:
        known = urlsplit(stored)
    except ValueError:
        return url
    if not known.password:
        return url.replace(f':{MASK}@', '@')
    user = quote(unquote(incoming.username or known.username or ''), safe='')
    secret = quote(unquote(known.password), safe='')
    host = incoming.hostname or known.hostname or ''
    port = incoming.port or known.port
    netloc = f'{user}:{secret}@{host}' + (f':{port}' if port else '')
    return urlunsplit((incoming.scheme or known.scheme, netloc, '', '', ''))


def list_routes() -> list[dict]:
    """线路列表（脱敏、按名字排序）。"""
    return [describe(name, url) for name, url in sorted(routes().items())]


def upsert(name: object, url: object, *, allow_overwrite: bool = True) -> dict:
    clean_name = validate_name(name)
    clean_url = normalize_url(keep_secret(str(url or ''), routes().get(clean_name, '')))
    cfg = _load()
    table = cfg.get('proxies') if isinstance(cfg.get('proxies'), dict) else {}
    if clean_name in table and not allow_overwrite:
        raise RouteError(f'线路 {clean_name!r} 已存在', 409)
    table[clean_name] = clean_url
    cfg['proxies'] = table
    _save(cfg)
    return describe(clean_name, clean_url)


def delete(name: object, *, auth_dir: Path | None = None,
           clear_bindings: bool = False) -> dict:
    """删除线路。仍被账号绑着时必须显式 `clear_bindings`（路由层用 force）。

    不默认清绑定的理由：绑定被悄悄清掉 = 那些账号的出口**换了**，而出口换了
    在风控眼里是特征变化 —— 这种事要让用户点过一次确认（界面上会把账号数说出来）。
    """
    clean_name = validate_name(name)
    table = routes()
    if clean_name not in table:
        raise RouteError(f'线路 {clean_name!r} 不存在', 404)
    bound = bindings(clean_name, auth_dir)
    if bound and not clear_bindings:
        names = '、'.join(a['nickname'] or a['uid'] for a in bound[:3])
        more = f' 等 {len(bound)} 台' if len(bound) > 3 else ''
        raise RouteError(f'仍有账号绑着这条线路（{names}{more}）——'
                         f'确认删除会把它们改回默认出口', 409)
    cfg = _load()
    table = cfg.get('proxies') if isinstance(cfg.get('proxies'), dict) else {}
    table.pop(clean_name, None)
    cfg['proxies'] = table
    _save(cfg)
    cleared = 0
    for account in bound:
        try:
            wb2api.set_account_proxy(account['file'], '', Path(account['dir']))
            cleared += 1
        except (OSError, ValueError):
            continue
    return {'name': clean_name, 'bound_cleared': cleared}


def rename(old: object, new: object, *, auth_dir: Path | None = None) -> dict:
    """改名：绑着它的账号一并改到新名字上（不改的话那些账号会直接失败）。"""
    clean_old = validate_name(old)
    clean_new = validate_name(new)
    if clean_old == clean_new:
        return {'name': clean_new, 'old_name': clean_old, 'bound_updated': 0}
    cfg = _load()
    table = cfg.get('proxies') if isinstance(cfg.get('proxies'), dict) else {}
    if clean_old not in table:
        raise RouteError(f'线路 {clean_old!r} 不存在', 404)
    if clean_new in table:
        raise RouteError(f'线路 {clean_new!r} 已存在', 409)
    table[clean_new] = table.pop(clean_old)
    cfg['proxies'] = table
    _save(cfg)
    updated = 0
    for account in bindings(clean_old, auth_dir):
        try:
            wb2api.set_account_proxy(account['file'], clean_new, Path(account['dir']))
            updated += 1
        except (OSError, ValueError):
            continue
    return {'name': clean_new, 'old_name': clean_old, 'bound_updated': updated}


def _auth_dirs(auth_dir: Path | None) -> list[Path]:
    """要扫的账号目录：指定分组就只扫它，否则扫**全部分组**。

    为什么默认跨分组：线路表本身是全局的（读的是同一份 config.json 的 `proxies`），
    而绑定关系散在各组的 auths 目录下 —— 只看默认分组会漏掉别的组里绑着它的账号，
    于是「删除线路」时那几台会留在一条不存在的线路上、请求直接失败。
    """
    if auth_dir is not None:
        return [auth_dir]
    from .. import upstreamsvc

    dirs: list[Path] = []
    for group in upstreamsvc.list_upstreams():
        raw = str(group.get('auth_dir') or '').strip()
        if raw:
            dirs.append(Path(raw))
    return dirs


def _accounts(auth_dir: Path | None) -> list[tuple[Path, dict]]:
    """(所在目录, 账号) 列表 —— 重绑时要写回**它自己那份目录**。"""
    out: list[tuple[Path, dict]] = []
    for directory in _auth_dirs(auth_dir):
        try:
            accounts = wb2api.list_auth_accounts(directory)
        except OSError:
            continue
        out.extend((directory, account) for account in accounts)
    return out


def bindings(name: str, auth_dir: Path | None = None) -> list[dict]:
    return [{'file': account['file'], 'uid': account['uid'],
             'nickname': account.get('nickname') or '', 'dir': str(directory)}
            for directory, account in _accounts(auth_dir)
            if str(account.get('proxy') or '') == name]


def bound_counts(auth_dir: Path | None = None) -> dict[str, int]:
    """线路名 → 绑定账号数（列表页一次算完，避免每行查一次目录）。"""
    out: dict[str, int] = {}
    for _directory, account in _accounts(auth_dir):
        bound = str(account.get('proxy') or '')
        if bound:
            out[bound] = out.get(bound, 0) + 1
    return out


def parse_import(text: str) -> tuple[dict[str, str], list[str]]:
    """批量导入：每行 `名字=地址`（也认 `名字 地址` 与 `名字: 地址`）。

    返回 (可导入的表, 逐行问题)。**逐行报问题而不是整批拒绝**：从别处粘贴时
    常混着空行、注释与重复条目，全拒会让人反复试。
    """
    ok: dict[str, str] = {}
    problems: list[str] = []
    for lineno, raw_line in enumerate(str(text or '').splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.match(r'^([^=\s:]+)\s*[:=]?\s*(\S.*)$', line)
        if not match:
            problems.append(f'第 {lineno} 行：认不出格式（应写成 名字=地址）')
            continue
        name, url = match.group(1), match.group(2).strip()
        try:
            clean_name = validate_name(name)
            clean_url = normalize_url(keep_secret(url, routes().get(clean_name, '')))
        except RouteError as exc:
            problems.append(f'第 {lineno} 行：{exc}')
            continue
        ok[clean_name] = clean_url
    if not ok and not problems:
        problems.append('没有可导入的内容')
    return ok, problems


def import_routes(text: str) -> dict:
    table, problems = parse_import(text)
    for name, url in table.items():
        upsert(name, url)
    return {'imported': len(table), 'problems': problems}


async def probe_url(proxy: str, *, timeout: float = 8.0,
                    check_url: str | None = None) -> dict:
    """经这个代理地址发一次轻请求（默认查出口 IP 与归属地）。

    探测失败原因**原样回给界面**：配住宅代理时最该确认的就是「出口到底落在哪」，
    而失败原因（连不上 / 认证失败 / 超时）恰恰是排查起点。
    """
    import httpx

    target = check_url or config.PROXY_PROBE_URL
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=timeout, trust_env=False) as client:
            resp = await client.get(target)
        elapsed = int((time.monotonic() - started) * 1000)
        if resp.status_code != 200:
            return {'ok': False, 'ms': elapsed, 'error': f'出口返回 HTTP {resp.status_code}'}
        data = resp.json() if 'json' in resp.headers.get('content-type', '') else {}
        return {
            'ok': True,
            'ms': elapsed,
            'exit_ip': str(data.get('ip') or data.get('query') or ''),
            'exit_info': ' · '.join(str(x) for x in (data.get('country'), data.get('city'),
                                                     data.get('org')) if x),
        }
    except Exception as exc:  # noqa: BLE001 —— 探测失败原因原样回给界面
        return {'ok': False, 'ms': int((time.monotonic() - started) * 1000),
                'error': f'{type(exc).__name__}: {exc}'}


async def probe_route(name: str, *, timeout: float = 8.0,
                      check_url: str | None = None) -> dict:
    """按线路名探测（界面列表里的「测试」按钮）。"""
    url = routes().get(validate_name(name))
    if not url:
        raise RouteError(f'线路 {name!r} 不存在', 404)
    return await probe_url(url, timeout=timeout, check_url=check_url)
