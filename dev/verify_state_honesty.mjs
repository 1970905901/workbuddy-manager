/**
 * 「取不到 ≠ 没有」的浏览器断言（静态导出产物）。
 *
 * 安全页（PR #93）：
 *   A 全挂     → 整页「数据加载失败」+ 重试；**不**出现「暂无规则」，**不**渲染开关
 *   B 只 config 挂 → 策略卡说「未取到」，**一个开关都不渲染**，其余内容仍在
 *   C 恢复     → 规则与开关回来
 *
 * 任务记录页（PR #97）：
 *   D 慢加载   → 有骨架、**不**出现「暂无签到记录 / 暂无自动任务记录」
 *   E 签到记录挂 → 该面板说「签到记录加载失败」，**不**出现「共 0 条」，另一块照常
 *   F 恢复     → 两块内容与页脚都回来
 *
 * 密钥页（PR #100）：
 *   G 全挂     → 整页「数据加载失败」+ 重试；**不**出现「暂无 API 密钥」
 *   H 只密钥列表挂 → 顶部「部分数据加载失败」，**不**出现「暂无 API 密钥」
 *                    （手上这份列表不知道有没有，就不能说「没有」）
 *   I 只上游列表挂 → 顶部「部分数据加载失败」，空列表**照常**说「暂无 API 密钥」
 *                    （这一份确实取到了、确实为空。用它反过来证明 H 用的判据是
 *                     「**这一份**失败了」，而不是「有任一份失败」——后者会把一个
 *                     正常的空列表也藏起来）
 *   J 恢复     → 回到正常的空列表
 */
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const BASE = process.env.WB_BASE;
const PASS = process.env.WB_PASS;
const OUT = process.env.WB_SHOTS;

async function loadPlaywright() {
  const candidates = [
    ...(process.env.WB_PLAYWRIGHT ? [process.env.WB_PLAYWRIGHT] : []),
    path.join(process.env.TEMP || '/tmp', 'wb-i18n-verify', 'node_modules', 'playwright-core', 'index.js'),
    'playwright-core',
  ];
  for (const c of candidates) {
    try {
      const mod = await import(c.startsWith('/') || /^[A-Za-z]:/.test(c) ? pathToFileURL(c).href : c);
      return mod.chromium ?? mod.default?.chromium;
    } catch { /* 试下一个 */ }
  }
  throw new Error(`找不到 playwright-core（试过：${candidates.join(', ')}）`);
}

function chromiumExecutable() {
  const root = path.join(process.env.LOCALAPPDATA || '', 'ms-playwright');
  if (!fs.existsSync(root)) return undefined;
  const dir = fs.readdirSync(root)
    .filter((d) => d.startsWith('chromium-') && !d.includes('headless_shell'))
    .sort().pop();
  if (!dir) return undefined;
  const exe = path.join(root, dir, 'chrome-win64', 'chrome.exe');
  return fs.existsSync(exe) ? exe : undefined;
}

const findings = [];
const step = (ok, label, detail = '') => {
  console.log(`  ${ok ? '✓ ' : '✗ '} ${label}${detail ? `\n       ${detail}` : ''}`);
  if (!ok) findings.push(label + (detail ? `: ${detail}` : ''));
};

fs.mkdirSync(OUT, {recursive: true});
const chromium = await loadPlaywright();
const browser = await chromium.launch({executablePath: chromiumExecutable()});
// 断言全是中文文案，所以语言必须钉死：应用的 detectLocale() 会读 navigator.languages，
// 在浏览器语言不是中文的机器上，下面每一条正向断言都会失败——而页面其实完全正常。
// 更坏的是负向断言（「不许出现『暂无规则』」这类）照样是绿的，于是整份报告看起来像
// 「应用坏了」，方向完全错。钉住之后在哪台机器上跑结果都一样。
const ctx = await browser.newContext({viewport: {width: 1400, height: 1000}, locale: 'zh-CN'});
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));

