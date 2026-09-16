#!/usr/bin/env python3
"""Recover the company's published GitBook sites into standalone static pages.

The historical custom domains no longer resolve publicly, but GitBook still serves
the company-owned published sites.  This script talks to GitBook's hosting edge
directly, extracts the server-rendered article content, localises images and
rewrites every documentation link to the restored GESIA domain structure.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import html
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
GITBOOK_IP = os.environ.get("GESIA_GITBOOK_IP", "172.64.147.209")
ASSET_DIR = ROOT / "common" / "resources" / "recovered-docs"
MAX_WORKERS = 8


@dataclass(frozen=True)
class Source:
    host: str
    prefix: str
    language: str
    section_label: str
    home_route: str | None


SOURCES = (
    Source("docs-en.gesia.io", "docs/en", "en", "Documentation", "introduction"),
    Source("docs-kr.gesia.io", "docs/kr", "ko", "기술 문서", "introduce"),
    Source(
        "carboncredit-docs-en.gesia.io",
        "carbon-credits/en",
        "en",
        "Carbon Credit Products",
        None,
    ),
    Source(
        "carboncredit-docs-kr.gesia.io",
        "carbon-credits/kr",
        "ko",
        "탄소 크레딧 제품",
        None,
    ),
)

SOURCE_BY_HOST = {source.host: source for source in SOURCES}
asset_lock = threading.Lock()
asset_paths: dict[str, str] = {}


def run_curl(url: str, *, host: str | None = None) -> bytes:
    command = [
        "curl",
        "--compressed",
        "-L",
        "--retry",
        "2",
        "--retry-delay",
        "1",
        "--max-time",
        "45",
        "-fsS",
    ]
    if host in SOURCE_BY_HOST:
        command.extend(["-k", "--resolve", f"{host}:443:{GITBOOK_IP}"])
    command.append(url)
    result = subprocess.run(command, check=True, capture_output=True)
    return result.stdout


def text_from_host(host: str, route: str) -> str:
    url = f"https://{host}/{route.lstrip('/')}"
    return run_curl(url, host=host).decode("utf-8", errors="replace")


def parse_index(source: Source) -> list[dict[str, str]]:
    llms = text_from_host(source.host, "/llms.txt")
    expression = re.compile(r"^- \[([^]]+)]\((https?://[^)]+\.md)\)(?::\s*(.*))?$", re.M)
    pages: list[dict[str, str]] = []
    seen: set[str] = set()
    for title, url, description in expression.findall(llms):
        parsed = urlparse(url)
        if parsed.hostname != source.host:
            continue
        route = unquote(parsed.path.lstrip("/")[:-3]).rstrip("/")
        if not route or route in seen:
            continue
        seen.add(route)
        pages.append({"title": title, "route": route, "description": description.strip()})
    if not pages:
        raise RuntimeError(f"No pages found for {source.host}")
    return pages


def page_url(source: Source, route: str) -> str:
    if source.home_route and route == source.home_route:
        return f"/{source.prefix}/"
    return f"/{source.prefix}/{quote(route, safe='/+._-')}/"


def map_link(href: str, current_source: Source) -> str:
    if not href:
        return href
    if href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return href

    parsed = urlparse(href)
    fragment = f"#{parsed.fragment}" if parsed.fragment else ""
    query = f"?{parsed.query}" if parsed.query else ""

    target_source = SOURCE_BY_HOST.get(parsed.hostname or "")
    if target_source:
        route = unquote(parsed.path.strip("/"))
        if route.endswith(".md"):
            route = route[:-3]
        if route in ("", "llms.txt"):
            return f"/{target_source.prefix}/{fragment}"
        return page_url(target_source, route) + query + fragment

    # The historical explorer was an operational application backed by the
    # original infrastructure.  Keep its addresses visible in the recovered
    # article text, but send clicks to an honest local status explanation
    # instead of a broken legacy hostname.
    if parsed.hostname == "explorer.gesia.io":
        return "/site-info/status/#legacy-explorer"

    if not parsed.scheme and href.startswith("/"):
        route = unquote(parsed.path.strip("/"))
        if route.endswith(".md"):
            route = route[:-3]
        if not route:
            return f"/{current_source.prefix}/{fragment}"
        return page_url(current_source, route) + query + fragment

    return href


def image_extension(data: bytes, url: str) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if b"<svg" in data[:500].lower():
        return ".svg"
    if len(data) > 12 and data[4:12] in (b"ftypavif", b"ftypavis"):
        return ".avif"

    parsed = urlparse(url)
    candidate = parsed.path
    if "/~gitbook/image" in candidate:
        upstream = parse_qs(parsed.query).get("url", [""])[0]
        candidate = urlparse(unquote(upstream)).path
    suffix = Path(candidate).suffix.lower()
    return suffix if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif"} else ".bin"


def localize_image(url: str) -> str:
    normalized = html.unescape(url)
    with asset_lock:
        existing = asset_paths.get(normalized)
        if existing:
            return existing

    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]
    cached = next(ASSET_DIR.glob(f"{digest}.*"), None)
    if cached:
        public_path = f"/common/resources/recovered-docs/{cached.name}"
        with asset_lock:
            asset_paths[normalized] = public_path
        return public_path

    parsed = urlparse(normalized)
    try:
        data = run_curl(normalized, host=parsed.hostname)
    except subprocess.CalledProcessError:
        return normalized

    suffix = image_extension(data, normalized)
    destination = ASSET_DIR / f"{digest}{suffix}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{threading.get_ident()}.tmp")
    temporary.write_bytes(data)
    temporary.replace(destination)
    public_path = f"/common/resources/recovered-docs/{destination.name}"
    with asset_lock:
        asset_paths[normalized] = public_path
    return public_path


def clean_contents(raw_html: str, source: Source) -> tuple[str, str]:
    soup = BeautifulSoup(raw_html, "html.parser")
    document_title = soup.title.get_text(" ", strip=True) if soup.title else source.section_label
    document_title = document_title.split("|")[0].strip()
    main = soup.find("main")
    contents = main.find("div", class_="contents") if main else None
    if contents is None:
        empty_label = (
            "This section groups the related pages. Choose a detailed page from the document menu."
            if source.language == "en"
            else "이 화면은 관련 자료를 묶은 상위 분류입니다. 문서 메뉴에서 세부 항목을 선택하세요."
        )
        return document_title, f"<p>{empty_label}</p>"

    for element in contents.select("script, style, noscript, svg, form, input"):
        element.decompose()
    for element in contents.select(".hash"):
        element.decompose()

    for button in list(contents.find_all("button")):
        label = button.get_text(" ", strip=True)
        if button.get("role") == "tab" and label:
            replacement = soup.new_tag("h4")
            replacement["class"] = "doc-tab-title"
            replacement.string = label
            button.replace_with(replacement)
        else:
            button.decompose()

    for element in contents.select('[role="tabpanel"]'):
        element["class"] = "doc-tab-panel"
        element.attrs.pop("hidden", None)
    for element in contents.select('[role="tablist"]'):
        element["class"] = "doc-tab-list"

    image_tags = list(contents.find_all("img"))
    for image in image_tags:
        source_url = image.get("src")
        if source_url and source_url.startswith(("http://", "https://")):
            image["src"] = localize_image(source_url)
        image.attrs.pop("srcset", None)
        image.attrs.pop("sizes", None)
        image.attrs["loading"] = "lazy"

    for anchor in contents.find_all("a"):
        anchor["href"] = map_link(anchor.get("href", ""), source)
        if anchor["href"].startswith("http"):
            anchor["target"] = "_blank"
            anchor["rel"] = "noopener noreferrer"
        else:
            anchor.attrs.pop("target", None)
            anchor.attrs.pop("rel", None)

    keep_by_tag = {
        "a": {"href", "target", "rel"},
        "img": {"src", "alt", "width", "height", "loading"},
        "h1": {"id"},
        "h2": {"id"},
        "h3": {"id"},
        "h4": {"id", "class"},
        "h5": {"id"},
        "h6": {"id"},
        "code": {"class"},
        "div": {"class", "role"},
        "td": {"colspan", "rowspan"},
        "th": {"colspan", "rowspan", "scope"},
        "ol": {"start"},
    }
    for element in contents.find_all(True):
        allowed = keep_by_tag.get(element.name, set())
        element.attrs = {key: value for key, value in element.attrs.items() if key in allowed}

    for anchor in list(contents.find_all("a")):
        if not anchor.get_text(" ", strip=True) and not anchor.find("img"):
            anchor.decompose()

    return document_title, contents.decode_contents()


def nav_html(source: Source, pages: list[dict[str, str]], active_route: str) -> str:
    links: list[str] = []
    for page in pages:
        route = page["route"]
        depth = route.count("/")
        active = ' aria-current="page" class="active"' if route == active_route else ""
        links.append(
            f'<li style="--depth:{min(depth, 4)}"><a href="{page_url(source, route)}"{active}>'
            f'{html.escape(page["title"])}</a></li>'
        )
    return "\n".join(links)


def switch_url(source: Source, route: str) -> str:
    if source.host == "docs-en.gesia.io":
        return "/docs/kr/"
    if source.host == "docs-kr.gesia.io":
        return "/docs/en/"
    if source.host == "carboncredit-docs-en.gesia.io":
        return "/carbon-credits/kr/"
    return "/carbon-credits/en/"


def render_page(
    source: Source,
    pages: list[dict[str, str]],
    page: dict[str, str],
    title: str,
    article_html: str,
) -> str:
    index = pages.index(page)
    previous_link = ""
    next_link = ""
    if index > 0:
        previous = pages[index - 1]
        previous_link = (
            f'<a class="pager-link previous" href="{page_url(source, previous["route"])}">'
            f'<span>Previous</span><strong>{html.escape(previous["title"])}</strong></a>'
        )
    if index + 1 < len(pages):
        following = pages[index + 1]
        next_link = (
            f'<a class="pager-link next" href="{page_url(source, following["route"])}">'
            f'<span>Next</span><strong>{html.escape(following["title"])}</strong></a>'
        )
    language_label = "한국어" if source.language == "en" else "English"
    escaped_title = html.escape(title)
    return f"""<!doctype html>
