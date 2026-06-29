#!/usr/bin/env python3
# Copyright    2026  Xiaomi Corp.        (authors:  Han Zhu)
#
# See ../LICENSE for clarification regarding multiple authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Extract raw story text from HTML pages or saved HTML files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class ExtractResult:
    title: str
    chapter_title: str
    content: str
    source: str
    method: str


REMOVE_TAGS = (
    "script",
    "style",
    "noscript",
    "iframe",
    "svg",
    "canvas",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
    "button",
    "input",
    "select",
)

REMOVE_ATTR_PARTS = (
    "ads",
    "advert",
    "comment",
    "share",
    "social",
    "navigation",
    "pagination",
    "chapter-nav",
)

CONTENT_SELECTORS = (
    '[itemprop="articleBody"]',
    ".entry-content",
    ".post-content",
    ".chapter-content",
    "#chapter-content",
    ".chapter-c",
    ".reading-content",
    ".story-content",
    ".article-content",
    ".content-post",
    ".post-body",
    ".single-content",
    "article",
    "main",
)

ANTI_BOT_PHRASES = (
    "trang web này sử dụng dịch vụ bảo mật",
    "xác minh bạn không phải là bot",
    "checking your browser",
    "verify you are human",
    "cloudflare",
    "access denied",
    "403 forbidden",
    "just a moment",
    "enable javascript",
)

NOISE_LINE_PHRASES = (
    "chương trước",
    "chương tiếp",
    "chương sau",
    "danh sách chương",
    "bình luận",
    "đăng nhập",
    "đăng ký",
    "màu nền",
    "phông chữ",
    "size chữ",
    "facebook",
    "share",
    "report",
    "quảng cáo",
)

ANTI_BOT_ERROR = (
    "Website đang chặn bot/crawler. Hãy mở trang bằng browser thật, "
    "lưu HTML sau khi load xong rồi dùng HTML file mode."
)


def extract_story_from_html(html: str, source: str | None = None) -> ExtractResult:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency beautifulsoup4. Install project dependencies first."
        ) from exc

    soup = BeautifulSoup(html or "", "lxml")
    _remove_noise_nodes(soup)

    body_text = soup.get_text("\n", strip=True)
    _raise_if_anti_bot(body_text)

    title = _first_text(soup, "h1") or _first_text(soup, "title")
    chapter_title = _first_text(soup, "h1") or _first_text(soup, "h2")

    content = _extract_from_preferred_selectors(soup)
    if not _is_good_content(content):
        content = _extract_from_paragraphs(soup)

    content = _clean_text(content)
    if not _is_good_content(content):
        raise ValueError("Không tìm thấy nội dung truyện đủ rõ trong HTML.")

    return ExtractResult(
        title=title,
        chapter_title=chapter_title,
        content=content,
        source=source or "",
        method="html",
    )


async def extract_story_from_url_async(url: str) -> ExtractResult:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency playwright. Install dependencies and run "
            "`python -m playwright install chromium` first."
        ) from exc

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ],
        )
        try:
            context = await browser.new_context(
                viewport={"width": 1365, "height": 900},
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                extra_http_headers={"Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8"},
            )
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            await wait_page_stable(page)
            await auto_scroll_to_bottom(page)

            body_text = await page.evaluate(
                "() => document.body ? document.body.innerText : ''"
            )
            _raise_if_anti_bot(body_text)

            html = await page.evaluate("() => document.documentElement.outerHTML")
            result = extract_story_from_html(html, source=url)
            result.method = "url"
            return result
        finally:
            await browser.close()


async def wait_page_stable(page) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass
    await page.wait_for_timeout(800)


async def auto_scroll_to_bottom(page) -> None:
    stable_count = 0
    last_height = 0

    for _ in range(80):
        state = await page.evaluate(
            """() => ({
                y: window.scrollY,
                h: window.innerHeight,
                sh: document.body ? document.body.scrollHeight : 0
            })"""
        )
        near_bottom = state["y"] + state["h"] >= state["sh"] - 5
        same_height = state["sh"] == last_height

        if near_bottom and same_height:
            stable_count += 1
        else:
            stable_count = 0

        if stable_count >= 3:
            break

        last_height = state["sh"]
        await page.evaluate("() => window.scrollBy(0, 800)")
        await page.wait_for_timeout(350)


def _remove_noise_nodes(soup) -> None:
    for node in soup.find_all(REMOVE_TAGS):
        node.decompose()

    for node in list(soup.find_all(True)):
        if getattr(node, "attrs", None) is None:
            continue
        attr_text = " ".join(
            [
                str(node.get("id", "")),
                " ".join(node.get("class", []))
                if isinstance(node.get("class"), list)
                else str(node.get("class", "")),
            ]
        ).lower()
        if any(part in attr_text for part in REMOVE_ATTR_PARTS):
            node.decompose()


def _extract_from_preferred_selectors(soup) -> str:
    best = ""
    for selector in CONTENT_SELECTORS:
        for node in soup.select(selector):
            text = _node_text(node)
            if len(text) > len(best):
                best = text
        if _is_good_content(best):
            return best
    return best


def _extract_from_paragraphs(soup) -> str:
    lines = []
    for p in soup.find_all("p"):
        text = _clean_line(p.get_text(" ", strip=True))
        if len(text) >= 20 and not _is_noise_line(text):
            lines.append(text)
    return "\n\n".join(lines)


def _node_text(node) -> str:
    for br in node.find_all("br"):
        br.replace_with("\n")
    return node.get_text("\n", strip=True)


def _clean_text(text: str) -> str:
    paragraphs = []
    for raw_line in (text or "").splitlines():
        line = _clean_line(raw_line)
        if not line or _is_noise_line(line):
            continue
        paragraphs.append(line)
    return "\n\n".join(paragraphs)


def _clean_line(line: str) -> str:
    return re.sub(r"[ \t\u00a0]+", " ", line).strip()


def _is_noise_line(line: str) -> bool:
    low = line.lower().strip()
    if re.fullmatch(r"(https?://\S+|www\.\S+)", low):
        return True
    if re.fullmatch(r"[\*\s·\-_=~]{3,}", low):
        return True
    return any(phrase in low for phrase in NOISE_LINE_PHRASES) and len(low) <= 90


def _is_good_content(text: str) -> bool:
    return len((text or "").strip()) >= 200


def _raise_if_anti_bot(text: str) -> None:
    low = (text or "").lower()
    if any(phrase in low for phrase in ANTI_BOT_PHRASES):
        raise ValueError(ANTI_BOT_ERROR)


def _first_text(soup, selector: str) -> str:
    node = soup.select_one(selector)
    return _clean_line(node.get_text(" ", strip=True)) if node else ""