await page.goto(`${BASE}/login`, {waitUntil: 'domcontentloaded'});
// 等表单真的被 React 接管再填。dev 冷编译时 hydration 会晚于首次输入：那时 fill
// 只改了 DOM，React 的受控 state 仍是空的，提交上去就是**空密码**。这个坑的表现
// 特别误导——登录失败后每一页都被弹回登录页，于是正向断言全红、负向断言全绿。
// React 会给它接管过的 DOM 节点挂一个 `__react*` 内部键，可当作 hydration 信号。
await page.waitForFunction(() => {
  const el = document.querySelector('#password');
  return !!el && Object.keys(el).some((k) => k.startsWith('__react'));
}, null, {timeout: 20000}).catch(() => {});
await page.fill('#username', 'admin');
await page.fill('#password', PASS);
await Promise.all([page.waitForURL(/dashboard/).catch(() => {}),
                   page.click('button[type=submit]')]);

// 没登录成功就立刻退出，别往下跑。后面三十多条断言都是「页面上应该有这句话」，
// 而未登录时每一页都会被弹回登录页 —— 于是正向断言全红、负向断言（「不许出现
// 『暂无规则』」这类）反而全绿，整份报告看起来像「应用坏了」，方向完全错。
if (!/dashboard/.test(page.url())) {
  console.error(`✗ 登录没成功（当前停在 ${page.url()}）——后面的断言没有意义，直接退出`);
  await browser.close();
  process.exit(1);
}

// 运行时开关：0=全放行 1=安全页全挂 2=只 config 挂 3=签到记录延迟 4=签到记录 500
//              5=密钥+上游全挂 6=只密钥列表挂 7=只上游列表挂
const MODE = {value: 0};
await page.route('**/api/**', async (route) => {
  const url = route.request().url();
  const fail = (code = 500) => route.fulfill({
    status: code, contentType: 'application/json',
    body: JSON.stringify({detail: '模拟后端故障'})});
  if (MODE.value === 1 && /\/api\/security/.test(url)) return fail();
  if (MODE.value === 2 && /\/api\/security\?|config/.test(url)
      && /\/api\/security/.test(url)) return fail();
  if (MODE.value === 3 && /\/api\/checkin-logs/.test(url)) {
    await new Promise((r) => setTimeout(r, 3500));
  }
  if (MODE.value === 4 && /\/api\/checkin-logs/.test(url)) return fail();
  // 结尾用 (\?|$) 锚住：`/api/keys/import/status` 这类子路径是另一份数据
  // （弹窗打开时才探测的配件），不该被这里的「列表挂了」一起打掉。
  if (MODE.value === 5 && /\/api\/(keys|upstreams)(\?|$)/.test(url)) return fail();
  if (MODE.value === 6 && /\/api\/keys(\?|$)/.test(url)) return fail();
  if (MODE.value === 7 && /\/api\/upstreams(\?|$)/.test(url)) return fail();
  return route.continue();
});

const bodyText = () => page.locator('body').innerText();
/** 取这一阶段新增的前端异常（用于把异常定位到具体步骤） */
let errMark = 0;
const phaseErrors = () => {
  const fresh = errors.slice(errMark);
  errMark = errors.length;
  return fresh;
};

// ══ 安全页 ══════════════════════════════════════════════════════════
console.log('\n安全页（PR #93）');
MODE.value = 1;
await page.goto(`${BASE}/security`, {waitUntil: 'load'});
await page.waitForTimeout(3500);
let text = await bodyText();
step(/数据加载失败/.test(text), '全部取不到时给整页错误态');
step(/重试/.test(text), '错误态里有「重试」');
step(!/暂无规则/.test(text), '不许说「暂无规则」（那等于说没有任何网段被放行或拦截）');
step(!/暂无访问记录/.test(text), '不许说「暂无访问记录」（那等于说没人被拦过）');
step((await page.locator('[role=switch]').count()) === 0,
     '一个开关都不渲染（把「不知道」显示成「关着」比空白更糟）',
     `页面上的开关数：${await page.locator('[role=switch]').count()}`);