<html lang="{source.language}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{html.escape(page.get('description') or source.section_label)}">
  <title>{escaped_title} | GESIA</title>
  <link rel="icon" href="/common/resources/favicon/favicon.ico">
  <link rel="stylesheet" href="/common/css/docs.css">
</head>
<body class="docs-page">
  <header class="docs-header">
    <a class="docs-brand" href="/"><img src="/common/resources/gio-logo.png" alt="GESIA"><span>GESIA</span></a>
    <div class="docs-header-actions">
      <a href="{switch_url(source, page['route'])}" class="language-link">{language_label}</a>
      <a href="/" class="home-link">Main site</a>
      <button class="nav-toggle" type="button" aria-label="Open document menu" aria-expanded="false">Menu</button>
    </div>
  </header>
  <div class="docs-shell">
    <aside class="docs-sidebar" id="docs-sidebar">
      <div class="sidebar-heading"><strong>{html.escape(source.section_label)}</strong><span>{len(pages)} pages</span></div>
      <label class="nav-search-label" for="nav-search">Search pages</label>
      <input class="nav-search" id="nav-search" type="search" placeholder="Search" autocomplete="off">
      <nav aria-label="Documentation"><ul class="docs-nav">{nav_html(source, pages, page['route'])}</ul></nav>
    </aside>
    <main class="docs-main">
      <div class="source-note">Recovered from the company&rsquo;s original published documentation.</div>
      <h1>{escaped_title}</h1>
      <article class="doc-content">{article_html}</article>
      <nav class="pager" aria-label="Previous and next pages">{previous_link}{next_link}</nav>
      <footer class="docs-footer">GESIA Platform documentation &middot; Restored 2026</footer>
    </main>
  </div>
  <script src="/common/js/docs.js"></script>
