"""把 watchdog 路由与调度器接入 server/main.py（幂等）。

用于「文件复制」部署：只改 import、lifespan 的 start/stop 与 include_router，
其余代码一行不动。已打过补丁时直接返回，不会重复插入。
"""
from __future__ import annotations

import pathlib
import sys

path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else '/opt/workbuddy-manager/server/main.py')
src = path.read_text(encoding='utf-8')

if 'watchdog_router' in src:
    print('already patched')
    raise SystemExit(0)

old_import = """    proxies, settings, stats, system, tokens, upstreams,
)
from .services import accountlog, cooling, dailyplan, pgsync, renew, tasklog, taskrun
"""
new_import = """    proxies, settings, stats, system, tokens, upstreams, watchdog as watchdog_router,
)
from .services import (
    accountlog, cooling, dailyplan, pgsync, renew, tasklog, taskrun, watchdog,
)
"""
if old_import not in src:
    print('IMPORT ANCHOR NOT FOUND', file=sys.stderr)
    raise SystemExit(2)
src = src.replace(old_import, new_import, 1)

old_start = """    pgsync.start_scheduler()
"""
new_start = """    pgsync.start_scheduler()
    # 上游网页改版监控：抓结构指纹 -> 变化时 LLM 判读 / 查活动补偿 -> 邮件上报。
    # 默认关闭；邮件启用前会先探 SMTP 出口（出口不通就不给开）。
    watchdog.start_scheduler()
"""
if old_start not in src:
    print('START ANCHOR NOT FOUND', file=sys.stderr)
    raise SystemExit(2)
src = src.replace(old_start, new_start, 1)

old_stop = """        pgsync.stop_scheduler()
"""
new_stop = """        pgsync.stop_scheduler()
        watchdog.stop_scheduler()
"""
if old_stop not in src:
    print('STOP ANCHOR NOT FOUND', file=sys.stderr)
    raise SystemExit(2)
src = src.replace(old_stop, new_stop, 1)

old_router = "app.include_router(pgsync_router.router)\n"
new_router = "app.include_router(pgsync_router.router)\napp.include_router(watchdog_router.router)\n"
if old_router not in src:
    print('ROUTER ANCHOR NOT FOUND', file=sys.stderr)
    raise SystemExit(2)
src = src.replace(old_router, new_router, 1)

path.write_text(src, encoding='utf-8')
print('patched', path)
