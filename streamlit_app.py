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
        ["📊 Overview", "🏢 Company Scorecard", "🔧 Skill Premiums", "🏭 Sector Dashboard", "🎯 Role Explorer", "🔥 Hiring Intensity", "📖 Metrics Guide"],
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
    # Live counts for hero
    hero_counts = query("""
        SELECT
            COUNT(*) FILTER (WHERE status='raw' AND data_tier=1) as active,
            COUNT(*) FILTER (WHERE data_tier=1) as total,
            COUNT(DISTINCT company_id) FILTER (WHERE status='raw' AND data_tier=1) as companies
        FROM job_postings
    """)
    hc = hero_counts.iloc[0] if not hero_counts.empty else None
    active_count = f"{int(hc['active']):,}" if hc is not None else "5,000+"
    total_count  = f"{int(hc['total']):,}"  if hc is not None else "6,000+"
    co_count     = f"{int(hc['companies']):,}" if hc is not None else "1,000+"

    st.markdown(f"""
    <div style="margin-bottom:40px">
        <div style="font-family:'Syne',sans-serif;font-size:2.5rem;font-weight:800;color:#e8e6e0;letter-spacing:-0.03em;line-height:1.1">
            Data & ML Hiring<br>Intelligence
        </div>
        <div style="font-size:0.75rem;color:#555;margin-top:12px;max-width:560px">
            Insights from {active_count} active job postings across {co_count} companies.
            Understand how compensation, role design, and transparency impact hiring outcomes.
            Updated nightly from 6 ATS sources.
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

    # ── Key Market Signals ────────────────────────────────────────────────────
    signals = query("""
        SELECT
            ROUND(AVG(CASE WHEN salary_max_annual IS NULL THEN 1.0 ELSE 0.0 END)*100) as no_salary_pct,
            COUNT(DISTINCT company_id) as companies
        FROM job_postings
        WHERE data_tier=1 AND status='raw'
    """)
    skill_signals = query("""
        SELECT
            ROUND(PERCENTILE_CONT(0.8) WITHIN GROUP (ORDER BY sc.skill_count)) as p80_skills,
            ROUND(AVG(sc.skill_count),1) as avg_skills
        FROM job_postings jp
        JOIN (SELECT job_id, COUNT(*) as skill_count FROM job_skills GROUP BY job_id) sc
            ON sc.job_id = jp.job_id
        WHERE jp.data_tier=1 AND jp.status='raw'
    """)
    ghost_signals = query("""
        SELECT COUNT(*) as high_ghost
        FROM vw_ghost_job_index
        WHERE ghost_tier = 'high'
    """)
    salary_signals = query("""
        SELECT
            ROUND(PERCENTILE_CONT(0.4) WITHIN GROUP (ORDER BY salary_max_annual)) as p40_sal,
            ROUND(PERCENTILE_CONT(0.6) WITHIN GROUP (ORDER BY salary_max_annual)) as p60_sal
        FROM job_postings
        WHERE data_tier=1 AND status='raw'
          AND salary_max_annual BETWEEN 50000 AND 500000
    """)

    sv  = signals.iloc[0]      if not signals.empty      else None
    skv = skill_signals.iloc[0] if not skill_signals.empty else None
    gv  = ghost_signals.iloc[0] if not ghost_signals.empty else None
    salv= salary_signals.iloc[0] if not salary_signals.empty else None

    signal_bullets = []
    if sv  is not None: signal_bullets.append(f"{int(sv['no_salary_pct'])}% of active roles do not disclose salary")
    if skv is not None: signal_bullets.append(f"Top 20% of roles require {int(skv['p80_skills'])}+ skills — avg is {float(skv['avg_skills']):.0f}")
    if gv  is not None: signal_bullets.append(f"{int(gv['high_ghost']):,} active roles show high ghost job probability")
    if salv is not None and pd.notna(salv['p40_sal']) and pd.notna(salv['p60_sal']):
        signal_bullets.append(f"Mid-market salary bands cluster between ${int(salv['p40_sal']):,}–${int(salv['p60_sal']):,}")
    signal_bullets.append(f"{active_count} active roles tracked across {co_count} companies as of today")

    bullet_html = "".join([
        f"<div style='font-size:0.75rem;color:#aaa;padding:4px 0;border-bottom:1px solid #111'>· {b}</div>"
        for b in signal_bullets
    ])

    st.markdown("<div class='section-header'>Key Market Signals</div>", unsafe_allow_html=True)
    st.markdown(f"""
    <div style="background:#0d0d18;border:1px solid #1e1e2e;border-radius:4px;
                padding:16px 20px;margin-bottom:24px">
        {bullet_html}
    </div>
    """, unsafe_allow_html=True)

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

# ══════════════════════════════════════════════════════════════════════════════
# PAGE: ROLE EXPLORER
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🎯 Role Explorer":
    st.markdown("""
    <div style="font-family:'Syne',sans-serif;font-size:2rem;font-weight:800;color:#e8e6e0;margin-bottom:8px">
        Role Explorer
    </div>
    <div style="font-size:0.7rem;color:#555;margin-bottom:32px">
        Compensation, skills, and hiring patterns by role family — Tier 1 active roles only
    </div>
    """, unsafe_allow_html=True)

    ROLE_FAMILY_SQL = """
    CASE
        WHEN lower(r.role_name) ~ 'director|head of|vp |vice president|chief data|chief analytics|manager, data|manager, analytics|manager, ml|manager, machine|data science manager|analytics manager|marketing analytics manager|marketing science' THEN 'Leadership'
        WHEN lower(r.role_name) ~ 'data engineer|analytics engineer|data architect|data platform engineer|mlops|data science engineer|data reliability|data infrastructure' THEN 'Data Engineer'
        WHEN lower(r.role_name) ~ 'machine learning|ml engineer|ai/ml|computer vision|applied scientist|applied researcher|ai researcher' THEN 'ML Engineer'
        WHEN lower(r.role_name) ~ 'ai engineer|applied ai|llm engineer|ai specialist|ai data' THEN 'AI Engineer'
        WHEN lower(r.role_name) ~ 'data scientist|quantitative researcher|research scientist|data science' THEN 'Data Scientist'
        WHEN lower(r.role_name) ~ 'data analyst|business analyst|bi analyst|financial analyst|fp&a|reporting analyst|business intelligence|product analyst|marketing analyst|fraud analyst|growth analyst|risk analyst|pricing analyst|compensation analyst|credit analyst|actuarial|clinical data|data quality analyst|analytics consultant|analytics lead|data analytics|data product manager|product manager, data|senior data product' THEN 'Data Analyst'
        WHEN lower(r.role_name) ~ 'sales op|revenue op|marketing op|operations analyst|operations manager|operations lead|operations specialist|operations director|revops' THEN 'Revenue/Ops'
        ELSE 'Other'
    END
    """

    ROLE_COLORS = {
        'Data Analyst':  '#4488ff',
        'Data Engineer': '#c8f542',
        'Data Scientist':'#ffd166',
        'ML Engineer':   '#06d6a0',
        'AI Engineer':   '#ff6b6b',
        'Leadership':    '#cc88ff',
        'Revenue/Ops':   '#ff9944',
        'Other':         '#555577',
    }

    # ── Role family overview strip ────────────────────────────────────────────
    family_overview = query(f"""
        SELECT
            {ROLE_FAMILY_SQL} as role_family,
            COUNT(DISTINCT jp.job_id) as jobs,
            ROUND(AVG(jp.salary_max_annual) FILTER (WHERE jp.salary_max_annual BETWEEN 50000 AND 500000)) as avg_max,
            ROUND(AVG(CASE WHEN jp.salary_max_annual IS NOT NULL THEN 1.0 ELSE 0.0 END)*100,1) as transparency_pct
        FROM job_postings jp
        JOIN roles r ON r.role_id = jp.role_id
        WHERE jp.data_tier=1 AND jp.status='raw'
        GROUP BY 1
        ORDER BY jobs DESC
    """)

    FAMILY_ORDER = ['Data Analyst','Data Engineer','Data Scientist','ML Engineer','AI Engineer','Leadership','Revenue/Ops','Other']
    family_overview['role_family'] = pd.Categorical(family_overview['role_family'], categories=FAMILY_ORDER, ordered=True)
    family_overview = family_overview.sort_values('role_family')

    # Pill selector
    selected_family = st.radio(
        "Role Family",
        family_overview['role_family'].tolist(),
        horizontal=True,
        label_visibility="collapsed"
    )

    fam = family_overview[family_overview['role_family'] == selected_family].iloc[0]
    fam_color = ROLE_COLORS.get(selected_family, '#c8f542')


    # ── Market Reality Block ──────────────────────────────────────────────────
    reality = query(f"""
        SELECT
            ROUND(AVG(sc.skill_count), 1)                                           as avg_skills,
            PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY sc.skill_count)            as p75_skills,
            ROUND(AVG(jp.salary_max_annual)
                FILTER (WHERE jp.salary_max_annual BETWEEN 50000 AND 500000))       as avg_max,
            ROUND(AVG(jp.salary_min_annual)
                FILTER (WHERE jp.salary_min_annual BETWEEN 30000 AND 500000))       as avg_min,
            ROUND(AVG(CASE WHEN jp.salary_max_annual IS NOT NULL
                THEN 1.0 ELSE 0.0 END) * 100, 1)                                   as transparency_pct,
            ROUND(AVG(jp.salary_max_annual)
                FILTER (WHERE jp.experience_level = 'entry'
                    AND jp.salary_max_annual BETWEEN 50000 AND 500000))             as entry_avg_max,
            ROUND(AVG(jp.salary_max_annual)
                FILTER (WHERE jp.experience_level = 'mid'
                    AND jp.salary_max_annual BETWEEN 50000 AND 500000))             as mid_avg_max,
            ROUND(AVG(jp.salary_max_annual)
                FILTER (WHERE jp.experience_level = 'senior'
                    AND jp.salary_max_annual BETWEEN 50000 AND 500000))             as senior_avg_max,
            COUNT(DISTINCT jp.job_id)                                               as total_jobs,
            ROUND(AVG(CASE WHEN jp.workplace_type = 'remote'
                THEN 1.0 ELSE 0.0 END) * 100)                                      as remote_pct
        FROM job_postings jp
        JOIN roles r ON r.role_id = jp.role_id
        LEFT JOIN (
            SELECT job_id, COUNT(*) as skill_count FROM job_skills GROUP BY job_id
        ) sc ON sc.job_id = jp.job_id
        WHERE jp.data_tier = 1 AND jp.status = 'raw'
          AND ({ROLE_FAMILY_SQL}) = %s
    """, params=(selected_family,))

    if not reality.empty:
        rv = reality.iloc[0]

        # ── Build alerts from real data ───────────────────────────────────────
        alerts = []
        insights = []

        # Transparency
        t = float(rv['transparency_pct']) if pd.notna(rv['transparency_pct']) else 0
        if t < 50:
            alerts.append(f"Only {t:.0f}% of {selected_family} roles post salary — most employers are hiding comp")
        elif t >= 70:
            alerts.append(f"{t:.0f}% of {selected_family} roles post salary — above-average transparency for this market")

        # Entry-level salary
        if pd.notna(rv['entry_avg_max']):
            entry = int(rv['entry_avg_max'])
            alerts.append(f"Entry-level avg max: ${entry:,} — {'competitive starting point' if entry > 120000 else 'below $120K signals high competition for junior roles'}")

        # Skill complexity
        if pd.notna(rv['p75_skills']):
            p75 = int(rv['p75_skills'])
            avg_sk = float(rv['avg_skills']) if pd.notna(rv['avg_skills']) else 0
            alerts.append(f"Avg role requires {avg_sk:.0f} skills — top 25% of postings require {p75}+")

        # Remote
        if pd.notna(rv['remote_pct']):
            rpct = int(rv['remote_pct'])
            alerts.append(f"{rpct}% of active {selected_family} roles are remote")

        # ── What this means (hiring vs job seeking) ───────────────────────────
        if pd.notna(rv['avg_max']) and pd.notna(rv['mid_avg_max']):
            avg_max = int(rv['avg_max'])
            mid_max = int(rv['mid_avg_max'])
            p75_sk  = int(rv['p75_skills']) if pd.notna(rv['p75_skills']) else 8
            insights.append(("If you're hiring", [
                f"Posting below ${int(mid_max * 0.9):,} puts you below 90% of mid-level market rate",
                f"Requiring {p75_sk + 2}+ skills places your role in the top 10% complexity — expect longer fill times",
                f"{'Adding' if t < 50 else 'Keeping'} a salary band {'increases' if t < 50 else 'maintains'} applicant volume significantly",
            ]))
            insights.append(("If you're job seeking", [
                f"Market mid-level max is ${mid_max:,} — negotiate toward this if offered less",
                f"Roles requiring {p75_sk}+ skills are harder to fill — stronger negotiating position",
                f"{'Only' if t < 50 else ''} {t:.0f}% of roles show salary — always ask in screening",
            ]))

        # ── Render ────────────────────────────────────────────────────────────
        st.markdown(f"""
        <div style="background:#0d0d1a;border:1px solid #ff6b6b33;border-left:3px solid #ff6b6b;
                    border-radius:4px;padding:16px 20px;margin-bottom:24px">
            <div style="font-size:0.6rem;color:#ff6b6b;text-transform:uppercase;
                        letter-spacing:0.15em;margin-bottom:12px">
                🚨 Market Reality — {selected_family} (US, Active Roles)
            </div>
            {"".join([f"<div style='font-size:0.75rem;color:#ccc;padding:3px 0'>⚠️ {a}</div>" for a in alerts])}
        </div>
        """, unsafe_allow_html=True)

        if insights:
            ic1, ic2 = st.columns(2)
            for col, (label, points) in zip([ic1, ic2], insights):
                with col:
                    point_html = "".join([
                        f"<div style='font-size:0.7rem;color:#aaa;padding:3px 0'>→ {p}</div>"
                        for p in points
                    ])
                    st.markdown(f"""
                    <div style="background:#111120;border:1px solid #1e1e2e;border-left:3px solid #c8f542;
                                border-radius:4px;padding:14px 16px;height:100%">
                        <div style="font-size:0.6rem;color:#c8f542;text-transform:uppercase;
                                    letter-spacing:0.12em;margin-bottom:10px">🎯 {label}</div>
                        {point_html}
                    </div>
                    """, unsafe_allow_html=True)

        st.markdown("<div style='margin-bottom:8px'></div>", unsafe_allow_html=True)

    st.markdown("<div class='section-header'>Overview</div>", unsafe_allow_html=True)


    # Hero metrics
    hc1, hc2, hc3, hc4 = st.columns(4)
    with hc1:
        st.metric("Active Roles", f"{int(fam['jobs']):,}")
    with hc2:
        avg_max = f"${int(fam['avg_max']):,}" if pd.notna(fam['avg_max']) else "—"
        st.metric("Avg Max Salary", avg_max)
    with hc3:
        st.metric("Salary Transparency", f"{fam['transparency_pct']}%")
    with hc4:
        # Share of total market
        total = family_overview['jobs'].sum()
        share = round(int(fam['jobs']) / total * 100, 1)
        st.metric("Market Share", f"{share}%")

    # ── Salary by experience level ────────────────────────────────────────────
    st.markdown("<div class='section-header'>Compensation by Experience Level</div>", unsafe_allow_html=True)

    sal_by_level = query(f"""
        SELECT
            jp.experience_level,
            COUNT(DISTINCT jp.job_id) as jobs,
            ROUND(AVG(jp.salary_min_annual) FILTER (WHERE jp.salary_min_annual BETWEEN 30000 AND 500000)) as avg_min,
            ROUND(AVG(jp.salary_max_annual) FILTER (WHERE jp.salary_max_annual BETWEEN 50000 AND 500000)) as avg_max,
            ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY jp.salary_max_annual)
                FILTER (WHERE jp.salary_max_annual BETWEEN 50000 AND 500000)) as median_max
        FROM job_postings jp
        JOIN roles r ON r.role_id = jp.role_id
        WHERE jp.data_tier=1 AND jp.status='raw'
          AND jp.experience_level IS NOT NULL
          AND ({ROLE_FAMILY_SQL}) = %s
        GROUP BY jp.experience_level
        ORDER BY
            CASE jp.experience_level
                WHEN 'entry' THEN 1 WHEN 'associate' THEN 2
                WHEN 'mid' THEN 3 WHEN 'senior' THEN 4
                ELSE 5 END
    """, params=(selected_family,))

    if not sal_by_level.empty and sal_by_level['avg_max'].notna().any():
        lc1, lc2 = st.columns([2, 1])
        with lc1:
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=sal_by_level['experience_level'],
                y=sal_by_level['avg_min'],
                name='Avg Min',
                marker_color='#1e1e3e',
                marker_line_color=fam_color,
                marker_line_width=1,
            ))
            fig.add_trace(go.Bar(
                x=sal_by_level['experience_level'],
                y=sal_by_level['avg_max'],
                name='Avg Max',
                marker_color=fam_color,
                opacity=0.85,
            ))
            fig.update_layout(
                plot_bgcolor='#0a0a0f', paper_bgcolor='#0a0a0f',
                font_color='#888', font_family='DM Mono',
                height=280, barmode='overlay',
                margin=dict(l=0, r=0, t=0, b=0),
                xaxis=dict(showgrid=False),
                yaxis=dict(showgrid=False, tickformat='$,.0f'),
                legend=dict(bgcolor='#0a0a0f', bordercolor='#1e1e2e', borderwidth=1),
            )
            st.plotly_chart(fig, use_container_width=True)

        with lc2:
            for _, lvl in sal_by_level.iterrows():
                avg = f"${int(lvl['avg_max']):,}" if pd.notna(lvl['avg_max']) else "—"
                st.markdown(f"""
                <div style="padding:10px;border:1px solid #1e1e2e;border-radius:3px;margin-bottom:6px;background:#111120">
                    <div style="font-size:0.6rem;color:#444;text-transform:uppercase">{lvl['experience_level']}</div>
                    <div style="font-family:'Syne',sans-serif;font-size:1.1rem;font-weight:800;color:{fam_color}">{avg}</div>
                    <div style="font-size:0.6rem;color:#333">{int(lvl['jobs'])} roles</div>
                </div>
                """, unsafe_allow_html=True)
    else:
        st.markdown("<div style='color:#444;font-size:0.7rem'>Not enough salary data for this role family.</div>", unsafe_allow_html=True)

    # ── Top skills ────────────────────────────────────────────────────────────
    st.markdown("<div class='section-header'>Top Skills & Salary Premium</div>", unsafe_allow_html=True)

    top_skills = query(f"""
        SELECT s.skill_name,
            COUNT(DISTINCT jp.job_id) as jobs,
            ROUND(AVG(jp.salary_max_annual) FILTER (WHERE jp.salary_max_annual BETWEEN 50000 AND 500000)) as avg_max,
            ROUND((AVG(jp.salary_max_annual) FILTER (WHERE jp.salary_max_annual BETWEEN 50000 AND 500000) -
                (SELECT AVG(jp2.salary_max_annual)
                 FROM job_postings jp2 JOIN roles r2 ON r2.role_id = jp2.role_id
                 WHERE jp2.data_tier=1 AND jp2.status='raw'
                   AND jp2.salary_max_annual BETWEEN 50000 AND 500000
                   AND (CASE WHEN lower(r2.role_name) ~ 'director|head of|vp |vice president|chief data|chief analytics|manager, data|manager, analytics|manager, ml|manager, machine|data science manager|analytics manager|marketing analytics manager|marketing science' THEN 'Leadership'
                        WHEN lower(r2.role_name) ~ 'data engineer|analytics engineer|data architect|data platform engineer|mlops|data science engineer|data reliability|data infrastructure' THEN 'Data Engineer'
                        WHEN lower(r2.role_name) ~ 'machine learning|ml engineer|ai/ml|computer vision|applied scientist|applied researcher|ai researcher' THEN 'ML Engineer'
                        WHEN lower(r2.role_name) ~ 'ai engineer|applied ai|llm engineer|ai specialist|ai data' THEN 'AI Engineer'
                        WHEN lower(r2.role_name) ~ 'data scientist|quantitative researcher|research scientist|data science' THEN 'Data Scientist'
                        WHEN lower(r2.role_name) ~ 'data analyst|business analyst|bi analyst|financial analyst|fp&a|reporting analyst|business intelligence|product analyst|marketing analyst|fraud analyst|growth analyst|risk analyst|pricing analyst|compensation analyst|credit analyst|actuarial|clinical data|data quality analyst|analytics consultant|analytics lead|data analytics|data product manager|product manager, data|senior data product' THEN 'Data Analyst'
                        WHEN lower(r2.role_name) ~ 'sales op|revenue op|marketing op|operations analyst|operations manager|operations lead|operations specialist|operations director|revops' THEN 'Revenue/Ops'
                        ELSE 'Other' END) = %(fam)s))
                /
                NULLIF((SELECT AVG(jp2.salary_max_annual)
                 FROM job_postings jp2 JOIN roles r2 ON r2.role_id = jp2.role_id
                 WHERE jp2.data_tier=1 AND jp2.status='raw'
                   AND jp2.salary_max_annual BETWEEN 50000 AND 500000
                   AND (CASE WHEN lower(r2.role_name) ~ 'director|head of|vp |vice president|chief data|chief analytics|manager, data|manager, analytics|manager, ml|manager, machine|data science manager|analytics manager|marketing analytics manager|marketing science' THEN 'Leadership'
                        WHEN lower(r2.role_name) ~ 'data engineer|analytics engineer|data architect|data platform engineer|mlops|data science engineer|data reliability|data infrastructure' THEN 'Data Engineer'
                        WHEN lower(r2.role_name) ~ 'machine learning|ml engineer|ai/ml|computer vision|applied scientist|applied researcher|ai researcher' THEN 'ML Engineer'
                        WHEN lower(r2.role_name) ~ 'ai engineer|applied ai|llm engineer|ai specialist|ai data' THEN 'AI Engineer'
                        WHEN lower(r2.role_name) ~ 'data scientist|quantitative researcher|research scientist|data science' THEN 'Data Scientist'
                        WHEN lower(r2.role_name) ~ 'data analyst|business analyst|bi analyst|financial analyst|fp&a|reporting analyst|business intelligence|product analyst|marketing analyst|fraud analyst|growth analyst|risk analyst|pricing analyst|compensation analyst|credit analyst|actuarial|clinical data|data quality analyst|analytics consultant|analytics lead|data analytics|data product manager|product manager, data|senior data product' THEN 'Data Analyst'
                        WHEN lower(r2.role_name) ~ 'sales op|revenue op|marketing op|operations analyst|operations manager|operations lead|operations specialist|operations director|revops' THEN 'Revenue/Ops'
                        ELSE 'Other' END) = %(fam)s), 0)
                * 100, 1) as premium_pct
        FROM job_postings jp
        JOIN roles r ON r.role_id = jp.role_id
        JOIN job_skills js ON js.job_id = jp.job_id
        JOIN skills s ON s.skill_id = js.skill_id
        WHERE jp.data_tier=1 AND jp.status='raw'
          AND s.difficulty_relevant = true
          AND ({ROLE_FAMILY_SQL}) = %(fam)s
        GROUP BY s.skill_name
        HAVING COUNT(DISTINCT jp.job_id) >= 5
        ORDER BY jobs DESC
        LIMIT 20
    """, params={"fam": selected_family})

    if not top_skills.empty:
        sc1, sc2 = st.columns(2)
        with sc1:
            fig2 = px.bar(
                top_skills.head(15),
                x='jobs', y='skill_name',
                orientation='h',
                color='jobs',
                color_continuous_scale=[[0,'#1a1a2e'],[1,fam_color]],
                labels={'jobs':'Roles Requiring Skill','skill_name':''},
                text='jobs',
            )
            fig2.update_layout(
                plot_bgcolor='#0a0a0f', paper_bgcolor='#0a0a0f',
                font_color='#888', font_family='DM Mono',
                height=400, coloraxis_showscale=False,
                margin=dict(l=0, r=20, t=0, b=0),
                xaxis=dict(showgrid=False, showticklabels=False),
                yaxis=dict(showgrid=False, categoryorder='total ascending'),
                title=dict(text='Most In-Demand', font=dict(color='#555', size=10)),
            )
            fig2.update_traces(textposition='outside', textfont_color=fam_color, marker_line_width=0)
            st.plotly_chart(fig2, use_container_width=True)

        with sc2:
            premium_data = top_skills.dropna(subset=['premium_pct']).sort_values('premium_pct', ascending=False).head(15)
            if not premium_data.empty:
                fig3 = px.bar(
                    premium_data,
                    x='premium_pct', y='skill_name',
                    orientation='h',
                    color='premium_pct',
                    color_continuous_scale=[[0,'#1a1a2e'],[0.5,'#333366'],[1,fam_color]],
                    labels={'premium_pct':'Salary Premium %','skill_name':''},
                    text=premium_data['premium_pct'].apply(lambda x: f"+{x}%" if x > 0 else f"{x}%"),
                )
                fig3.update_layout(
                    plot_bgcolor='#0a0a0f', paper_bgcolor='#0a0a0f',
                    font_color='#888', font_family='DM Mono',
                    height=400, coloraxis_showscale=False,
                    margin=dict(l=0, r=20, t=0, b=0),
                    xaxis=dict(showgrid=False, zeroline=True, zerolinecolor='#1e1e2e', showticklabels=False),
                    yaxis=dict(showgrid=False, categoryorder='total ascending'),
                    title=dict(text='Salary Premium vs Baseline', font=dict(color='#555', size=10)),
                )
                fig3.update_traces(textposition='outside', textfont_color=fam_color, marker_line_width=0)
                st.plotly_chart(fig3, use_container_width=True)

    # ── Workplace & remote breakdown ──────────────────────────────────────────
    st.markdown("<div class='section-header'>Work Model & Top Hiring Companies</div>", unsafe_allow_html=True)

    wc1, wc2 = st.columns([1, 2])

    with wc1:
        workplace = query(f"""
            SELECT
                COALESCE(jp.workplace_type, 'unspecified') as workplace_type,
                COUNT(*) as jobs
            FROM job_postings jp
            JOIN roles r ON r.role_id = jp.role_id
            WHERE jp.data_tier=1 AND jp.status='raw'
              AND ({ROLE_FAMILY_SQL}) = %s
            GROUP BY 1 ORDER BY 2 DESC
        """, params=(selected_family,))

        if not workplace.empty:
            wp_colors = {'remote':'#06d6a0','hybrid':'#ffd166','onsite':'#ff6b6b','unspecified':'#333355'}
            fig4 = px.pie(
                workplace, values='jobs', names='workplace_type',
                color='workplace_type',
                color_discrete_map=wp_colors,
                hole=0.6,
            )
            fig4.update_layout(
                plot_bgcolor='#0a0a0f', paper_bgcolor='#0a0a0f',
                font_color='#888', font_family='DM Mono',
                height=280, margin=dict(l=0, r=0, t=0, b=0),
                showlegend=True,
                legend=dict(bgcolor='#0a0a0f', bordercolor='#1e1e2e', borderwidth=1, font=dict(size=10)),
            )
            fig4.update_traces(textinfo='percent', textfont_size=10)
            st.plotly_chart(fig4, use_container_width=True)

    with wc2:
        top_cos = query(f"""
            SELECT c.company_name, c.sector,
                COUNT(DISTINCT jp.job_id) as roles,
                ROUND(AVG(jp.salary_max_annual) FILTER (WHERE jp.salary_max_annual BETWEEN 50000 AND 500000)) as avg_max,
                ROUND(AVG(CASE WHEN jp.salary_max_annual IS NOT NULL THEN 1.0 ELSE 0.0 END)*100) as transparency_pct
            FROM job_postings jp
            JOIN roles r ON r.role_id = jp.role_id
            JOIN companies c ON c.company_id = jp.company_id
            WHERE jp.data_tier=1 AND jp.status='raw'
              AND ({ROLE_FAMILY_SQL}) = %s
            GROUP BY c.company_name, c.sector
            ORDER BY roles DESC
            LIMIT 12
        """, params=(selected_family,))

        if not top_cos.empty:
            top_cos['avg_max'] = top_cos['avg_max'].apply(lambda x: f"${int(x):,}" if pd.notna(x) else "—")
            top_cos['transparency_pct'] = top_cos['transparency_pct'].apply(lambda x: f"{int(x)}%" if pd.notna(x) else "—")
            st.dataframe(
                top_cos.rename(columns={
                    'company_name':'Company','sector':'Sector',
                    'roles':'Roles','avg_max':'Avg Max','transparency_pct':'Transparent'
                }),
                use_container_width=True, hide_index=True
            )




elif page == "📖 Metrics Guide":

    st.markdown("""
    <div style="margin-bottom:32px">
        <div style="font-family:'Syne',sans-serif;font-size:0.65rem;color:#444;
            text-transform:uppercase;letter-spacing:0.15em;margin-bottom:8px">Documentation</div>
        <h1 style="font-family:'Syne',sans-serif;font-size:2.2rem;font-weight:800;
            color:#e8e6e0;margin:0;line-height:1.1">How Our Metrics Work</h1>
        <p style="color:#555;margin-top:12px;max-width:600px;font-size:0.85rem;line-height:1.7">
            Every score on this platform is derived from live ATS data — not surveys, not estimates.
            Here's exactly how each metric is calculated.
        </p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div style="background:#0f0f18;border:1px solid #1e1e2e;border-radius:6px;padding:28px 32px;margin-bottom:16px">
        <div style="display:flex;align-items:flex-start;gap:20px">
            <div style="font-size:2rem;line-height:1;flex-shrink:0">👻</div>
            <div style="flex:1">
                <div style="font-family:'Syne',sans-serif;font-size:1.3rem;font-weight:800;color:#e8e6e0;margin-bottom:6px">Ghost Job Probability</div>
                <div style="font-size:0.75rem;color:#444;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:16px">0–100 · Higher = More Likely a Ghost</div>
                <p style="color:#888;font-size:0.85rem;line-height:1.7;margin-bottom:16px">
                    A ghost job is a posting that is no longer actively being filled — the position may be on hold,
                    already filled internally, or posted to build a pipeline. We calculate ghost probability using three signals:
                </p>
                <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:16px">
                    <div style="background:#0a0a0f;border:1px solid #1e1e2e;border-radius:4px;padding:14px">
                        <div style="font-size:0.65rem;color:#444;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px">Days Active</div>
                        <div style="font-size:0.85rem;color:#c8f542;font-weight:600;margin-bottom:6px">40%</div>
                        <div style="font-size:0.75rem;color:#666;line-height:1.5">Postings older than 30 days score higher. After 60 days the signal is strong. Most legitimate openings fill within 45 days.</div>
                    </div>
                    <div style="background:#0a0a0f;border:1px solid #1e1e2e;border-radius:4px;padding:14px">
                        <div style="font-size:0.65rem;color:#444;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px">Repost Count</div>
                        <div style="font-size:0.85rem;color:#c8f542;font-weight:600;margin-bottom:6px">35%</div>
                        <div style="font-size:0.75rem;color:#666;line-height:1.5">Jobs taken down and reposted repeatedly are a strong signal of pipeline-building rather than active hiring.</div>
                    </div>
                    <div style="background:#0a0a0f;border:1px solid #1e1e2e;border-radius:4px;padding:14px">
                        <div style="font-size:0.65rem;color:#444;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px">Company Velocity</div>
                        <div style="font-size:0.85rem;color:#c8f542;font-weight:600;margin-bottom:6px">25%</div>
                        <div style="font-size:0.75rem;color:#666;line-height:1.5">Companies with rapidly declining active role counts are more likely to have stale postings not yet cleaned up.</div>
                    </div>
                </div>
                <div style="background:#0a0a0f;border-left:3px solid #c8f542;padding:12px 16px;border-radius:0 4px 4px 0">
                    <div style="font-size:0.75rem;color:#888;line-height:1.6">
                        <strong style="color:#c8f542">Tiers:</strong>
                        &nbsp;🔴 High (75+) · 🟡 Medium (40–74) · 🟢 Low (15–39) · ✨ Fresh (&lt;15)
                        &nbsp;·&nbsp; Fresh jobs are under 7 days old and score near zero regardless of other signals.
                    </div>
                </div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div style="background:#0f0f18;border:1px solid #1e1e2e;border-radius:6px;padding:28px 32px;margin-bottom:16px">
        <div style="display:flex;align-items:flex-start;gap:20px">
            <div style="font-size:2rem;line-height:1;flex-shrink:0">🎯</div>
            <div style="flex:1">
                <div style="font-family:'Syne',sans-serif;font-size:1.3rem;font-weight:800;color:#e8e6e0;margin-bottom:6px">Honesty Score</div>
                <div style="font-size:0.75rem;color:#444;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:16px">0–100 · Higher = More Transparent</div>
                <p style="color:#888;font-size:0.85rem;line-height:1.7;margin-bottom:16px">
                    Measures how transparent and specific a job posting is. Vague postings with no salary,
                    generic requirements, and boilerplate descriptions score low. Honest postings score high.
                </p>
                <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:10px;margin-bottom:16px">
                    <div style="background:#0a0a0f;border:1px solid #1e1e2e;border-radius:4px;padding:14px">
                        <div style="font-size:0.65rem;color:#444;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px">Salary Disclosed</div>
                        <div style="font-size:0.85rem;color:#c8f542;font-weight:600;margin-bottom:6px">+40 pts</div>
                        <div style="font-size:0.75rem;color:#666;line-height:1.5">Disclosing a salary range is the single biggest honesty signal.</div>
                    </div>
                    <div style="background:#0a0a0f;border:1px solid #1e1e2e;border-radius:4px;padding:14px">
                        <div style="font-size:0.65rem;color:#444;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px">Description Quality</div>
                        <div style="font-size:0.85rem;color:#c8f542;font-weight:600;margin-bottom:6px">+30 pts</div>
                        <div style="font-size:0.75rem;color:#666;line-height:1.5">Scored on length, specificity, and ratio of responsibilities to boilerplate.</div>
                    </div>
                    <div style="background:#0a0a0f;border:1px solid #1e1e2e;border-radius:4px;padding:14px">
                        <div style="font-size:0.65rem;color:#444;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px">Requirement Fit</div>
                        <div style="font-size:0.85rem;color:#c8f542;font-weight:600;margin-bottom:6px">+20 pts</div>
                        <div style="font-size:0.75rem;color:#666;line-height:1.5">Checks whether years of experience match the stated level. "Entry level: 5+ years" scores a penalty.</div>
                    </div>
                    <div style="background:#0a0a0f;border:1px solid #1e1e2e;border-radius:4px;padding:14px">
                        <div style="font-size:0.65rem;color:#444;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px">Location Clarity</div>
                        <div style="font-size:0.85rem;color:#c8f542;font-weight:600;margin-bottom:6px">+10 pts</div>
                        <div style="font-size:0.75rem;color:#666;line-height:1.5">Specifying city/state or clearly stating remote earns full credit.</div>
                    </div>
                </div>
                <div style="background:#0a0a0f;border-left:3px solid #c8f542;padding:12px 16px;border-radius:0 4px 4px 0">
                    <div style="font-size:0.75rem;color:#888;line-height:1.6">
                        <strong style="color:#c8f542">Interpretation:</strong>
                        &nbsp;80–100 = High quality · 60–79 = Acceptable · Below 60 = Proceed with caution
                    </div>
                </div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div style="background:#0f0f18;border:1px solid #1e1e2e;border-radius:6px;padding:28px 32px;margin-bottom:16px">
        <div style="display:flex;align-items:flex-start;gap:20px">
            <div style="font-size:2rem;line-height:1;flex-shrink:0">🔥</div>
            <div style="flex:1">
                <div style="font-family:'Syne',sans-serif;font-size:1.3rem;font-weight:800;color:#e8e6e0;margin-bottom:6px">Hiring Intensity</div>
                <div style="font-size:0.75rem;color:#444;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:16px">Active Roles ÷ Total Employees × 100</div>
                <p style="color:#888;font-size:0.85rem;line-height:1.7;margin-bottom:16px">
                    The percentage of a company's workforce they are actively trying to hire for in data and ML roles right now.
                    Normalizes for company size — a startup with 5 open roles is hiring more aggressively than a Fortune 500 with 50.
                </p>
                <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:16px">
                    <div style="background:#0a0a0f;border:1px solid #1e1e2e;border-radius:4px;padding:14px;text-align:center">
                        <div style="font-family:'Syne',sans-serif;font-size:1.6rem;font-weight:800;color:#c8f542">&lt;0.5%</div>
                        <div style="font-size:0.7rem;color:#666;margin-top:6px;text-transform:uppercase;letter-spacing:0.08em">Steady State</div>
                        <div style="font-size:0.75rem;color:#555;margin-top:6px;line-height:1.5">Normal ongoing hiring. No urgency signal.</div>
                    </div>
                    <div style="background:#0a0a0f;border:1px solid #1e1e2e;border-radius:4px;padding:14px;text-align:center">
                        <div style="font-family:'Syne',sans-serif;font-size:1.6rem;font-weight:800;color:#c8f542">0.5–2%</div>
                        <div style="font-size:0.7rem;color:#666;margin-top:6px;text-transform:uppercase;letter-spacing:0.08em">Active Build-Out</div>
                        <div style="font-size:0.75rem;color:#555;margin-top:6px;line-height:1.5">Actively scaling the data team. Good signal for recruiters.</div>
                    </div>
                    <div style="background:#0a0a0f;border:1px solid #1e1e2e;border-radius:4px;padding:14px;text-align:center">
                        <div style="font-family:'Syne',sans-serif;font-size:1.6rem;font-weight:800;color:#c8f542">&gt;2%</div>
                        <div style="font-size:0.7rem;color:#666;margin-top:6px;text-transform:uppercase;letter-spacing:0.08em">Hyper-Growth</div>
                        <div style="font-size:0.75rem;color:#555;margin-top:6px;line-height:1.5">Major data team expansion. High urgency. Act fast.</div>
                    </div>
                </div>
                <div style="background:#0a0a0f;border-left:3px solid #c8f542;padding:12px 16px;border-radius:0 4px 4px 0">
                    <div style="font-size:0.75rem;color:#888;line-height:1.6">
                        <strong style="color:#c8f542">Data source:</strong>
                        &nbsp;Employee counts sourced from Wikipedia, public filings, and verified company data.
                        Intensity is scoped to <em>data and ML roles only</em> — not total company hiring.
                    </div>
                </div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div style="background:#0f0f18;border:1px solid #1e1e2e;border-radius:6px;padding:28px 32px;margin-bottom:16px">
        <div style="font-family:'Syne',sans-serif;font-size:1.1rem;font-weight:800;color:#e8e6e0;margin-bottom:16px">📡 Data Sources & Methodology</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px">
            <div>
                <div style="font-size:0.7rem;color:#444;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:10px">ATS Sources (Tier 1)</div>
                <div style="font-size:0.8rem;color:#666;line-height:2">Greenhouse · Lever · Ashby · Workday · Eightfold · Amazon<br><span style="color:#444">Full job descriptions, salary data, and metadata</span></div>
            </div>
            <div>
                <div style="font-size:0.7rem;color:#444;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:10px">Update Cadence</div>
                <div style="font-size:0.8rem;color:#666;line-height:2">All sources crawled nightly at 6am UTC<br>Scores rebuilt daily at 11am UTC<br><span style="color:#444">~6,000 active Tier 1 jobs tracked</span></div>
            </div>
            <div>
                <div style="font-size:0.7rem;color:#444;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:10px">Scope</div>
                <div style="font-size:0.8rem;color:#666;line-height:2">Data, ML, and analytics roles only<br>US market focus · Remote roles included<br><span style="color:#444">500+ companies with headcount data</span></div>
            </div>
            <div>
                <div style="font-size:0.7rem;color:#444;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:10px">Salary Data</div>
                <div style="font-size:0.8rem;color:#666;line-height:2">Parsed directly from job descriptions<br>Annualized from hourly/monthly where needed<br><span style="color:#444">~55% of active jobs have salary data</span></div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)



