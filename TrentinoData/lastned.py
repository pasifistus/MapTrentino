#!/usr/bin/env python3
"""Download every file in Trentino's LiDAR DTM ASCII directory.

Python 3.9+; standard library only. Running without --download previews the
remote directory. Downloaded files are preserved between runs, and interrupted
downloads are resumed when the server supports HTTP Range requests.
Use --file-list PATH to select filenames from a UTF-8 text file, one per line.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen


SOURCE = "https://siatservices.provincia.tn.it/stemdata/2014_lidar_dtm_asc/"
USER_AGENT = "Trentino-LiDAR-downloader/1.0 (Python standard library)"
CHUNK_SIZE = 1024 * 1024


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)


def request(url: str, *, offset: int = 0) -> Request:
    headers = {"User-Agent": USER_AGENT}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    return Request(url, headers=headers)


def retry(operation, attempts: int = 4):
    for attempt in range(attempts):
        try:
            return operation()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            if isinstance(exc, HTTPError) and exc.code in (401, 403, 404):
                raise
            if attempt == attempts - 1:
                raise
            wait = min(2**attempt, 8)
            print(f"  Prøver på nytt om {wait}s: {exc}", file=sys.stderr)
            time.sleep(wait)


def discover(base_url: str) -> list[tuple[str, str]]:
    """Return (URL, relative filename) for every file beneath a directory."""
    if not base_url.endswith("/"):
        base_url += "/"
    root = urlsplit(base_url)
    stack = [base_url]
    visited: set[str] = set()
    found: dict[str, str] = {}

    while stack:
        directory = stack.pop()
        if directory in visited:
            continue
        visited.add(directory)

        def fetch_listing():
            with urlopen(request(directory), timeout=60) as response:
                content_type = response.headers.get("Content-Type", "")
                if "html" not in content_type.lower():
                    raise ValueError(f"Ingen HTML-kataloglisting på {directory}")
                return response.read().decode("utf-8", errors="replace")

        html = retry(fetch_listing)
        parser = LinkParser()
        parser.feed(html)
        for href in parser.links:
            if href.startswith(("#", "?")):
                continue
            raw_url = urlsplit(urljoin(directory, href))
            if raw_url.scheme != root.scheme or raw_url.netloc != root.netloc:
                continue
            if not raw_url.path.startswith(root.path):
                continue  # Exclude parent-directory links and unrelated folders.
            if raw_url.query:
                continue  # Apache sorting links, not files.

            relative = unquote(raw_url.path[len(root.path) :])
            parts = relative.rstrip("/").split("/")
            if not relative or any(part in ("", ".", "..") for part in parts):
                continue

            clean_url = urlunsplit((raw_url.scheme, raw_url.netloc, raw_url.path, "", ""))
            if raw_url.path.endswith("/"):
                if clean_url not in visited:
                    stack.append(clean_url)
            else:
                found[relative] = clean_url

    return sorted(((url, name) for name, url in found.items()), key=lambda x: x[1])


def download_one(item: tuple[str, str], destination: Path) -> tuple[str, str, int]:
    url, relative = item
    target = destination.joinpath(*relative.split("/"))
    if not target.resolve().is_relative_to(destination.resolve()):
        raise ValueError(f"Utrygg filsti: {relative}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return "skipped", relative, 0

    temporary = target.with_name(target.name + ".part")
    start = 0

    def transfer() -> int:
        nonlocal start
        # Re-check after every failed attempt, so a partially downloaded
        # response can be resumed on the next retry.
        start = temporary.stat().st_size if temporary.exists() else 0
        try:
            response = urlopen(request(url, offset=start), timeout=120)
        except HTTPError as exc:
            if exc.code == 416 and start:
                total_match = re.search(r"\*/(\d+)", exc.headers.get("Content-Range", ""))
                if total_match and int(total_match.group(1)) == start:
                    temporary.replace(target)
                    return 0
            raise

        with response:
            status = response.status
            if start and status == 206:
                content_range = response.headers.get("Content-Range", "")
                match = re.match(r"bytes (\d+)-\d+/(\d+|\*)", content_range)
                if not match or int(match.group(1)) != start:
                    raise ValueError(f"Uventet Range-svar for {relative}: {content_range}")
                mode = "ab"
            elif status == 200:
                # Server does not support resuming: restart the .part file.
                start = 0
                mode = "wb"
            else:
                raise ValueError(f"Uventet HTTP-status {status}: {relative}")

            length = response.headers.get("Content-Length")
            expected = start + int(length) if length and length.isdigit() else None
            downloaded = 0
            with temporary.open(mode) as stream:
                while block := response.read(CHUNK_SIZE):
                    stream.write(block)
                    downloaded += len(block)
            if expected is not None and temporary.stat().st_size != expected:
                raise OSError(f"Ufullstendig nedlasting: {relative}")

        temporary.replace(target)  # Rename only after the download is complete.
        return downloaded

    downloaded = retry(transfer)
    return "downloaded", relative, downloaded


def read_file_list(path: Path) -> list[str]:
    """Read unique filenames in order; ignore blank lines and # comments."""
    names = dict.fromkeys(
        line.strip()
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if not names:
        raise ValueError(f"Fillisten er tom: {path}")
    return list(names)


def select_files(items: list[tuple[str, str]], names: list[str]) -> list[tuple[str, str]]:
    """Select listed files, failing before downloading if any are unavailable."""
    available = {name: url for url, name in items}
    missing = [name for name in names if name not in available]
    if missing:
        examples = ", ".join(missing[:5])
        raise ValueError(f"{len(missing)} filer fra fillisten finnes ikke i katalogen: {examples}")
    return [(available[name], name) for name in names]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=SOURCE, help="Katalog-URL (standard: Trentino 0,5 m DTM)")
    parser.add_argument("--output", type=Path, default=Path("trentino_lidar_asc"))
    parser.add_argument("--file-list", type=Path,
                        help="Last bare filer fra en UTF-8-tekstfil med ett filnavn per linje")
    parser.add_argument("--download", action="store_true", help="Start nedlasting (uten dette listes bare filer)")
    parser.add_argument("--yes", action="store_true", help="Ikke spør om bekreftelse")
    parser.add_argument("--workers", type=int, default=2, help="Samtidige nedlastinger (standard: 2)")
    parser.add_argument("--max-files", type=int, help="Valgfri grense for å teste med noen få filer")
    args = parser.parse_args()
    if args.workers < 1 or args.max_files is not None and args.max_files < 1:
        parser.error("--workers og --max-files må være positive")

    names = None
    if args.file_list is not None:
        try:
            names = read_file_list(args.file_list)
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"Kunne ikke lese fillisten: {exc}", file=sys.stderr)
            return 1

    print(f"Leser filliste fra {args.url}", flush=True)
    try:
        items = discover(args.url)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        print(f"Kunne ikke lese katalogen: {exc}", file=sys.stderr)
        return 1

    print(f"Fant {len(items)} filer i katalogen.")
    if names is not None:
        try:
            items = select_files(items, names)
        except ValueError as exc:
            print(f"Ugyldig filliste: {exc}", file=sys.stderr)
            return 1
        print(f"Valgte {len(items)} filer fra {args.file_list}.")
    if args.max_files is not None:
        items = items[:args.max_files]
        print(f"Begrenser til de første {len(items)} filene.")
    for _, name in items[:10]:
        print("  " + name)
    if len(items) > 10:
        print(f"  ... og {len(items) - 10} til")
    if not items or not args.download:
        print("Bruk --download for å laste ned filene.")
        return 0

    if not args.yes:
        try:
            confirm = input("Datasettet kan kreve svært mye diskplass. Fortsette? [ja/nei] ")
        except EOFError:
            confirm = ""
        if confirm.strip().lower() not in ("j", "ja", "y", "yes"):
            print("Avbrutt. Ingen filer lastet ned.")
            return 0

    args.output.mkdir(parents=True, exist_ok=True)
    counts = {"downloaded": 0, "skipped": 0, "failed": 0}
    total_bytes = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(download_one, item, args.output): item[1] for item in items}
        for future in as_completed(futures):
            try:
                status, name, size = future.result()
                counts[status] += 1
                total_bytes += size
                print(f"[{status}] {name}", flush=True)
            except Exception as exc:
                counts["failed"] += 1
                print(f"[FEIL] {futures[future]}: {exc}", file=sys.stderr, flush=True)

    print(f"Ferdig: {counts['downloaded']} lastet ned, {counts['skipped']} hoppet over, "
          f"{counts['failed']} feil; {total_bytes / 1e9:.2f} GB overført nå.")
    if counts["failed"]:
        print("Kjør samme kommando på nytt for å fortsette eller prøve feilede filer igjen.")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