</body>
</html>
"""


def render_collection_home(source: Source, pages: list[dict[str, str]]) -> str:
    cards = "".join(
        f'<a class="collection-card" href="{page_url(source, page["route"])}"><h2>{html.escape(page["title"])}</h2>'
        f'<p>{html.escape(page.get("description") or ("View project details" if source.language == "en" else "프로젝트 상세 보기"))}</p></a>'
        for page in pages
    )
    title = source.section_label
    language_label = "한국어" if source.language == "en" else "English"
    return f"""<!doctype html>
<html lang="{source.language}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{html.escape(title)}">
  <title>{html.escape(title)} | GESIA</title>
  <link rel="icon" href="/common/resources/favicon/favicon.ico">
  <link rel="stylesheet" href="/common/css/docs.css">
</head>
<body class="collection-page">
  <header class="docs-header">
    <a class="docs-brand" href="/"><img src="/common/resources/gio-logo.png" alt="GESIA"><span>GESIA</span></a>
    <div class="docs-header-actions"><a href="{switch_url(source, '')}" class="language-link">{language_label}</a><a href="/" class="home-link">Main site</a></div>
  </header>
  <main class="collection-main">
    <p class="eyebrow">GESIA</p><h1>{html.escape(title)}</h1>
    <p class="collection-intro">{('Restored project information from the original GESIA publication.' if source.language == 'en' else '원본 GESIA 게시 자료에서 복구한 프로젝트 정보입니다.')}</p>
    <section class="collection-grid">{cards}</section>
  </main>
  <footer class="docs-footer standalone">GESIA Platform &middot; Restored 2026</footer>
