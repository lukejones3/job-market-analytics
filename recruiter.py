import streamlit as st
import psycopg2
import pandas as pd
import os
from datetime import datetime, timedelta

st.set_page_config(
    page_title="DataHiringIQ · Recruiter Intelligence",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=IBM+Plex+Mono:wght@300;400;500&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap');

html, body, [class*="css"] { font-family: 'IBM Plex Mono', monospace; }
h1, h2, h3 { font-family: 'Bebas Neue', sans-serif !important; letter-spacing: 0.05em; }

.stApp { background-color: #080810; color: #d4d4d8; }

section[data-testid="stSidebar"] { display: none; }

div[data-testid="stMetric"] {
    background: #0e0e1a;
    border: 1px solid #1e1e32;
    border-radius: 3px;
    padding: 14px;
}
div[data-testid="stMetric"] label { color: #555 !important; font-size: 0.65rem !important; text-transform: uppercase; letter-spacing: 0.1em; }
div[data-testid="stMetric"] [data-testid="stMetricValue"] {
    color: #e2ff5d !important;
    font-family: 'Bebas Neue', sans-serif !important;
    font-size: 2rem !important;
}

.job-card {
    background: #0c0c18;
    border: 1px solid #1a1a2e;
    border-radius: 4px;
    padding: 18px 20px;
    margin-bottom: 6px;
    transition: border-color 0.15s;
}
.job-card:hover { border-color: #2a2a4a; }
.job-card.signal-fresh { border-left: 3px solid #e2ff5d; }
.job-card.signal-strong { border-left: 3px solid #4ade80; }
.job-card.signal-moderate { border-left: 3px solid #facc15; }
.job-card.signal-weak { border-left: 3px solid #f87171; }

.job-title {
    font-family: 'IBM Plex Sans', sans-serif;
    font-size: 1rem;
    font-weight: 600;
    color: #f0f0f4;
    margin-bottom: 2px;
}
.job-company {
    font-size: 0.75rem;
    color: #888;
    margin-bottom: 10px;
}
.badge {
    display: inline-block;
    font-size: 0.62rem;
    padding: 2px 8px;
    border-radius: 2px;
    margin-right: 4px;
    font-family: 'IBM Plex Mono', monospace;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
.badge-quality { background: #1a2a1a; color: #4ade80; border: 1px solid #4ade8033; }
.badge-urgency { background: #1a1a2a; color: #818cf8; border: 1px solid #818cf833; }
.badge-salary { background: #2a1a0a; color: #fb923c; border: 1px solid #fb923c33; }
.badge-sector { background: #1a1a1a; color: #71717a; border: 1px solid #27272a; }
.badge-age { background: #0a0a1a; color: #a78bfa; border: 1px solid #a78bfa33; }
.badge-source { background: #0a1a1a; color: #22d3ee; border: 1px solid #22d3ee22; }
.badge-location { background: #1a1a0a; color: #a3e635; border: 1px solid #a3e63533; }

.signal-dot-fresh { color: #e2ff5d; }
.signal-dot-strong { color: #4ade80; }
.signal-dot-moderate { color: #facc15; }
.signal-dot-weak { color: #f87171; }

.skill-tag {
    display: inline-block;
    background: #13131f;
    border: 1px solid #1e1e32;
    color: #6b6b8a;
    font-size: 0.6rem;
    padding: 1px 7px;
    border-radius: 2px;
    margin: 1px;
    font-family: 'IBM Plex Mono', monospace;
}

.section-divider {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.6rem;
    color: #333;
    text-transform: uppercase;
    letter-spacing: 0.2em;
    border-bottom: 1px solid #131320;
    padding-bottom: 6px;
    margin: 20px 0 14px 0;
}

.paywall {
    max-width: 560px;
    margin: 80px auto;
    text-align: center;
    padding: 48px;
    background: #0c0c18;
    border: 1px solid #1e1e32;
    border-radius: 6px;
}

.stSelectbox label, .stMultiSelect label { color: #666 !important; font-size: 0.65rem !important; text-transform: uppercase; letter-spacing: 0.08em; }
.stTextInput label { color: #666 !important; font-size: 0.65rem !important; }

.hiring-manager-cell {
    font-size: 0.7rem;
    color: #4ade80;
}
.hiring-manager-empty {
    font-size: 0.65rem;
    color: #333;
    font-style: italic;
}
</style>
""", unsafe_allow_html=True)

# ── DB Connection ─────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(
        host=st.secrets.get("db_host", os.getenv("PGHOST", "208.68.38.249")),
        port=int(st.secrets.get("db_port", os.getenv("PGPORT", 5432))),
        dbname=st.secrets.get("db_name", os.getenv("PGDATABASE", "job_analytics")),
        user=st.secrets.get("db_user", os.getenv("PGUSER", "lukejones")),
        password=st.secrets.get("db_password", os.getenv("PGPASSWORD", "")),
    )

@st.cache_data(ttl=1800)
def query(sql, params=None):
    conn = get_conn()
    try:
        return pd.read_sql(sql, conn, params=params)
    finally:
        conn.close()

def verify_token(token: str) -> dict | None:
    """Check token against api_keys table. Returns client info or None."""
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT client_name, client_email, tier, active
            FROM api_keys
            WHERE api_key_prefix = %s AND active = true
            LIMIT 1
        """, (token[:8] if len(token) >= 8 else token,))
        row = cur.fetchone()
        conn.close()
        if row:
            return {"name": row[0], "email": row[1], "tier": row[2]}
        return None
    except Exception:
        return None

# ── Signal strength mapping ───────────────────────────────────────────────────
def signal_strength(urgency_score):
    """Convert hiring urgency score to signal label."""
    if urgency_score is None:
        return "unknown", "●"
    u = float(urgency_score)
    if u >= 85:
        return "fresh", "⚡"
    if u >= 65:
        return "strong", "●"
    if u >= 40:
        return "moderate", "●"
    return "weak", "●"

def urgency_from_ghost(ghost_prob, days_old):
    """Invert ghost probability + recency into hiring urgency score."""
    if ghost_prob is None:
        ghost_prob = 50
    base = 100 - float(ghost_prob)
    # Boost for recency
    if days_old <= 1:
        base = min(100, base + 20)
    elif days_old <= 3:
        base = min(100, base + 10)
    elif days_old >= 30:
        base = max(0, base - 15)
    return round(base, 1)

def posting_quality(honesty_score):
    """Map honesty score to posting quality score (same thing, different name)."""
    return honesty_score

# ── Token check ───────────────────────────────────────────────────────────────
params = st.query_params
token = params.get("token", "")

# For demo/dev — allow a hardcoded preview token
PREVIEW_TOKEN = "preview2026"
is_preview = (token == PREVIEW_TOKEN)

client = None
if token and not is_preview:
    client = verify_token(token)

# ── PAYWALL ───────────────────────────────────────────────────────────────────
if not token or (not client and not is_preview):
    st.markdown("""
    <div class="paywall">
        <div style="font-family:'Bebas Neue',sans-serif;font-size:3rem;color:#e2ff5d;
            letter-spacing:0.05em;line-height:1;margin-bottom:8px">
            DATAHIRINGIQ
        </div>
        <div style="font-size:0.65rem;color:#444;text-transform:uppercase;
            letter-spacing:0.2em;margin-bottom:32px">
            Recruiter Intelligence Feed
        </div>
        <p style="color:#666;font-size:0.85rem;line-height:1.7;margin-bottom:28px">
            Live data & ML job intelligence — updated nightly from Greenhouse,
            Workday, Lever, Ashby, and Eightfold. See fresh data & ML postings before they hit LinkedIn — with verified hiring manager contacts so you can reach out before anyone else.
        </p>
        <div style="background:#080810;border:1px solid #1e1e32;border-radius:4px;
            padding:20px;margin-bottom:28px;text-align:left">
            <div style="font-size:0.6rem;color:#444;text-transform:uppercase;
                letter-spacing:0.15em;margin-bottom:12px">What you get</div>
            <div style="font-size:0.75rem;color:#888;line-height:2">
                👤 Verified hiring manager contact — name, email & LinkedIn for data & ML teams<br>
                ⚡ Fresh postings — last 5 days, updated nightly<br>
                📊 Posting Quality Score — how complete & specific the role is<br>
                🎯 Hiring Urgency Score — likelihood the role is actively filling<br>
                💰 Salary data where disclosed (55%+ of roles)<br>
                🔧 Required skills for each role<br>
                🏢 Company sector, size, and hiring intensity
            </div>
        </div>
        <div style="font-family:'Bebas Neue',sans-serif;font-size:2rem;color:#e2ff5d;
            margin-bottom:4px">$500 / month</div>
        <div style="font-size:0.65rem;color:#444;margin-bottom:24px">
            Cancel anytime · One placement pays for months of access
        </div>
        <a href="https://buy.stripe.com/6oUbIUeBO1S7bQD8WYfnO00"
           style="background:#e2ff5d;color:#080810;font-family:'IBM Plex Mono',monospace;
               font-size:0.75rem;font-weight:500;padding:12px 28px;border-radius:3px;
               text-decoration:none;letter-spacing:0.05em;text-transform:uppercase">
            Subscribe Now →
        </a>
        <div style="margin-top:20px;font-size:0.6rem;color:#333">
            datahiringiq.com · jones31luke@gmail.com
        </div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# ── HEADER ────────────────────────────────────────────────────────────────────
client_name = client["name"] if client else "Preview"

st.markdown(f"""
<div style="display:flex;justify-content:space-between;align-items:flex-start;
    margin-bottom:32px;padding-bottom:20px;border-bottom:1px solid #131320">
    <div>
        <div style="font-family:'Bebas Neue',sans-serif;font-size:2.2rem;
            color:#e2ff5d;letter-spacing:0.05em;line-height:1">
            DATAHIRINGIQ
        </div>
        <div style="font-size:0.6rem;color:#444;text-transform:uppercase;
            letter-spacing:0.2em;margin-top:2px">
            Recruiter Intelligence · Fresh Job Feed
        </div>
    </div>
    <div style="text-align:right">
        <div style="font-size:0.65rem;color:#555">Welcome, {client_name}</div>
        <div style="font-size:0.6rem;color:#333;margin-top:2px">
            Updated nightly · {datetime.now().strftime('%b %d, %Y')}
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

# ── FILTERS ───────────────────────────────────────────────────────────────────
f1, f2, f3, f4, f5 = st.columns([2, 2, 1, 1, 1])

with f1:
    sectors_df = query("""
        SELECT DISTINCT c.sector FROM companies c
        JOIN job_postings jp ON jp.company_id = c.company_id
        WHERE c.sector IS NOT NULL AND jp.data_tier=1 AND jp.status='raw'
          AND jp.ingested_at > NOW() - INTERVAL '5 days'
        ORDER BY c.sector
    """)
    sector_filter = st.multiselect(
        "Sector", sectors_df["sector"].tolist(),
        placeholder="All sectors", key="rf_sector"
    )

with f2:
    role_types = ["Data Engineer", "Data Scientist", "ML Engineer", "AI Engineer",
                  "Data Analyst", "Analytics Engineer", "Leadership", "Revenue/Ops"]
    role_filter = st.multiselect("Role Type", role_types, placeholder="All roles", key="rf_role")

with f3:
    signal_filter = st.selectbox("Min Signal", ["All", "⚡ Fresh", "● Strong", "● Moderate"], key="rf_signal")

with f4:
    days_filter = st.selectbox("Posted within", ["5 days", "3 days", "24 hours", "12 hours"], key="rf_days")

with f5:
    salary_only = st.checkbox("Salary disclosed only", key="rf_salary")

days_map = {"5 days": 5, "3 days": 3, "24 hours": 1, "12 hours": 0.5}
days_back = days_map[days_filter]

# ── QUERY ─────────────────────────────────────────────────────────────────────
role_family_sql = """
CASE
    WHEN lower(r.role_name) ~ 'director|head of|vp |vice president|chief data|chief analytics|manager, data|manager, analytics|manager, ml|data science manager|analytics manager' THEN 'Leadership'
    WHEN lower(r.role_name) ~ 'data engineer|analytics engineer|data architect|data platform|mlops|data reliability|data infrastructure' THEN 'Data Engineer'
    WHEN lower(r.role_name) ~ 'machine learning|ml engineer|ai/ml|computer vision|applied scientist|applied researcher|ai researcher' THEN 'ML Engineer'
    WHEN lower(r.role_name) ~ 'ai engineer|applied ai|llm engineer|ai specialist' THEN 'AI Engineer'
    WHEN lower(r.role_name) ~ 'data scientist|quantitative researcher|research scientist|data science' THEN 'Data Scientist'
    WHEN lower(r.role_name) ~ 'analytics engineer' THEN 'Analytics Engineer'
    WHEN lower(r.role_name) ~ 'data analyst|business analyst|bi analyst|financial analyst|fp&a|product analyst|marketing analyst|fraud analyst|growth analyst|risk analyst|pricing analyst' THEN 'Data Analyst'
    WHEN lower(r.role_name) ~ 'sales op|revenue op|marketing op|operations analyst|revops' THEN 'Revenue/Ops'
    ELSE 'Other'
END
"""

where_clauses = [
    "jp.data_tier = 1",
    "(jp.source != 'workday' OR jp.posted_date IS NOT NULL)",
    "jp.status = 'raw'",
    f"(CASE WHEN jp.source = 'workday' AND jp.posted_date IS NOT NULL THEN jp.posted_date::timestamp ELSE jp.date_found END) > NOW() - INTERVAL '{days_back} days'",
    """(jp.source != 'workday' OR (
        SELECT COUNT(*) FROM job_postings jp2
        WHERE jp2.source = 'workday'
          AND DATE(jp2.date_found) = DATE(jp.date_found)
          AND jp2.data_tier = 1
    ) < 300)"""
]

if sector_filter:
    sectors_str = ",".join([f"'{s}'" for s in sector_filter])
    where_clauses.append(f"c.sector IN ({sectors_str})")

if role_filter:
    role_conditions = []
    role_map = {
        "Data Engineer": "data engineer|analytics engineer|data architect|data platform|mlops",
        "Data Scientist": "data scientist|quantitative researcher|research scientist",
        "ML Engineer": "machine learning|ml engineer|ai/ml|computer vision|applied scientist",
        "AI Engineer": "ai engineer|applied ai|llm engineer|ai specialist",
        "Data Analyst": "data analyst|business analyst|bi analyst|financial analyst|product analyst|marketing analyst",
        "Analytics Engineer": "analytics engineer",
        "Leadership": "director|head of|vp |vice president|chief data|manager, data|manager, analytics",
        "Revenue/Ops": "sales op|revenue op|marketing op|operations analyst|revops",
    }
    for rf in role_filter:
        if rf in role_map:
            role_conditions.append(f"lower(r.role_name) ~ '{role_map[rf]}'")
    if role_conditions:
        where_clauses.append(f"({' OR '.join(role_conditions)})")

if salary_only:
    where_clauses.append("jp.salary_max_annual IS NOT NULL")

where_sql = " AND ".join(where_clauses)

fresh_jobs = query(f"""
    SELECT
        jp.job_id,
        r.role_name,
        c.company_name,
        c.sector,
        jp.source,
        jp.ingested_at,
        jp.salary_min_annual,
        jp.salary_max_annual,
        jp.workplace_type,
        jp.experience_level,
        cc.full_name as contact_name,
        cc.title as contact_title,
        cc.email as contact_email,
        cc.linkedin_url as contact_linkedin,
        l.location,
        l.state,
        jh.honesty_score,
        gi.ghost_probability,
        EXTRACT(EPOCH FROM (NOW() - CASE WHEN jp.source = 'workday' AND jp.posted_date IS NOT NULL THEN jp.posted_date::timestamp ELSE jp.date_found END))/3600 as hours_old,
        STRING_AGG(DISTINCT s.skill_name, ', ' ORDER BY s.skill_name) as skills,
        ch.employee_count,
        {role_family_sql} as role_family
    FROM job_postings jp
    JOIN roles r ON r.role_id = jp.role_id
    LEFT JOIN companies c ON c.company_id = jp.company_id
    LEFT JOIN job_honesty_latest jh ON jh.job_id = jp.job_id
    LEFT JOIN vw_ghost_job_index gi ON gi.job_id = jp.job_id
    LEFT JOIN job_skills js ON js.job_id = jp.job_id
    LEFT JOIN skills s ON s.skill_id = js.skill_id
    LEFT JOIN company_headcount ch ON ch.company_name = c.company_name
    LEFT JOIN locations l ON l.location_id = jp.location_id
    LEFT JOIN LATERAL (
        SELECT full_name, title, email, linkedin_url
        FROM company_contacts cc2
        WHERE cc2.company_id = jp.company_id
          AND cc2.email IS NOT NULL
        ORDER BY cc2.fetched_at DESC
        LIMIT 1
    ) cc ON true
    WHERE {where_sql}
    GROUP BY jp.job_id, r.role_name, c.company_name, c.sector, jp.source,
             jp.ingested_at, jp.salary_min_annual, jp.salary_max_annual,
             jp.workplace_type, jp.experience_level, jh.honesty_score,
             l.location, l.state,
             cc.full_name, cc.title, cc.email, cc.linkedin_url,
             gi.ghost_probability, ch.employee_count
    ORDER BY (CASE WHEN jp.source = 'workday' AND jp.posted_date IS NOT NULL
                THEN jp.posted_date::timestamp
                ELSE jp.date_found::timestamp END) DESC
    LIMIT 500
""")

# ── COMPUTE SCORES ────────────────────────────────────────────────────────────
if not fresh_jobs.empty:
    fresh_jobs["hours_old"] = fresh_jobs["hours_old"].fillna(0).astype(float)
    fresh_jobs["days_old"] = (fresh_jobs["hours_old"] / 24).round(1)
    fresh_jobs["urgency_score"] = fresh_jobs.apply(
        lambda r: urgency_from_ghost(r["ghost_probability"], r["days_old"]), axis=1
    )
    fresh_jobs["quality_score"] = fresh_jobs["honesty_score"]
    fresh_jobs["signal_label"] = fresh_jobs["urgency_score"].apply(lambda u: signal_strength(u)[0])
    fresh_jobs["signal_icon"] = fresh_jobs["urgency_score"].apply(lambda u: signal_strength(u)[1])

    # Apply signal filter
    signal_map = {"⚡ Fresh": "fresh", "● Strong": "strong", "● Moderate": "moderate"}
    if signal_filter != "All" and signal_filter in signal_map:
        min_signal = signal_map[signal_filter]
        signal_order = ["weak", "moderate", "strong", "fresh"]
        min_idx = signal_order.index(min_signal)
        fresh_jobs = fresh_jobs[fresh_jobs["signal_label"].apply(
            lambda s: signal_order.index(s) >= min_idx if s in signal_order else False
        )]

# ── SUMMARY METRICS ───────────────────────────────────────────────────────────
m1, m2, m3, m4, m5 = st.columns(5)
with m1:
    st.metric("Fresh Roles", f"{len(fresh_jobs):,}" if not fresh_jobs.empty else "0")
with m2:
    if not fresh_jobs.empty:
        fresh_count = len(fresh_jobs[fresh_jobs["signal_label"] == "fresh"])
        st.metric("⚡ Fresh Signal", f"{fresh_count:,}")
    else:
        st.metric("⚡ Fresh Signal", "0")
with m3:
    if not fresh_jobs.empty and fresh_jobs["quality_score"].notna().any():
        avg_q = fresh_jobs["quality_score"].dropna().mean()
        st.metric("Avg Quality Score", f"{avg_q:.0f}/100")
    else:
        st.metric("Avg Quality Score", "—")
with m4:
    if not fresh_jobs.empty:
        sal_pct = fresh_jobs["salary_max_annual"].notna().mean() * 100
        st.metric("Salary Disclosed", f"{sal_pct:.0f}%")
    else:
        st.metric("Salary Disclosed", "—")
with m5:
    if not fresh_jobs.empty:
        cos = fresh_jobs["company_name"].nunique()
        st.metric("Companies", f"{cos:,}")
    else:
        st.metric("Companies", "0")

# ── JOB FEED ─────────────────────────────────────────────────────────────────
st.markdown(f"<div class='section-divider'>{len(fresh_jobs):,} roles — sorted by most recent</div>",
            unsafe_allow_html=True)

if fresh_jobs.empty:
    st.markdown("<div style='color:#444;font-size:0.8rem;padding:40px;text-align:center'>No roles match current filters.</div>",
                unsafe_allow_html=True)
else:
    for _, row in fresh_jobs.iterrows():
        signal, icon = signal_strength(row["urgency_score"])

        # Format age
        hours = float(row["hours_old"])
        if hours < 1:
            age_str = "< 1hr ago"
        elif hours < 24:
            age_str = f"{hours:.0f}h ago"
        else:
            age_str = f"{hours/24:.0f}d ago"

        # Salary
        if pd.notna(row["salary_min_annual"]) and pd.notna(row["salary_max_annual"]):
            sal_str = f"${int(row['salary_min_annual']):,}–${int(row['salary_max_annual']):,}"
        elif pd.notna(row["salary_max_annual"]):
            sal_str = f"Up to ${int(row['salary_max_annual']):,}"
        else:
            sal_str = None

        # Quality score
        qs = f"{row['quality_score']:.0f}" if pd.notna(row["quality_score"]) else "—"
        us = f"{row['urgency_score']:.0f}" if pd.notna(row["urgency_score"]) else "—"

        # Skills
        skills_html = ""
        if pd.notna(row["skills"]) and row["skills"]:
            for sk in str(row["skills"]).split(", ")[:8]:
                skills_html += f"<span class='skill-tag'>{sk.strip()}</span>"

        # Workplace
        wp = (row["workplace_type"] if pd.notna(row["workplace_type"]) else "").capitalize()
        exp = (row["experience_level"] if pd.notna(row["experience_level"]) else "").capitalize()
        source_map = {
            "greenhouse": ("GH", "#22c55e", "#052e16"),
            "workday":    ("WD", "#38bdf8", "#0c1a2e"),
            "lever":      ("LV", "#f97316", "#2a1000"),
            "ashby":      ("AS", "#e879f9", "#1a0a1e"),
            "eightfold":  ("EF", "#facc15", "#1a1400"),
            "amazon":     ("AMZ","#fb923c", "#1a0800"),
        }
        src_info = source_map.get(row["source"], (row["source"][:2].upper(), "#71717a", "#111"))
        source_display, src_color, src_bg = src_info

        sal_badge = ("<span class=\"badge badge-salary\">💰 " + sal_str + "</span>") if sal_str else ""
        sector_badge = ("<span class=\"badge badge-sector\">" + str(row["sector"]) + "</span>") if pd.notna(row["sector"]) and row["sector"] else ""
        wp_badge = ("<span class=\"badge badge-sector\">" + wp + "</span>") if wp else ""
        loc_str = ""
        if pd.notna(row.get("state")) and row["state"]:
            loc_str = str(row["state"])
            if pd.notna(row.get("location")) and row["location"] and len(str(row["location"])) < 30:
                city = str(row["location"]).split(",")[0].strip()
                if city.lower() not in ("remote","united states","us","usa",""):
                    loc_str = city + ", " + str(row["state"])
        elif wp == "remote":
            loc_str = ""
        loc_badge = ("<span class=\"badge badge-location\">" + loc_str + "</span>") if loc_str else ""
        exp_badge = ("<span class=\"badge badge-sector\">" + exp + "</span>") if exp else ""
        age_badge = "<span class=\"badge badge-age\">" + icon + " " + age_str + "</span>"
        q_badge = "<span class=\"badge badge-quality\">Quality " + qs + "</span>"
        u_badge = "<span class=\"badge badge-urgency\">Urgency " + us + "</span>"
        src_badge = f'<span style="display:inline-block;font-size:0.62rem;padding:2px 8px;border-radius:2px;margin-right:4px;font-family:IBM Plex Mono,monospace;text-transform:uppercase;letter-spacing:0.05em;background:{src_bg};color:{src_color};border:1px solid {src_color}33">{source_display}</span>'
        badges = age_badge + q_badge + u_badge + sal_badge + loc_badge + sector_badge + wp_badge + exp_badge + src_badge

        # Build contact HTML
        if pd.notna(row.get('contact_name')) and row.get('contact_name'):
            li_url = row.get('contact_linkedin', '')
            email = row.get('contact_email', '')
            contact_html = (
                f'<div style="font-size:0.72rem;color:#e2ff5d;font-weight:500">{row["contact_name"]}</div>'
                f'<div style="font-size:0.62rem;color:#666;margin:2px 0">{(row.get("contact_title") or "")[:35]}</div>'
                f'<div style="margin-top:4px;display:flex;gap:6px;justify-content:flex-end">'
            )
            if email:
                contact_html += f'<a href="mailto:{email}" style="font-size:0.6rem;color:#4ade80;text-decoration:none;border:1px solid #4ade8044;padding:2px 6px;border-radius:2px">✉ Email</a>'
            if li_url:
                contact_html += f'<a href="{li_url}" target="_blank" style="font-size:0.6rem;color:#38bdf8;text-decoration:none;border:1px solid #38bdf844;padding:2px 6px;border-radius:2px">in</a>'
            contact_html += '</div>'
        else:
            contact_html = '<div style="font-size:0.65rem;color:#333;font-style:italic">No contact found</div>'

        company_display = row['company_name'] or '—'
        sector_sub = f" · {row['sector']}" if pd.notna(row['sector']) and row['sector'] else ""
        emp_sub = f" · {int(row['employee_count']):,} employees" if pd.notna(row.get('employee_count')) else ""
        card_html = (
            f"<div class=\"job-card signal-{signal}\">"
            f"<div style=\"display:flex;justify-content:space-between;align-items:flex-start\">"
            f"<div style=\"flex:1\">"
            f"<div class=\"job-title\">{row['role_name']}</div>"
            f"<div class=\"job-company\">{company_display}{sector_sub}{emp_sub}</div>"
            f"<div style=\"margin-bottom:8px\">{badges}</div>"
            f"<div>{skills_html}</div>"
            f"</div>"
            f"<div style=\"text-align:right;min-width:140px;padding-left:16px\">"
            f"<div style=\"font-size:0.6rem;color:#333;text-transform:uppercase;"
            f"letter-spacing:0.1em;margin-bottom:4px\">Hiring Contact</div>"
            f"<div>{contact_html}</div>"
            f"</div></div></div>"
        )
        st.markdown(card_html, unsafe_allow_html=True)

# ── FOOTER ────────────────────────────────────────────────────────────────────
st.markdown("""
<div style="margin-top:48px;padding-top:16px;border-top:1px solid #131320;
    font-size:0.6rem;color:#2a2a3a;display:flex;justify-content:space-between">
    <span>DataHiringIQ Recruiter Intelligence · Confidential</span>
    <span>datahiringiq.com · jones31luke@gmail.com</span>
</div>
""", unsafe_allow_html=True)
