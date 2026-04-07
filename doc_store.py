"""
doc_store.py
============
Lightweight .md-file-based document store with fast text search.
No vector DB, no embeddings, no indexing delay.

Upload  = copy/convert file to knowledge_base/ folder (instant)
Search  = multi-strategy text search: filename -> headers -> full-text

Usage:
    store = DocStore()
    store.add_file("/path/to/guide.pdf")          # converts & saves as .md
    results = store.search("how to create OIC connection")
    context = store.get_context("OIC adapter")     # ready for LLM prompt
"""

from __future__ import annotations

import os
import re
import shutil
import logging
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Default knowledge base folder (next to this file)
_DEFAULT_KB_DIR = Path(__file__).parent / "knowledge_base"


@dataclass
class SearchResult:
    """A single search hit."""
    filename: str
    heading: str          # nearest heading above the match
    snippet: str          # matched text chunk
    score: float          # relevance score (higher = better)
    match_type: str       # "filename" | "heading" | "content"


class DocStore:
    """
    File-based document store with instant upload and fast text search.

    Stores all documents as .md files in a single folder.
    Supports .md, .txt, .pdf, .docx, .csv uploads (auto-converts to .md).
    """

    def __init__(self, kb_dir: Optional[str] = None):
        self._kb_dir = Path(kb_dir) if kb_dir else _DEFAULT_KB_DIR
        self._kb_dir.mkdir(parents=True, exist_ok=True)

    @property
    def kb_dir(self) -> Path:
        return self._kb_dir

    @property
    def document_count(self) -> int:
        """Number of .md files in the knowledge base."""
        return len(list(self._kb_dir.glob("*.md")))

    def list_documents(self) -> list[str]:
        """List all document filenames in the knowledge base."""
        return sorted(p.name for p in self._kb_dir.glob("*.md"))

    # ── Upload / Add Files ─────────────────────────────────────────────────────

    def add_file(self, file_path: str) -> str:
        """
        Add a file to the knowledge base. Converts to .md if needed.
        Returns the saved .md filename.
        """
        src = Path(file_path)
        if not src.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        suffix = src.suffix.lower()

        if suffix == ".md":
            # Direct copy
            dest = self._kb_dir / src.name
            shutil.copy2(str(src), str(dest))
            logger.info(f"Copied .md file: {src.name}")
            return dest.name

        if suffix == ".txt":
            # Rename to .md
            dest_name = src.stem + ".md"
            dest = self._kb_dir / dest_name
            shutil.copy2(str(src), str(dest))
            logger.info(f"Copied .txt as .md: {dest_name}")
            return dest_name

        if suffix == ".csv":
            # Copy as-is with .md extension
            dest_name = src.stem + ".md"
            dest = self._kb_dir / dest_name
            text = src.read_text(encoding="utf-8", errors="ignore")
            dest.write_text(f"# {src.stem}\n\n```csv\n{text}\n```\n", encoding="utf-8")
            logger.info(f"Converted .csv to .md: {dest_name}")
            return dest_name

        if suffix == ".pdf":
            return self._convert_pdf(src)

        if suffix in (".docx", ".doc"):
            return self._convert_docx(src)

        # Fallback: try reading as text
        try:
            text = src.read_text(encoding="utf-8", errors="ignore")
            dest_name = src.stem + ".md"
            dest = self._kb_dir / dest_name
            dest.write_text(f"# {src.stem}\n\n{text}\n", encoding="utf-8")
            logger.info(f"Converted unknown format to .md: {dest_name}")
            return dest_name
        except Exception as ex:
            raise ValueError(f"Cannot convert {src.name}: {ex}")

    def _convert_pdf(self, src: Path) -> str:
        """Convert PDF to .md."""
        text = ""
        try:
            import pypdf
            reader = pypdf.PdfReader(str(src))
            pages = []
            for i, page in enumerate(reader.pages):
                content = page.extract_text() or ""
                if content.strip():
                    pages.append(f"## Page {i + 1}\n\n{content}")
            text = "\n\n".join(pages)
        except ImportError:
            try:
                import pdfminer.high_level as pdfm
                text = pdfm.extract_text(str(src))
            except ImportError:
                raise ImportError("Install pypdf or pdfminer.six to upload PDFs")

        dest_name = src.stem + ".md"
        dest = self._kb_dir / dest_name
        dest.write_text(f"# {src.stem}\n\n{text}\n", encoding="utf-8")
        logger.info(f"Converted PDF to .md: {dest_name}")
        return dest_name

    def _convert_docx(self, src: Path) -> str:
        """Convert DOCX to .md."""
        try:
            import docx
            doc = docx.Document(str(src))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            text = "\n\n".join(paragraphs)
        except ImportError:
            raise ImportError("Install python-docx to upload .docx files")

        dest_name = src.stem + ".md"
        dest = self._kb_dir / dest_name
        dest.write_text(f"# {src.stem}\n\n{text}\n", encoding="utf-8")
        logger.info(f"Converted DOCX to .md: {dest_name}")
        return dest_name

    def remove_file(self, filename: str) -> bool:
        """Remove a document from the knowledge base."""
        path = self._kb_dir / filename
        if path.exists():
            path.unlink()
            logger.info(f"Removed: {filename}")
            return True
        return False

    def clear_all(self):
        """Remove all .md files from the knowledge base."""
        for p in self._kb_dir.glob("*.md"):
            p.unlink()
        logger.info("Knowledge base cleared")

    # ── Search ─────────────────────────────────────────────────────────────────

    def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        """
        Search across all .md files using a 3-tier strategy:
          1. Filename match   (score boost: +10)
          2. Heading match    (score boost: +5)
          3. Full-text match  (score boost: +1 per keyword hit)

        Returns results sorted by relevance score (highest first).
        """
        if not query or not query.strip():
            return []

        keywords = self._extract_keywords(query)
        if not keywords:
            return []

        results: list[SearchResult] = []

        for md_file in self._kb_dir.glob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            fname_lower = md_file.stem.lower().replace("_", " ").replace("-", " ")

            # Tier 1: Filename match
            fname_hits = sum(1 for kw in keywords if kw in fname_lower)
            if fname_hits > 0:
                results.append(SearchResult(
                    filename=md_file.name,
                    heading=md_file.stem,
                    snippet=content[:500],
                    score=10.0 * fname_hits,
                    match_type="filename",
                ))

            # Parse headings and sections
            sections = self._parse_sections(content, md_file.name)

            for section in sections:
                heading_lower = section["heading"].lower()
                text_lower = section["text"].lower()

                # Tier 2: Heading match
                heading_hits = sum(1 for kw in keywords if kw in heading_lower)
                if heading_hits > 0:
                    results.append(SearchResult(
                        filename=md_file.name,
                        heading=section["heading"],
                        snippet=section["text"][:500],
                        score=5.0 * heading_hits,
                        match_type="heading",
                    ))
                    continue  # skip content match for this section if heading matched

                # Tier 3: Content match
                content_hits = sum(1 for kw in keywords if kw in text_lower)
                if content_hits > 0:
                    # Find the best matching snippet around keywords
                    snippet = self._extract_snippet(section["text"], keywords)
                    results.append(SearchResult(
                        filename=md_file.name,
                        heading=section["heading"],
                        snippet=snippet,
                        score=1.0 * content_hits + (0.1 * content_hits / max(len(keywords), 1)),
                        match_type="content",
                    ))

        # Sort by score descending
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:max_results]

    def get_context(self, query: str, max_chars: int = 4000) -> str:
        """
        Search and return combined text suitable for LLM context.
        Concatenates top search results up to max_chars.
        """
        results = self.search(query, max_results=8)
        if not results:
            return ""

        context_parts = []
        total_chars = 0

        for r in results:
            section = f"[Source: {r.filename} | {r.heading}]\n{r.snippet}\n"
            if total_chars + len(section) > max_chars:
                remaining = max_chars - total_chars
                if remaining > 100:
                    context_parts.append(section[:remaining])
                break
            context_parts.append(section)
            total_chars += len(section)

        return "\n---\n".join(context_parts)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _extract_keywords(self, query: str) -> list[str]:
        """Extract meaningful search keywords from a query string."""
        stop_words = {
            "a", "an", "the", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "shall", "can",
            "of", "in", "to", "for", "with", "on", "at", "from", "by",
            "about", "as", "into", "through", "during", "before", "after",
            "and", "but", "or", "nor", "not", "so", "yet", "both",
            "what", "how", "why", "when", "where", "which", "who",
            "this", "that", "these", "those", "it", "its", "i", "me",
            "my", "we", "our", "you", "your", "he", "she", "they",
            "explain", "define", "describe", "tell",
        }
        # Tokenize and filter
        words = re.findall(r'[a-zA-Z0-9_]+', query.lower())
        keywords = [w for w in words if w not in stop_words and len(w) >= 2]
        return keywords

    def _parse_sections(self, content: str, filename: str) -> list[dict]:
        """
        Split .md content into sections based on headings.
        Returns list of {"heading": str, "text": str}.
        """
        lines = content.split("\n")
        sections = []
        current_heading = filename  # default heading if no # found
        current_lines: list[str] = []

        for line in lines:
            heading_match = re.match(r'^(#{1,4})\s+(.+)', line)
            if heading_match:
                # Save previous section
                if current_lines:
                    text = "\n".join(current_lines).strip()
                    if text:
                        sections.append({"heading": current_heading, "text": text})
                current_heading = heading_match.group(2).strip()
                current_lines = []
            else:
                current_lines.append(line)

        # Save last section
        if current_lines:
            text = "\n".join(current_lines).strip()
            if text:
                sections.append({"heading": current_heading, "text": text})

        # If no sections found, treat entire content as one section
        if not sections and content.strip():
            sections.append({"heading": filename, "text": content.strip()})

        return sections

    def _extract_snippet(self, text: str, keywords: list[str],
                         window: int = 250) -> str:
        """Extract a snippet around the first keyword match."""
        text_lower = text.lower()
        best_pos = len(text)

        for kw in keywords:
            pos = text_lower.find(kw)
            if pos != -1 and pos < best_pos:
                best_pos = pos

        start = max(0, best_pos - window // 2)
        end = min(len(text), best_pos + window)

        snippet = text[start:end].strip()
        if start > 0:
            snippet = "..." + snippet
        if end < len(text):
            snippet = snippet + "..."

        return snippet
