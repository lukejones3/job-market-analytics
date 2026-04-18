import streamlit as st
import psycopg2
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import os

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Job Market Analytics",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Styling ───────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Mono:wght@300;400;500&display=swap');

html, body, [class*="css"] {
    font-family: 'DM Mono', monospace;
}

h1, h2, h3 { font-family: 'Syne', sans-serif !important; font-weight: 800; }

.stApp { background-color: #0a0a0f; color: #e8e6e0; }

section[data-testid="stSidebar"] {
    background-color: #0f0f18;
    border-right: 1px solid #1e1e2e;
}

.metric-card {
    background: #111120;
    border: 1px solid #1e1e2e;
    border-radius: 4px;
    padding: 20px;
    margin: 4px 0;
}

.metric-value {
    font-family: 'Syne', sans-serif;
    font-size: 2.2rem;
    font-weight: 800;
    color: #c8f542;
    line-height: 1;
}

.metric-label {
    font-size: 0.7rem;
    color: #666;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-top: 6px;
}

.score-pill {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 2px;
    font-size: 0.75rem;
    font-weight: 500;
    font-family: 'DM Mono', monospace;
}

.score-high { background: #2d1f1f; color: #ff6b6b; border: 1px solid #ff6b6b33; }
.score-med  { background: #2d2a1f; color: #ffd166; border: 1px solid #ffd16633; }
.score-low  { background: #1f2d1f; color: #06d6a0; border: 1px solid #06d6a033; }

.tag {
    display: inline-block;
    background: #1a1a2e;
    border: 1px solid #2e2e4e;
    color: #8888aa;
    font-size: 0.65rem;
    padding: 2px 8px;
    border-radius: 2px;
    margin: 2px;
}

.section-header {
    font-family: 'Syne', sans-serif;
    font-size: 0.65rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.2em;
    color: #444;
    border-bottom: 1px solid #1e1e2e;
    padding-bottom: 8px;
    margin: 24px 0 16px 0;
}

div[data-testid="stMetric"] {
    background: #111120;
    border: 1px solid #1e1e2e;
    border-radius: 4px;
    padding: 16px;
}

div[data-testid="stMetric"] label { color: #666 !important; font-size: 0.7rem !important; }
div[data-testid="stMetric"] [data-testid="stMetricValue"] {
    color: #c8f542 !important;
    font-family: 'Syne', sans-serif !important;
    font-weight: 800 !important;
}

.stDataFrame { border: 1px solid #1e1e2e; }
.stSelectbox label, .stMultiSelect label { color: #888 !important; font-size: 0.7rem !important; }

.hero-stat {
    font-family: 'Syne', sans-serif;
    font-size: 4rem;
    font-weight: 800;
    color: #c8f542;
    line-height: 1;
}
.hero-label {
    font-size: 0.7rem;
    color: #555;
    text-transform: uppercase;
    letter-spacing: 0.15em;
}

.stAlert { background: #111120 !important; border-color: #1e1e2e !important; }
</style>
""", unsafe_allow_html=True)

# ── DB Connection ─────────────────────────────────────────────────────────────
def get_connection():
    return psycopg2.connect(
        host=st.secrets.get("db_host", os.getenv("PGHOST", "REMOVED_DB_HOST")),
        port=int(st.secrets.get("db_port", os.getenv("PGPORT", 5432))),
        dbname=st.secrets.get("db_name", os.getenv("PGDATABASE", "job_analytics")),
        user=st.secrets.get("db_user", os.getenv("PGUSER", "lukejones")),
        password=st.secrets.get("db_password", os.getenv("PGPASSWORD", "")),
    )

@st.cache_data(ttl=3600)
def query(sql, params=None):
    conn = get_connection()
    try:
        return pd.read_sql(sql, conn, params=params)
    finally:
        conn.close()

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div style="margin-bottom:32px">
        <div style="font-family:'Syne',sans-serif;font-size:1.1rem;font-weight:800;color:#c8f542;letter-spacing:-0.02em">
            JOB MARKET<br>ANALYTICS
        </div>
        <div style="font-size:0.6rem;color:#444;text-transform:uppercase;letter-spacing:0.15em;margin-top:4px">
            Data & ML Hiring Intelligence
        </div>
    </div>
    """, unsafe_allow_html=True)

    page = st.radio(
        "Navigate",
        ["📊 Overview", "🏢 Company Scorecard", "🔧 Skill Premiums", "🏭 Sector Dashboard"],
        label_visibility="collapsed"
    )

    st.markdown("---")
    st.markdown("""
    <div style="font-size:0.6rem;color:#333;text-transform:uppercase;letter-spacing:0.1em">
        Coverage
    </div>
    """, unsafe_allow_html=True)

    # Quick stats
    stats = query("""
        SELECT
            COUNT(*) FILTER (WHERE status='raw' AND data_tier=1) as active,
            COUNT(DISTINCT company_id) FILTER (WHERE status='raw' AND data_tier=1) as companies,
            MAX(ingested_at)::date as last_updated
        FROM job_postings
        WHERE data_tier=1
    """)
    if not stats.empty:
        r = stats.iloc[0]
        st.markdown(f"""
        <div style="margin-top:8px">
            <div style="font-family:'Syne',sans-serif;font-size:1.4rem;font-weight:800;color:#c8f542">{r['active']:,}</div>
            <div style="font-size:0.6rem;color:#555">active Tier 1 jobs</div>
            <div style="font-family:'Syne',sans-serif;font-size:1.4rem;font-weight:800;color:#c8f542;margin-top:8px">{r['companies']:,}</div>
            <div style="font-size:0.6rem;color:#555">companies hiring</div>
            <div style="font-size:0.6rem;color:#333;margin-top:12px">Updated {r['last_updated']}</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("""
    <div style="font-size:0.6rem;color:#333;text-transform:uppercase;letter-spacing:0.1em">Sources</div>
    <div style="margin-top:6px;font-size:0.65rem;color:#555;line-height:2">
        Greenhouse · Lever · Ashby<br>Workday · Amazon · Eightfold
    </div>
    """, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════
if page == "📊 Overview":
    st.markdown("""
    <div style="margin-bottom:40px">
        <div style="font-family:'Syne',sans-serif;font-size:2.5rem;font-weight:800;color:#e8e6e0;letter-spacing:-0.03em;line-height:1.1">
            Data & ML Hiring<br>Intelligence Platform
        </div>
        <div style="font-size:0.75rem;color:#555;margin-top:12px;max-width:500px">
            6,100+ enriched job postings across 1,000+ companies. Updated nightly from
            6 ATS sources. Proprietary scoring on salary transparency, hiring difficulty,
            and posting quality.
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Top metrics
    col1, col2, col3, col4, col5 = st.columns(5)

    overview = query("""
        SELECT
            COUNT(*) FILTER (WHERE status='raw' AND data_tier=1) as active_jobs,
            COUNT(DISTINCT company_id) FILTER (WHERE status='raw' AND data_tier=1) as companies,
            ROUND(AVG(CASE WHEN salary_max_annual IS NOT NULL THEN 1.0 ELSE 0.0 END) FILTER (WHERE status='raw' AND data_tier=1)*100,1) as salary_pct,
            ROUND(AVG(jh.honesty_score),1) as avg_honesty,
            COUNT(*) FILTER (WHERE status='expired' AND data_tier=1) as expired
        FROM job_postings jp
        LEFT JOIN job_honesty_latest jh ON jh.job_id = jp.job_id
        WHERE data_tier=1
    """)

    if not overview.empty:
        r = overview.iloc[0]
        with col1:
            st.metric("Active Roles", f"{int(r['active_jobs']):,}")
        with col2:
            st.metric("Companies", f"{int(r['companies']):,}")
        with col3:
            st.metric("Salary Coverage", f"{r['salary_pct']}%")
        with col4:
            st.metric("Avg Honesty Score", f"{r['avg_honesty']}/100")
        with col5:
            st.metric("Tracked (Total)", f"{int(r['active_jobs'] + r['expired']):,}")

    st.markdown("<div class='section-header'>Ghost Job Index — Active Postings</div>", unsafe_allow_html=True)

    ghost = query("""
        SELECT ghost_tier, COUNT(*) as jobs,
            ROUND(AVG(ghost_probability)::numeric,1) as avg_prob
        FROM vw_ghost_job_index
        GROUP BY ghost_tier
        ORDER BY avg_prob DESC
    """)

    if not ghost.empty:
        gcol1, gcol2, gcol3, gcol4 = st.columns(4)
        colors = {'high': '#ff6b6b', 'medium': '#ffd166', 'low': '#06d6a0', 'fresh': '#c8f542'}
        icons  = {'high': '🔴', 'medium': '🟡', 'low': '🟢', 'fresh': '✨'}
        cols = [gcol1, gcol2, gcol3, gcol4]
        for i, row in ghost.iterrows():
            tier = row['ghost_tier']
            with cols[i % 4]:
                st.markdown(f"""
                <div class="metric-card">
                    <div style="font-size:0.6rem;color:#444;text-transform:uppercase;letter-spacing:0.1em">{icons.get(tier,'')} {tier}</div>
                    <div class="metric-value" style="color:{colors.get(tier,'#c8f542')}">{int(row['jobs']):,}</div>
                    <div class="metric-label">{row['avg_prob']}% avg probability</div>
                </div>
                """, unsafe_allow_html=True)

    st.markdown("<div class='section-header'>Salary Premium by Skill — Top 15</div>", unsafe_allow_html=True)

    skills = query("""
        SELECT s.skill_name,
            COUNT(DISTINCT jp.job_id) as jobs,
            ROUND(AVG(jp.salary_max_annual)) as avg_max,
            ROUND((AVG(jp.salary_max_annual) -
                (SELECT AVG(salary_max_annual) FROM job_postings
                 WHERE data_tier=1 AND salary_max_annual BETWEEN 50000 AND 500000)) /
                (SELECT AVG(salary_max_annual) FROM job_postings
                 WHERE data_tier=1 AND salary_max_annual BETWEEN 50000 AND 500000) * 100, 1) as premium_pct
        FROM job_skills js
        JOIN skills s ON s.skill_id = js.skill_id
        JOIN job_postings jp ON jp.job_id = js.job_id
        WHERE jp.data_tier=1 AND jp.status='raw'
          AND jp.salary_max_annual BETWEEN 50000 AND 500000
          AND s.difficulty_relevant = true
        GROUP BY s.skill_name
        HAVING COUNT(DISTINCT jp.job_id) >= 30
        ORDER BY premium_pct DESC
        LIMIT 15
    """)

    if not skills.empty:
        fig = px.bar(
            skills,
            x='premium_pct', y='skill_name',
            orientation='h',
            color='premium_pct',
            color_continuous_scale=[[0,'#1a1a2e'],[0.5,'#4444aa'],[1,'#c8f542']],
            labels={'premium_pct': 'Salary Premium %', 'skill_name': ''},
            text=skills['premium_pct'].apply(lambda x: f"+{x}%"),
        )
        fig.update_layout(
            plot_bgcolor='#0a0a0f', paper_bgcolor='#0a0a0f',
            font_color='#888', font_family='DM Mono',
            height=420,
            coloraxis_showscale=False,
            margin=dict(l=0, r=20, t=0, b=0),
            xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            yaxis=dict(showgrid=False, categoryorder='total ascending'),
        )
        fig.update_traces(textposition='outside', textfont_color='#c8f542', marker_line_width=0)
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("<div class='section-header'>Sector Snapshot</div>", unsafe_allow_html=True)

    sectors = query("""
        SELECT c.sector,
            COUNT(DISTINCT jp.job_id) as active_roles,
            ROUND(AVG(CASE WHEN jp.salary_max_annual IS NOT NULL THEN 1.0 ELSE 0.0 END)*100) as transparency_pct,
            ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY jp.salary_max_annual)
                FILTER (WHERE jp.salary_max_annual BETWEEN 50000 AND 500000)) as median_salary,
            ROUND(AVG(jh.honesty_score),1) as avg_honesty
        FROM job_postings jp
        JOIN companies c ON c.company_id = jp.company_id
        LEFT JOIN job_honesty_latest jh ON jh.job_id = jp.job_id
        WHERE jp.data_tier=1 AND jp.status='raw' AND c.sector IS NOT NULL
        GROUP BY c.sector
        HAVING COUNT(DISTINCT jp.job_id) >= 10
        ORDER BY active_roles DESC
        LIMIT 12
    """)

    if not sectors.empty:
        sectors['median_salary_fmt'] = sectors['median_salary'].apply(
            lambda x: f"${int(x/1000)}K" if pd.notna(x) else "—"
        )
        fig2 = make_subplots(specs=[[{"secondary_y": True}]])
        fig2.add_trace(go.Bar(
            x=sectors['sector'], y=sectors['active_roles'],
            name='Active Roles', marker_color='#1e1e3e',
            marker_line_color='#c8f542', marker_line_width=1,
        ))
        fig2.add_trace(go.Scatter(
            x=sectors['sector'], y=sectors['transparency_pct'],
            name='Transparency %', mode='lines+markers',
            line=dict(color='#c8f542', width=2),
            marker=dict(size=6, color='#c8f542'),
        ), secondary_y=True)
        fig2.update_layout(
            plot_bgcolor='#0a0a0f', paper_bgcolor='#0a0a0f',
            font_color='#888', font_family='DM Mono',
            height=320, showlegend=True,
            legend=dict(bgcolor='#0a0a0f', bordercolor='#1e1e2e', borderwidth=1),
            margin=dict(l=0, r=0, t=0, b=0),
            xaxis=dict(showgrid=False, tickangle=-35),
            yaxis=dict(showgrid=False, title='Active Roles', title_font_color='#444'),
            yaxis2=dict(showgrid=False, title='Transparency %', title_font_color='#c8f542',
                        range=[0,100]),
        )
        st.plotly_chart(fig2, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: COMPANY SCORECARD
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🏢 Company Scorecard":
    st.markdown("""
    <div style="font-family:'Syne',sans-serif;font-size:2rem;font-weight:800;color:#e8e6e0;margin-bottom:8px">
        Company Scorecard
    </div>
    <div style="font-size:0.7rem;color:#555;margin-bottom:32px">
        Hiring intelligence + action flags — updated nightly
    </div>
    """, unsafe_allow_html=True)

    fcol1, fcol2, fcol3, fcol4 = st.columns([2, 2, 1, 1])
    with fcol1:
        sectors_list = query("SELECT DISTINCT sector FROM analytics_analytics.mart_company_scorecard WHERE sector IS NOT NULL ORDER BY sector")
        sector_filter = st.multiselect("Filter by Sector", sectors_list["sector"].tolist(), placeholder="All sectors")
    with fcol2:
        search = st.text_input("Search company", placeholder="e.g. Capital One, OpenAI...")
    with fcol3:
        sort_by = st.selectbox("Sort by", ["active_roles", "difficulty_score", "avg_honesty_score", "transparency_pct", "avg_ghost_probability", "salary_below_score"])
    with fcol4:
        flag_filter = st.selectbox("Show only", ["All companies", "🚨 Has action flags", "💸 Underpaying", "🧱 Over-specified", "👻 Ghost risk", "🔴 Opaque"])

    where = ["active_roles >= 3"]
    if sector_filter:
        where.append(f"sector IN ({','.join([repr(s) for s in sector_filter])})")
    if search:
        where.append(f"lower(company_name) LIKE lower('%{search}%')")

    scorecard = query(f"""
        SELECT company_name, sector, active_roles,
            difficulty_score, rarity_score, complexity_score,
            salary_below_score, avg_honesty_score, transparency_pct,
            avg_ghost_probability, ghost_rate_pct,
            avg_max_salary, primary_level, primary_workplace,
            left(top_skills, 150) as top_skills
        FROM analytics_analytics.mart_company_scorecard
        WHERE {' AND '.join(where)}
        ORDER BY {sort_by} DESC NULLS LAST
        LIMIT 150
    """)

    def get_flags(row):
        flags = []
        if pd.notna(row["salary_below_score"]) and row["salary_below_score"] > 30:
            flags.append(("underpaying", f"💸 Underpaying ~{int(row['salary_below_score'])}% vs sector median"))
        if pd.notna(row["complexity_score"]) and row["complexity_score"] > 70:
            flags.append(("overspec", "🧱 Over-specified role"))
        if pd.notna(row["avg_ghost_probability"]) and row["avg_ghost_probability"] > 60:
            flags.append(("ghost", f"👻 Ghost risk {int(row['avg_ghost_probability'])}%"))
        if pd.notna(row["transparency_pct"]) and row["transparency_pct"] == 0:
            flags.append(("opaque", "🔴 0% salary transparency"))
        if pd.notna(row["rarity_score"]) and row["rarity_score"] > 15:
            flags.append(("rare", "🎯 Niche skill requirements"))
        return flags

    def get_recs(flags, row):
        recs = []
        types = [f[0] for f in flags]
        if "underpaying" in types and pd.notna(row["avg_max_salary"]) and row["salary_below_score"] < 100:
            target = int(row["avg_max_salary"] / (1 - row["salary_below_score"] / 100) / 1000) * 1000
            recs.append(f"Raise max salary to ~${target:,} to match sector median")
        if "opaque" in types:
            recs.append("Add salary band — transparency drives 2-3x more applicants")
        if "overspec" in types:
            recs.append("Reduce required skills to 5-7 core requirements")
        if "rare" in types:
            recs.append("Replace niche tools with transferable equivalents")
        if "ghost" in types:
            recs.append("Audit open roles — high ghost probability signals stale postings")
        return recs

    def estimate_ttf(row):
        base = 30
        if pd.notna(row["avg_ghost_probability"]):
            base += row["avg_ghost_probability"] * 0.3
        if pd.notna(row["difficulty_score"]):
            base += row["difficulty_score"] * 0.2
        return int(base)

    if not scorecard.empty:
        scorecard["_flags"] = scorecard.apply(get_flags, axis=1)
        scorecard["_ftypes"] = scorecard["_flags"].apply(lambda f: [x[0] for x in f])

        if flag_filter == "🚨 Has action flags":
            scorecard = scorecard[scorecard["_flags"].apply(len) > 0]
        elif flag_filter == "💸 Underpaying":
            scorecard = scorecard[scorecard["_ftypes"].apply(lambda f: "underpaying" in f)]
        elif flag_filter == "🧱 Over-specified":
            scorecard = scorecard[scorecard["_ftypes"].apply(lambda f: "overspec" in f)]
        elif flag_filter == "👻 Ghost risk":
            scorecard = scorecard[scorecard["_ftypes"].apply(lambda f: "ghost" in f)]
        elif flag_filter == "🔴 Opaque":
            scorecard = scorecard[scorecard["_ftypes"].apply(lambda f: "opaque" in f)]

    st.markdown(f"<div style='font-size:0.65rem;color:#444;margin-bottom:16px'>{len(scorecard)} companies</div>", unsafe_allow_html=True)

    if not scorecard.empty:
        for _, row in scorecard.iterrows():
            flags = row["_flags"]
            recs  = get_recs(flags, row)
            ttf   = estimate_ttf(row)

            diff  = row["difficulty_score"]
            hon   = row["avg_honesty_score"]
            ghost = row["avg_ghost_probability"]
            trans = row["transparency_pct"]

            diff_class  = "score-high" if pd.notna(diff)  and diff  > 70 else "score-med" if pd.notna(diff)  and diff  > 40 else "score-low"
            hon_class   = "score-low"  if pd.notna(hon)   and hon   >= 85 else "score-med" if pd.notna(hon)  and hon   >= 70 else "score-high"
            trans_class = "score-low"  if pd.notna(trans) and trans >= 80 else "score-med" if pd.notna(trans) and trans >= 50 else "score-high"

            diff_str  = f"<span class='score-pill {diff_class}'>Difficulty {diff:.0f}</span>"  if pd.notna(diff)  else ""
            hon_str   = f"<span class='score-pill {hon_class}'>Honesty {hon:.0f}</span>"        if pd.notna(hon)   else ""
            ghost_str = f"<span class='score-pill {'score-high' if ghost > 50 else 'score-low'}'>Ghost {ghost:.0f}%</span>" if pd.notna(ghost) else ""
            trans_str = f"<span class='score-pill {trans_class}'>{trans:.0f}% transparent</span>" if pd.notna(trans) else ""
            sal_str   = f"${int(row['avg_max_salary']):,} avg max" if pd.notna(row["avg_max_salary"]) else "No salary data"

            skills_html = ""
            if pd.notna(row["top_skills"]):
                for sk in row["top_skills"].split(", ")[:8]:
                    skills_html += f"<span class='tag'>{sk.strip()}</span>"

            border = "#ff6b6b44" if flags else "#1e1e2e"
            bg     = "#110d0d"   if flags else "#0d0d1a"
            lvl    = row["primary_level"] or ""
            wp     = row["primary_workplace"] or ""
            meta   = f"{lvl} · {wp}" if lvl or wp else ""

            # Main card — no interpolated HTML blocks, just static values
            st.markdown(f"""
            <div style="border:1px solid {border};border-radius:4px;padding:16px;margin-bottom:4px;background:{bg}">
                <div style="display:flex;justify-content:space-between;align-items:flex-start">
                    <div>
                        <span style="font-family:'Syne',sans-serif;font-size:1rem;font-weight:700;color:#e8e6e0">{row["company_name"]}</span>
                        <span style="font-size:0.65rem;color:#444;margin-left:10px">{row["sector"] or "—"}</span>
                        <span style="font-size:0.6rem;color:#333;margin-left:8px">{meta}</span>
                    </div>
                    <div style="text-align:right">
                        <div style="font-family:'Syne',sans-serif;font-size:1.1rem;font-weight:800;color:#c8f542">{int(row["active_roles"])} roles</div>
                        <div style="font-size:0.6rem;color:#444">{sal_str}</div>
                    </div>
                </div>
                <div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:4px">
                    {diff_str} {hon_str} {trans_str} {ghost_str}
                </div>
                <div style="margin-top:8px">{skills_html}</div>
            </div>
            """, unsafe_allow_html=True)

            # Action flags — separate st.markdown call, no quote conflicts
            if flags:
                flag_rows = "".join([f"<div style='font-size:0.7rem;color:#ff6b6b;padding:2px 0'>{f[1]}</div>" for f in flags])
                st.markdown(f"""
                <div style="margin:-4px 0 4px 0;padding:10px 12px;background:#1a0f0f;border:1px solid #ff6b6b22;border-left:3px solid #ff6b6b;border-radius:0 0 3px 3px">
                    <div style="font-size:0.6rem;color:#555;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px">Action Required</div>
                    {flag_rows}
                </div>
                """, unsafe_allow_html=True)

                # Recommendations
                if recs:
                    rec_rows = "".join([f"<div style='font-size:0.68rem;color:#aaa;padding:2px 0'>→ {r}</div>" for r in recs])
                    st.markdown(f"""
                    <div style="margin:-4px 0 4px 0;padding:10px 12px;background:#0f1a0f;border:1px solid #06d6a022;border-left:3px solid #06d6a0;border-radius:0 0 3px 3px">
                        <div style="font-size:0.6rem;color:#555;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px">Recommended Fix</div>
                        {rec_rows}
                    </div>
                    """, unsafe_allow_html=True)

                # If unchanged
                pool_reduction = min(60, len(flags) * 15)
                competing = max(5, int(row["active_roles"] * 3.2)) if pd.notna(row["active_roles"]) else 20
                st.markdown(f"""
                <div style="margin:-4px 0 12px 0;padding:10px 12px;background:#111120;border:1px solid #1e1e2e;border-radius:0 0 4px 4px">
                    <div style="font-size:0.6rem;color:#555;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px">If Unchanged</div>
                    <div style="font-size:0.68rem;color:#666">
                        Est. time-to-fill: <span style="color:#ffd166">{ttf}–{ttf+18} days</span>
                        &nbsp;·&nbsp; Candidate pool: <span style="color:#ff6b6b">-{pool_reduction}%</span>
                        &nbsp;·&nbsp; Competing roles with better terms: <span style="color:#ff6b6b">{competing}</span>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown("<div style='margin-bottom:8px'></div>", unsafe_allow_html=True)



# ══════════════════════════════════════════════════════════════════════════════
# PAGE: SKILL PREMIUMS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🔧 Skill Premiums":
    st.markdown("""
    <div style="font-family:'Syne',sans-serif;font-size:2rem;font-weight:800;color:#e8e6e0;margin-bottom:8px">
        Skill Salary Premiums
    </div>
    <div style="font-size:0.7rem;color:#555;margin-bottom:32px">
        % compensation lift vs dataset average ($192K baseline) — Tier 1 active roles only
    </div>
    """, unsafe_allow_html=True)

    dataset_avg = query("""
        SELECT ROUND(AVG(salary_max_annual)) as avg
        FROM job_postings
        WHERE data_tier=1 AND status='raw'
          AND salary_max_annual BETWEEN 50000 AND 500000
    """).iloc[0]['avg']

    all_skills = query("""
        SELECT s.skill_name,
            COUNT(DISTINCT jp.job_id) as jobs,
            ROUND(AVG(jp.salary_max_annual)) as avg_max,
            ROUND((AVG(jp.salary_max_annual) - %s) / %s * 100, 1) as premium_pct
        FROM job_skills js
        JOIN skills s ON s.skill_id = js.skill_id
        JOIN job_postings jp ON jp.job_id = js.job_id
        WHERE jp.data_tier=1 AND jp.status='raw'
          AND jp.salary_max_annual BETWEEN 50000 AND 500000
          AND s.difficulty_relevant = true
        GROUP BY s.skill_name
        HAVING COUNT(DISTINCT jp.job_id) >= 20
        ORDER BY premium_pct DESC
    """, params=(float(dataset_avg), float(dataset_avg)))

    scol1, scol2 = st.columns([1, 2])
    with scol1:
        selected_skill = st.selectbox("Select a skill", all_skills['skill_name'].tolist())
        min_jobs = st.slider("Min jobs required", 10, 200, 30)

    filtered = all_skills[all_skills['jobs'] >= min_jobs]

    with scol2:
        st.metric("Dataset Avg Max Salary", f"${int(dataset_avg):,}")

    # Skill detail
    if selected_skill:
        skill_row = all_skills[all_skills['skill_name'] == selected_skill].iloc[0]
        st.markdown("<div class='section-header'>Selected Skill Detail</div>", unsafe_allow_html=True)
        dc1, dc2, dc3, dc4 = st.columns(4)
        with dc1:
            st.metric("Skill", selected_skill)
        with dc2:
            st.metric("Avg Max Salary", f"${int(skill_row['avg_max']):,}")
        with dc3:
            premium = skill_row['premium_pct']
            st.metric("Premium vs Baseline", f"+{premium}%" if premium > 0 else f"{premium}%")
        with dc4:
            st.metric("Jobs Requiring It", f"{int(skill_row['jobs']):,}")

        # Top companies for this skill
        top_cos = query("""
            SELECT c.company_name, c.sector,
                COUNT(DISTINCT jp.job_id) as jobs,
                ROUND(AVG(jp.salary_max_annual)) as avg_salary
            FROM job_skills js
            JOIN skills s ON s.skill_id = js.skill_id
            JOIN job_postings jp ON jp.job_id = js.job_id
            JOIN companies c ON c.company_id = jp.company_id
            WHERE jp.data_tier=1 AND jp.status='raw'
              AND s.skill_name = %s
              AND jp.salary_max_annual BETWEEN 50000 AND 500000
            GROUP BY c.company_name, c.sector
            ORDER BY avg_salary DESC NULLS LAST
            LIMIT 10
        """, params=(selected_skill,))

        if not top_cos.empty:
            st.markdown(f"<div class='section-header'>Top Companies Hiring for {selected_skill}</div>", unsafe_allow_html=True)
            top_cos['avg_salary'] = top_cos['avg_salary'].apply(lambda x: f"${int(x):,}" if pd.notna(x) else "—")
            st.dataframe(
                top_cos.rename(columns={'company_name':'Company','sector':'Sector','jobs':'Roles','avg_salary':'Avg Max Salary'}),
                use_container_width=True, hide_index=True
            )

    st.markdown("<div class='section-header'>All Skills — Salary Premium Ranking</div>", unsafe_allow_html=True)

    fig = px.scatter(
        filtered,
        x='jobs', y='premium_pct',
        text='skill_name',
        size='jobs',
        color='premium_pct',
        color_continuous_scale=[[0,'#1a1a2e'],[0.4,'#333366'],[1,'#c8f542']],
        labels={'jobs':'Jobs Requiring Skill','premium_pct':'Salary Premium %','skill_name':'Skill'},
    )
    fig.update_traces(
        textposition='top center',
        textfont=dict(size=9, color='#888', family='DM Mono'),
        marker=dict(line=dict(width=0)),
    )
    fig.update_layout(
        plot_bgcolor='#0a0a0f', paper_bgcolor='#0a0a0f',
        font_color='#888', font_family='DM Mono',
        height=480,
        coloraxis_showscale=False,
        margin=dict(l=0, r=0, t=0, b=0),
        xaxis=dict(showgrid=False, title='Number of Jobs Requiring Skill'),
        yaxis=dict(showgrid=False, title='Salary Premium vs Baseline %',
                   zeroline=True, zerolinecolor='#1e1e2e', zerolinewidth=1),
    )
    st.plotly_chart(fig, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: SECTOR DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🏭 Sector Dashboard":
    st.markdown("""
    <div style="font-family:'Syne',sans-serif;font-size:2rem;font-weight:800;color:#e8e6e0;margin-bottom:8px">
        Sector Dashboard
    </div>
    <div style="font-size:0.7rem;color:#555;margin-bottom:32px">
        Hiring activity, compensation, transparency, and ghost rates by sector
    </div>
    """, unsafe_allow_html=True)

    sector_data = query("""
        SELECT c.sector,
            COUNT(DISTINCT jp.job_id) as active_roles,
            COUNT(DISTINCT c.company_id) as companies,
            ROUND(AVG(CASE WHEN jp.salary_max_annual IS NOT NULL THEN 1.0 ELSE 0.0 END)*100,1) as transparency_pct,
            ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY jp.salary_max_annual)
                FILTER (WHERE jp.salary_max_annual BETWEEN 50000 AND 500000)) as median_salary,
            ROUND(AVG(jh.honesty_score),1) as avg_honesty
        FROM job_postings jp
        JOIN companies c ON c.company_id = jp.company_id
        LEFT JOIN job_honesty_latest jh ON jh.job_id = jp.job_id
        WHERE jp.data_tier=1 AND jp.status='raw' AND c.sector IS NOT NULL
        GROUP BY c.sector
        HAVING COUNT(DISTINCT jp.job_id) >= 10
        ORDER BY active_roles DESC
    """)

    if not sector_data.empty:
        # Highlight top/bottom
        best_trans = sector_data.loc[sector_data['transparency_pct'].idxmax(), 'sector']
        worst_trans = sector_data.loc[sector_data['transparency_pct'].idxmin(), 'sector']
        highest_pay = sector_data.loc[sector_data['median_salary'].idxmax(), 'sector']

        hcol1, hcol2, hcol3 = st.columns(3)
        with hcol1:
            st.markdown(f"""
            <div class="metric-card">
                <div style="font-size:0.6rem;color:#444;text-transform:uppercase;letter-spacing:0.1em">Most Transparent</div>
                <div style="font-family:'Syne',sans-serif;font-size:1.3rem;font-weight:800;color:#06d6a0;margin-top:4px">{best_trans}</div>
                <div style="font-size:0.7rem;color:#555">{sector_data[sector_data['sector']==best_trans]['transparency_pct'].values[0]:.0f}% post salary</div>
            </div>
            """, unsafe_allow_html=True)
        with hcol2:
            st.markdown(f"""
            <div class="metric-card">
                <div style="font-size:0.6rem;color:#444;text-transform:uppercase;letter-spacing:0.1em">Least Transparent</div>
                <div style="font-family:'Syne',sans-serif;font-size:1.3rem;font-weight:800;color:#ff6b6b;margin-top:4px">{worst_trans}</div>
                <div style="font-size:0.7rem;color:#555">{sector_data[sector_data['sector']==worst_trans]['transparency_pct'].values[0]:.0f}% post salary</div>
            </div>
            """, unsafe_allow_html=True)
        with hcol3:
            pay_val = sector_data[sector_data['sector']==highest_pay]['median_salary'].values[0]
            st.markdown(f"""
            <div class="metric-card">
                <div style="font-size:0.6rem;color:#444;text-transform:uppercase;letter-spacing:0.1em">Highest Paying</div>
                <div style="font-family:'Syne',sans-serif;font-size:1.3rem;font-weight:800;color:#c8f542;margin-top:4px">{highest_pay}</div>
                <div style="font-size:0.7rem;color:#555">${int(pay_val/1000)}K median max</div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("<div class='section-header'>Transparency vs Median Salary</div>", unsafe_allow_html=True)

        fig = px.scatter(
            sector_data.dropna(subset=['median_salary']),
            x='transparency_pct', y='median_salary',
            text='sector', size='active_roles',
            color='avg_honesty',
            color_continuous_scale=[[0,'#ff6b6b'],[0.5,'#ffd166'],[1,'#06d6a0']],
            labels={
                'transparency_pct': 'Salary Transparency %',
                'median_salary': 'Median Max Salary ($)',
                'avg_honesty': 'Honesty Score'
            },
        )
        fig.update_traces(
            textposition='top center',
            textfont=dict(size=9, color='#888', family='DM Mono'),
            marker=dict(line=dict(width=0)),
        )
        fig.update_layout(
            plot_bgcolor='#0a0a0f', paper_bgcolor='#0a0a0f',
            font_color='#888', font_family='DM Mono',
            height=420,
            margin=dict(l=0, r=0, t=0, b=0),
            xaxis=dict(showgrid=False, range=[0,105]),
            yaxis=dict(showgrid=False,
                       tickformat='$,.0f'),
            coloraxis_colorbar=dict(title='Honesty', tickfont=dict(color='#888')),
        )
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("<div class='section-header'>Full Sector Table</div>", unsafe_allow_html=True)

        display = sector_data.copy()
        display['median_salary'] = display['median_salary'].apply(
            lambda x: f"${int(x/1000)}K" if pd.notna(x) else "—"
        )
        display['transparency_pct'] = display['transparency_pct'].apply(lambda x: f"{x:.0f}%")
        display['avg_honesty'] = display['avg_honesty'].apply(lambda x: f"{x:.1f}" if pd.notna(x) else "—")

        st.dataframe(
            display.rename(columns={
                'sector':'Sector','active_roles':'Active Roles',
                'companies':'Companies','transparency_pct':'Transparency',
                'median_salary':'Median Max Salary','avg_honesty':'Honesty Score'
            }),
            use_container_width=True, hide_index=True
        )

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("""
<div style="margin-top:48px;padding-top:16px;border-top:1px solid #1e1e2e;font-size:0.6rem;color:#333;display:flex;justify-content:space-between">
    <span>Job Market Analytics · Data & ML Hiring Intelligence</span>
    <span>jones31luke@gmail.com · linkedin.com/in/luke-j-78a02121b</span>
</div>
""", unsafe_allow_html=True)