</body>
</html>
"""


def output_path(source: Source, route: str) -> Path:
    return ROOT / source.prefix / Path(route) / "index.html"


def fetch_page(source: Source, page: dict[str, str]) -> tuple[dict[str, str], str, str]:
    raw = text_from_host(source.host, page["route"])
    title, body = clean_contents(raw, source)
    return page, title or page["title"], body


def recover_source(source: Source) -> tuple[int, list[str]]:
    pages = parse_index(source)
    results: list[tuple[dict[str, str], str, str]] = []
    failures: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_page, source, page): page for page in pages}
        for future in concurrent.futures.as_completed(futures):
            page = futures[future]
            try:
                results.append(future.result())
            except Exception as error:  # keep an auditable list instead of silently dropping pages
                failures.append(f"{source.host}/{page['route']}: {error}")

    by_route = {page["route"]: (page, title, body) for page, title, body in results}
    for page in pages:
        recovered = by_route.get(page["route"])
        if recovered is None:
            continue
        recovered_page, title, body = recovered
        destination = output_path(source, recovered_page["route"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(render_page(source, pages, recovered_page, title, body), encoding="utf-8")

    prefix_root = ROOT / source.prefix
    prefix_root.mkdir(parents=True, exist_ok=True)
    if source.home_route and source.home_route in by_route:
        page, title, body = by_route[source.home_route]
        (prefix_root / "index.html").write_text(
            render_page(source, pages, page, title, body), encoding="utf-8"
        )
    else:
        (prefix_root / "index.html").write_text(render_collection_home(source, pages), encoding="utf-8")

    return len(results), failures


def main() -> int:
    for generated in (ROOT / "docs", ROOT / "carbon-credits"):
        if generated.exists():
            shutil.rmtree(generated)
    ASSET_DIR.mkdir(parents=True, exist_ok=True)

    total_pages = 0
    all_failures: list[str] = []
    for source in SOURCES:
        print(f"Recovering {source.host} ...", flush=True)
        recovered, failures = recover_source(source)
        total_pages += recovered
        all_failures.extend(failures)
        print(f"  recovered {recovered} pages", flush=True)

    print(f"Recovered {total_pages} pages and {len(list(ASSET_DIR.iterdir()))} assets.")
    if all_failures:
        print("Failed pages:", file=sys.stderr)
        print("\n".join(all_failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
