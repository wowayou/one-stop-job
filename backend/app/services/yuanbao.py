"""可选：用 Playwright 自动驱动腾讯元宝网页版，抓取它给出的公众号文章链接。

⚠️ 默认关闭（config.yaml wechat.yuanbao_automation.enabled=false）。元宝是 JS 重 SPA：
- 登录为微信/QQ 扫码，需一次性人工扫码；用持久化 user_data_dir 复用会话（会过期需重扫）。
- 选择器为混淆类名，可能随前端发版失效；下面的选择器是兜底猜测，按需调整。
- 自动化元宝涉及其 ToS 与账号风险，请仅本地、低频、人工触发。

本模块只负责「发问 → 抓 mp.weixin 链接」；抓正文/解析仍复用 WeChatPasteCollector。
playwright 为可选依赖（requirements-automation.txt），未安装时调用会抛 ImportError。
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

YUANBAO_URL = "https://yuanbao.tencent.com/"


def collect_yuanbao_links(auto_cfg: dict, prompt: str, *, headless: bool = False) -> list[str]:
    """打开元宝、发问、等回答稳定，抓取页面中的 mp.weixin 链接（去重）。"""
    from playwright.sync_api import sync_playwright  # 延迟 import：仅 opt-in 时需要

    from .wechat import extract_mp_links

    user_data_dir = auto_cfg.get("user_data_dir", "./data/.yuanbao")
    settle_seconds = float(auto_cfg.get("settle_seconds", 3))
    max_wait = float(auto_cfg.get("max_wait_seconds", 90))

    links: list[str] = []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(user_data_dir, headless=headless)
        page = ctx.new_page()
        try:
            page.goto(YUANBAO_URL, wait_until="domcontentloaded", timeout=60000)

            # 首次需人工扫码登录；持久化目录已登录则直接可用。
            # 输入框选择器为兜底猜测，可能需按当前元宝前端调整。
            box = page.locator("textarea, [contenteditable='true']").first
            box.wait_for(timeout=int(max_wait * 1000))
            box.click()
            try:
                box.fill(prompt)
            except Exception:
                page.keyboard.insert_text(prompt)
            page.keyboard.press("Enter")

            # 等待流式回答稳定：轮询页面内容长度连续两次不再增长即视为结束
            prev_len, stable = -1, 0
            deadline = time.time() + max_wait
            while time.time() < deadline:
                time.sleep(settle_seconds)
                cur_len = len(page.content())
                if cur_len == prev_len:
                    stable += 1
                    if stable >= 2:
                        break
                else:
                    stable = 0
                prev_len = cur_len

            # 抓链接：先取所有 <a href>，再对整页 HTML 正则兜底
            try:
                hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
                for href in hrefs:
                    links.extend(extract_mp_links(href))
            except Exception as exc:
                logger.warning("读取元宝页面 anchor 失败: %s", exc)
            links.extend(extract_mp_links(page.content()))
        finally:
            ctx.close()

    out: list[str] = []
    seen: set[str] = set()
    for link in links:
        if link not in seen:
            seen.add(link)
            out.append(link)
    logger.info("元宝自动化抓到 %d 个 mp.weixin 链接", len(out))
    return out
