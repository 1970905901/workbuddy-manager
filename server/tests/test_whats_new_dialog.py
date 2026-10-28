"""「本次更新」公告弹窗的源码级不变量。

弹窗本身是前端组件（无法在后端套件里渲染），但几条**用户可见的行为**可以
机械校验，且写错时的表现都很隐蔽：

1. 每个版本只弹一次（localStorage 记录已读版本号）——漏了就会每次刷新都弹；
2. 拿不到更新日志时不弹（弹窗不该成为新的报错来源）；
3. 只在主布局（已登录区域）挂载，登录页不弹；
4. 弹窗里必须带「使用边界」那段免责声明，且文案与 README 一致
   （两处各写一份必然漂移）。
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_ROOT = Path(__file__).resolve().parents[2]
_DIALOG = _ROOT / 'web' / 'components' / 'common' / 'layout' / 'WhatsNewDialog.tsx'
_LAYOUT = _ROOT / 'web' / 'app' / '(main)' / 'layout.tsx'
_LOCALES = _ROOT / 'web' / 'lib' / 'i18n' / 'locales'


class WhatsNewDialogTest(unittest.TestCase):
    def _src(self) -> str:
        self.assertTrue(_DIALOG.is_file(), f'缺少 {_DIALOG}')
        return _DIALOG.read_text(encoding='utf-8')

    def test_shows_once_per_version(self) -> None:
        src = self._src()
        self.assertIn("wb.whats-new.seen", src, '没有记录已读版本 —— 每次刷新都会弹')
        self.assertIn('readSeen() === res.current', src,
                      '判断条件必须是「已读版本 == 当前版本」')

    def test_silent_when_changelog_unavailable(self) -> None:
        src = self._src()
        self.assertIn('!res.available', src, '拿不到更新日志时应当不弹')
        self.assertIn('.catch(', src)

    def test_mounted_in_main_layout_only(self) -> None:
        layout = _LAYOUT.read_text(encoding='utf-8')
        self.assertIn('WhatsNewDialog', layout, '没有挂到主布局')
        login = _ROOT / 'web' / 'app' / 'login' / 'page.tsx'
        if login.is_file():
            self.assertNotIn('WhatsNewDialog', login.read_text(encoding='utf-8'),
                             '登录页不该弹公告')

    def test_carries_usage_boundary(self) -> None:
        src = self._src()
        self.assertIn("t('whatsNew.boundary')", src, '弹窗里没有使用边界声明')
        self.assertIn("t('whatsNew.disclaimerLink')", src, '没有指向完整免责声明')


class WhatsNewCopyTest(unittest.TestCase):
    """五种语言的公告文案必须齐全，且边界声明与 README 同义。"""

    REQUIRED = ('title', 'titleFallback', 'thanks', 'boundaryTitle',
                'boundary', 'disclaimerLink', 'fullChangelog',
                'starTitle', 'starBody', 'starLink')

    def test_star_block_links_to_the_repo(self) -> None:
        """2k Stars 模块必须同时给出仓库地址与 Star 入口，且都指向本仓库。"""
        src = _DIALOG.read_text(encoding='utf-8')
        self.assertIn('whatsNew.starTitle', src, '弹窗里没有 Star 里程碑')
        self.assertIn('whatsNew.starLink', src, '没有 Star 按钮')
        self.assertIn('https://github.com/ithtelab/workbuddy-manager', src)
        # 仓库地址只写一份常量：两处各写一份必然会漂移
        self.assertIn('const REPO_URL', src)
        self.assertIn('stargazers', src, 'Star 按钮应直达 stargazers 页')

    def test_version_match_ignores_v_prefix(self) -> None:
        """`/api/system/changelog` 的 current 是 `v2.11.1`，CHANGELOG 段标题是
        `[2.11.1]` —— 严格相等会让弹窗一条更新内容都不显示。"""
        src = _DIALOG.read_text(encoding='utf-8')
        self.assertNotIn('v.version === data?.current', src,
                         '版本号必须去掉 v 前缀再比（与 ChangelogPanel 同口径）')
        self.assertIn("replace(/^v/i", src, '没有对版本号做 v 前缀归一')
        self.assertIn('!v.unreleased', src, '「未发布」段不该被当成当前版本')

    def test_repo_links_are_external_safe(self) -> None:
        src = _DIALOG.read_text(encoding='utf-8')
        self.assertGreaterEqual(src.count('rel="noreferrer"'), 3,
                                '站外链接必须带 rel=noreferrer')

    def test_all_locales_have_keys(self) -> None:
        import json
        for name in ('zh-CN', 'zh-TW', 'en', 'ja', 'ko'):
            data = json.loads((_LOCALES / f'{name}.json').read_text(encoding='utf-8'))
            block = data.get('whatsNew')
            self.assertIsInstance(block, dict, f'{name} 缺 whatsNew 段')
            for key in self.REQUIRED:
                self.assertTrue(str(block.get(key) or '').strip(),
                                f'{name}.whatsNew.{key} 为空')

    def test_boundary_matches_readme_terms(self) -> None:
        """README 首屏与弹窗的四类禁止项必须一致，避免两处说法漂移。"""
        import json
        zh = json.loads((_LOCALES / 'zh-CN.json').read_text(encoding='utf-8'))
        boundary = zh['whatsNew']['boundary']
        self.assertIn('本人及亲属', boundary)
        for term in ('批量注册', '账号交易', '接码', '绕过平台风控', '盈利'):
            self.assertIn(term, boundary, f'边界声明漏了「{term}」')

    def test_no_duplicate_keys_after_insert(self) -> None:
        src = (_LOCALES / 'zh-CN.json').read_text(encoding='utf-8')
        self.assertEqual(src.count('"whatsNew"'), 1, 'whatsNew 段出现多次')


if __name__ == '__main__':
    unittest.main()
