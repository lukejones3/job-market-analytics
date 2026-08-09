#!/usr/bin/env python3
"""Refresh sourced external evidence for Company Radar.

The worker prioritizes followed companies, then companies with fresh hiring momentum.
Serper supplies candidate sources. An optional OpenAI Responses call classifies and
summarizes only the supplied snippets; source URLs always come from Serper and are
never invented by the model. This DAG is deliberately independent of ingestion.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extras import Json, RealDictCursor

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("company-radar-research")

SERPER_NEWS_URL = "https://google.serper.dev/news"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
SERPER_COST_PER_QUERY = float(os.getenv("SERPER_COST_PER_QUERY", "0.001"))
MONTHLY_SERPER_BUDGET = int(os.getenv("COMPANY_RADAR_MONTHLY_SERPER_QUERIES", "1500"))
MONTHLY_AI_BUDGET = int(os.getenv("COMPANY_RADAR_MONTHLY_AI_CALLS", "500"))
AI_MODEL = os.getenv("COMPANY_RADAR_AI_MODEL", "gpt-5-nano")
AI_COST_PER_CALL = float(os.getenv("COMPANY_RADAR_AI_COST_PER_CALL", "0"))

EVENT_TYPES = {"expansion", "funding", "leadership", "layoff", "earnings", "hiring", "other"}
EVENT_PATTERNS = {
    "layoff": re.compile(r"\b(layoffs?|job cuts?|workforce reduction|redundan(?:cy|cies)|cuts? staff)\b", re.I),
    "funding": re.compile(r"\b(raises?|funding|financing|series [a-f]|venture capital|investment round)\b", re.I),
    "expansion": re.compile(r"\b(expands?|expansion|new office|new facility|opens? (?:a )?(?:hub|site)|headcount growth)\b", re.I),
    "leadership": re.compile(r"\b(appoints?|names?|hires?)\b.{0,60}\b(ceo|cfo|cto|chief|president|executive|vp)\b", re.I),
    "earnings": re.compile(r"\b(earnings|revenue|quarterly results|fiscal (?:year|quarter)|guidance)\b", re.I),
    "hiring": re.compile(r"\b(hiring|open roles?|job openings?|recruiting|adds? jobs?|talent)\b", re.I),
}


@dataclass(frozen=True)
class Source:
    title: str
    url: str
    snippet: str
    published_at: str | None
    domain: str


def db_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def classify_event(text: str) -> str:
    for event_type, pattern in EVENT_PATTERNS.items():
        if pattern.search(text):
            return event_type
    return "other"


def clean_text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def source_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def parse_published_at(value: Any) -> str | None:
    raw = clean_text(value, 100)
    if not raw:
        return None
    relative = re.fullmatch(r"(\d+)\s+(minute|hour|day|week|month)s?\s+ago", raw, re.I)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2).lower()
        days = amount * 30 if unit == "month" else amount * 7 if unit == "week" else amount if unit == "day" else 0
        delta = timedelta(days=days, hours=amount if unit == "hour" else 0, minutes=amount if unit == "minute" else 0)
        return (datetime.now(timezone.utc) - delta).isoformat()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def normalize_sources(payload: dict[str, Any], limit: int = 6) -> list[Source]:
    sources: list[Source] = []
    seen: set[str] = set()
    for item in payload.get("news", []):
        url = clean_text(item.get("link"), 2000)
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or url in seen:
            continue
        title = clean_text(item.get("title"), 500)
        snippet = clean_text(item.get("snippet"), 1200)
        if not title or not snippet:
            continue
        seen.add(url)
        sources.append(
            Source(
                title=title,
                url=url,
                snippet=snippet,
                published_at=parse_published_at(item.get("date")),
                domain=source_domain(url),
            )
        )
        if len(sources) >= limit:
            break
    return sources


def source_mentions_company(company_name: str, source: Source) -> bool:
    haystack = re.sub(r"[^a-z0-9]+", " ", f"{source.title} {source.snippet}".lower())
    company = re.sub(r"[^a-z0-9]+", " ", company_name.lower()).strip()
    if company and company in haystack:
        return True
    ignored = {
        "inc", "llc", "ltd", "corp", "corporation", "company", "co", "group", "holdings",
        "industries", "technology", "technologies", "systems", "solutions",
    }
    tokens = [token for token in company.split() if len(token) >= 4 and token not in ignored]
    return bool(tokens) and all(token in haystack for token in tokens[:2])


def deterministic_brief(source: Source) -> dict[str, Any]:
    text = f"{source.title}. {source.snippet}"
    return {
        "source_index": 0,
        "event_type": classify_event(text),
        "headline": source.title,
        "summary": source.snippet,
        "confidence": 0.58,
    }


def extract_response_text(payload: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                chunks.append(str(content["text"]))
    return "".join(chunks)


def ai_briefs(company_name: str, sources: list[Source]) -> list[dict[str, Any]] | None:
    key = os.getenv("OPENAI_API_KEY")
    if not key or not sources:
        return None
    evidence = [
        {"source_index": i, "title": source.title, "snippet": source.snippet, "domain": source.domain}
        for i, source in enumerate(sources)
    ]
    schema = {
        "type": "object",
        "properties": {
            "briefs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source_index": {"type": "integer"},
                        "event_type": {"type": "string", "enum": sorted(EVENT_TYPES)},
                        "headline": {"type": "string"},
                        "summary": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["source_index", "event_type", "headline", "summary", "confidence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["briefs"],
        "additionalProperties": False,
    }
    response = requests.post(
        OPENAI_RESPONSES_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": AI_MODEL,
            "store": False,
            "input": [
                {
                    "role": "developer",
                    "content": (
                        "Classify and summarize company hiring evidence. Use only supplied evidence. "
                        "Do not infer an event that the title/snippet does not support. Keep summaries under 45 words."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Company: {company_name}\nEvidence JSON:\n{json.dumps(evidence, ensure_ascii=False)}",
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "company_radar_briefs",
                    "strict": True,
                    "schema": schema,
                }
            },
        },
        timeout=45,
    )
    response.raise_for_status()
    parsed = json.loads(extract_response_text(response.json()))
    valid: list[dict[str, Any]] = []
    for brief in parsed.get("briefs", []):
        index = brief.get("source_index")
        if not isinstance(index, int) or index < 0 or index >= len(sources):
            continue
        # The deterministic classifier is the final authority: the model may
        # compress evidence, but cannot upgrade a snippet into an unsupported event.
        event_type = classify_event(f"{sources[index].title} {sources[index].snippet}")
        valid.append(
            {
                "source_index": index,
                "event_type": event_type,
                "headline": clean_text(brief.get("headline"), 500) or sources[index].title,
                "summary": clean_text(brief.get("summary"), 1200) or sources[index].snippet,
                "confidence": max(0.0, min(1.0, float(brief.get("confidence") or 0.5))),
            }
        )
    return valid or None


def monthly_usage(cur, provider: str) -> int:
    cur.execute(
        """SELECT COALESCE(SUM(request_count), 0)::integer
           FROM company_radar_usage
           WHERE provider=%s AND created_at >= date_trunc('month', now())""",
        (provider,),
    )
    return int(cur.fetchone()[0])


def candidate_companies(cur, limit: int) -> list[dict[str, Any]]:
    cur.execute(
        """
        WITH latest_day AS (SELECT MAX(snapshot_date) AS day FROM company_radar_daily),
        latest AS (
          SELECT r.* FROM company_radar_daily r JOIN latest_day d ON r.snapshot_date=d.day
          WHERE r.domain='all'
        ), followed AS (
          SELECT company_id, COUNT(*)::integer AS follower_count FROM user_followed_companies GROUP BY company_id
        )
        SELECT c.company_id, c.company_name, COALESCE(f.follower_count,0) AS follower_count,
               l.active_opportunities, l.new_opportunities_7d,
               s.last_attempted_at, s.last_succeeded_at
        FROM latest l
        JOIN companies c ON c.company_id=l.company_id
        LEFT JOIN followed f ON f.company_id=l.company_id
        LEFT JOIN company_radar_research_state s ON s.company_id=l.company_id
        WHERE c.company_name IS NOT NULL
          AND (s.last_attempted_at IS NULL OR s.last_attempted_at < now()-interval '7 days')
        ORDER BY (COALESCE(f.follower_count,0)>0) DESC, f.follower_count DESC NULLS LAST,
                 l.new_opportunities_7d DESC, l.active_opportunities DESC
        LIMIT %s
        """,
        (limit,),
    )
    return [dict(row) for row in cur.fetchall()]


def serper_news(company_name: str) -> list[Source]:
    key = os.getenv("SERPER_API_KEY")
    if not key:
        return []
    query = f'"{company_name}" (hiring OR expansion OR funding OR layoffs OR earnings OR leadership)'
    response = requests.post(
        SERPER_NEWS_URL,
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        json={"q": query, "num": 10, "tbs": "qdr:m"},
        timeout=25,
    )
    response.raise_for_status()
    return [source for source in normalize_sources(response.json()) if source_mentions_company(company_name, source)]


def write_company(cur, company: dict[str, Any], sources: list[Source], briefs: list[dict[str, Any]], ai_used: bool):
    by_index = {brief["source_index"]: brief for brief in briefs}
    inserted = 0
    for index, source in enumerate(sources):
        source_ai_used = ai_used and index in by_index
        brief = by_index.get(index) or deterministic_brief(source)
        cur.execute(
            """
            INSERT INTO company_research_events (
              company_id,event_type,headline,summary,source_url,source_domain,source_title,
              source_published_at,provider,evidence,confidence,fetched_at,expires_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,
              CASE WHEN %s IS NULL THEN NULL ELSE %s::timestamptz END,
              %s,%s,%s,now(),now()+interval '45 days')
            ON CONFLICT (company_id,source_url) DO UPDATE SET
              event_type=EXCLUDED.event_type,headline=EXCLUDED.headline,summary=EXCLUDED.summary,
              source_domain=EXCLUDED.source_domain,source_title=EXCLUDED.source_title,
              source_published_at=COALESCE(EXCLUDED.source_published_at,company_research_events.source_published_at),
              provider=EXCLUDED.provider,evidence=EXCLUDED.evidence,confidence=EXCLUDED.confidence,
              fetched_at=now(),expires_at=now()+interval '45 days'
            """,
            (
                company["company_id"], brief["event_type"], brief["headline"], brief["summary"],
                source.url, source.domain, source.title, source.published_at, source.published_at,
                "serper+openai" if source_ai_used else "serper",
                Json({"snippet": source.snippet, "ai_grounded": source_ai_used}),
                brief["confidence"],
            ),
        )
        inserted += 1
    cur.execute(
        """INSERT INTO company_radar_research_state(company_id,last_attempted_at,last_succeeded_at,last_error,source_count,updated_at)
           VALUES(%s,now(),now(),NULL,%s,now())
           ON CONFLICT(company_id) DO UPDATE SET last_attempted_at=now(),last_succeeded_at=now(),
             last_error=NULL,source_count=%s,updated_at=now()""",
        (company["company_id"], inserted, inserted),
    )
    return inserted


def record_failure(cur, company_id: str, error: Exception):
    cur.execute(
        """INSERT INTO company_radar_research_state(company_id,last_attempted_at,last_error,updated_at)
           VALUES(%s,now(),%s,now())
           ON CONFLICT(company_id) DO UPDATE SET last_attempted_at=now(),last_error=%s,updated_at=now()""",
        (company_id, clean_text(error, 1000), clean_text(error, 1000)),
    )


def main():
    parser = argparse.ArgumentParser(description="Refresh sourced Company Radar research")
    parser.add_argument("--apply", action="store_true", help="Write results (default is a candidate-only dry run)")
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()
    limit = max(1, min(args.limit, 200))

    if not os.getenv("SERPER_API_KEY"):
        log.warning("SERPER_API_KEY is not configured; no external research performed")
        return

    db = db_conn()
    db.autocommit = False
    try:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            serper_remaining = max(0, MONTHLY_SERPER_BUDGET - monthly_usage(cur, "serper"))
            ai_remaining = max(0, MONTHLY_AI_BUDGET - monthly_usage(cur, "openai"))
            companies = candidate_companies(cur, min(limit, serper_remaining))
            log.info("Selected %s companies; Serper budget remaining=%s, AI budget remaining=%s", len(companies), serper_remaining, ai_remaining)
            if not args.apply:
                for company in companies:
                    log.info("DRY RUN %s (%s)", company["company_name"], company["company_id"])
                db.rollback()
                return

            for company in companies:
                try:
                    sources = serper_news(company["company_name"])
                    cur.execute(
                        """INSERT INTO company_radar_usage(provider,operation,company_id,request_count,estimated_cost_usd,metadata)
                           VALUES('serper','company_news',%s,1,%s,%s)""",
                        (company["company_id"], SERPER_COST_PER_QUERY, Json({"results": len(sources)})),
                    )
                    briefs = None
                    ai_used = False
                    if sources and ai_remaining > 0 and os.getenv("OPENAI_API_KEY"):
                        try:
                            briefs = ai_briefs(company["company_name"], sources)
                            ai_used = bool(briefs)
                            ai_remaining -= 1
                            cur.execute(
                                """INSERT INTO company_radar_usage(provider,operation,company_id,request_count,estimated_cost_usd,metadata)
                                   VALUES('openai','grounded_company_brief',%s,1,%s,%s)""",
                                (company["company_id"], AI_COST_PER_CALL, Json({"model": AI_MODEL, "sources": len(sources)})),
                            )
                        except Exception as exc:
                            log.warning("AI brief failed for %s; using source snippets: %s", company["company_name"], exc)
                    fallback = [dict(deterministic_brief(source), source_index=i) for i, source in enumerate(sources)]
                    inserted = write_company(cur, company, sources, briefs or fallback, ai_used)
                    db.commit()
                    log.info("Stored %s sourced events for %s", inserted, company["company_name"])
                except Exception as exc:
                    db.rollback()
                    with db.cursor() as fail_cur:
                        record_failure(fail_cur, company["company_id"], exc)
                    db.commit()
                    log.exception("Research failed for %s", company["company_name"])
    finally:
        db.close()


if __name__ == "__main__":
    main()
