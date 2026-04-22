"""
Run from repo root:
    python3 patch_market_reality.py

Adds a "Market Reality" judgment block at the top of the Role Explorer page.
"""

MARKET_REALITY_BLOCK = '''
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
'''

ANCHOR = "    st.markdown(\"<div class='section-header'>Overview</div>\", unsafe_allow_html=True)"

with open('streamlit_app.py', 'r') as f:
    content = f.read()

# Find the anchor inside the Role Explorer page (after ROLE_COLORS def)
role_explorer_start = content.find("# PAGE: ROLE EXPLORER")
if role_explorer_start == -1:
    print("ERROR: Role Explorer page not found")
    exit(1)

anchor_idx = content.find(ANCHOR, role_explorer_start)
if anchor_idx == -1:
    print("ERROR: Overview anchor not found")
    exit(1)

# Insert market reality block before the Overview header, and remove the duplicate header
# (the block ends with its own Overview header so we replace the existing one)
new_content = content[:anchor_idx] + MARKET_REALITY_BLOCK + content[anchor_idx + len(ANCHOR):]

with open('streamlit_app.py', 'w') as f:
    f.write(new_content)

print("✅ Market Reality block added — test with: streamlit run streamlit_app.py")