await page.screenshot({path: `${OUT}/01-security-all-failed.png`, fullPage: true});

MODE.value = 2;
await page.reload({waitUntil: 'load'});
await page.waitForTimeout(3500);
text = await bodyText();
step(/未取到/.test(text), '只 config 没取到时，策略卡如实说「未取到」');
step((await page.locator('[role=switch]').count()) === 0, '仍然不渲染开关');
step(!/请检查网络连接或后端服务是否正常/.test(text),
     '其余数据取到了 → 不升级成整页错误（只有顶部那条常驻提示）',
     /部分数据加载失败/.test(text) ? '顶部有「部分数据加载失败」常驻提示' : '（没有常驻提示）');
step(phaseErrors().length === 0, '这一步没有前端异常', phaseErrors().join(' | '));
await page.screenshot({path: `${OUT}/02-security-config-missing.png`, fullPage: true});

MODE.value = 0;
await page.reload({waitUntil: 'load'});
await page.waitForTimeout(3000);
text = await bodyText();
step((await page.locator('[role=switch]').count()) > 0, '恢复后开关回来（正常态没有被搞坏）');
step(!/数据加载失败/.test(text) && !/未取到/.test(text), '正常态不出现错误态文案');
await page.screenshot({path: `${OUT}/03-security-ok.png`, fullPage: true});

// ══ 任务记录页 ══════════════════════════════════════════════════════
console.log('\n任务记录页（PR #97）');
MODE.value = 3;
await page.goto(`${BASE}/tasks`, {waitUntil: 'load'});
await page.waitForTimeout(1200);            // 数据还在路上（签到记录被延迟 3.5s）
text = await bodyText();
step(!/暂无签到记录/.test(text),
     '加载期不说「暂无签到记录」（看起来像账号从没签过到）',
     (text.match(/暂无[^\n]{0,10}/) || ['（无）'])[0]);
step(!/暂无自动任务记录/.test(text), '加载期不说「暂无自动任务记录」（看起来像采集器没在跑）');
await page.screenshot({path: `${OUT}/04-tasks-loading.png`, fullPage: true});
await page.waitForTimeout(4000);
text = await bodyText();
step(/共 \d+ 条/.test(text), '数据到位后两块正片与页脚都出来',
     (text.match(/共 \d+ 条/) || [''])[0]);

MODE.value = 4;
await page.reload({waitUntil: 'load'});
await page.waitForTimeout(4000);
text = await bodyText();
step(/签到记录加载失败/.test(text), '签到记录挂了：该面板说「加载失败」而不是「暂无」');
step(!/暂无签到记录/.test(text), '不说「暂无签到记录」');
// 两个面板各有自己的页脚（种子数据：签到 2 条 / 自动任务 1 条）。挂掉的那一块必须
// 连页脚一起收起来 —— 取不到时总数就是 0，「共 0 条」挂在「加载失败」下面等于自己
// 打自己；而另一块的 1 条必须照常留着（否则就是「整块都没了」而不是「如实收起」）。
step(!/共 2 条/.test(text),
     '挂掉的那一块连页脚一起收起来了（它自己的 2 条不该露面）',
     (text.match(/共 \d+ 条/g) || ['（没有页脚）']).join(' / '));
step(/共 1 条/.test(text), '另一块（自动任务）照常带着自己的页脚');
step(!/暂无签到记录/.test(text), '也不出现「共 0 条」式的空态文案');
step(!/数据加载失败/.test(text), '只有一块挂 → 不升级成整页错误');
await page.screenshot({path: `${OUT}/05-tasks-one-failed.png`, fullPage: true});

MODE.value = 0;
await page.reload({waitUntil: 'load'});
await page.waitForTimeout(3500);
text = await bodyText();
step(!/加载失败/.test(text) && /共 2 条/.test(text) && /共 1 条/.test(text),
     '恢复后两块内容与页脚都回来');

