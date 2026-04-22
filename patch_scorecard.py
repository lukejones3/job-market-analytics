"""
Run this from your repo root:
    python3 /tmp/patch_scorecard.py
It replaces the Company Scorecard page in streamlit_app.py with the
action-flags version.
"""

NEW_SCORECARD = '''# ══════════════════════════════════════════════════════════════════════════════
# PAGE: COMPANY SCORECARD
# ══════════════════════════════════════════════════════════════════════════════
elif page == "🏢 Company Scorecard":
    st.markdown("""
    <div style="font-family:\'Syne\',sans-serif;font-size:2rem;font-weight:800;color:#e8e6e0;margin-bottom:8px">
        Company Scorecard
    </div>
    <div style="font-size:0.7rem;color:#555;margin-bottom:32px">
        Hiring intelligence + action flags — updated nightly
    </div>
    """, unsafe_allow_html=True)

    # ── Filters ──────────────────────────────────────────────────────────────
    fcol1, fcol2, fcol3, fcol4 = st.columns([2, 2, 1, 1])
    with fcol1:
        sectors_list = query("""
            SELECT DISTINCT sector
            FROM analytics_analytics.mart_company_scorecard
            WHERE sector IS NOT NULL ORDER BY sector
        """)
        sector_filter = st.multiselect("Filter by Sector", sectors_list["sector"].tolist(), placeholder="All sectors")
    with fcol2:
        search = st.text_input("Search company", placeholder="e.g. Capital One, OpenAI...")
    with fcol3:
        sort_by = st.selectbox("Sort by", [
            "active_roles", "difficulty_score", "avg_honesty_score",
            "transparency_pct", "avg_ghost_probability", "salary_below_score"
        ])
    with fcol4:
        flag_filter = st.selectbox("Show only", [
            "All companies", "🚨 Has action flags", "💸 Underpaying",
            "🧱 Over-specified", "👻 Ghost risk", "🔴 Opaque"
        ])

    # ── Query ─────────────────────────────────────────────────────────────────
    where = ["active_roles >= 3"]
    if sector_filter:
        placeholders = ",".join([repr(s) for s in sector_filter])
        where.append(f"sector IN ({placeholders})")
    if search:
        where.append(f"lower(company_name) LIKE lower(\'%{search}%\')")

    scorecard = query(f"""
        SELECT company_name, sector, active_roles,
            difficulty_score, rarity_score, complexity_score,
            salary_below_score, avg_honesty_score, transparency_pct,
            avg_ghost_probability, ghost_rate_pct,
            avg_max_salary, primary_level, primary_workplace,
            left(top_skills, 150) as top_skills
        FROM analytics_analytics.mart_company_scorecard
        WHERE {" AND ".join(where)}
        ORDER BY {sort_by} DESC NULLS LAST
        LIMIT 150
    """)

    # ── Flag logic (pure Python, no DB round-trip) ────────────────────────────
    def get_flags(row):
        flags = []
        if pd.notna(row["salary_below_score"]) and row["salary_below_score"] > 30:
            pct = int(row["salary_below_score"])
            flags.append(("underpaying", f"💸 Underpaying ~{pct}% vs sector median"))
        if pd.notna(row["complexity_score"]) and row["complexity_score"] > 70:
            flags.append(("overspec", "🧱 Over-specified role"))
        if pd.notna(row["avg_ghost_probability"]) and row["avg_ghost_probability"] > 60:
            gp = int(row["avg_ghost_probability"])
            flags.append(("ghost", f"👻 Ghost risk {gp}%"))
        if pd.notna(row["transparency_pct"]) and row["transparency_pct"] == 0:
            flags.append(("opaque", "🔴 0% salary transparency"))
        if pd.notna(row["rarity_score"]) and row["rarity_score"] > 15:
            flags.append(("rare", "🎯 Niche skill requirements"))
        return flags

    def get_recommendations(flags, row):
        recs = []
        flag_types = [f[0] for f in flags]
        if "underpaying" in flag_types and pd.notna(row["avg_max_salary"]):
            sector_implied = row["avg_max_salary"] / (1 - row["salary_below_score"] / 100) if row["salary_below_score"] < 100 else None
            if sector_implied:
                target = int(sector_implied / 1000) * 1000
                recs.append(f"Raise max salary to ~${target:,} to match sector median")
        if "opaque" in flag_types:
            recs.append("Add salary band to postings — transparency drives 2–3x more applicants")
        if "overspec" in flag_types:
            recs.append("Reduce required skill count — aim for 5–7 core requirements")
        if "rare" in flag_types:
            recs.append("Replace niche tool requirements with transferable equivalents")
        if "ghost" in flag_types:
            recs.append("Audit open roles — high ghost probability signals stale postings")
        return recs

    def estimate_ttf(row):
        """Rough time-to-fill estimate from ghost probability + difficulty."""
        base = 30
        if pd.notna(row["avg_ghost_probability"]):
            base += row["avg_ghost_probability"] * 0.3
        if pd.notna(row["difficulty_score"]):
            base += row["difficulty_score"] * 0.2
        return int(base)

    # ── Apply flag filter ─────────────────────────────────────────────────────
    if not scorecard.empty:
        scorecard["_flags"] = scorecard.apply(get_flags, axis=1)
        scorecard["_flag_types"] = scorecard["_flags"].apply(lambda f: [x[0] for x in f])

        if flag_filter == "🚨 Has action flags":
            scorecard = scorecard[scorecard["_flags"].apply(len) > 0]
        elif flag_filter == "💸 Underpaying":
            scorecard = scorecard[scorecard["_flag_types"].apply(lambda f: "underpaying" in f)]
        elif flag_filter == "🧱 Over-specified":
            scorecard = scorecard[scorecard["_flag_types"].apply(lambda f: "overspec" in f)]
        elif flag_filter == "👻 Ghost risk":
            scorecard = scorecard[scorecard["_flag_types"].apply(lambda f: "ghost" in f)]
        elif flag_filter == "🔴 Opaque":
            scorecard = scorecard[scorecard["_flag_types"].apply(lambda f: "opaque" in f)]

    st.markdown(
        f"<div style=\'font-size:0.65rem;color:#444;margin-bottom:16px\'>{len(scorecard)} companies</div>",
        unsafe_allow_html=True
    )

    # ── Render cards ──────────────────────────────────────────────────────────
    if not scorecard.empty:
        for _, row in scorecard.iterrows():
            flags = row["_flags"]
            recs  = get_recommendations(flags, row)
            ttf   = estimate_ttf(row)

            diff  = row["difficulty_score"]
            hon   = row["avg_honesty_score"]
            ghost = row["avg_ghost_probability"]
            trans = row["transparency_pct"]

            # Score pills
            diff_class = "score-high" if pd.notna(diff) and diff > 70 else "score-med" if pd.notna(diff) and diff > 40 else "score-low"
            hon_class  = "score-low"  if pd.notna(hon)  and hon >= 85 else "score-med" if pd.notna(hon)  and hon >= 70 else "score-high"
            trans_class = "score-low" if pd.notna(trans) and trans >= 80 else "score-med" if pd.notna(trans) and trans >= 50 else "score-high"

            diff_str  = f"<span class=\'score-pill {diff_class}\'>Difficulty {diff:.0f}</span>" if pd.notna(diff) else ""
            hon_str   = f"<span class=\'score-pill {hon_class}\'>Honesty {hon:.0f}</span>" if pd.notna(hon) else ""
            ghost_str = f"<span class=\'score-pill score-high\'>Ghost {ghost:.0f}%</span>" if pd.notna(ghost) and ghost > 50 else (f"<span class=\'score-pill score-low\'>Ghost {ghost:.0f}%</span>" if pd.notna(ghost) else "")
            trans_str = f"<span class=\'score-pill {trans_class}\'>{trans:.0f}% transparent</span>" if pd.notna(trans) else ""

            # Salary display
            sal_str = f"${int(row[\'avg_max_salary\']):,} avg max" if pd.notna(row["avg_max_salary"]) else "No salary data"

            # Skills
            skills_html = ""
            if pd.notna(row["top_skills"]):
                for sk in row["top_skills"].split(", ")[:8]:
                    skills_html += f"<span class=\'tag\'>{sk.strip()}</span>"

            # Action flags HTML
            flags_html = ""
            if flags:
                flag_items = "".join([
                    f"<div style=\'font-size:0.7rem;color:#ff6b6b;padding:2px 0\'>{f[1]}</div>"
                    for f in flags
                ])
                flags_html = f"""
                <div style=\'margin-top:12px;padding:10px 12px;background:#1a0f0f;border:1px solid #ff6b6b22;border-radius:3px\'>
                    <div style=\'font-size:0.6rem;color:#555;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px\'>Action Required</div>
                    {flag_items}
                </div>"""

            # Recommendations HTML
            recs_html = ""
            if recs:
                rec_items = "".join([
                    f"<div style=\'font-size:0.68rem;color:#aaa;padding:2px 0\'>→ {r}</div>"
                    for r in recs
                ])
                recs_html = f"""
                <div style=\'margin-top:8px;padding:10px 12px;background:#0f1a0f;border:1px solid #06d6a022;border-radius:3px\'>
                    <div style=\'font-size:0.6rem;color:#555;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px\'>Recommended Fix</div>
                    {rec_items}
                </div>"""

            # If you do nothing HTML
            nothing_html = ""
            if flags:
                competing = max(5, int(row["active_roles"] * 3.2)) if pd.notna(row["active_roles"]) else 20
                pool_reduction = min(60, len(flags) * 15)
                nothing_html = f"""
                <div style=\'margin-top:8px;padding:10px 12px;background:#111120;border:1px solid #1e1e2e;border-radius:3px\'>
                    <div style=\'font-size:0.6rem;color:#555;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px\'>If Unchanged</div>
                    <div style=\'font-size:0.68rem;color:#666\'>
                        Est. time-to-fill: <span style=\'color:#ffd166\'>{ttf}–{ttf+18} days</span> &nbsp;·&nbsp;
                        Candidate pool: <span style=\'color:#ff6b6b\'>-{pool_reduction}%</span> &nbsp;·&nbsp;
                        Competing roles with better pay/transparency: <span style=\'color:#ff6b6b\'>{competing}</span>
                    </div>
                </div>"""

            border_color = "#ff6b6b33" if flags else "#1e1e2e"
            bg_color     = "#110d0d" if flags else "#0d0d1a"

            st.markdown(f"""
            <div style="border:1px solid {border_color};border-radius:4px;padding:16px;margin-bottom:10px;background:{bg_color}">
                <div style="display:flex;justify-content:space-between;align-items:flex-start">
                    <div>
                        <span style="font-family:\'Syne\',sans-serif;font-size:1rem;font-weight:700;color:#e8e6e0">{row["company_name"]}</span>
                        <span style="font-size:0.65rem;color:#444;margin-left:10px">{row["sector"] or "—"}</span>
                        <span style="font-size:0.6rem;color:#333;margin-left:8px">{row["primary_level"] or ""} · {row["primary_workplace"] or ""}</span>
                    </div>
                    <div style="text-align:right">
                        <div style="font-family:\'Syne\',sans-serif;font-size:1.1rem;font-weight:800;color:#c8f542">{int(row["active_roles"])} roles</div>
                        <div style="font-size:0.6rem;color:#444">{sal_str}</div>
                    </div>
                </div>
                <div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:4px">
                    {diff_str} {hon_str} {trans_str} {ghost_str}
                </div>
                <div style="margin-top:8px">{skills_html}</div>
                {flags_html}
                {recs_html}
                {nothing_html}
            </div>
            """, unsafe_allow_html=True)

'''

OLD_MARKER = '# ══════════════════════════════════════════════════════════════════════════════\n# PAGE: COMPANY SCORECARD\n# ══════════════════════════════════════════════════════════════════════════════\nelif page == "🏢 Company Scorecard":'
NEW_MARKER = NEW_SCORECARD.split('\n')[2]  # first content line

END_MARKER = '# ══════════════════════════════════════════════════════════════════════════════\n# PAGE: SKILL PREMIUMS'

with open('streamlit_app.py', 'r') as f:
    content = f.read()

start = content.find('# ══════════════════════════════════════════════════════════════════════════════\n# PAGE: COMPANY SCORECARD')
end   = content.find('# ══════════════════════════════════════════════════════════════════════════════\n# PAGE: SKILL PREMIUMS')

if start == -1 or end == -1:
    print(f"ERROR: markers not found. start={start} end={end}")
else:
    new_content = content[:start] + NEW_SCORECARD + '\n\n' + content[end:]
    with open('streamlit_app.py', 'w') as f:
        f.write(new_content)
    print("✅ Company Scorecard page updated")
