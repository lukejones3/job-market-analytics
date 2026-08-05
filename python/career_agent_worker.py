#!/usr/bin/env python3
"""Lander Career Agent worker.

Claims durable campaign rows, combines Lander's live job/contact graph with
independent recruiter web research, then writes a sourced approval queue.
Nothing in this worker sends email or messages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor, Json

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("career-agent")

SERPER_URL = "https://google.serper.dev/search"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MODEL = os.getenv("CAREER_AGENT_MODEL", "gpt-5-nano")
MAX_LEADS = int(os.getenv("CAREER_AGENT_MAX_LEADS", "20"))


def conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST"), port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE"), user=os.getenv("PGUSER"), password=os.getenv("PGPASSWORD")
    )


def event(cur, campaign_id: str, stage: str, message: str, metadata: dict | None = None):
    cur.execute(
        "INSERT INTO career_run_events(campaign_id,stage,message,metadata) VALUES(%s,%s,%s,%s)",
        (campaign_id, stage, message, Json(metadata or {})),
    )
    cur.execute(
        "UPDATE career_campaigns SET stage=%s,status_message=%s,updated_at=now() WHERE campaign_id=%s",
        (stage, message, campaign_id),
    )


def usage(cur, campaign_id: str, provider: str, operation: str, units: float, cost: float, metadata=None):
    cur.execute(
        "INSERT INTO career_agent_usage(campaign_id,provider,operation,units,estimated_cost_usd,metadata) VALUES(%s,%s,%s,%s,%s,%s)",
        (campaign_id, provider, operation, units, cost, Json(metadata or {})),
    )


def claim_campaign(db, campaign_id: str | None = None) -> dict | None:
    with db.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("BEGIN")
        if campaign_id:
            cur.execute(
                "SELECT * FROM career_campaigns WHERE campaign_id=%s AND status='queued' FOR UPDATE SKIP LOCKED", (campaign_id,)
            )
        else:
            cur.execute(
                "SELECT * FROM career_campaigns WHERE status='queued' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
            )
        row = cur.fetchone()
        if not row:
            db.rollback()
            return None
        cur.execute(
            "UPDATE career_campaigns SET status='running',stage='starting',status_message='Research worker started',started_at=now(),updated_at=now() WHERE campaign_id=%s",
            (row["campaign_id"],),
        )
        cur.execute(
            "INSERT INTO career_run_events(campaign_id,stage,message) VALUES(%s,'starting','Research worker started')",
            (row["campaign_id"],),
        )
        db.commit()
        return dict(row)


def load_profile(cur, user_id: str) -> tuple[str, dict]:
    cur.execute("SELECT candidate_summary,links FROM career_profiles WHERE user_id=%s", (user_id,))
    row = cur.fetchone() or {}
    summary = (row.get("candidate_summary") or "").strip()
    # Use the private resume only to create the candidate packet; it is never sent to search.
    if not summary:
        cur.execute("SELECT to_regclass('public.resumes') AS table_name")
        if (cur.fetchone() or {}).get("table_name"):
            cur.execute(
                "SELECT resume_text FROM resumes WHERE user_id=%s AND status='matched' ORDER BY updated_at DESC LIMIT 1", (user_id,)
            )
            resume = cur.fetchone()
            if resume and resume.get("resume_text"):
                summary = re.sub(r"\s+", " ", resume["resume_text"])[:6000]
    return summary, row.get("links") or {}


def target_jobs(cur, requirements: dict, limit: int = 160) -> list[dict]:
    locations = [str(value) for value in requirements.get("locations", [])]
    roles = [str(value) for value in requirements.get("roleFamilies", [])]
    skills = [str(value) for value in requirements.get("skills", [])]
    min_salary = requirements.get("minimumSalary")
    terms = list(dict.fromkeys(roles + skills))
    values: list[Any] = []
    where = ["jp.status='raw'", "jp.data_tier=1", "COALESCE(jp.source,'') <> 'adzuna'"]
    if terms:
        values.append(terms)
        where.append("EXISTS (SELECT 1 FROM unnest(%s::text[]) term WHERE lower(COALESCE(r.role_name,'') || ' ' || COALESCE(jp.role_category,'') || ' ' || COALESCE(jp.description_text,'')) LIKE '%%' || lower(term) || '%%')")
    if locations:
        values.append(locations)
        where.append("EXISTS (SELECT 1 FROM unnest(%s::text[]) loc WHERE lower(COALESCE(jp.loc_city,'') || ' ' || COALESCE(jp.loc_state,'') || ' ' || COALESCE(l.location,'')) LIKE '%%' || lower(loc) || '%%') OR lower(COALESCE(jp.workplace_type,''))='remote'")
    if min_salary:
        values.append(float(min_salary) * 0.8)
        where.append("(jp.salary_max_annual IS NULL OR jp.salary_max_annual >= %s)")
    values.append(limit)
    cur.execute(
        f"""
        SELECT jp.job_id::text,COALESCE(r.role_name,jp.role_category,'Role') AS title,c.company_id::text,
               c.company_name,jp.job_url,jp.posted_date::text,jp.workplace_type,
               jp.salary_min_annual,jp.salary_max_annual,jp.domain,
               COALESCE(jp.loc_city,'') AS city,COALESCE(jp.loc_state,l.state,'') AS state
        FROM job_postings jp JOIN companies c ON c.company_id=jp.company_id
        LEFT JOIN roles r ON r.role_id=jp.role_id LEFT JOIN locations l ON l.location_id=jp.location_id
        WHERE {' AND '.join(where)}
        ORDER BY jp.posted_date DESC NULLS LAST LIMIT %s
        """, values
    )
    return [dict(row) for row in cur.fetchall()]


def lander_contacts(cur, jobs: list[dict]) -> list[dict]:
    company_ids = list(dict.fromkeys(job["company_id"] for job in jobs))
    if not company_ids:
        return []
    cur.execute(
        """
        SELECT cc.contact_id::text,cc.full_name,cc.company_name AS firm,cc.title,cc.email AS business_email,
               cc.linkedin_url,cc.domain AS specialty,cc.source,cc.fetched_at::text,
               cc.company_id::text
        FROM company_contacts cc WHERE cc.company_id::text = ANY(%s::text[])
        ORDER BY cc.fetched_at DESC NULLS LAST
        """, (company_ids,)
    )
    by_company: dict[str, list[dict]] = {}
    for job in jobs:
        by_company.setdefault(job["company_id"], []).append(job)
    contacts = []
    for row in cur.fetchall():
        item = dict(row)
        title = str(item.get("title") or "")
        # A live opening does not make an unrelated executive a useful lead.
        if not re.search(r"recruit|talent acquisition|talent partner|staffing|sourc|people partner|human resources", title, re.I):
            continue
        openings = by_company.get(item["company_id"], [])[:3]
        item.update({
            "source_kind": "lander", "evidence_urls": [opening["job_url"] for opening in openings if opening.get("job_url")],
            "evidence": f"Lander has {len(by_company.get(item['company_id'], []))} current matching openings at {item['firm']}; contact sourced from {item.get('source') or 'company contact data'}.",
            "openings": [{"title": opening["title"], "company": opening["company_name"], "url": opening.get("job_url"), "source": "Lander"} for opening in openings],
            "location": None,
        })
        contacts.append(item)
    return contacts


def search_queries(requirements: dict) -> list[str]:
    roles = requirements.get("roleFamilies") or requirements.get("skills") or ["technology"]
    locations = requirements.get("locations") or (["United States"] if requirements.get("remoteAllowed", True) else [])
    role_phrase = " OR ".join(f'"{role}"' for role in roles[:4])
    location = locations[0] if locations else "United States"
    queries = [
        f'site:linkedin.com/in (recruiter OR "talent acquisition") ({role_phrase}) "{location}"',
        f'("technical recruiter" OR "data recruiter" OR "AI recruiter") "{location}" ({role_phrase})',
        f'site:linkedin.com/posts recruiter ({role_phrase}) (hiring OR opportunity) "{location}"',
        f'("staffing" OR "recruiting firm") ({role_phrase}) "{location}"',
    ]
    if requirements.get("remoteAllowed", True):
        queries.extend([
            f'site:linkedin.com/in recruiter ({role_phrase}) "remote"',
            f'site:linkedin.com/posts recruiter ({role_phrase}) "remote" hiring',
        ])
    return queries[:8]


def serper_search(query: str) -> list[dict]:
    key = os.getenv("SERPER_API_KEY")
    if not key:
        return []
    response = requests.post(SERPER_URL, headers={"X-API-KEY": key, "Content-Type": "application/json"}, json={"q": query, "num": 10}, timeout=25)
    response.raise_for_status()
    return response.json().get("organic", [])


EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])[a-z0-9][a-z0-9._%+-]{0,63}@[a-z0-9.-]+\.[a-z]{2,}(?![\w.-])")


def public_email_from_results(results: list[dict]) -> tuple[str | None, str | None]:
    """Return only an address literally present in public search evidence."""
    rejected = {"example.com", "email.com", "domain.com"}
    for result in results:
        haystack = " ".join(str(result.get(key) or "") for key in ("title", "snippet"))
        for email in EMAIL_RE.findall(haystack):
            normalized = email.lower().strip(".,;:()[]{}<>")
            local, domain = normalized.rsplit("@", 1)
            if domain in rejected or local in {"noreply", "no-reply", "support", "privacy", "info"}:
                continue
            return normalized, result.get("link")
    return None, None


def enrich_recruiter_emails(contacts: list[dict], limit: int = 24) -> tuple[list[dict], int]:
    queries = 0
    for contact in contacts[:limit]:
        if contact.get("business_email"):
            continue
        name = str(contact.get("full_name") or "").strip()
        firm = str(contact.get("firm") or "").replace("Independent / verify", "").strip()
        if not name:
            continue
        query = f'"{name}" recruiter email' + (f' "{firm}"' if firm else "")
        try:
            results = serper_search(query)
            queries += 1
            email, source_url = public_email_from_results(results)
            if email:
                contact["business_email"] = email
                if source_url:
                    contact["evidence_urls"] = list(dict.fromkeys(contact.get("evidence_urls", []) + [source_url]))
                contact["evidence"] = f"{contact.get('evidence') or ''} Public business email found in cited web evidence.".strip()
        except Exception as exc:
            log.warning("Recruiter email search failed for %s: %s", name, exc)
    return contacts, queries


def extract_json(text: str) -> Any:
    cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(cleaned)


def model_json(system: str, payload: dict) -> tuple[Any, dict]:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not configured")
    response = requests.post(
        OPENAI_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": MODEL, "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload)}], "response_format": {"type": "json_object"}},
        timeout=90,
    )
    response.raise_for_status()
    body = response.json()
    return extract_json(body["choices"][0]["message"]["content"]), body.get("usage", {})


def fallback_web_contacts(results: list[dict]) -> list[dict]:
    contacts = []
    for result in results:
        link = result.get("link", "")
        title = re.sub(r"\s*\|\s*LinkedIn.*$", "", result.get("title", ""), flags=re.I)
        if "linkedin.com/in/" not in link or " - " not in title:
            continue
        name, rest = title.split(" - ", 1)
        if not re.search(r"recruit|talent|staff", rest, re.I):
            continue
        contacts.append({"full_name": name.strip(), "title": rest.strip(), "firm": "Independent / verify", "linkedin_url": link, "location": None, "specialty": None, "business_email": None, "evidence": result.get("snippet", ""), "evidence_urls": [link], "openings": []})
    return contacts


def web_contacts(results: list[dict], requirements: dict) -> tuple[list[dict], dict]:
    unique = {result.get("link"): result for result in results if result.get("link")}.values()
    compact = [{"title": r.get("title"), "url": r.get("link"), "snippet": r.get("snippet"), "date": r.get("date")} for r in unique]
    try:
        parsed, model_usage = model_json(
            "Extract professional recruiters from supplied search results only. Never invent a person, employer, email, URL, opening, or fact. Return JSON with key leads. Each lead: full_name, firm, title, location, specialty, linkedin_url, evidence, evidence_urls, relevant_openings. Include recruiters at staffing firms and independent recruiters even when no listed company job exists. Exclude job-seeker profiles and unverifiable names.",
            {"requirements": requirements, "search_results": compact},
        )
        leads = parsed.get("leads", []) if isinstance(parsed, dict) else []
        allowed_urls = {item["url"] for item in compact}
        safe = []
        for lead in leads:
            urls = [url for url in lead.get("evidence_urls", []) if url in allowed_urls]
            linkedin = lead.get("linkedin_url") if lead.get("linkedin_url") in allowed_urls else next((url for url in urls if "linkedin.com/in/" in url), None)
            if not lead.get("full_name") or not urls:
                continue
            safe.append({**lead, "linkedin_url": linkedin, "evidence_urls": urls, "business_email": None, "openings": lead.get("relevant_openings", [])})
        return safe, model_usage
    except Exception as exc:
        log.warning("Model extraction failed, using strict parser: %s", exc)
        return fallback_web_contacts(list(unique)), {}


def contact_key(contact: dict) -> str:
    identity = contact.get("linkedin_url") or f"{contact.get('full_name','')}|{contact.get('firm','')}"
    return "pc_" + hashlib.sha256(identity.lower().encode()).hexdigest()[:24]


def merge_contacts(lander: list[dict], web: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for item in lander + web:
        key = (item.get("linkedin_url") or f"{item.get('full_name')}|{item.get('firm')}").lower()
        if key in merged:
            prior = merged[key]
            prior.update({k: v for k, v in item.items() if v and not prior.get(k)})
            prior["source_kind"] = "both"
            prior["evidence_urls"] = list(dict.fromkeys(prior.get("evidence_urls", []) + item.get("evidence_urls", [])))
            prior["openings"] = (prior.get("openings", []) + item.get("openings", []))[:5]
        else:
            merged[key] = {**item, "source_kind": item.get("source_kind", "web")}
    return list(merged.values())


def score_contact(contact: dict, requirements: dict) -> float:
    text = " ".join(str(contact.get(key) or "") for key in ("title", "specialty", "evidence", "location", "firm")).lower()
    roles = [str(value).lower() for value in requirements.get("roleFamilies", []) + requirements.get("skills", [])]
    locations = [str(value).lower() for value in requirements.get("locations", [])]
    score = 34.0
    score += 10 if re.search(r"recruit|talent acquisition|staffing", text) else 0
    score += 5 if re.search(r"staffing|recruiting|talent", str(contact.get("firm") or ""), re.I) else 0
    score += min(25, 7 * sum(term in text for term in roles))
    score += min(14, 7 * sum(location in text for location in locations))
    score += 12 if contact.get("openings") else 0
    score += 8 if contact.get("source_kind") == "both" else 4 if contact.get("source_kind") == "lander" else 0
    score += 5 if contact.get("linkedin_url") else 0
    score += 6 if contact.get("business_email") else 0
    return min(99.0, score)


def select_contacts(contacts: list[dict], limit: int = MAX_LEADS) -> list[dict]:
    """Keep independent discovery first-class alongside job-grounded leads."""
    ranked = sorted(contacts, key=lambda item: item["score"], reverse=True)
    web = [item for item in ranked if item.get("source_kind") in {"web", "both"}]
    lander = [item for item in ranked if item.get("source_kind") == "lander"]
    selected = web[: min(10, limit)]
    selected_keys = {contact_key(item) for item in selected}
    for item in lander + ranked:
        if len(selected) >= limit:
            break
        if contact_key(item) not in selected_keys:
            selected.append(item)
            selected_keys.add(contact_key(item))
    return sorted(selected, key=lambda item: item["score"], reverse=True)


def draft_leads(contacts: list[dict], requirements: dict, summary: str, links: dict) -> tuple[dict[str, dict], dict]:
    compact = [{"id": contact_key(c), "name": c.get("full_name"), "firm": c.get("firm"), "title": c.get("title"), "evidence": c.get("evidence"), "openings": c.get("openings", [])[:2]} for c in contacts]
    try:
        parsed, model_usage = model_json(
            "Write concise, specific recruiter outreach using only supplied facts. Do not use inflated praise. Return JSON object with key drafts, an array of: id, subject, body, connection_message, match_reason. Body should be 110-180 words, direct, human, and mention the strongest true evidence. connection_message must be 200 characters or fewer. The candidate is asking to connect or enter the recruiter's candidate pool, not claiming entitlement to a role.",
            {"requirements": requirements, "candidate_summary": summary[:5000], "links": links, "contacts": compact},
        )
        return {item["id"]: item for item in parsed.get("drafts", []) if item.get("id")}, model_usage
    except Exception as exc:
        log.warning("Draft generation failed, using grounded template: %s", exc)
        drafts = {}
        roles = ", ".join(requirements.get("roleFamilies", [])[:3]) or "technical data and automation"
        locations = ", ".join(requirements.get("locations", [])[:2]) or "remote"
        for contact in contacts:
            cid = contact_key(contact)
            first = (contact.get("full_name") or "there").split()[0]
            drafts[cid] = {"id": cid, "subject": f"{locations} {roles} candidate", "body": f"Hi {first} — I’m targeting {locations} {roles} roles and found your recruiting work while researching the market. My background includes production SQL/Python data systems, workflow automation, and independently shipped technical products.\n\nI’m open to relevant direct-hire, contract, or contract-to-hire opportunities. Would you be open to connecting or adding me to your candidate pool? I can send a concise resume and project links.", "connection_message": f"Hi {first} — I’m targeting {locations} {roles} roles and your recruiting focus looked relevant. Open to connecting?", "match_reason": contact.get("evidence") or "Recruiting focus overlaps the requested search."}
        return drafts, {}


def estimated_model_cost(model_usage: dict) -> float:
    prompt = float(model_usage.get("prompt_tokens", 0))
    completion = float(model_usage.get("completion_tokens", 0))
    # Default estimate for GPT-5 nano; exact usage is retained for later reconciliation.
    return prompt / 1_000_000 * 0.05 + completion / 1_000_000 * 0.40


def process(db, campaign: dict):
    campaign_id = campaign["campaign_id"]
    requirements = campaign.get("requirements") or {}
    with db.cursor(cursor_factory=RealDictCursor) as cur:
        try:
            summary, links = load_profile(cur, campaign["user_id"])
            event(cur, campaign_id, "lander_search", "Searching Lander's live hiring graph")
            db.commit()
            jobs = target_jobs(cur, requirements)
            l_contacts = lander_contacts(cur, jobs)
            event(cur, campaign_id, "web_search", f"Found {len(jobs)} matching openings; expanding into independent recruiter search", {"jobs": len(jobs), "lander_contacts": len(l_contacts)})
            db.commit()

            queries = search_queries(requirements)
            results = []
            for query in queries:
                try:
                    results.extend(serper_search(query))
                except Exception as exc:
                    log.warning("Serper query failed: %s", exc)
            usage(cur, campaign_id, "serper", "recruiter_search", len(queries), 0, {"queries": queries})
            event(cur, campaign_id, "verification", f"Reviewing {len(results)} web results for recruiter evidence", {"queries": len(queries), "results": len(results)})
            db.commit()

            w_contacts, extraction_usage = web_contacts(results, requirements)
            if extraction_usage:
                usage(cur, campaign_id, "openai", "evidence_extraction", extraction_usage.get("total_tokens", 0), estimated_model_cost(extraction_usage), extraction_usage)
            contacts = merge_contacts(l_contacts, w_contacts)
            event(cur, campaign_id, "email_search", f"Searching public sources for recruiter email addresses", {"contacts": len(contacts)})
            db.commit()
            contacts, email_queries = enrich_recruiter_emails(contacts)
            usage(cur, campaign_id, "serper", "public_email_search", email_queries, 0, {"contacts_checked": min(len(contacts), 24)})
            for contact in contacts:
                contact["score"] = score_contact(contact, requirements)
            contacts = select_contacts(contacts)
            event(cur, campaign_id, "drafting", f"Preparing {len(contacts)} sourced contacts for approval", {"web_contacts": len(w_contacts), "lander_contacts": len(l_contacts)})
            db.commit()

            drafts, draft_usage = draft_leads(contacts, requirements, summary, links)
            if draft_usage:
                usage(cur, campaign_id, "openai", "outreach_drafting", draft_usage.get("total_tokens", 0), estimated_model_cost(draft_usage), draft_usage)

            for rank, contact in enumerate(contacts, 1):
                cid = contact_key(contact)
                draft = drafts.get(cid, {})
                kinds = [contact.get("source_kind", "web")]
                cur.execute(
                    """INSERT INTO professional_contacts(contact_id,full_name,firm,title,location,specialty,linkedin_url,business_email,source_kinds,evidence,evidence_urls,verified_at,updated_at)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),now())
                       ON CONFLICT(contact_id) DO UPDATE SET full_name=EXCLUDED.full_name,firm=EXCLUDED.firm,
                         title=COALESCE(EXCLUDED.title,professional_contacts.title),location=COALESCE(EXCLUDED.location,professional_contacts.location),
                         specialty=COALESCE(EXCLUDED.specialty,professional_contacts.specialty),linkedin_url=COALESCE(EXCLUDED.linkedin_url,professional_contacts.linkedin_url),
                         business_email=COALESCE(EXCLUDED.business_email,professional_contacts.business_email),source_kinds=ARRAY(SELECT DISTINCT unnest(professional_contacts.source_kinds||EXCLUDED.source_kinds)),
                         evidence=EXCLUDED.evidence,evidence_urls=EXCLUDED.evidence_urls,verified_at=now(),updated_at=now()""",
                    (cid, contact.get("full_name"), contact.get("firm") or "Independent recruiter", contact.get("title"), contact.get("location"), contact.get("specialty"), contact.get("linkedin_url"), contact.get("business_email"), kinds, contact.get("evidence"), Json(contact.get("evidence_urls", []))),
                )
                cur.execute(
                    """INSERT INTO career_campaign_leads(campaign_id,contact_id,rank,score,source_kind,match_reason,relevant_job_ids,relevant_openings,draft_subject,draft_body,connection_message)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(campaign_id,contact_id) DO UPDATE SET rank=EXCLUDED.rank,score=EXCLUDED.score,source_kind=EXCLUDED.source_kind,
                         match_reason=EXCLUDED.match_reason,relevant_openings=EXCLUDED.relevant_openings,draft_subject=EXCLUDED.draft_subject,draft_body=EXCLUDED.draft_body,connection_message=EXCLUDED.connection_message,updated_at=now()""",
                    (campaign_id, cid, rank, contact["score"], contact.get("source_kind", "web"), draft.get("match_reason") or contact.get("evidence"), [opening.get("job_id") for opening in contact.get("openings", []) if opening.get("job_id")], Json(contact.get("openings", [])), draft.get("subject"), draft.get("body"), (draft.get("connection_message") or "")[:200]),
                )

            cur.execute(
                "UPDATE career_campaigns SET status='ready',stage='ready',status_message=%s,lead_count=%s,completed_at=now(),updated_at=now() WHERE campaign_id=%s",
                (f"{len(contacts)} contacts are ready for approval", len(contacts), campaign_id),
            )
            event(cur, campaign_id, "ready", f"{len(contacts)} sourced recruiter contacts ready for approval", {"leads": len(contacts)})
            db.commit()
        except Exception as exc:
            db.rollback()
            log.exception("Campaign %s failed", campaign_id)
            with db.cursor() as fail_cur:
                fail_cur.execute("UPDATE career_campaigns SET status='failed',stage='failed',error=%s,status_message='Research failed',completed_at=now(),updated_at=now() WHERE campaign_id=%s", (str(exc)[:2000], campaign_id))
                fail_cur.execute("INSERT INTO career_run_events(campaign_id,stage,message,metadata) VALUES(%s,'failed','Campaign research failed',%s)", (campaign_id, Json({"error": str(exc)[:500]})))
            db.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--campaign-id")
    parser.add_argument("--poll-seconds", type=int, default=8)
    args = parser.parse_args()
    db = conn()
    db.autocommit = False
    try:
        while True:
            campaign = claim_campaign(db, args.campaign_id)
            if campaign:
                log.info("Processing %s", campaign["campaign_id"])
                process(db, campaign)
            if args.once or args.campaign_id:
                break
            time.sleep(args.poll_seconds)
    finally:
        db.close()


if __name__ == "__main__":
    main()
