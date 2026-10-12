"""会话模板 + 池化（P0/P1）的行为测试。

锁四件事：
  1. **独享**：带 `{account}` 的线路，每个账号渲染出自己的会话名（= 各自的住宅 IP）；
     **共享**：不带占位符的线路，所有绑定账号拿同一条出口；
  2. 会话名**存下来**：绑定后改昵称不会悄悄换掉出口 IP（要换就显式「换会话」）；
  3. **池化**：一个账号可以挂多条线路，按权重挑，且窗口内粘住同一条
     （一会儿换一个出口在风控眼里就是特征）；
  4. 绑定/解绑/换会话/改名/删除都只动该动的字段 —— 池里别的线路不能跟着丢。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import config, db, security
from server.routers import proxies
from server.services import proxyroutes as pr

ADMIN = {'username': 'admin', 'role': 'admin'}
EXCLUSIVE = 'socks5://demo_c_CN_city_Shanghai_s_{account}:pw@gw.example.dev:10000'
SHARED = 'socks5://demo_c_CN_city_Shanghai_s_fixed:pw@gw.example.dev:10000'


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.auth_dir = self.root / 'auths'
        self.auth_dir.mkdir()
        self.cfg = self.root / 'config.json'
        self._write_config({'px-sh': EXCLUSIVE, 'px-jp': SHARED})
        for name, value in (('AUTH_DIR', self.auth_dir), ('UPSTREAM_CONFIG', self.cfg),
                            ('PROXY_STICKY_SECONDS', 1800)):
            patch = mock.patch.object(config, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(config, 'DB_PATH', self.root / 'm.db')
        patch.start()
        self.addCleanup(patch.stop)
        db._conn = None
        db.connect()
        self.addCleanup(self._close_db)
        db.set_setting('proxy_sticky', {})

        self._account('workbuddy-a.json', 'uid-aaa', '小王')
        self._account('workbuddy-b.json', 'uid-bbb', '小李')

        app = FastAPI()
        app.include_router(proxies.router)
        app.dependency_overrides[security.current_user] = lambda: ADMIN
        app.dependency_overrides[security.require_session_admin] = lambda: ADMIN
        self.client = TestClient(app)

    def _close_db(self) -> None:
        if db._conn is not None:
            db._conn.close()
        db._conn = None

    def _write_config(self, routes: dict) -> None:
        self.cfg.write_text(json.dumps({'api_key': 'keep', 'proxies': routes},
                                       ensure_ascii=False, indent=2), encoding='utf-8')

    def _account(self, name: str, uid: str, nickname: str, **extra) -> None:
        doc = {'account': {'uid': uid, 'nickname': nickname}, 'auth': {'accessToken': 't'}}
        doc.update(extra)
        (self.auth_dir / name).write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')

    def _read(self, name: str) -> dict:
        return json.loads((self.auth_dir / name).read_text(encoding='utf-8'))


class SessionTemplateTest(_Base):
    def test_exclusive_route_renders_per_account_session(self) -> None:
        pr.set_bindings('px-sh', ['workbuddy-a.json', 'workbuddy-b.json'],
                        auth_dir=self.auth_dir)
        a = config.resolve_account_proxy(self._read('workbuddy-a.json'))
        b = config.resolve_account_proxy(self._read('workbuddy-b.json'))
        self.assertIn('_s_uid-aaa', a, a)   # 中文昵称 → 退回 uid 前缀（ASCII）
        self.assertIn('_s_uid-bbb', b, b)
        self.assertNotEqual(a, b, '独享线路的两个账号拿到了同一个会话')
        self.assertNotIn('{account}', a, '占位符没被替换')

    def test_session_survives_nickname_change(self) -> None:
        """绑定后改昵称不能换掉出口 IP —— 这正是把会话名存下来的原因。"""
        pr.set_bindings('px-sh', ['workbuddy-a.json'], auth_dir=self.auth_dir)
        before = config.resolve_account_proxy(self._read('workbuddy-a.json'))
        doc = self._read('workbuddy-a.json')
        doc['account']['nickname'] = '换了个名字'
        (self.auth_dir / 'workbuddy-a.json').write_text(json.dumps(doc, ensure_ascii=False),
                                                        encoding='utf-8')
        after = config.resolve_account_proxy(self._read('workbuddy-a.json'))
        self.assertEqual(before, after, '改昵称把出口 IP 悄悄换了')

    def test_shared_route_is_one_egress_for_all(self) -> None:
        pr.set_bindings('px-jp', ['workbuddy-a.json', 'workbuddy-b.json'],
                        auth_dir=self.auth_dir)
        a = config.resolve_account_proxy(self._read('workbuddy-a.json'))
        b = config.resolve_account_proxy(self._read('workbuddy-b.json'))
        self.assertEqual(a, b, '共享线路的两个账号出口不一致')
        self.assertIn('_s_fixed', a)

    def test_rotate_gives_a_new_session(self) -> None:
        pr.set_bindings('px-sh', ['workbuddy-a.json'], auth_dir=self.auth_dir)
        before = self._read('workbuddy-a.json')['proxy_session']
        result = pr.rotate_sessions(['workbuddy-a.json'], auth_dir=self.auth_dir)
        after = self._read('workbuddy-a.json')['proxy_session']
        self.assertEqual(result['updated'], 1)
        self.assertNotEqual(before, after, '换会话没有换掉会话名')
        self.assertTrue(after.startswith(before.split('-')[0]), f'新会话名不可读：{after}')

    def test_invalid_binding_raises_instead_of_falling_back(self) -> None:
        self._account('workbuddy-c.json', 'uid-ccc', '小张', proxy='does-not-exist')
        with self.assertRaises(ValueError):
            config.resolve_account_proxy(self._read('workbuddy-c.json'))


class SessionNamingTest(_Base):
    def test_ascii_nickname_becomes_the_session(self) -> None:
        """昵称是 ASCII 时按昵称派生（好看好认）；中文昵称退回 uid 前缀。"""
        self._account('workbuddy-en.json', 'uid-en-1', 'Xiao Wang')
        self.assertEqual(config.route_session_name({'nickname': 'Xiao Wang',
                                                    'uid': 'uid-en-1'}),
                         'xiao-wang')
        self.assertEqual(config.route_session_name({'nickname': '小王',
                                                    'uid': 'uid-aaa'}), 'uid-aaa')


class PoolTest(_Base):
    def _bind_pool(self, names: list[str], file: str = 'workbuddy-a.json') -> None:
        doc = self._read(file)
        doc['proxy'] = names[0]
        doc['proxy_pool'] = names
        (self.auth_dir / file).write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')

    def test_pool_picks_by_weight(self) -> None:
        self._write_config({'heavy': SHARED, 'light': SHARED.replace('fixed', 'other')})
        self._bind_pool(['heavy', 'light'])
        account = self._read('workbuddy-a.json')
        picks = [config.resolve_account_proxy(account) for _ in range(1)]
        # 窗口内会粘住第一次的选择；把窗口关掉才看得到概率分布
        with mock.patch.object(config, 'PROXY_STICKY_SECONDS', 0):
            db.set_setting('proxy_sticky', {})
            weights = {'heavy': 1000, 'light': 1}
            with mock.patch.object(config, 'proxy_route_weights', return_value=weights):
                counts = {'heavy': 0, 'light': 0}
                for _ in range(200):
                    db.set_setting('proxy_sticky', {})
                    url = config.resolve_account_proxy(account)
                    counts['heavy' if 'fixed' in url else 'light'] += 1
        self.assertTrue(picks)
        self.assertGreater(counts['heavy'], 150, f'权重没起作用：{counts}')

    def test_pool_choice_sticks_within_window(self) -> None:
        self._write_config({'r1': SHARED, 'r2': SHARED.replace('fixed', 'two')})
        self._bind_pool(['r1', 'r2'])
        account = self._read('workbuddy-a.json')
        first = config.resolve_account_proxy(account)
        for _ in range(5):
            self.assertEqual(config.resolve_account_proxy(account), first,
                             '粘性窗口内换了出口 —— 风控里这就是特征')

    def test_unreachable_route_is_reported_loudly(self) -> None:
        self._bind_pool(['ghost'])
        with self.assertRaises(ValueError) as ctx:
            config.resolve_account_proxy(self._read('workbuddy-a.json'))
        self.assertIn('ghost', str(ctx.exception))


class AutoAssignTest(_Base):
    """「自动分配 N 台」：挑人顺序不能漂移，否则每次挑到的一批不一样（= 换出口）。"""

    def _many(self, n: int, *, bound: str = '') -> None:
        for i in range(n):
            name = f'workbuddy-m{i:02d}.json'
            self._account(name, f'uid-m{i:02d}', f'账号{i:02d}', **({'proxy': bound} if bound else {}))

    def test_prefers_unbound_then_least_bound_and_is_stable(self) -> None:
        self._many(6)                                  # 6 台未绑定（账号00..05）
        # 两台已经绑了别的线路：其中一台还绑了两条 —— 它们应当排在未绑定之后
        self._account('workbuddy-m06.json', 'uid-m06', '账号06', proxy='px-jp')
        self._account('workbuddy-m07.json', 'uid-m07', '账号07', proxy='px-jp')
        self._account('workbuddy-m08.json', 'uid-m08', '账号08')
        pr.set_bindings('px-sh', ['workbuddy-m08.json'], auth_dir=self.auth_dir)
        pr.set_bindings('px-jp', ['workbuddy-m08.json'], auth_dir=self.auth_dir)

        first = pr.auto_assign('px-sh', 3, auth_dir=self.auth_dir)
        self.assertEqual(first['requested'], 3)
        picked = {row['file'] for row in pr.account_rows(self.auth_dir)
                  if 'px-sh' in row['routes']}
        # 三台候选应当是「未绑定里按昵称排前」的三个，而不是已绑过线路的那几台
        self.assertIn('workbuddy-a.json', picked)
        self.assertIn('workbuddy-b.json', picked)
        self.assertIn('workbuddy-m00.json', picked)
        self.assertNotIn('workbuddy-m06.json', picked, '优先挑了已绑定的账号')
        self.assertEqual(first['unbound_left'], 5, '未绑定剩余的台数不对')

        # 再分一批：应当接着同一顺序往下挑（挑到不同批次 = 给它们换了出口 IP）
        second = pr.auto_assign('px-sh', 2, auth_dir=self.auth_dir)
        self.assertEqual(second['updated'], 2)
        picked2 = ({row['file'] for row in pr.account_rows(self.auth_dir)
                    if 'px-sh' in row['routes']} - picked)
        self.assertEqual(picked2, {'workbuddy-m01.json', 'workbuddy-m02.json'},
                         '第二批没按同一顺序挑（顺序漂移就是给别的账号换了出口）')

    def test_count_is_capped_by_available_accounts(self) -> None:
        self._many(2)                                  # 只有 2 台可挑
        result = pr.auto_assign('px-sh', 10, auth_dir=self.auth_dir)
        self.assertEqual(result['requested'], 10)
        self.assertEqual(result['updated'], 4, '应把能挑的都挑上（含 fixture 的两台）')
        self.assertEqual(result['unbound_left'], 0)

    def test_exclusive_route_generates_sessions_for_auto_picked(self) -> None:
        self._many(2)
        pr.auto_assign('px-sh', 2, auth_dir=self.auth_dir)
        sessions = [row['session'] for row in pr.account_rows(self.auth_dir)
                    if 'px-sh' in row['routes']]
        self.assertTrue(all(sessions), f'自动分配没生成会话名：{sessions}')
        self.assertEqual(len(set(sessions)), len(sessions), '会话名重复（IP 会撞）')

    def test_invalid_count_and_missing_route(self) -> None:
        for bad in (0, -1, 100000):
            with self.subTest(count=bad):
                with self.assertRaises(pr.RouteError):
                    pr.auto_assign('px-sh', bad, auth_dir=self.auth_dir)
        with self.assertRaises(pr.RouteError):
            pr.auto_assign('ghost', 1, auth_dir=self.auth_dir)

    def test_endpoint_audits_and_reports(self) -> None:
        self._many(3)
        response = self.client.post('/api/proxy-routes/px-sh/auto-assign', json={'count': 3})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['updated'], 3)
        self.assertEqual(body['requested'], 3)
        rows = db.query("SELECT action, target FROM audit_logs ORDER BY id DESC LIMIT 1")
        self.assertEqual(rows[0]['action'], 'auto_assign_proxy_route')
        self.assertEqual(rows[0]['target'], 'px-sh')
        bad = self.client.post('/api/proxy-routes/px-sh/auto-assign', json={'count': 0})
        self.assertEqual(bad.status_code, 400)


class BindingApiTest(_Base):
    def test_batch_bind_and_unbind_only_touches_this_route(self) -> None:
        pr.set_bindings('px-jp', ['workbuddy-a.json'], auth_dir=self.auth_dir)
        self.assertEqual(self._read('workbuddy-a.json')['proxy'], 'px-jp')

        response = self.client.post('/api/proxy-routes/px-sh/accounts',
                                    json={'files': ['workbuddy-a.json', 'workbuddy-b.json'],
                                          'add': True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['updated'], 2)
        doc = self._read('workbuddy-a.json')
        self.assertEqual(doc['proxy_pool'], ['px-jp', 'px-sh'], '绑定没进池')
        self.assertTrue(doc.get('proxy_session'), '独享线路没给账号生成会话名')

        out = self.client.post('/api/proxy-routes/px-sh/accounts',
                               json={'files': ['workbuddy-a.json'], 'add': False}).json()
        self.assertEqual(out['updated'], 1)
        doc = self._read('workbuddy-a.json')
        self.assertEqual(doc['proxy'], 'px-jp', '解绑把别的线路也弄丢了')
        # 只剩一条时不必再写 proxy_pool（老字段就是单条）
        self.assertIn(doc.get('proxy_pool'), (None, ['px-jp']))

    def test_binding_drawer_lists_accounts(self) -> None:
        pr.set_bindings('px-sh', ['workbuddy-a.json'], auth_dir=self.auth_dir)
        body = self.client.get('/api/proxy-routes/px-sh/accounts').json()
        self.assertEqual(body['mode'], 'exclusive')
        by_file = {a['file']: a for a in body['accounts']}
        self.assertTrue(by_file['workbuddy-a.json']['bound'])
        self.assertFalse(by_file['workbuddy-b.json']['bound'])
        self.assertTrue(by_file['workbuddy-a.json']['session_preview'])
        self.assertNotIn('pw', json.dumps(body), '抽屉里泄露了代理密码')

    def test_rotate_endpoint_rotates_only_selected(self) -> None:
        pr.set_bindings('px-sh', ['workbuddy-a.json', 'workbuddy-b.json'],
                        auth_dir=self.auth_dir)
        before_b = self._read('workbuddy-b.json')['proxy_session']
        response = self.client.post('/api/proxy-routes/rotate',
                                    json={'files': ['workbuddy-a.json']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['updated'], 1)
        self.assertEqual(self._read('workbuddy-b.json')['proxy_session'], before_b,
                         '换会话影响到了没选中的账号')

    def test_weight_roundtrip(self) -> None:
        created = self.client.put('/api/proxy-routes/px-jp', json={'weight': 30}).json()
        self.assertEqual(created['weight'], 30)
        stored = json.loads(self.cfg.read_text(encoding='utf-8'))['proxies']['px-jp']
        self.assertEqual(stored, {'url': SHARED, 'weight': 30}, '权重没落盘成对象形态')
        listing = self.client.get('/api/proxy-routes').json()
        self.assertEqual({r['name']: r['weight'] for r in listing['routes']}['px-jp'], 30)
        bad = self.client.put('/api/proxy-routes/px-jp', json={'weight': 0})
        self.assertEqual(bad.status_code, 400)

    def test_rename_and_delete_follow_pool_membership(self) -> None:
        pr.set_bindings('px-jp', ['workbuddy-a.json'], auth_dir=self.auth_dir)
        pr.set_bindings('px-sh', ['workbuddy-a.json'], auth_dir=self.auth_dir)
        self.client.put('/api/proxy-routes/px-sh', json={'new_name': 'px-cn'})
        self.assertEqual(self._read('workbuddy-a.json')['proxy_pool'], ['px-jp', 'px-cn'],
                         '改名没有同步池里的引用')
        out = self.client.delete('/api/proxy-routes/px-cn?force=1').json()
        self.assertEqual(out['bound_cleared'], 1)
        self.assertEqual(self._read('workbuddy-a.json')['proxy_pool'], ['px-jp'],
                         '删除把池里别的线路带走了') if 'proxy_pool' in self._read(
            'workbuddy-a.json') else self.assertEqual(self._read('workbuddy-a.json')['proxy'],
                                                       'px-jp')

    def test_list_reports_mode_health_and_shared_warning(self) -> None:
        for name in ('workbuddy-a.json', 'workbuddy-b.json'):
            pr.set_bindings('px-jp', [name], auth_dir=self.auth_dir)
        self._account('workbuddy-c.json', 'uid-ccc', '小张')
        pr.set_bindings('px-jp', ['workbuddy-c.json'], auth_dir=self.auth_dir)
        pr.record_probe('px-jp', {'ok': False, 'ms': 12, 'error': 'ConnectError: boom'})
        # 独享线路的预览要能看出「每个号一个会话」，所以先给它绑一台账号
        pr.set_bindings('px-sh', ['workbuddy-a.json'], auth_dir=self.auth_dir)
        listing = self.client.get('/api/proxy-routes').json()
        by_name = {r['name']: r for r in listing['routes']}
        self.assertEqual(by_name['px-sh']['mode'], 'exclusive')
        self.assertIn('uid-*', by_name['px-sh']['session_preview'],
                      '独享线路没给出会话预览')
        self.assertEqual(by_name['px-jp']['mode'], 'shared')
        self.assertEqual(by_name['px-jp']['session_preview'], '',
                         '共享线路不该有「每个账号一个会话」的预览')
        self.assertTrue(by_name['px-jp']['shared_warning'], '三台共用没给提示')
        self.assertEqual(by_name['px-jp']['bound'], 3)
        self.assertFalse(by_name['px-jp']['probe']['ok'])
        self.assertEqual(by_name['px-jp']['probe']['error'], 'ConnectError: boom')


if __name__ == '__main__':
    unittest.main()
