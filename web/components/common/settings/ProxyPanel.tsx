'use client';

import {useCallback, useEffect, useState} from 'react';
import {Activity, Globe, Loader2, Pencil, Plus, Trash2, Upload} from 'lucide-react';
import {errText, proxyApi} from '@/lib/api';
import {useAuth} from '@/lib/auth-context';
import {useI18n} from '@/lib/i18n/provider';
import {notify} from '@/lib/toast';
import type {ProxyProbe, ProxyRoute, ProxySettings} from '@/lib/types';
import {ConfirmDialog} from '@/components/common/layout/ConfirmDialog';
import {EmptyState} from '@/components/common/layout/EmptyState';
import {Badge} from '@/components/ui/badge';
import {Button} from '@/components/ui/button';
import {Input} from '@/components/ui/input';
import {Label} from '@/components/ui/label';
import {Textarea} from '@/components/ui/textarea';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/animate-ui/radix/dialog';

/**
 * 「设置 → 代理」：命名线路表 + 全局出口代理。
 *
 * 为什么要有这一页：线路表以前只能手改上游 config.json（界面上有「出口线路」
 * 下拉，但下拉里的名字加不进去）；全局出口代理更是只有环境变量，容器里还得
 * 改 compose。两件事的说明见 docs/account-proxy-routes.md。
 *
 * 界面上的两条约定：
 *   · **凭据只进不出**：列表里显示的是脱敏串（`***`），编辑时保留它 = 不改密码；
 *   · 删除线路若仍有账号绑着，先说清有几台、会改成默认出口，再让人确认。
 */
