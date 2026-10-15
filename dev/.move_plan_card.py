#!/usr/bin/env python3
"""把「计划任务」卡片从账号页挪到任务页（用户反馈：它不是账号的事）。

顺带把它的 22 个文案键从 accounts.* 归到 tasks.*（本仓库的键按页面分组）。
"""
from __future__ import annotations

import collections
import json
import re
from pathlib import Path

REPO = Path('.')
CARD = REPO / 'web/components/common/accounts/RefreshPlanCard.tsx'
ACCOUNTS = REPO / 'web/app/(main)/accounts/page.tsx'
TASKS = REPO / 'web/app/(main)/tasks/page.tsx'
LOCALES = ['zh-CN', 'zh-TW', 'en', 'ja', 'ko']

# 1) 组件里的 t('accounts.X') → t('tasks.X')
t = CARD.read_text(encoding='utf-8')
keys = sorted(set(re.findall(r"t\('accounts\.([A-Za-z0-9_]+)'", t)))
assert keys, '没找到该组件的文案键'
print('归属的键:', len(keys), '个:', ' '.join(keys))
t2 = re.sub(r"t\('accounts\.", "t('tasks.", t)
CARD.write_text(t2, encoding='utf-8')
print('组件：键前缀已改为 tasks.')

# 2) 五语文案：把键挪到 tasks 段（保持语言文件里顺序稳定：插在 tasks 段末尾）
for loc in LOCALES:
    p = REPO / f'web/lib/i18n/locales/{loc}.json'
    data = json.loads(p.read_text(encoding='utf-8'), object_pairs_hook=collections.OrderedDict)
    src, dst = data['accounts'], data['tasks']
    moved = []
    for key in keys:
        if key in src:
            moved.append((key, src.pop(key)))
    for key, value in moved:
        assert key not in dst, (loc, key)
        dst[key] = value
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'  {loc}: 挪了 {len(moved)} 个键')

# 3) 账号页：拿掉这张卡
t = ACCOUNTS.read_text(encoding='utf-8')
old_import = "import {RefreshPlanCard} from '@/components/common/accounts/RefreshPlanCard';\n"
assert t.count(old_import) == 1
t = t.replace(old_import, '', 1)
m = re.search(r"\n *\{/\* 计划任务（令牌刷新计划）.*?\n *\{isAdmin && <RefreshPlanCard onRan=\{reloadAll\} />\}\n", t, re.S)
assert m, '账号页里没找到那张卡的 JSX'
t = t[:m.start()] + '\n' + t[m.end():]
ACCOUNTS.write_text(t, encoding='utf-8')
print('账号页：卡片与 import 已移除')

# 4) 任务页：放在「一键执行」面板之后（同一页里都是「任务」这件事）
t = TASKS.read_text(encoding='utf-8')
anchor = "import {PageSectionTabs} from '@/components/common/layout/PageSectionTabs';"
assert t.count(anchor) == 1, '任务页的 import 锚点变了'
t = t.replace(anchor, anchor + "\nimport {RefreshPlanCard} from '@/components/common/accounts/RefreshPlanCard';", 1)
anchor2 = "  const runner = isAdmin && (realm ?? 'cn') === 'cn' ? <TaskRunnerPanel /> : null;"
assert t.count(anchor2) == 1
t = t.replace(anchor2, anchor2 + """

  // 计划任务（令牌刷新计划 + 每日任务）：到点自动跑的东西放「任务」这一页才对 ——
  // 它跟「这个账号是谁」无关，只跟「什么时候做什么」有关（用户反馈：原先挂在账号页）
  const planCard = isAdmin ? <RefreshPlanCard /> : null;""", 1)
print('任务页：卡片已接入（planCard）')
TASKS.write_text(t, encoding='utf-8')
