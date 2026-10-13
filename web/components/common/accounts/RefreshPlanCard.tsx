'use client';

import {useCallback, useEffect, useState} from 'react';
import {CalendarClock, Loader2, Play, Save} from 'lucide-react';
import {accountApi, errText} from '@/lib/api';
import type {RefreshPlan} from '@/lib/types';
import {fmtDateTime} from '@/lib/format';
import {notify} from '@/lib/toast';
import {useT} from '@/lib/i18n/provider';
import {Button} from '@/components/ui/button';
import {Input} from '@/components/ui/input';
import {Label} from '@/components/ui/label';
import {Switch} from '@/components/ui/switch';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

/** 计划任务的 0-23 整点下拉项 */
const HOURS = Array.from({length: 24}, (_, h) => h);

/**
 * 令牌刷新计划（计划任务）：每 N 天自动全量刷新一次所有账号的 accessToken。
 *
 * 为什么要单独做这个卡：自动续期只在**剩余寿命 < 3 天**时动手，而用户想的是
 * 「不管剩几天，每隔 5 天就把全部号刷一遍」——两条路径的判据不同，不能合并。
 * 这里给它一个显式的开关 + 间隔 + 执行整点，并显示上次/下次执行时刻，让用户
 * 确认「真的排上了」，而不是只能信任一个开关。
 *
 * 「立即执行一次」与「一键刷新令牌」都真续期，区别只在并发与是否重载上游：
 * 见后端 `run_refresh_plan_now` 的说明。
 */
export function RefreshPlanCard({onRan}: {onRan?: () => void}) {
  const t = useT();
  const [plan, setPlan] = useState<RefreshPlan | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [days, setDays] = useState(5);
  const [hour, setHour] = useState(3);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);

  const apply = useCallback((p: RefreshPlan) => {
    setPlan(p);
    setEnabled(p.enabled);
    setDays(p.interval_days);
    setHour(p.hour);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      apply(await accountApi.refreshPlan());
    } catch (e) {
      notify.err(errText(e));
    } finally {
      setLoading(false);
    }
  }, [apply]);

  useEffect(() => {
    load();
  }, [load]);

  // 有未保存改动才允许点保存：避免「点了没反应」的困惑，也让「已生效」可判。
  const dirty =
    !!plan &&
    (plan.enabled !== enabled || plan.interval_days !== days || plan.hour !== hour);

  const save = useCallback(async () => {
    setSaving(true);
    try {
      apply(await accountApi.saveRefreshPlan(enabled, days, hour));
      notify.ok(t('accounts.planSaved'));
    } catch (e) {
      notify.err(errText(e));
    } finally {
      setSaving(false);
    }
  }, [apply, enabled, days, hour, t]);

  const runNow = useCallback(async () => {
    setRunning(true);
    try {
      const r = await accountApi.runRefreshPlan();
      const detail = t('accounts.planRunDetail', {ok: r.renewed, total: r.checked});
      if (r.failed === 0) {
        notify.ok(t('accounts.planRunDone'), detail);
      } else {
        notify.warn(t('accounts.planRunPartial', {failed: r.failed}), detail);
      }
      await load();
      onRan?.();
    } catch (e) {
      notify.err(errText(e));
    } finally {
      setRunning(false);
    }
  }, [load, onRan, t]);

  if (loading) {
    return (
      <section className="rounded-[20px] bg-muted px-3.5 py-3" aria-busy="true">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          {t('common.loading')}
        </div>
      </section>
    );
  }

  return (
    <section className="rounded-[20px] bg-muted px-3.5 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <CalendarClock className="h-4 w-4 shrink-0 text-muted-foreground" />
          <div className="min-w-0">
            <div className="text-sm font-medium">{t('accounts.planTitle')}</div>
            <div className="text-[11px] text-muted-foreground">{t('accounts.planDesc')}</div>
          </div>
        </div>
        <Switch
          checked={enabled}
          onCheckedChange={setEnabled}
          aria-label={t('accounts.planTitle')}
        />
      </div>

      <div className="mt-3 flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label className="text-[11px] text-muted-foreground">{t('accounts.planInterval')}</Label>
          <div className="flex items-center gap-1.5">
            <Input
              type="number"
              min={1}
              max={365}
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              className="h-8 w-20 bg-background"
              aria-label={t('accounts.planInterval')}
            />
            <span className="text-xs text-muted-foreground">{t('accounts.planDays')}</span>
          </div>
        </div>
        <div className="space-y-1">
          <Label className="text-[11px] text-muted-foreground">{t('accounts.planHour')}</Label>
          <Select value={String(hour)} onValueChange={(v) => setHour(Number(v))}>
            <SelectTrigger className="h-8 w-24 bg-background" aria-label={t('accounts.planHour')}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {HOURS.map((h) => (
                <SelectItem key={h} value={String(h)}>
                  {String(h).padStart(2, '0')}:00
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            className="rounded-full"
            disabled={!dirty || saving}
            onClick={save}
          >
            {saving ? <Loader2 className="animate-spin" /> : <Save />}
            {t('common.save')}
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="rounded-full"
            disabled={running}
            onClick={runNow}
            title={t('accounts.planRunNowTitle')}
          >
            {running ? <Loader2 className="animate-spin" /> : <Play />}
            {t('accounts.planRunNow')}
          </Button>
        </div>
      </div>

      <div className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
        <span>
          {t('accounts.planLast')}：
          {plan?.last_run ? fmtDateTime(plan.last_run) : t('accounts.planNever')}
        </span>
        <span>
          {t('accounts.planNext')}：
          {plan?.next_run ? fmtDateTime(plan.next_run) : t('accounts.planDisabled')}
        </span>
      </div>
    </section>
  );
}
