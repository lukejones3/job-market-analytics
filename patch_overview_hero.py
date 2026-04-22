"""
Run from repo root:
    python3 patch_overview_hero.py

Updates:
1. Hero headline + subheader with live job/company counts
2. Replaces static subheader text with live query
3. Adds "Key Market Signals" block above the ghost job index
"""

with open('streamlit_app.py', 'r') as f:
    content = f.read()

# ── 1. Replace hero block ─────────────────────────────────────────────────────
old_hero = '''    st.markdown("""
    <div style="margin-bottom:40px">
        <div style="font-family:\'Syne\',sans-serif;font-size:2.5rem;font-weight:800;color:#e8e6e0;letter-spacing:-0.03em;line-height:1.1">
            Data & ML Hiring<br>Intelligence Platform
        </div>
        <div style="font-size:0.75rem;color:#555;margin-top:12px;max-width:500px">
            6,100+ enriched job postings across 1,000+ companies. Updated nightly from
            6 ATS sources. Proprietary scoring on salary transparency, hiring difficulty,
            and posting quality.
        </div>
    </div>
    """, unsafe_allow_html=True)'''

new_hero = '''    # Live counts for hero
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
        <div style="font-family:\'Syne\',sans-serif;font-size:2.5rem;font-weight:800;color:#e8e6e0;letter-spacing:-0.03em;line-height:1.1">
            Data & ML Hiring<br>Intelligence
        </div>
        <div style="font-size:0.75rem;color:#555;margin-top:12px;max-width:560px">
            Insights from {active_count} active job postings across {co_count} companies.
            Understand how compensation, role design, and transparency impact hiring outcomes.
            Updated nightly from 6 ATS sources.
        </div>
    </div>
    """, unsafe_allow_html=True)'''

if old_hero in content:
    content = content.replace(old_hero, new_hero, 1)
    print("✅ Hero block updated")
else:
    print("⚠️  Hero block not found — check manually")

# ── 2. Replace Ghost Job Index header + add Key Market Signals before it ──────
old_ghost_header = "    st.markdown(\"<div class='section-header'>Ghost Job Index — Active Postings</div>\", unsafe_allow_html=True)"

new_signals_block = '''    # ── Key Market Signals ────────────────────────────────────────────────────
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

    st.markdown("<div class='section-header'>Ghost Job Index — Active Postings</div>", unsafe_allow_html=True)'''

if old_ghost_header in content:
    content = content.replace(old_ghost_header, new_signals_block, 1)
    print("✅ Key Market Signals block added")
else:
    print("⚠️  Ghost header not found — check manually")

with open('streamlit_app.py', 'w') as f:
    f.write(content)

print("✅ Done — test with: streamlit run streamlit_app.py")
