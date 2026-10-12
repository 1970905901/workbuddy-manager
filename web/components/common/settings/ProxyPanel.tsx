'use client';

import {useCallback, useEffect, useMemo, useState} from 'react';
import {
  Activity,
  Copy,
  Globe,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  Users,
} from 'lucide-react';
import {errText, proxyApi} from '@/lib/api';
import {useAuth} from '@/lib/auth-context';
import {useI18n} from '@/lib/i18n/provider';
import {notify} from '@/lib/toast';
import type {ProxyProbe, ProxyRoute, ProxySettings, RouteAccount} from '@/lib/types';
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
 * 线路有两种模式，界面上要一眼看清：
 *   · **独享**：地址里带 `{account}` 会话模板 —— 每个绑定账号渲染出自己的会话名，
 *     住宅代理按会话给粘性 IP，等于「一个账号一个住宅 IP」；
 *   · **共享**：不带模板 —— 所有绑定账号用同一条会话，即**共用一个出口 IP**
 *     （省 IP 额度，但几个号被上游按 IP 聚类时容易互相关联，绑到 3 台给提示）。
 *
 * 池化：一个账号可以同时绑多条线路（勾选多条即是池），选择按权重分摊、并在粘性
 * 窗口内固定（服务端实现）。「换会话」是显式动作 —— 换 IP 是特征变化，不该由别的
 * 操作顺带发生。
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
  const [form, setForm] = useState({name: '', url: '', weight: '100'});
  const [globalForm, setGlobalForm] = useState({http: '', update: ''});
  const [query, setQuery] = useState('');
  const [sortBy, setSortBy] = useState<'name' | 'bound'>('name');
  const [bindFor, setBindFor] = useState<ProxyRoute | null>(null);
  const [bindRows, setBindRows] = useState<RouteAccount[]>([]);
  const [bindPicked, setBindPicked] = useState<Set<string>>(new Set());
  const [bindBusy, setBindBusy] = useState(false);

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
    // 是住宅代理的账号标识），拉了只会吃 403 并弹一条没有可行动信息的报错。
    if (isAdmin) void load();
  }, [load, isAdmin]);

  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const list = routes.filter((r) => !needle
      || r.name.toLowerCase().includes(needle)
      || r.masked.toLowerCase().includes(needle)
      || (r.bound_names || []).some((n) => n.toLowerCase().includes(needle)));
    return [...list].sort((a, b) => (sortBy === 'bound'
      ? b.bound - a.bound || a.name.localeCompare(b.name)
      : a.name.localeCompare(b.name)));
  }, [routes, query, sortBy]);

  async function submitRoute() {
    const name = form.name.trim();
    const url = form.url.trim();
    const weight = Number(form.weight || 100);
    if (!url) {
      notify.warn(t('settings.proxyUrlRequired'));
      return;
    }
    setSaving(true);
    try {
      if (editing) {
        await proxyApi.update(editing.name, {url, weight});
      } else {
        await proxyApi.create(name, url, weight);
      }
      notify.ok(t('settings.proxySaved'));
      setEditing(null);
      setCreating(false);
      setForm({name: '', url: '', weight: '100'});
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
      await load();
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

  async function openBindings(route: ProxyRoute) {
    setBindFor(route);
    setBindRows([]);
    setBindPicked(new Set());
    try {
      const body = await proxyApi.accounts(route.name);
      setBindRows(body.accounts);
      setBindPicked(new Set(body.accounts.filter((a) => a.bound).map((a) => a.file)));
    } catch (e) {
      notify.err(errText(e));
      setBindFor(null);
    }
  }

  async function refreshBindings(name: string) {
    const body = await proxyApi.accounts(name);
    setBindRows(body.accounts);
    setBindPicked(new Set(body.accounts.filter((a) => a.bound).map((a) => a.file)));
  }

  async function saveBindings() {
    if (!bindFor) return;
    setBindBusy(true);
    try {
      const before = new Set(bindRows.filter((a) => a.bound).map((a) => a.file));
      const add = [...bindPicked].filter((f) => !before.has(f));
      const drop = [...before].filter((f) => !bindPicked.has(f));
      if (add.length) await proxyApi.bind(bindFor.name, add, true);
      if (drop.length) await proxyApi.bind(bindFor.name, drop, false);
      notify.ok(t('settings.proxySaved'));
      setBindFor(null);
      await load();
    } catch (e) {
      notify.err(errText(e));
    } finally {
      setBindBusy(false);
    }
  }

  async function rotate(files: string[]) {
    if (!files.length) return;
    try {
      const result = await proxyApi.rotate(files);
      notify.ok(t('settings.proxyRotated', {count: String(result.updated)}),
                t('settings.proxyRotatedHint'));
      if (bindFor) await refreshBindings(bindFor.name);
      await load();
    } catch (e) {
      notify.err(errText(e));
    }
  }

  async function copyAddress(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      notify.ok(t('settings.proxyCopied'));
    } catch {
      notify.warn(t('settings.proxyCopied'), text);
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
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input className="h-8 w-44 rounded-full bg-background pl-8 text-xs"
                     value={query} placeholder={t('settings.proxySearch')}
                     onChange={(e) => setQuery(e.target.value)} />
            </div>
            <Button variant="outline" size="sm" className="rounded-full"
                    onClick={() => setSortBy(sortBy === 'name' ? 'bound' : 'name')}>
              {sortBy === 'name' ? t('settings.proxySortName') : t('settings.proxySortBound')}
            </Button>
            <Button variant="outline" size="sm" className="rounded-full"
                    onClick={() => setImporting('')}>
              <Upload className="mr-1.5 h-3.5 w-3.5" />
              {t('settings.proxyImport')}
            </Button>
            <Button size="sm" className="rounded-full"
                    onClick={() => {
                      setForm({name: '', url: '', weight: '100'});
                      setCreating(true);
                    }}>
              <Plus className="mr-1.5 h-3.5 w-3.5" />
              {t('settings.proxyAdd')}
            </Button>
          </div>
        </div>
        <div className="mb-3 text-[11px] leading-4 text-muted-foreground">
          {t('settings.proxyRoutesDesc')}
        </div>

        {shown.length === 0 ? (
          <EmptyState icon={Globe} title={t('settings.proxyEmpty')}
                      description={t('settings.proxyEmptyDesc')} />
        ) : (
          <div className="overflow-hidden rounded-2xl bg-background/60">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-24 text-xs">{t('settings.proxyColStatus')}</TableHead>
                  <TableHead className="text-xs">{t('settings.proxyName')}</TableHead>
                  <TableHead className="text-xs">{t('settings.proxyUrl')}</TableHead>
                  <TableHead className="w-56 text-xs">{t('settings.proxyBound')}</TableHead>
                  <TableHead className="w-24 pr-4 text-right text-xs">
                    {t('settings.proxyColActions')}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {shown.map((route) => {
                  const result = probes[route.name] || (route.probe
                    ? {ok: route.probe.ok, ms: route.probe.ms,
                       error: route.probe.error, exit_ip: route.probe.exit_ip}
                    : undefined);
                  return (
                    <TableRow key={route.name}>
                      <TableCell className="text-[11px]">
                        {testing === route.name ? (
                          <span className="flex items-center gap-1.5 text-muted-foreground">
                            <Loader2 className="h-3 w-3 animate-spin" />
                            {t('settings.proxyTesting')}
                          </span>
                        ) : result ? (
                          <span className={result.ok
                            ? 'flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400'
                            : 'flex items-center gap-1.5 text-red-600 dark:text-red-400'}
                                title={result.error || result.exit_ip || ''}>
                            <span className={`h-1.5 w-1.5 rounded-full ${
                              result.ok ? 'bg-emerald-500' : 'bg-red-500'}`} />
                            {result.ok ? `${result.ms} ms` : t('settings.proxyTestFail')}
                          </span>
                        ) : (
                          <span className="flex items-center gap-1.5 text-muted-foreground">
                            <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/40" />
                            {t('settings.proxyProbeNever')}
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="text-xs font-medium">
                        <span className="flex flex-wrap items-center gap-1.5">
                          {route.name}
                          <Badge variant="secondary" className="text-[10px]">
                            {route.mode === 'exclusive' ? t('settings.proxyModeExclusive')
                              : t('settings.proxyModeShared')}
                          </Badge>
                          {route.weight !== 100 && (
                            <Badge variant="outline" className="text-[10px]"
                                   title={t('settings.proxyWeightHint')}>
                              x{route.weight}
                            </Badge>
                          )}
                          {route.shared_warning && (
                            <Badge className="bg-amber-500/15 text-[10px] text-amber-700 dark:text-amber-300"
                                   title={t('settings.proxySharedWarning',
                                            {count: String(route.bound)})}>
                              {t('settings.proxySharedBadge')}
                            </Badge>
                          )}
                          {route.local && (
                            <Badge variant="secondary" className="text-[10px]">
                              {t('settings.proxyLocalBadge')}
                            </Badge>
                          )}
                        </span>
                      </TableCell>
                      <TableCell className="max-w-[22rem]">
                        <span className="flex items-center gap-1.5">
                          <span className="truncate font-mono text-[11px] text-muted-foreground"
                                title={route.session_preview || route.masked}>
                            {route.session_preview || route.masked}
                          </span>
                          <Button variant="ghost" size="icon" className="h-6 w-6 rounded-md"
                                  title={t('settings.proxyCopy')}
                                  onClick={() => void copyAddress(route.masked)}>
                            <Copy className="h-3 w-3" />
                          </Button>
                        </span>
                      </TableCell>
                      <TableCell>
                        <span className="flex flex-wrap items-center gap-1">
                          {(route.bound_names || []).map((n) => (
                            <Badge key={n} variant="secondary" className="text-[10px]">{n}</Badge>
                          ))}
                          {route.bound > (route.bound_names || []).length && (
                            <Badge variant="outline" className="text-[10px]">
                              +{route.bound - (route.bound_names || []).length}
                            </Badge>
                          )}
                          <Button variant="ghost" size="sm"
                                  className="h-6 rounded-full px-2 text-[10px]"
                                  onClick={() => void openBindings(route)}>
                            <Users className="mr-1 h-3 w-3" />
                            {route.bound ? t('settings.proxyManageBindings')
                              : t('settings.proxyNoBindings')}
                          </Button>
                        </span>
                      </TableCell>
                      <TableCell className="pr-4">
                        <div className="flex justify-end gap-1">
                          <Button variant="ghost" size="icon" className="h-7 w-7 rounded-md"
                                  title={t('settings.proxyTest')}
                                  disabled={testing === route.name}
                                  onClick={() => void probe(route.name)}>
                            <Activity className="h-3.5 w-3.5" />
                          </Button>
                          <Button variant="ghost" size="icon" className="h-7 w-7 rounded-md"
                                  title={t('common.edit')}
                                  onClick={() => {
                                    setForm({name: route.name, url: route.masked,
                                             weight: String(route.weight)});
                                    setEditing({name: route.name, url: route.masked});
                                  }}>
                            <Pencil className="h-3.5 w-3.5" />
                          </Button>
                          <ConfirmDialog destructive
                                         title={t('settings.proxyDeleteTitle', {name: route.name})}
                                         description={route.bound
                                           ? t('settings.proxyDeleteBoundWarning',
                                               {count: String(route.bound)})
                                           : t('settings.proxyDeleteDesc')}
                                         confirmText={route.bound
                                           ? t('settings.proxyDeleteForce') : t('common.delete')}
                                         onConfirm={() => remove(route.name, route.bound > 0)}
                                         trigger={
                                           <Button variant="ghost" size="icon"
                                                   className="h-7 w-7 rounded-md"
                                                   title={t('common.delete')}>
                                             <Trash2 className="h-3.5 w-3.5" />
                                           </Button>
                                         } />
                        </div>
                      </TableCell>
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
                 placeholder={t('settings.proxyGlobalPlaceholder')}
                 value={globalForm.http}
                 onChange={(e) => setGlobalForm({...globalForm, http: e.target.value})} />
          <Button className="rounded-full" disabled={saving} onClick={() => void saveGlobal()}>
            {t('common.save')}
          </Button>
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
                 placeholder="http://user:pass@host:7890"
                 value={globalForm.update}
                 onChange={(e) => setGlobalForm({...globalForm, update: e.target.value})} />
          <Button className="rounded-full" disabled={saving} onClick={() => void saveGlobal()}>
            {t('common.save')}
          </Button>
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
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>{editing ? t('settings.proxyEdit') : t('settings.proxyAdd')}</DialogTitle>
            <DialogDescription>{t('settings.proxyDialogDesc')}</DialogDescription>
          </DialogHeader>
          <DialogBody className="space-y-2.5">
            <div className="rounded-2xl bg-muted/40 px-3 py-2">
              <Label className="text-xs">{t('settings.proxyName')}</Label>
              <Input className="mt-1 h-8 bg-background" value={form.name}
                     disabled={editing !== null}
                     placeholder={t('settings.proxyNamePlaceholder')}
                     onChange={(e) => setForm({...form, name: e.target.value})} />
            </div>
            <div className="rounded-2xl bg-muted/40 px-3 py-2">
              <Label className="text-xs">{t('settings.proxyUrl')}</Label>
              <Input className="mt-1 h-8 bg-background font-mono text-xs" value={form.url}
                     placeholder="socks5://user_c_CN_city_Shanghai_s_{account}:pass@host:10000"
                     onChange={(e) => setForm({...form, url: e.target.value})} />
              <p className="mt-1 text-[10px] leading-4 text-muted-foreground">
                {t('settings.proxyTemplateHint')}
              </p>
              <p className="mt-0.5 text-[10px] leading-4 text-muted-foreground">
                {t('settings.proxyUrlHint')}
              </p>
            </div>
            <div className="rounded-2xl bg-muted/40 px-3 py-2">
              <Label className="text-xs">{t('settings.proxyWeight')}</Label>
              <Input className="mt-1 h-8 w-28 bg-background text-right tabular-nums"
                     value={form.weight}
                     onChange={(e) => setForm({...form, weight: e.target.value})} />
              <p className="mt-1 text-[10px] leading-4 text-muted-foreground">
                {t('settings.proxyWeightHint')}
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
            <div className="rounded-2xl bg-muted/40 px-3 py-2">
              <Textarea className="min-h-40 bg-background font-mono text-xs"
                        value={importing ?? ''}
                        placeholder={t('settings.proxyImportPlaceholder')}
                        onChange={(e) => setImporting(e.target.value)} />
            </div>
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

      {/* 绑定账号：勾选多条线路就是一个池 */}
      <Dialog open={bindFor !== null} onOpenChange={(open) => !open && setBindFor(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{t('settings.proxyBindingsTitle', {name: bindFor?.name || ''})}</DialogTitle>
            <DialogDescription>
              {bindFor?.mode === 'exclusive'
                ? t('settings.proxyBindingsDescExclusive')
                : t('settings.proxyBindingsDescShared')}
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="max-h-[26rem] space-y-1.5 overflow-y-auto">
            {bindRows.length === 0 ? (
              <div className="py-6 text-center text-xs text-muted-foreground">
                {t('settings.proxyBindingsEmpty')}
              </div>
            ) : bindRows.map((row) => (
              <div key={row.file}
                   className="flex items-center gap-3 rounded-xl bg-muted/40 px-3 py-1.5">
                <input type="checkbox" className="h-3.5 w-3.5" checked={bindPicked.has(row.file)}
                       onChange={(e) => {
                         const next = new Set(bindPicked);
                         if (e.target.checked) next.add(row.file); else next.delete(row.file);
                         setBindPicked(next);
                       }} />
                <span className="min-w-0 flex-1 truncate text-xs">
                  {row.nickname || row.uid}
                  <span className="ml-2 text-[10px] text-muted-foreground">
                    {row.routes.length ? row.routes.join(' · ') : t('settings.proxyNoBindings')}
                  </span>
                </span>
                {bindFor?.mode === 'exclusive' && (
                  <span className="font-mono text-[10px] text-muted-foreground"
                        title={t('settings.proxySessionHint')}>
                    {row.session_preview || t('settings.proxySessionNone')}
                  </span>
                )}
                <Button variant="ghost" size="sm" className="h-6 rounded-full px-2 text-[10px]"
                        disabled={bindBusy || !row.bound}
                        title={t('settings.proxyRotateHint')}
                        onClick={() => void rotate([row.file])}>
                  <RefreshCw className="mr-1 h-3 w-3" />
                  {t('settings.proxyRotate')}
                </Button>
              </div>
            ))}
          </DialogBody>
          <DialogFooter>
            <span className="mr-auto text-[11px] text-muted-foreground">
              {t('settings.proxyBindingsSelected', {count: String(bindPicked.size)})}
            </span>
            <Button variant="ghost" className="rounded-full" onClick={() => setBindFor(null)}>
              {t('common.cancel')}
            </Button>
            <Button className="rounded-full" disabled={bindBusy} onClick={() => void saveBindings()}>
              {t('common.save')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
