'use client';

import {useRef, useState} from 'react';
import {Upload, Loader2} from 'lucide-react';
import {Button} from '@/components/ui/button';
import {useT} from '@/lib/i18n/provider';
import {accountApi, errText} from '@/lib/api';
import {notify} from '@/lib/toast';

export function UploadAccountsButton({
  upstreamId,
  onSuccess,
}: {
  upstreamId?: number | null;
  onSuccess?: () => void;
}) {
  const t = useT();
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [overwrite, setOverwrite] = useState(false);

  async function upload(files: File[]) {
    if (!files.length || busy) return;
    setBusy(true);
    try {
      const result = await accountApi.upload(files, upstreamId, overwrite);
      if (result.added.length || result.overwritten.length) {
        notify.ok(
          t('accounts.uploadDone'),
          t('accounts.uploadDoneDetail', {
            added: result.added.length,
            overwritten: result.overwritten.length,
          }),
        );
        onSuccess?.();
      }
      if (result.rejected.length || result.failed.length) {
        notify.err(
          t('accounts.uploadPartial'),
          [...result.rejected, ...result.failed]
            .map((item) => `${item.file}: ${item.message}`)
            .join('\n'),
        );
      }
    } catch (error) {
      notify.err(errText(error));
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = '';
    }
  }

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept=".json,application/json"
        multiple
        className="hidden"
        onChange={(event) => void upload(Array.from(event.target.files ?? []))}
      />
      <div className="flex items-center gap-2">
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={overwrite}
            disabled={busy}
            onChange={(event) => setOverwrite(event.target.checked)}
          />
          {t('accounts.uploadAllowOverwrite')}
        </label>
        <Button
          size="sm"
          variant="outline"
          className="rounded-full"
          disabled={busy}
          onClick={() => inputRef.current?.click()}
          title={t('accounts.uploadJson')}
        >
          {busy ? <Loader2 className="animate-spin" /> : <Upload />}
          <span>{t('accounts.uploadJson')}</span>
        </Button>
      </div>
    </>
  );
}