elif page == "🔥 Hiring Intensity":
    st.markdown("""
    <div style="margin-bottom:32px">
        <div style="font-family:'Syne',sans-serif;font-size:0.65rem;color:#444;
            text-transform:uppercase;letter-spacing:0.15em;margin-bottom:8px">Company Intelligence</div>
        <h1 style="font-family:'Syne',sans-serif;font-size:2.2rem;font-weight:800;
            color:#e8e6e0;margin:0;line-height:1.1">Hiring Intensity</h1>
        <p style="color:#555;margin-top:12px;max-width:600px;font-size:0.85rem;line-height:1.7">
            Active data & ML roles as a % of total headcount. Normalizes for company size —
            a startup hiring 5 data roles is more aggressive than a Fortune 500 hiring 50.
        </p>
    </div>
    """, unsafe_allow_html=True)

    # Filters
    hi_col1, hi_col2, hi_col3 = st.columns([2, 1, 1])
    with hi_col1:
        hi_sectors = query("SELECT DISTINCT sector FROM analytics_analytics.mart_company_scorecard WHERE sector IS NOT NULL AND hiring_intensity_pct IS NOT NULL ORDER BY sector")
        hi_sector_filter = st.multiselect("Filter by Sector", hi_sectors["sector"].tolist(), placeholder="All sectors", key="hi_sector")
    with hi_col2:
        min_employees = st.selectbox("Min headcount", [0, 200, 500, 1000, 2000, 5000], index=0, key="hi_min_emp")
    with hi_col3:
        hi_sort = st.selectbox("Sort by", ["hiring_intensity_pct", "active_roles", "employee_count"], key="hi_sort")

    hi_where = ["hiring_intensity_pct IS NOT NULL", f"employee_count >= {min_employees}"]
    if hi_sector_filter:
        hi_where.append(f"sector IN ({','.join([repr(s) for s in hi_sector_filter])})")

    intensity_data = query(f"""
        SELECT company_name, sector, active_roles, employee_count,
               hiring_intensity_pct, avg_max_salary
        FROM analytics_analytics.mart_company_scorecard
        WHERE {' AND '.join(hi_where)}
        ORDER BY {hi_sort} DESC NULLS LAST
        LIMIT 500
    """)

    if not intensity_data.empty:
        # Summary metrics
        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.metric("Companies Tracked", f"{len(intensity_data):,}")
        with m2:
            avg_i = intensity_data['hiring_intensity_pct'].mean()
            st.metric("Avg Intensity", f"{avg_i:.2f}%")
        with m3:
            hyper = len(intensity_data[intensity_data['hiring_intensity_pct'] >= 2])
            st.metric("Hyper-Growth (>2%)", f"{hyper}")
        with m4:
            active_build = len(intensity_data[(intensity_data['hiring_intensity_pct'] >= 0.5) & (intensity_data['hiring_intensity_pct'] < 2)])
            st.metric("Active Build-Out (0.5–2%)", f"{active_build}")

        # Tier bands
        st.markdown("<div class='section-header'>Intensity Distribution</div>", unsafe_allow_html=True)
        tc1, tc2, tc3 = st.columns(3)
        hyper_df  = intensity_data[intensity_data['hiring_intensity_pct'] >= 2]
        active_df = intensity_data[(intensity_data['hiring_intensity_pct'] >= 0.5) & (intensity_data['hiring_intensity_pct'] < 2)]
        steady_df = intensity_data[intensity_data['hiring_intensity_pct'] < 0.5]

        for col, df, label, color, desc in [
            (tc1, hyper_df,  "🔥 Hyper-Growth",    "#ff6b6b", ">2% intensity"),
            (tc2, active_df, "📈 Active Build-Out", "#ffd166", "0.5–2% intensity"),
            (tc3, steady_df, "🟢 Steady State",     "#06d6a0", "<0.5% intensity"),
        ]:
            with col:
                st.markdown(f"""
                <div class="metric-card">
                    <div style="font-size:0.6rem;color:#444;text-transform:uppercase;letter-spacing:0.1em">{label}</div>
                    <div style="font-family:'Syne',sans-serif;font-size:2rem;font-weight:800;color:{color};margin-top:4px">{len(df)}</div>
                    <div style="font-size:0.65rem;color:#555;margin-top:4px">{desc}</div>
                    <div style="margin-top:10px;font-size:0.7rem;color:#666;line-height:1.8">
                        {"<br>".join([f"<span style='color:#888'>{r['company_name']}</span> <span style='color:{color}'>{r['hiring_intensity_pct']}%</span>" for _, r in df.head(5).iterrows()])}
                    </div>
                </div>
                """, unsafe_allow_html=True)

        # Bubble chart
        st.markdown("<div class='section-header'>Roles vs Headcount — Bubble = Intensity</div>", unsafe_allow_html=True)

        chart_df = intensity_data[intensity_data['employee_count'] > 0].copy()
        chart_df['size'] = chart_df['hiring_intensity_pct'].clip(upper=10)
        chart_df['label'] = chart_df['company_name'] + "<br>" + chart_df['hiring_intensity_pct'].apply(lambda x: f"{x:.1f}%")

        fig = px.scatter(
            chart_df,
            x='employee_count', y='active_roles',
            size='size', color='hiring_intensity_pct',
            color_continuous_scale=[[0,'#1a1a2e'],[0.3,'#333366'],[0.7,'#ffd166'],[1,'#ff6b6b']],
            hover_name='company_name',
            hover_data={'sector': True, 'hiring_intensity_pct': ':.2f', 'employee_count': ':,', 'active_roles': True, 'size': False},
            labels={'employee_count': 'Total Employees', 'active_roles': 'Active Data/ML Roles', 'hiring_intensity_pct': 'Intensity %'},
            log_x=True,
        )
        fig.update_layout(
            plot_bgcolor='#0a0a0f', paper_bgcolor='#0a0a0f',
            font_color='#888', font_family='DM Mono',
            height=480,
            margin=dict(l=0, r=0, t=0, b=0),
            xaxis=dict(showgrid=False, title='Total Employees (log scale)'),
            yaxis=dict(showgrid=False, title='Active Data & ML Roles'),
            coloraxis_colorbar=dict(title='Intensity %', tickfont=dict(color='#888')),
        )
        st.plotly_chart(fig, use_container_width=True)

        # Full table
        st.markdown("<div class='section-header'>Full Rankings</div>", unsafe_allow_html=True)

        display = intensity_data.copy()
        display['hiring_intensity_pct'] = display['hiring_intensity_pct'].apply(lambda x: f"{x:.2f}%")
        display['avg_max_salary'] = display['avg_max_salary'].apply(lambda x: f"${int(x):,}" if pd.notna(x) else "—")
        display['employee_count'] = display['employee_count'].apply(lambda x: f"{int(x):,}" if pd.notna(x) else "—")

        def tier_label(row):
            pct = float(row['hiring_intensity_pct'].replace('%',''))
            if pct >= 2: return "🔥 Hyper"
            if pct >= 0.5: return "📈 Active"
            return "🟢 Steady"

        display['tier'] = display.apply(tier_label, axis=1)

        st.dataframe(
            display[['company_name','sector','tier','active_roles','employee_count','hiring_intensity_pct','avg_max_salary']]
            .rename(columns={
                'company_name':'Company', 'sector':'Sector', 'tier':'Tier',
                'active_roles':'Active Roles', 'employee_count':'Employees',
                'hiring_intensity_pct':'Intensity', 'avg_max_salary':'Avg Max Salary'
            }),
            use_container_width=True, hide_index=True
        )
    else:
        st.markdown("<div style='color:#444;font-size:0.8rem'>No data available with current filters.</div>", unsafe_allow_html=True)


# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("""
<div style="margin-top:48px;padding-top:16px;border-top:1px solid #1e1e2e;font-size:0.6rem;color:#333;display:flex;justify-content:space-between">
    <span>Job Market Analytics · Data & ML Hiring Intelligence</span>
    <span>jones31luke@gmail.com · linkedin.com/in/luke-j-78a02121b</span>
</div>
""", unsafe_allow_html=True)
