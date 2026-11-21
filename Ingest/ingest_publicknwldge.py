"""Fetch public financial guidance and index it in Azure AI Search."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Ingest.categories import PUBLIC_KNOWLEDGE_CATEGORIES


def load_env_file(env_path: Path = PROJECT_ROOT / ".env") -> None:
    """Load local configuration without overwriting explicitly-set variables."""
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_env_file()

DEFAULT_SEARCH_ENDPOINT = os.environ.get("AZURE_SEARCH_ENDPOINT")
DEFAULT_SEARCH_INDEX = os.environ.get("AZURE_SEARCH_INDEX")
SEARCH_API_VERSION = os.environ.get("AZURE_SEARCH_API_VERSION")
DEFAULT_CHUNK_SIZE = 1800
DEFAULT_CHUNK_OVERLAP = 250


@dataclass(frozen=True)
class KnowledgeSource:
    url: str
    category: str
    account_types: tuple[str, ...]
    source_name: str
    tax_year: int | None = None


KNOWLEDGE_SOURCES_CONFIG = PROJECT_ROOT / "Ingest" / "public_knowledge_sources.json"


def load_knowledge_sources(config_path: Path = KNOWLEDGE_SOURCES_CONFIG) -> tuple[KnowledgeSource, ...]:
    """Load and validate public knowledge source metadata from JSON."""
    entries = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError(f"Knowledge source config must contain a JSON array: {config_path}")

    sources = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"Each knowledge source must be a JSON object: {config_path}")
        try:
            source = KnowledgeSource(
                url=str(entry["url"]),
                category=str(entry["category"]),
                account_types=tuple(str(value) for value in entry.get("account_types", [])),
                source_name=str(entry["source_name"]),
                tax_year=int(entry["tax_year"]) if entry.get("tax_year") is not None else None,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid knowledge source entry in {config_path}: {entry}") from error
        if source.category not in PUBLIC_KNOWLEDGE_CATEGORIES:
            raise ValueError(f"Unsupported public knowledge category in {config_path}: {source.category}")
        sources.append(source)
    return tuple(sources)


KNOWLEDGE_SOURCES = load_knowledge_sources()


class VisibleTextParser(HTMLParser):
    """Extract visible text and the first useful headings without dependencies."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.headings: list[str] = []
        self._ignored = 0
        self._heading = False
        self._heading_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg", "template"}:
            self._ignored += 1
        elif tag in {"h1", "h2", "h3"} and not self._ignored:
            self._heading = True
            self._heading_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg", "template"}:
            self._ignored = max(0, self._ignored - 1)
        elif tag in {"h1", "h2", "h3"} and self._heading:
            heading = normalize_text(" ".join(self._heading_parts))
            if heading:
                self.headings.append(heading)
            self._heading = False

    def handle_data(self, data: str) -> None:
        if self._ignored:
            return
        value = normalize_text(data)
        if value:
            self.parts.append(value)
            if self._heading:
                self._heading_parts.append(value)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def fetch_page(url: str, timeout: int = 30) -> str:
    request = Request(url, headers={"User-Agent": "FinancialPanditKnowledgeIndexer/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except (HTTPError, URLError) as error:
        raise RuntimeError(f"Could not fetch knowledge source {url}: {error}") from error


def chunk_text(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP) -> list[str]:
    if chunk_size <= overlap:
        raise ValueError("chunk_size must be greater than overlap")
    sentences = [normalize_text(x) for x in re.split(r"(?<=[.!?])\s+", text) if normalize_text(x)]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > chunk_size:
            chunks.append(current)
            current = f"{current[-overlap:]} {sentence}".strip()
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def parse_page(source: KnowledgeSource, page_html: str, chunk_size: int, overlap: int) -> tuple[str, list[str]]:
    parser = VisibleTextParser()
    parser.feed(page_html)
    text = normalize_text(" ".join(parser.parts))
    if not text:
        raise ValueError(f"Knowledge source returned no readable text: {source.url}")
    fallback = urlparse(source.url).path.rstrip("/").split("/")[-1] or source.source_name
    return (parser.headings[0] if parser.headings else fallback, chunk_text(text, chunk_size, overlap))


def build_documents(source: KnowledgeSource, title: str, chunks: Iterable[str], retrieved_at: str) -> list[dict[str, object]]:
    if source.category not in PUBLIC_KNOWLEDGE_CATEGORIES:
        raise ValueError(f"Unsupported public knowledge category: {source.category}")
    documents = []
    for number, content in enumerate(chunks, start=1):
        digest = hashlib.sha256(f"{source.url}|{number}|{content}".encode()).hexdigest()[:20]
        documents.append({
            "@search.action": "mergeOrUpload",
            "id": f"{source.category}-{digest}",
            "title": title,
            "section_heading": title,
            "content": content,
            "category": source.category,
            "account_types": list(source.account_types),
            "source_name": source.source_name,
            "source_url": source.url,
            "tax_year": source.tax_year,
            "retrieved_at": retrieved_at,
        })
    return documents


def upload_documents(endpoint: str, index_name: str, api_key: str, documents: list[dict[str, object]]) -> None:
    url = f"{endpoint.rstrip('/')}/indexes/{index_name}/docs/index?api-version={SEARCH_API_VERSION}"
    request = Request(
        url,
        data=json.dumps({"value": documents}).encode("utf-8"),
        headers={"Content-Type": "application/json", "api-key": api_key},
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Azure AI Search upload failed: {body}") from error
    except URLError as error:
        raise RuntimeError(f"Could not reach Azure AI Search: {error}") from error
    failed = [item for item in result.get("value", []) if not item.get("status", False)]
    if failed:
        raise RuntimeError(f"Azure AI Search rejected {len(failed)} document(s): {failed[:2]}")


def ingest_public_knowledge(
    sources: Iterable[KnowledgeSource] = KNOWLEDGE_SOURCES,
    *, endpoint: str | None = None, index_name: str | None = None,
    api_key: str | None = None, chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> int:
    endpoint = endpoint or DEFAULT_SEARCH_ENDPOINT
    index_name = index_name or DEFAULT_SEARCH_INDEX
    api_key = api_key or os.environ.get("AZURE_SEARCH_API_KEY")
    missing = [
        name for name, value in (
            ("AZURE_SEARCH_ENDPOINT", endpoint),
            ("AZURE_SEARCH_INDEX", index_name),
            ("AZURE_SEARCH_API_VERSION", SEARCH_API_VERSION),
            ("AZURE_SEARCH_API_KEY", api_key),
        ) if not value
    ]
    if missing:
        raise RuntimeError(f"Missing required .env setting(s): {', '.join(missing)}")
    retrieved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    total = 0
    for source in sources:
        title, chunks = parse_page(source, fetch_page(source.url), chunk_size, chunk_overlap)
        documents = build_documents(source, title, chunks, retrieved_at)
        upload_documents(endpoint, index_name, api_key, documents)
        total += len(documents)
        print(f"{source.category}: uploaded {len(documents)} document(s) from {source.url}")
    return total


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Index public financial guidance in Azure AI Search.")
    parser.add_argument("--endpoint", default=None)
    parser.add_argument("--index", dest="index_name", default=None)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--chunk-overlap", type=int, default=DEFAULT_CHUNK_OVERLAP)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    count = ingest_public_knowledge(
        endpoint=args.endpoint, index_name=args.index_name,
        chunk_size=args.chunk_size, chunk_overlap=args.chunk_overlap,
    )
    print(f"Uploaded {count} public-knowledge document(s)")
