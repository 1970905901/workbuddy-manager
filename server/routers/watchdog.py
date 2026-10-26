"""上游网页改版监控：配置、状态、立即运行、邮件出口检测、测试发信。

接口都挂在 `/api/watchdog` 下。**全部要求管理员**：状态里有账号昵称与收件
地址，配置里有 LLM base url 与打码后的凭据（打码仍能看出"配没配"）。写动作
与发信都写审计日志。

为什么「立即运行」不做成后台任务：一轮检测是「几次页面抓取 + 一两个账号的
只读查询」，正常几秒内结束。同步返回结果，界面能直接把差异与判读写出来，
不用再引入一套任务轮询。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import security
from ..iputil import client_ip
from ..services import watchdog

router = APIRouter(prefix='/api/watchdog', tags=['watchdog'])


@router.get('/config')
def get_config(user: dict = Depends(security.require_admin)) -> dict:
    return {'config': watchdog.public_config()}


@router.put('/config')
async def put_config(body: dict, request: Request,
                     user: dict = Depends(security.require_admin)) -> dict:
    try:
        result = await watchdog.save_config(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    cfg = result['config']
    security.audit(
        user, 'update_watchdog', '',
        f"enabled={cfg['enabled']} interval={cfg['interval_minutes']}m "
        f"targets={len(cfg['targets'])} llm={cfg['llm']['enabled']} "
        f"mail={cfg['mail']['enabled']}；来源 {client_ip(request)}",
        ip=client_ip(request),
    )
    return result


@router.get('/status')
def get_status(user: dict = Depends(security.require_admin)) -> dict:
    return watchdog.status()


@router.post('/run')
async def run_now(request: Request,
                  user: dict = Depends(security.require_admin)) -> dict:
    result = await watchdog.run_once(trigger='manual')
    security.audit(user, 'run_watchdog', '',
                   f"changed={','.join(result.get('changed') or []) or '无'}；"
                   f"来源 {client_ip(request)}", ip=client_ip(request))
    return result


@router.post('/baseline')
def reset_baseline(request: Request,
                   user: dict = Depends(security.require_admin)) -> dict:
    count = watchdog.reset_baselines()
    security.audit(user, 'reset_watchdog_baseline', '',
                   f'清掉 {count} 份快照；来源 {client_ip(request)}',
                   ip=client_ip(request))
    return {'ok': True, 'cleared': count,
            'message': '已清除基线，下次检测会重新建立基线且不告警'}


@router.post('/mail/egress')
async def check_egress(request: Request,
                       user: dict = Depends(security.require_admin)) -> dict:
    cfg = watchdog.load_config()
    mail = cfg['mail']
    if not mail.get('host'):
        raise HTTPException(status_code=400, detail='请先填写 SMTP 主机')
    result = await asyncio.to_thread(
        watchdog.probe_smtp_egress, mail['host'], mail['port'])
    watchdog._save_state(egress=result)  # noqa: SLF001 — 同模块状态，仅供界面展示
    security.audit(user, 'check_mail_egress', mail['host'],
                   f"port={mail['port']} ok={result['ok']}；来源 {client_ip(request)}",
                   ip=client_ip(request))
    return result


@router.post('/mail/test')
async def test_mail(request: Request,
                    user: dict = Depends(security.require_admin)) -> dict:
    cfg = watchdog.load_config()
    if not cfg['mail'].get('host') or not cfg['mail'].get('to'):
        raise HTTPException(status_code=400, detail='请先填好 SMTP 与收件人')
    result = await watchdog.test_mail(cfg)
    security.audit(user, 'test_watchdog_mail', cfg['mail'].get('host', ''),
                   f"ok={result.get('ok')}；来源 {client_ip(request)}",
                   ip=client_ip(request))
    return result


@router.post('/llm/test')
async def test_llm(request: Request,
                   user: dict = Depends(security.require_admin)) -> dict:
    cfg = watchdog.load_config()
    if not cfg['llm'].get('enabled'):
        raise HTTPException(status_code=400, detail='请先启用并配置 LLM')
    sample_diff = {
        'added': 3, 'removed': 1,
        'excerpt': ('--- before\n+++ after\n+TAG button class=claim-btn\n'
                    '+TEXT 限时活动：领取 500 积分\n-TAG div class=old-entry'),
        'truncated': False,
    }
    sample_hits = [{'word': '领取', 'line': 'TEXT 限时活动：领取 500 积分'}]
    try:
        verdict = await watchdog.ask_llm(
            cfg['llm'], {'name': 'sample', 'url': 'https://example.invalid/'},
            sample_diff, sample_hits)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f'LLM 调用失败: {exc}') from exc
    security.audit(user, 'test_watchdog_llm', str(cfg['llm'].get('model') or ''),
                   f"source={verdict.get('source')}；来源 {client_ip(request)}",
                   ip=client_ip(request))
    return {'ok': True, 'verdict': verdict}
