"""
Run from repo root:
    python3 patch_role_explorer.py
"""

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

NEW_PAGE = r'''# ══════════════════════════════════════════════════════════════════════════════
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
                (SELECT AVG(salary_max_annual) FROM job_postings
                 WHERE data_tier=1 AND status='raw' AND salary_max_annual BETWEEN 50000 AND 500000))
                /
                NULLIF((SELECT AVG(salary_max_annual) FROM job_postings
                 WHERE data_tier=1 AND status='raw' AND salary_max_annual BETWEEN 50000 AND 500000), 0)
                * 100, 1) as premium_pct
        FROM job_postings jp
        JOIN roles r ON r.role_id = jp.role_id
        JOIN job_skills js ON js.job_id = jp.job_id
        JOIN skills s ON s.skill_id = js.skill_id
        WHERE jp.data_tier=1 AND jp.status='raw'
          AND s.difficulty_relevant = true
          AND ({ROLE_FAMILY_SQL}) = %s
        GROUP BY s.skill_name
        HAVING COUNT(DISTINCT jp.job_id) >= 5
        ORDER BY jobs DESC
        LIMIT 20
    """, params=(selected_family,))

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

'''

FOOTER = """# ── Footer ────────────────────────────────────────────────────────────────────"""

with open('streamlit_app.py', 'r') as f:
    content = f.read()

# Update sidebar nav to include Role Explorer
old_nav = '["📊 Overview", "🏢 Company Scorecard", "🔧 Skill Premiums", "🏭 Sector Dashboard"]'
new_nav = '["📊 Overview", "🏢 Company Scorecard", "🔧 Skill Premiums", "🏭 Sector Dashboard", "🎯 Role Explorer"]'

if old_nav in content:
    content = content.replace(old_nav, new_nav)
    print("✅ Nav updated")
else:
    print("⚠️  Nav not found — check manually")

# Insert Role Explorer page before footer
footer_idx = content.find(FOOTER)
if footer_idx == -1:
    print("ERROR: footer marker not found")
else:
    content = content[:footer_idx] + NEW_PAGE + '\n\n' + content[footer_idx:]
    print("✅ Role Explorer page inserted")

with open('streamlit_app.py', 'w') as f:
    f.write(content)

print("✅ Done — test with: streamlit run streamlit_app.py")