export function ProxyPanel() {
  const {t} = useI18n();
  const {isAdmin} = useAuth();
  const [routes, setRoutes] = useState<ProxyRoute[]>([]);
  const [settings, setSettings] = useState<ProxySettings | null>(null);
  const [probes, setProbes] = useState<Record<string, ProxyProbe>>({});
  const [testing, setTesting] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState<{name: string; url: string} | null>(null);
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState<string | null>(null);
  const [form, setForm] = useState({name: '', url: ''});
  const [globalForm, setGlobalForm] = useState({http: '', update: ''});

  const load = useCallback(async () => {
    try {
      const [list, cfg] = await Promise.all([proxyApi.routes(), proxyApi.settings()]);
      setRoutes(list.routes);
      setSettings(cfg);
      setGlobalForm({
        http: cfg.http_configured ? cfg.http_proxy : '',
        update: cfg.update_configured ? cfg.update_proxy : '',
      });
    } catch (e) {
      notify.err(errText(e));
    }
  }, []);

  useEffect(() => {
    // 非管理员不拉列表：这两个接口都是 require_session_admin（脱敏串里的用户名
    // 是住宅代理的账号标识），拉了只会吃 403 并弹一条没有可行动信息的报错
    // —— 与「访问令牌」「数据备份」几个面板的处置一致。
    if (isAdmin) void load();
  }, [load, isAdmin]);

  async function submitRoute() {
    const name = form.name.trim();
    const url = form.url.trim();
    if (!url) {
      notify.warn(t('settings.proxyUrlRequired'));
      return;
    }
    setSaving(true);
    try {
      if (editing) {
        await proxyApi.update(editing.name, {url});
      } else {
        await proxyApi.create(name, url);
      }
      notify.ok(t('settings.proxySaved'));
      setEditing(null);
      setCreating(false);
      setForm({name: '', url: ''});
      await load();
    } catch (e) {
      notify.err(errText(e));
    } finally {
      setSaving(false);
    }
  }

  async function probe(name: string) {
    setTesting(name);
    try {
      const result = await proxyApi.probe(name);
      setProbes((prev) => ({...prev, [name]: result}));
      if (result.ok) {
        notify.ok(t('settings.proxyTestOk', {ms: String(result.ms)}),
                  result.exit_info || result.exit_ip || '');
      } else {
        notify.err(t('settings.proxyTestFail'), result.error || '');
      }
    } catch (e) {
      notify.err(errText(e));
    } finally {
      setTesting(null);
    }
  }

  async function remove(name: string, force: boolean) {
    try {
      const result = await proxyApi.remove(name, force);
      notify.ok(t('settings.proxyDeleted'), result.bound_cleared
        ? t('settings.proxyDeletedBound', {count: String(result.bound_cleared)}) : '');
      await load();
    } catch (e) {
      notify.err(errText(e));
    }
  }

  async function runImport() {
    if (!importing?.trim()) return;
    setSaving(true);
    try {
      const result = await proxyApi.import(importing);
      notify.ok(t('settings.proxyImportDone', {count: String(result.imported)}),
                result.problems.length
                  ? t('settings.proxyImportProblems', {count: String(result.problems.length)})
                  : '');
      if (result.problems.length) {
        // 逐行问题直接摆出来：整批拒绝会让人反复试（服务端也是逐行报问题）。
        notify.warn(t('settings.proxyImportTitle'), result.problems.slice(0, 3).join(' / '));
      }
      setImporting(null);
      await load();
    } catch (e) {
      notify.err(errText(e));
    } finally {
      setSaving(false);
    }
  }

  async function saveGlobal() {
    setSaving(true);
    try {
      const next = await proxyApi.saveSettings({
        http_proxy: globalForm.http.trim(),
        update_proxy: globalForm.update.trim(),
      });
      setSettings(next);
      setGlobalForm({
        http: next.http_configured ? next.http_proxy : '',
        update: next.update_configured ? next.update_proxy : '',
      });
      notify.ok(t('settings.proxySaved'));
    } catch (e) {
      notify.err(errText(e));
    } finally {
      setSaving(false);
    }
  }

  async function probeGlobal() {
    setTesting('__global__');
    try {
      const result = await proxyApi.probeGlobal();
      setProbes((prev) => ({...prev, __global__: result}));
      if (result.ok) {
        notify.ok(t('settings.proxyTestOk', {ms: String(result.ms)}),
                  result.exit_info || result.exit_ip || '');
      } else {
        notify.err(t('settings.proxyTestFail'), result.error || '');
      }
    } catch (e) {
      notify.err(errText(e));
    } finally {
      setTesting(null);
    }
  }

  if (!isAdmin) {
    return (
      <div className="rounded-[20px] bg-muted p-8 text-center text-sm">
        <div className="font-medium">{t('settings.proxyAdminRequired')}</div>
        <div className="mx-auto mt-2 max-w-[38rem] text-xs text-muted-foreground">
          {t('settings.proxyAdminRequiredDesc')}
        </div>
      </div>
    );
  }

  const sourceKey = settings?.source === 'panel' ? 'settings.proxyGlobalSourcePanel'
    : settings?.source === 'env' ? 'settings.proxyGlobalSourceEnv'
      : 'settings.proxyGlobalSourceNone';

  return (
    <div className="space-y-4">
      {/* ── 线路表 ── */}
      <div className="rounded-[20px] bg-muted p-4">
        <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
          <div className="text-sm font-medium">{t('settings.proxyRoutesTitle')}</div>
          {isAdmin && (
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" className="rounded-full"
                      onClick={() => setImporting('')}>
                <Upload className="mr-1.5 h-3.5 w-3.5" />
                {t('settings.proxyImport')}
              </Button>
              <Button size="sm" className="rounded-full"
                      onClick={() => {
                        setForm({name: '', url: ''});
                        setCreating(true);
                      }}>
                <Plus className="mr-1.5 h-3.5 w-3.5" />
                {t('settings.proxyAdd')}
              </Button>
            </div>
          )}
        </div>
        <div className="mb-3 text-[11px] leading-4 text-muted-foreground">
          {t('settings.proxyRoutesDesc')}
        </div>

        {routes.length === 0 ? (
          <EmptyState icon={Globe} title={t('settings.proxyEmpty')}
                      description={t('settings.proxyEmptyDesc')} />
        ) : (
          <div className="overflow-hidden rounded-2xl bg-background/60">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t('settings.proxyName')}</TableHead>
                  <TableHead>{t('settings.proxyUrl')}</TableHead>
                  <TableHead className="w-28">{t('settings.proxyBound')}</TableHead>
                  <TableHead className="w-40">{t('settings.proxyTest')}</TableHead>
                  {isAdmin && <TableHead className="w-24 text-right">{''}</TableHead>}
                </TableRow>
              </TableHeader>
              <TableBody>
                {routes.map((route) => {
                  const result = probes[route.name];
                  return (
                    <TableRow key={route.name}>
                      <TableCell className="text-xs font-medium">
                        <span className="flex items-center gap-1.5">
                          {route.name}
                          {route.local && (
                            <Badge variant="secondary" className="text-[10px]">
                              {t('settings.proxyLocalBadge')}
                            </Badge>
                          )}
                        </span>
                      </TableCell>
                      <TableCell className="max-w-[26rem] truncate font-mono text-[11px] text-muted-foreground"
                                 title={route.masked}>
                        {route.masked}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {route.bound
                          ? t('settings.proxyBoundCount', {count: String(route.bound)})
                          : t('settings.proxyBoundNone')}
                      </TableCell>
                      <TableCell className="text-[11px]">
                        {testing === route.name ? (
                          <span className="flex items-center gap-1.5 text-muted-foreground">
                            <Loader2 className="h-3 w-3 animate-spin" />
                            {t('settings.proxyTesting')}
                          </span>
                        ) : result ? (
                          <span className={result.ok ? 'text-emerald-600 dark:text-emerald-400'
                                                     : 'text-red-600 dark:text-red-400'}
                                title={result.exit_info || result.error || ''}>
                            {result.ok
                              ? t('settings.proxyTestResult', {ms: String(result.ms)})
                              : (result.error || t('settings.proxyTestFail'))}
                          </span>
                        ) : (
                          <Button variant="ghost" size="sm" className="h-7 rounded-full px-2 text-[11px]"
                                  onClick={() => void probe(route.name)}>
                            <Activity className="mr-1 h-3 w-3" />
                            {t('settings.proxyTestRun')}
                          </Button>
                        )}
                      </TableCell>
                      {isAdmin && (
                        <TableCell className="pr-3">
                          <div className="flex justify-end gap-1">
                            <Button variant="ghost" size="icon" className="h-7 w-7 rounded-md"
                                    title={t('common.edit')}
                                    onClick={() => {
                                      setForm({name: route.name, url: route.masked});
                                      setEditing({name: route.name, url: route.masked});
                                    }}>
                              <Pencil className="h-3.5 w-3.5" />
                            </Button>
                            {route.bound > 0 ? (
                              <ConfirmDialog destructive
                                             title={t('settings.proxyDeleteTitle', {name: route.name})}
                                             description={t('settings.proxyDeleteBoundWarning',
                                                            {count: String(route.bound)})}
                                             confirmText={t('settings.proxyDeleteForce')}
                                             onConfirm={() => remove(route.name, true)}
                                             trigger={
                                               <Button variant="ghost" size="icon"
                                                       className="h-7 w-7 rounded-md"
                                                       title={t('common.delete')}>
                                                 <Trash2 className="h-3.5 w-3.5" />
                                               </Button>
                                             } />
                            ) : (
                              <ConfirmDialog destructive
                                             title={t('settings.proxyDeleteTitle', {name: route.name})}
                                             description={t('settings.proxyDeleteDesc')}
                                             confirmText={t('common.delete')}
                                             onConfirm={() => remove(route.name, false)}
                                             trigger={
                                               <Button variant="ghost" size="icon"
                                                       className="h-7 w-7 rounded-md"
                                                       title={t('common.delete')}>
                                                 <Trash2 className="h-3.5 w-3.5" />
                                               </Button>
                                             } />
                            )}
                          </div>
                        </TableCell>
                      )}
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </div>

      {/* ── 全局出口代理 ── */}
      <div className="rounded-[20px] bg-muted p-4">
        <div className="mb-1 text-sm font-medium">{t('settings.proxyGlobalTitle')}</div>
        <div className="mb-3 text-[11px] leading-4 text-muted-foreground">
          {t('settings.proxyGlobalDesc')}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Input className="h-9 min-w-[16rem] flex-1 bg-background font-mono text-xs"
                 disabled={!isAdmin}
                 placeholder={t('settings.proxyGlobalPlaceholder')}
                 value={globalForm.http}
                 onChange={(e) => setGlobalForm({...globalForm, http: e.target.value})} />
          {isAdmin && (
            <Button className="rounded-full" disabled={saving} onClick={() => void saveGlobal()}>
              {t('common.save')}
            </Button>
          )}
          <Button variant="outline" className="rounded-full"
                  disabled={testing === '__global__'}
                  onClick={() => void probeGlobal()}>
            {testing === '__global__'
              ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              : <Activity className="mr-1.5 h-3.5 w-3.5" />}
            {t('settings.proxyTest')}
          </Button>
        </div>
        <div className="mt-2 space-y-1 text-[11px] leading-4 text-muted-foreground">
          <div>{t(sourceKey, {value: settings?.effective || ''})}</div>
          {probes.__global__ && (
            <div className={probes.__global__.ok
                            ? 'text-emerald-600 dark:text-emerald-400'
                            : 'text-red-600 dark:text-red-400'}>
              {probes.__global__.ok
                ? t('settings.proxyExit', {ms: String(probes.__global__.ms),
                                           where: probes.__global__.exit_info
                                             || probes.__global__.exit_ip || ''})
                : probes.__global__.error}
            </div>
          )}
        </div>
      </div>

      {/* ── 更新下载代理 ── */}
      <div className="rounded-[20px] bg-muted p-4">
        <div className="mb-1 text-sm font-medium">{t('settings.proxyUpdateTitle')}</div>
        <div className="mb-3 text-[11px] leading-4 text-muted-foreground">
          {t('settings.proxyUpdateDesc')}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Input className="h-9 min-w-[16rem] flex-1 bg-background font-mono text-xs"
                 disabled={!isAdmin}
                 placeholder="http://user:pass@host:7890"
                 value={globalForm.update}
                 onChange={(e) => setGlobalForm({...globalForm, update: e.target.value})} />
          {isAdmin && (
            <Button className="rounded-full" disabled={saving} onClick={() => void saveGlobal()}>
              {t('common.save')}
            </Button>
          )}
        </div>
        {settings?.env_update_proxy && (
          <div className="mt-2 text-[11px] text-muted-foreground">
            {t('settings.proxyUpdateEnv', {value: settings.env_update_proxy})}
          </div>
        )}
      </div>

      {/* ── 说明 ── */}
      <div className="rounded-[20px] bg-muted p-4 text-[11px] leading-5 text-muted-foreground">
        {t('settings.proxyScopeNote')}
      </div>

      {/* 新增 / 编辑线路 */}
      <Dialog open={creating || editing !== null}
              onOpenChange={(open) => {
                if (!open) {
                  setCreating(false);
                  setEditing(null);
                }
              }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {editing ? t('settings.proxyEdit') : t('settings.proxyAdd')}
            </DialogTitle>
            <DialogDescription>{t('settings.proxyDialogDesc')}</DialogDescription>
          </DialogHeader>
          <DialogBody className="space-y-3">
            <div className="space-y-1.5">
              <Label className="text-[11px] text-muted-foreground">{t('settings.proxyName')}</Label>
              <Input value={form.name} disabled={editing !== null}
                     onChange={(e) => setForm({...form, name: e.target.value})} />
            </div>
            <div className="space-y-1.5">
              <Label className="text-[11px] text-muted-foreground">{t('settings.proxyUrl')}</Label>
              <Input className="font-mono text-xs" value={form.url}
                     placeholder="socks5://user:pass@host:10000"
                     onChange={(e) => setForm({...form, url: e.target.value})} />
              <p className="text-[10px] leading-4 text-muted-foreground">
                {t('settings.proxyUrlHint')}
              </p>
            </div>
          </DialogBody>
          <DialogFooter>
            <Button variant="ghost" className="rounded-full"
                    onClick={() => {
                      setCreating(false);
                      setEditing(null);
                    }}>
              {t('common.cancel')}
            </Button>
            <Button className="rounded-full" disabled={saving} onClick={() => void submitRoute()}>
              {t('common.save')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 导入 */}
      <Dialog open={importing !== null} onOpenChange={(open) => !open && setImporting(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{t('settings.proxyImportTitle')}</DialogTitle>
            <DialogDescription>{t('settings.proxyImportDesc')}</DialogDescription>
          </DialogHeader>
          <DialogBody>
            <Textarea className="min-h-40 font-mono text-xs" value={importing ?? ''}
                      placeholder={t('settings.proxyImportPlaceholder')}
                      onChange={(e) => setImporting(e.target.value)} />
          </DialogBody>
          <DialogFooter>
            <Button variant="ghost" className="rounded-full" onClick={() => setImporting(null)}>
              {t('common.cancel')}
            </Button>
            <Button className="rounded-full" disabled={saving} onClick={() => void runImport()}>
              {t('settings.proxyImportRun')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