// ══ 密钥页 ══════════════════════════════════════════════════════════
console.log('\n密钥页（PR #100）');
MODE.value = 5;
await page.goto(`${BASE}/keys`, {waitUntil: 'load'});
await page.waitForTimeout(3500);
text = await bodyText();
step(/数据加载失败/.test(text), '全部取不到时给整页错误态');
step(/重试/.test(text), '错误态里有「重试」');
// 这一句是这一页最要紧的一条：密钥是**凭据**，说「暂无 API 密钥」读起来是
// 「我的密钥被删了」，用户会顺手点旁边的「新建密钥」重建一个 —— 于是建出重复密钥，
// 而重复密钥会让「按密钥限额 / 按密钥统计用量」全都对不上。
step(!/暂无 API 密钥/.test(text),
     '不许说「暂无 API 密钥」（那是「确实没有」，而事实是不知道有没有）',
     (text.match(/暂无[^\n]{0,12}/) || ['（无）'])[0]);
step(phaseErrors().length === 0, '这一步没有前端异常', phaseErrors().join(' | '));
await page.screenshot({path: `${OUT}/06-keys-all-failed.png`, fullPage: true});

MODE.value = 6;
await page.reload({waitUntil: 'load'});
await page.waitForTimeout(3500);
text = await bodyText();
step(/部分数据加载失败/.test(text), '只密钥列表没取到时，顶部给常驻提示');
step(!/请检查网络连接或后端服务是否正常/.test(text),
     '上游列表取到了 → 不升级成整页错误（只有顶部那条常驻提示）');
step(!/暂无 API 密钥/.test(text),
     '列表这一份没取到 → 不说「暂无 API 密钥」',
     (text.match(/暂无[^\n]{0,12}/) || ['（无）'])[0]);
// 同一句谎话的另一半：空状态挡住了，tab 上却还挂着「普通密钥 · 0」——列表取不到时
// `keys` 就是空的，那个 0 没有依据，读起来仍是「一个密钥都没有」。（任务记录页的
// 「共 0 条」是同一处，PR #97 才补上。）
step(!/普通密钥 · 0/.test(text),
     'tab 上的数字也一起收起来（否则等于说「一个密钥都没有」）',
     (text.match(/普通密钥[^\n]{0,8}/) || ['（无）'])[0]);
step(phaseErrors().length === 0, '这一步没有前端异常', phaseErrors().join(' | '));
await page.screenshot({path: `${OUT}/07-keys-list-failed.png`, fullPage: true});

MODE.value = 7;
await page.reload({waitUntil: 'load'});
await page.waitForTimeout(3500);
text = await bodyText();
step(/部分数据加载失败/.test(text), '只上游列表没取到时，同样给常驻提示');
step(/暂无 API 密钥/.test(text),
     '密钥列表这一份**取到了且确实为空** → 照常显示空态（判据是「这一份失败没」，'
     + '不是「有任一份失败」）');
step(/普通密钥 · 0/.test(text),
     '数字也照常显示 0 —— 这一份确实取到了、确实为空，那个 0 是如实的',
     (text.match(/普通密钥[^\n]{0,8}/) || ['（无）'])[0]);
step(!/请检查网络连接或后端服务是否正常/.test(text), '仍然不升级成整页错误');
step(phaseErrors().length === 0, '这一步没有前端异常', phaseErrors().join(' | '));
await page.screenshot({path: `${OUT}/08-keys-upstreams-failed.png`, fullPage: true});

MODE.value = 0;
await page.reload({waitUntil: 'load'});
await page.waitForTimeout(3000);
text = await bodyText();
step(/暂无 API 密钥/.test(text) && !/数据加载失败/.test(text),
     '恢复后回到正常的空列表');

step(errors.length === 0, '全程没有未捕获的前端异常', errors.slice(0, 2).join(' | '));

await browser.close();
console.log('\n=== 结果 ===');
if (findings.length) {
  console.log('FAILED:\n  - ' + findings.join('\n  - '));
  process.exit(1);
}
console.log('ALL CHECKS PASSED');
