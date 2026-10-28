'use client';

import {useEffect, useState} from 'react';
import {useRouter} from 'next/navigation';
import {Megaphone, Sparkles, X} from 'lucide-react';
import {Button} from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/animate-ui/radix/dialog';
import {systemApi} from '@/lib/api';
import {useI18n} from '@/lib/i18n/provider';
import {RichText} from '@/lib/i18n/rich-text';
import type {Changelog, ChangelogItem} from '@/lib/types';

/**
 * 「本次更新」公告弹窗。
 *
 * 为什么需要它：升级后用户打开面板，看不到这一版改了什么 —— 除非自己去翻
 * 「设置 → 更新日志」。而这一版常常包含**行为变化**（默认值、冷却时长、
 * 路径策略），不知道就会把新行为当成 bug。所以升级后主动弹一次。
 *
 * 三个边界：
 *   · 每个版本**只弹一次**（localStorage 记已读版本）。关掉后不再打扰，
 *     即使同一浏览器里反复刷新；
 *   · 只在**已登录**的面板里弹（挂在主布局，登录页不弹）；
 *   · 拿不到更新日志时不弹 —— 弹窗本身不该成为新的报错来源。
 */
const SEEN_KEY = 'wb.whats-new.seen';

/** 分类配色（与「设置 → 更新日志」同一套语义色） */
const SECTION_STYLE: Record<string, string> = {
  安全: 'bg-rose-500/15 text-rose-600 dark:text-rose-400',
  新增: 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400',
  修复: 'bg-amber-500/15 text-amber-600 dark:text-amber-400',
  改进: 'bg-sky-500/15 text-sky-600 dark:text-sky-400',
  说明: 'bg-muted text-muted-foreground',
  计划中: 'bg-violet-500/15 text-violet-600 dark:text-violet-400',
};

const SECTION_LABEL_KEYS: Record<string, string> = {
  安全: 'changelog.sectionSecurity',
  新增: 'changelog.sectionAdded',
  修复: 'changelog.sectionFixed',
  改进: 'changelog.sectionImproved',
  说明: 'changelog.sectionNotes',
  计划中: 'changelog.sectionPlanned',
};

function readSeen(): string {
  try {
    return window.localStorage.getItem(SEEN_KEY) || '';
  } catch {
    return '';
  }
}

function writeSeen(version: string): void {
  try {
    window.localStorage.setItem(SEEN_KEY, version);
  } catch {
    /* 写不进去只影响下次仍会弹，不影响阅读 */
  }
}

/** 行内 `**加粗**` / `` `代码` ``，与更新日志页同一套轻量渲染 */
function InlineText({text}: {text: string}) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean);
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          return <strong key={i} className="font-medium text-foreground">{part.slice(2, -2)}</strong>;
        }
        if (part.startsWith('`') && part.endsWith('`')) {
          return (
            <code key={i} className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]">
              {part.slice(1, -1)}
            </code>
          );
        }
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}

function ItemList({items}: {items: ChangelogItem[]}) {
  return (
    <ul className="space-y-1.5">
      {items.map((it, i) => (
        <li
          key={i}
          className={
            'flex gap-2 text-xs leading-5 text-muted-foreground'
            + (it.level > 0 ? ' ml-3.5 border-l border-border pl-2.5' : '')
          }
        >
          <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-muted-foreground/60" />
          <span className="min-w-0 flex-1 break-words"><InlineText text={it.text} /></span>
        </li>
      ))}
    </ul>
  );
}

export function WhatsNewDialog() {
  const {t} = useI18n();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<Changelog | null>(null);

  useEffect(() => {
    let alive = true;
    systemApi.changelog()
      .then((res) => {
        if (!alive || !res.available || !res.current) return;
        // 已读过这一版就不再弹；版本号变化即视为「刚升级」。
        if (readSeen() === res.current) return;
        setData(res);
        setOpen(true);
      })
      .catch(() => { /* 拿不到就不弹，不当成错误打扰用户 */ });
    return () => { alive = false; };
  }, []);

  function dismiss() {
    if (data?.current) writeSeen(data.current);
    setOpen(false);
  }

  function openFullChangelog() {
    dismiss();
    router.push('/settings/changelog');
  }

  // 只展示**当前版本**那一节：历史版本用户自己去「更新日志」看。
  const current = data?.versions?.find((v) => v.version === data?.current);
  const sections = current?.sections ?? [];

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) dismiss(); }}>
      <DialogContent className="max-h-[86vh] max-w-[620px] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="size-4 text-primary" />
            {data?.current
              ? t('whatsNew.title', {version: data.current})
              : t('whatsNew.titleFallback')}
          </DialogTitle>
          <DialogDescription className="pt-1 text-[12px] leading-relaxed">
            {t('whatsNew.thanks')}
          </DialogDescription>
        </DialogHeader>

        {sections.length > 0 && (
          <div className="space-y-3">
            {sections.map((section) => (
              <div key={section.title}>
                <div className="mb-1.5 flex items-center gap-2">
                  <span
                    className={
                      'rounded-full px-2 py-0.5 text-[10px] font-medium '
                      + (SECTION_STYLE[section.title] || 'bg-muted text-muted-foreground')
                    }
                  >
                    {SECTION_LABEL_KEYS[section.title]
                      ? t(SECTION_LABEL_KEYS[section.title])
                      : section.title}
                  </span>
                </div>
                <ItemList items={section.items} />
              </div>
            ))}
          </div>
        )}

        {/* 使用边界与免责声明：每次升级都重申一次，而不是只在 README 里躺着。
            文案与 README 首屏完全一致，避免两处说法漂移。 */}
        <div className="mt-1 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3">
          <div className="mb-1 flex items-center gap-1.5 text-[12px] font-medium text-amber-700 dark:text-amber-300">
            <Megaphone className="size-3.5" />
            {t('whatsNew.boundaryTitle')}
          </div>
          <p className="text-[11px] leading-relaxed text-amber-800 dark:text-amber-200">
            <RichText text={t('whatsNew.boundary')} />
          </p>
          <a
            href="https://github.com/ithtelab/workbuddy-manager#使用边界与免责声明"
            target="_blank"
            rel="noreferrer"
            className="mt-1.5 inline-block text-[11px] underline underline-offset-2 text-amber-700 hover:text-amber-800 dark:text-amber-300"
          >
            {t('whatsNew.disclaimerLink')}
          </a>
        </div>

        <DialogFooter>
          <Button variant="ghost" size="sm" className="rounded-full" onClick={dismiss}>
            <X className="mr-1 size-3.5" />
            {t('common.close')}
          </Button>
          <Button size="sm" className="rounded-full" onClick={openFullChangelog}>
            {t('whatsNew.fullChangelog')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
