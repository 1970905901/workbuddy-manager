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
    """用户名 / 密码按 URL 规则转义：不转义的话特殊字符会让拼回去的地址变样。

    `{}` 要放进 safe：会话模板用的是 `{account}` 占位符，转义成 `%7Baccount%7D`
    就再也匹配不上了（症状是"每个账号都拿同一个会话"）。
    """
    user = quote(unquote(parts.username or ''), safe='{}')
    if not parts.password:
        return user
    return f'{user}:{quote(unquote(parts.password), safe="{}")}'


def mask_url(url: str) -> str:
    """脱敏：保留协议、用户名、主机与端口，密码换成 `***`。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return MASK
    if not parts.password:
        return url
    # `{}` 要保留：会话模板的占位符被转义成 %7Baccount%7D 之后，界面上显示的
    # 就是乱码，而且编辑线路时若原样提交会把占位符**改坏**（存成编码后的字面量）。
    user = quote(unquote(parts.username or ''), safe='{}')
    netloc = f'{user}:{MASK}@{parts.hostname}'
    if parts.port:
        netloc = f'{netloc}:{parts.port}'
    return urlunsplit((parts.scheme, netloc, '', '', ''))


def _is_local(host: str) -> bool:
    """内网 / 本机地址（界面上标一下：当出口通常只在特殊部署里有意义）。"""
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return host == 'localhost' or host.endswith(('.local', '.internal', '.lan'))
    return bool(addr.is_private or addr.is_loopback or addr.is_link_local)


def describe(name: str, url: str, *, weight: int = 100, session: str = '') -> dict:
    """给界面用的一条线路（凭据已脱敏）。

    `mode` 取独享 / 共享：带 `{account}` 的线路每个账号一个会话（= 一个住宅 IP），
    不带的就是所有绑定账号共用一条出口 —— 这是用户最需要一眼看清的区别。
    """
    try:
        parts = urlsplit(url)
        scheme, host, port = parts.scheme, parts.hostname or '', parts.port
        has_auth = bool(parts.password)
    except ValueError:
        scheme, host, port, has_auth = '', '', None, False
    exclusive = config.route_is_exclusive(url)
    return {
        'name': name,
        'scheme': scheme,
        'host': host,
        'port': port,
        'has_auth': has_auth,
        'masked': mask_url(url),
        'weight': int(weight),
        'mode': 'exclusive' if exclusive else 'shared',
        # 独享线路给一个渲染示例（用真实会话名脱敏后展示），让用户看出「每个号一个 IP」
        # 传的是**模板**（不是渲染后的地址）：mask_session 的活儿就是把
        # `{account}` 换成一个短会话名，渲染过就没有占位符可换了。
        'session_preview': (mask_session(url, session) if exclusive and session else ''),
        # 内网 / 本机地址当出口通常只在特殊部署里有意义（例如同机房的代理），
        # 标注一下，免得用户以为是「写错了」而反复改。
        'local': _is_local(host),
    }


def mask_session(template: str, session: str) -> str:
    """界面预览：把 `{account}` 换成短会话名、密码换成 `***`，**其余原样**。

    不能用 mask_url(渲染后的地址)：它会把用户名按 URL 规则重新转义，短名里的
    `*` 会变成 `%2A`，在界面上像乱码。这里只做字符串替换，别的字符一个不动。
    """
    if not session:
        return mask_url(template)
    short = (session[:4] + '*') if len(session) > 4 else session
    preview = template.replace(config.SESSION_PLACEHOLDER, short)
    if MASK in preview:
        return preview
    try:
        parts = urlsplit(preview)
    except ValueError:
        return preview
    if not parts.password:
        return preview
    return preview.replace(f':{parts.password}@', f':{MASK}@', 1)


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
    user = quote(unquote(incoming.username or known.username or ''), safe='{}')
    secret = quote(unquote(known.password), safe='{}')
    host = incoming.hostname or known.hostname or ''
    port = incoming.port or known.port
    netloc = f'{user}:{secret}@{host}' + (f':{port}' if port else '')
    return urlunsplit((incoming.scheme or known.scheme, netloc, '', '', ''))


def list_routes(auth_dir: Path | None = None) -> list[dict]:
    """线路列表（脱敏、按名字排序）：带权重、模式与绑定账号概览。"""
    weights = config.proxy_route_weights()
    accounts = account_rows(auth_dir)
    out: list[dict] = []
    for name, url in sorted(routes().items()):
        bound = [a for a in accounts if name in a['routes']]
        sample = next((a.get('session') or '' for a in bound if a.get('session')), '')
        item = describe(name, url, weight=weights.get(name, 100), session=sample)
        item['bound'] = len(bound)
        item['bound_names'] = [a['nickname'] or a['uid'] for a in bound[:3]]
        out.append(item)
    return out


def upsert(name: object, url: object, *, weight: object = None,
           allow_overwrite: bool = True) -> dict:
    clean_name = validate_name(name)
    raw_url = str(url or '')
    stored = routes().get(clean_name, '')
    if MASK in raw_url and not stored:
        # 没存过密码时 `***` 无从还原：与其存成一条「没密码」的记录（调用时才失败、
        # 报错还看不出原因），不如当场说清楚。
        raise RouteError('地址里还有 ***，请粘贴完整地址')
    clean_url = normalize_url(keep_secret(raw_url, stored))
    cfg = _load()
    table = cfg.get('proxies') if isinstance(cfg.get('proxies'), dict) else {}
    if clean_name in table and not allow_overwrite:
        raise RouteError(f'线路 {clean_name!r} 已存在', 409)
    clean_weight = _validate_weight(weight if weight is not None
                                   else (table.get(clean_name) or {}).get('weight')
                                   if isinstance(table.get(clean_name), dict) else None)
    # 权重 100 且原来是字符串写法时保持字符串：老配置文件改动最小，肉眼也好读。
    if clean_weight == 100 and not isinstance(table.get(clean_name), dict):
        table[clean_name] = clean_url
    else:
        table[clean_name] = {'url': clean_url, 'weight': clean_weight}
    cfg['proxies'] = table
    _save(cfg)
    return describe(clean_name, clean_url, weight=clean_weight)


def _validate_weight(weight: object) -> int:
    if weight in (None, ''):
        return 100
    try:
        value = int(weight)
    except (TypeError, ValueError) as exc:
        raise RouteError('权重必须是 1~1000 的整数') from exc
    if not 1 <= value <= 1000:
        raise RouteError('权重必须在 1~1000 之间（越大越常被选中）')
    return value


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
    for row in account_rows(auth_dir):
        if clean_name not in row['routes']:
            continue
        try:
            _write_routes(row['file'], Path(row['dir']),
                          [n for n in row['routes'] if n != clean_name])
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
    for row in account_rows(auth_dir):
        if clean_old not in row['routes']:
            continue
        try:
            _write_routes(row['file'], Path(row['dir']),
                          [clean_new if n == clean_old else n for n in row['routes']])
            updated += 1
        except (OSError, ValueError):
            continue
    return {'name': clean_new, 'old_name': clean_old, 'bound_updated': updated}


def _routes_of(account: dict) -> list[str]:
    """这个账号用到的全部线路（单条 `proxy` + 可选的 `proxy_pool`）。"""
    out: list[str] = []
    single = str(account.get('proxy') or '').strip()
    if single:
        out.append(single)
    pool = account.get('proxy_pool')
    if isinstance(pool, (list, tuple)):
        for item in pool:
            name = str(item).strip()
            if name and name not in out:
                out.append(name)
    return out


def account_rows(auth_dir: Path | None = None) -> list[dict]:
    """全部账号 + 它们的线路绑定（写绑定抽屉与列表概览用一次就够）。"""
    rows: list[dict] = []
    for directory, account in _accounts(auth_dir):
        routes_of = _routes_of(account)
        rows.append({
            'file': account['file'],
            'uid': account.get('uid') or '',
            'nickname': account.get('nickname') or '',
            'routes': routes_of,
            'primary': routes_of[0] if routes_of else '',
            'session': str(account.get('proxy_session') or ''),
            'dir': str(directory),
        })
    return rows


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


def _write_routes(filename: str, directory: Path, names: list[str],
                  session: str | None = None) -> None:
    """把账号的线路（单条或一组）写回账号文件。

    单条仍写老字段 `proxy`（与上游/文档的既有口径一致）；选了多条时第一条进
    `proxy`、整组进 `proxy_pool` —— 这样老版本上游只认 `proxy` 也不会读不懂。
    """
    wb2api.set_account_proxy(filename, names[0] if names else '', directory)
    path = directory / filename
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise RouteError(f'账号文件读取失败：{exc}') from exc
    if not isinstance(raw, dict):
        raise RouteError('账号文件格式异常')
    if len(names) > 1:
        raw['proxy_pool'] = names
    else:
        raw.pop('proxy_pool', None)
    if session is not None:
        if session:
            raw['proxy_session'] = session
        else:
            raw.pop('proxy_session', None)
    _atomic_write_json(path, raw)


def set_bindings(name: object, files: list[str], *, auth_dir: Path | None = None,
                 add: bool = True) -> dict:
    """把一批账号绑定到这条线路（或从池里摘掉）。

    绑定 = 这条线路进入该账号的线路组。**不会**动它已有的其它线路（池化意味着
    一个账号可以有多条），只有在池里一条都不剩时才写空。
    会话名：独享线路首次绑定会给账号生成一个（来自昵称），之后一直沿用 ——
    这样用户改昵称不会悄悄换掉出口 IP（要换就在界面上显式「换会话」）。
    """
    clean_name = validate_name(name)
    table = routes()
    if clean_name not in table:
        raise RouteError(f'线路 {clean_name!r} 不存在', 404)
    exclusive = config.route_is_exclusive(table[clean_name])
    wanted = {str(f) for f in files}
    changed = 0
    skipped: list[str] = []
    for row in account_rows(auth_dir):
        if row['file'] not in wanted:
            continue
        routes_now = list(row['routes'])
        if add:
            if clean_name not in routes_now:
                routes_now.append(clean_name)
        else:
            routes_now = [n for n in routes_now if n != clean_name]
        session = row['session'] or None
        if add and exclusive and not session:
            session = config.route_session_name({'nickname': row['nickname'],
                                                 'uid': row['uid']})
        try:
            _write_routes(row['file'], Path(row['dir']), routes_now, session)
            changed += 1
        except (OSError, ValueError, RouteError) as exc:  # noqa: BLE001
            skipped.append(f'{row["nickname"] or row["uid"]}: {exc}')
    return {'updated': changed, 'skipped': skipped, 'name': clean_name}


def rotate_sessions(files: list[str], *, auth_dir: Path | None = None) -> dict:
    """换会话（= 换出口 IP）：给这批账号生成新的会话名。

    为什么单独一个动作：会话名决定出口 IP，而它应该是**显式**变化的 —— 换 IP 在
    风控眼里是特征变化，不该由别的操作顺带发生。
    """
    import secrets as _secrets

    wanted = {str(f) for f in files}
    changed = 0
    out: list[dict] = []
    for row in account_rows(auth_dir):
        if row['file'] not in wanted:
            continue
        base = config.route_session_name({'nickname': row['nickname'], 'uid': row['uid']})
        fresh = f'{base}-{_secrets.token_hex(2)}'
        try:
            _write_routes(row['file'], Path(row['dir']), row['routes'], fresh)
            changed += 1
            out.append({'file': row['file'], 'session': fresh})
        except (OSError, ValueError, RouteError):
            continue
    return {'updated': changed, 'sessions': out}


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
    """哪些账号用着这条线路（池里挂着也算）。"""
    return [{'file': row['file'], 'uid': row['uid'], 'nickname': row['nickname'],
             'dir': row['dir']}
            for row in account_rows(auth_dir) if name in row['routes']]


def bound_counts(auth_dir: Path | None = None) -> dict[str, int]:
    """线路名 → 绑定账号数（列表页一次算完，避免每行查一次目录）。"""
    out: dict[str, int] = {}
    for row in account_rows(auth_dir):
        for name in row['routes']:
            out[name] = out.get(name, 0) + 1
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


def record_probe(name: str, result: dict) -> None:
    """把探测结果记进面板设置：列表刷新后仍能显示「上次测试」的状态。"""
    try:
        from .. import db
        table = db.get_setting('proxy_health', {}) or {}
        if not isinstance(table, dict):
            table = {}
        table[name] = {'ok': bool(result.get('ok')), 'ms': int(result.get('ms') or 0),
                       'at': int(time.time()),
                       'exit_ip': str(result.get('exit_ip') or ''),
                       'error': str(result.get('error') or '')[:200]}
        if len(table) > 500:
            keep = sorted(table.items(), key=lambda kv: int(kv[1].get('at') or 0),
                          reverse=True)[:500]
            table = dict(keep)
        db.set_setting('proxy_health', table)
    except Exception:            # noqa: BLE001 —— 旁路信息，写不进去不影响探测本身
        pass


def health() -> dict[str, dict]:
    """线路名 → 上次探测结果（没有记录就不出现在返回里）。"""
    try:
        from .. import db
        table = db.get_setting('proxy_health', {}) or {}
    except Exception:            # noqa: BLE001
        return {}
    return table if isinstance(table, dict) else {}


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
