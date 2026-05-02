import streamlit as st
# RECRUITER_RESUME_PATCH_v1 + RECRUITER_RESUME_HOTFIX_v1
import sys, os
# Resolve python/ dir relative to this file — works on both droplet and Streamlit Cloud
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_RESUME_PATH = os.path.join(_THIS_DIR, "python")
if _RESUME_PATH not in sys.path:
    sys.path.insert(0, _RESUME_PATH)
# Fallback for droplet location
_DROPLET_PATH = "/opt/job-market-analytics/python"
if os.path.isdir(_DROPLET_PATH) and _DROPLET_PATH not in sys.path:
    sys.path.insert(0, _DROPLET_PATH)
try:
    from resume import parse_resume, extract_skills, infer_experience_level
    _RESUME_AVAILABLE = True
except Exception as _e:
    _RESUME_AVAILABLE = False
    _RESUME_IMPORT_ERR = str(_e)
import psycopg2
import pandas as pd
import os
from datetime import datetime, timedelta

st.set_page_config(
    page_title="DataHiringIQ · Data & ML Job Search",
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

/* BUTTON_TEXT_CONTRAST_v1: force dark text on primary button (lime is too light for white) */
.stButton > button[kind="primary"] {
    color: #080810 !important;
    font-weight: 600 !important;
    border: none !important;
}
.stButton > button[kind="primary"]:hover {
    color: #080810 !important;
    background-color: #c9e84f !important;
}

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

def verify_token(token: str):
    """Check token against api_keys table. Returns client info or None.
    Rejects expired tokens (past expires_at)."""
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT client_name, client_email, tier, active, expires_at
            FROM api_keys
            WHERE api_key_prefix = %s
              AND active = true
              AND (expires_at IS NULL OR expires_at > NOW())
            LIMIT 1
        """, (token[:8] if len(token) >= 8 else token,))
        row = cur.fetchone()
        conn.close()
        if row:
            return {"name": row[0], "email": row[1], "tier": row[2], "expires_at": row[4]}
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
# FREEMIUM_STATE_v1: three-state freemium detection
# Replaces the old all-or-nothing paywall.
_tier = (client.get("tier") if client else None) or ("pro" if is_preview else None)
is_paid       = (_tier == "pro")
is_free       = (_tier == "free")
is_anonymous  = (not is_paid and not is_free)
# Helpful flags downstream:
#   is_paid       — full feed, all features unlocked
#   is_free       — 500-job feed, blurred premium features, sticky upgrade banner
#   is_anonymous  — 25-job preview, signup CTA front and center

# UPGRADE_BANNER_v1: free-tier upgrade banner + floating CTA
if is_free:
    import urllib.parse as _urlparse
    _free_email = (client.get("email") if client else "") or ""
    _stripe_url = "https://buy.stripe.com/3cI4gs9hugN14obehifnO02"
    if _free_email:
        _stripe_url = f"{_stripe_url}?prefilled_email={_urlparse.quote(_free_email)}"

    # Banner at top of dashboard
    st.markdown(f"""
    <div style="background:linear-gradient(90deg,#0f0f1a 0%,#1a1a2e 100%);
        border:1px solid #2a2a4a;border-left:3px solid #e2ff5d;
        border-radius:4px;padding:14px 20px;margin-bottom:20px;
        display:flex;justify-content:space-between;align-items:center;
        flex-wrap:wrap;gap:12px">
        <div style="font-size:0.85rem;color:#d4d4d8;flex:1;min-width:280px">
            <span style="color:#e2ff5d;font-weight:600">You're on the free plan</span>
            <span style="color:#666;margin:0 8px">·</span>
            <span style="color:#aaa">Upgrade to Pro for $19/mo to unlock the full 2,000-job feed, hiring manager LinkedIn, and full resume match top-50.</span>
        </div>
        <a href="{_stripe_url}" target="_blank"
           style="background:#e2ff5d;color:#080810;font-family:'IBM Plex Mono',monospace;
               font-size:0.7rem;font-weight:600;padding:8px 18px;border-radius:3px;
               text-decoration:none;letter-spacing:0.05em;text-transform:uppercase;
               white-space:nowrap;flex-shrink:0">
            Upgrade $19/mo →
        </a>
    </div>
    """, unsafe_allow_html=True)

    # Floating CTA bottom-right (always visible while scrolling)
    st.markdown(f"""
    <a href="{_stripe_url}" target="_blank"
       style="position:fixed;bottom:24px;right:24px;z-index:9999;
           background:#e2ff5d;color:#080810;font-family:'IBM Plex Mono',monospace;
           font-size:0.72rem;font-weight:600;padding:10px 18px;border-radius:24px;
           text-decoration:none;letter-spacing:0.05em;text-transform:uppercase;
           box-shadow:0 4px 16px rgba(226,255,93,0.25);
           border:1px solid #c9e84f;
           transition:transform 0.15s,box-shadow 0.15s">
        💎 Upgrade
    </a>
    <style>
    a[href="{_stripe_url}"]:hover {{
        transform: translateY(-1px);
        box-shadow: 0 6px 20px rgba(226,255,93,0.4);
    }}
    </style>
    """, unsafe_allow_html=True)


# FREEMIUM_LANDING_v1: anonymous landing hero + email signup form
if is_anonymous:
    import requests as _requests

    # Initialize session state for signup feedback
    if "_signup_status" not in st.session_state:
        st.session_state._signup_status = None  # None | "ok" | "err"
    if "_signup_msg" not in st.session_state:
        st.session_state._signup_msg = ""

    # Hero / landing
    st.markdown("""
    <div style="text-align:center;padding:60px 20px 20px 20px">
        <div style="font-family:'Bebas Neue',sans-serif;font-size:4rem;color:#e2ff5d;
            letter-spacing:0.05em;line-height:1;margin-bottom:8px">
            DATAHIRINGIQ
        </div>
        <div style="font-size:0.7rem;color:#666;text-transform:uppercase;
            letter-spacing:0.2em;margin-bottom:32px">
            Fresh Data &amp; ML Jobs · Updated Nightly
        </div>
        <p style="color:#888;font-size:1rem;line-height:1.7;max-width:580px;
            margin:0 auto 40px auto">
            Stop wasting applications on ghost jobs. We track 1,000+ companies across
            6 ATS sources, score each posting for honesty, and map the hiring manager's
            LinkedIn so you can reach out directly.
        </p>
    </div>
    """, unsafe_allow_html=True)

    # Signup form — centered
    _form_col_l, _form_col_c, _form_col_r = st.columns([1, 2, 1])
    with _form_col_c:
        # ANON_LANDING_POLISH_v1: value-prop header replaces "Get Free Access" label
        st.markdown("""
        <div style="background:#0f0f1a;border:1px solid #1e1e32;border-radius:6px;
            padding:24px;margin-bottom:8px">
            <div style="font-size:1.05rem;color:#e2ff5d;font-weight:600;
                margin-bottom:6px;text-align:center;letter-spacing:0.02em">
                Try it free — get 500 fresh roles
            </div>
            <div style="font-size:0.72rem;color:#888;text-align:center;
                margin-bottom:6px;letter-spacing:0.05em">
                No credit card · Updated nightly · Drop your email below
            </div>
        </div>
        """, unsafe_allow_html=True)

        _email = st.text_input(
            "Email",
            placeholder="you@example.com",
            label_visibility="collapsed",
            key="_signup_email",
        )

        _btn_clicked = st.button(
            "Send me my access link →",
            type="primary",
            use_container_width=True,
            key="_signup_btn",
        )

        if _btn_clicked:
            _e = (_email or "").strip().lower()
            if "@" not in _e or "." not in _e.split("@")[-1]:
                st.session_state._signup_status = "err"
                st.session_state._signup_msg = "Please enter a valid email address."
            else:
                try:
                    _r = _requests.post(
                        "https://api.datahiringiq.com/auth/free-signup",
                        json={"email": _e},
                        timeout=8,
                    )
                    if _r.status_code == 200:
                        st.session_state._signup_status = "ok"
                        st.session_state._signup_msg = (
                            "Check your email — your access link is on its way. "
                            "It may take a minute to arrive."
                        )
                    elif _r.status_code == 400:
                        st.session_state._signup_status = "err"
                        try:
                            st.session_state._signup_msg = _r.json().get("detail", "Invalid request.")
                        except Exception:
                            st.session_state._signup_msg = "Invalid request."
                    else:
                        st.session_state._signup_status = "err"
                        st.session_state._signup_msg = "Something went wrong. Please try again."
                except Exception as _ex:
                    st.session_state._signup_status = "err"
                    st.session_state._signup_msg = f"Connection error. Please try again."

        if st.session_state._signup_status == "ok":
            st.success(st.session_state._signup_msg)
        elif st.session_state._signup_status == "err":
            st.error(st.session_state._signup_msg)

        st.markdown("""
        <div style="font-size:0.7rem;color:#555;text-align:center;margin-top:16px;line-height:1.6">
            Free tier includes:<br>
            500 fresh jobs daily · Basic filters · Traffic-light job signals<br><br>
            <span style="color:#666">Or upgrade to Pro for $19/mo: full 2,000-job feed, hiring manager LinkedIn, full resume match top-50.</span>
        </div>
        """, unsafe_allow_html=True)

    # Below the form: small "preview the feed" header
    st.markdown("""
    <div style="text-align:center;margin:40px 0 20px 0;padding-top:30px;
        border-top:1px solid #131320">
        <div style="font-size:0.65rem;color:#444;text-transform:uppercase;
            letter-spacing:0.2em">
            Preview · 25 random jobs from today's feed
        </div>
    </div>
    """, unsafe_allow_html=True)


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
            Data & ML Job Search · Fresh Nightly
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

# ── PERSONALIZE WITH RESUME ────────────────────────────────────────────────────
# RECRUITER_RESUME_PATCH_v1
if "resume_skills" not in st.session_state:
    st.session_state.resume_skills = None
    st.session_state.resume_skills_names = set()
    st.session_state.resume_exp = "mid"
    st.session_state.resume_skill_count = 0
    st.session_state.resume_filename = None

with st.expander("📄 Personalize with your resume (optional)", expanded=bool(st.session_state.resume_skills)):
    if not _RESUME_AVAILABLE:
        st.error(f"Resume module unavailable: {_RESUME_IMPORT_ERR}")
    else:
        rcol1, rcol2, rcol3 = st.columns([2, 1, 1])
        with rcol1:
            uploaded = st.file_uploader(
                "Upload .pdf / .docx / .txt",
                type=["pdf", "docx", "txt"],
                key="resume_upload",
                label_visibility="visible",
            )
            if uploaded is not None and uploaded.name != st.session_state.resume_filename:
                try:
                    file_bytes = uploaded.read()
                    text_content = parse_resume(file_bytes, uploaded.name)
                    # RECRUITER_RESUME_HOTFIX_v2: use existing get_conn() helper
                    from psycopg2.extras import RealDictCursor
                    _conn = get_conn()
                    _cur = _conn.cursor(cursor_factory=RealDictCursor)
                    skills = extract_skills(text_content, _cur)
                    inferred_exp = infer_experience_level(text_content)
                    _cur.close()
                    _conn.close()

                    st.session_state.resume_skills = skills
                    st.session_state.resume_skills_names = {
                        v["name"].lower() for v in skills.values()
                    }
                    st.session_state.resume_exp = inferred_exp
                    st.session_state.resume_exp_parsed = inferred_exp  # RECRUITER_RESUME_HOTFIX_v3
                    if "resume_exp_choice" in st.session_state:
                        del st.session_state["resume_exp_choice"]  # reset override on new upload
                    st.session_state.resume_skill_count = len(skills)
                    st.session_state.resume_filename = uploaded.name
                    st.success(f"Parsed {uploaded.name}: {len(skills)} skills, level={inferred_exp}")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to parse resume: {e}")

        with rcol2:
            # RECRUITER_RESUME_HOTFIX_v3: auto-detected default, explicit override
            level_options = ["entry", "associate", "mid", "senior"]
            # Track parser's last-inferred value separately from override
            parser_exp = st.session_state.get("resume_exp_parsed", st.session_state.resume_exp)
            override_options = [f"Auto-detected ({parser_exp})"] + level_options
            # Default to "Auto-detected" unless user explicitly picked something
            new_choice = st.selectbox("Your level (auto-detected, override if needed)", override_options, index=0, key="resume_exp_choice")
            if new_choice.startswith("Auto-detected"):
                st.session_state.resume_exp = parser_exp
            else:
                st.session_state.resume_exp = new_choice
        with rcol3:
            salary_floor = st.number_input(
                "Salary floor ($, optional)",
                min_value=0, max_value=500000, value=0, step=5000,
                key="resume_salary_floor",
            )
            if st.button("Clear resume", key="resume_clear"):
                st.session_state.resume_skills = None
                st.session_state.resume_skills_names = set()
                st.session_state.resume_exp = "mid"
                st.session_state.resume_exp_parsed = "mid"  # RECRUITER_RESUME_HOTFIX_v3
                st.session_state.resume_skill_count = 0
                st.session_state.resume_filename = None
                if "resume_exp_choice" in st.session_state:
                    del st.session_state["resume_exp_choice"]
                st.rerun()

        if st.session_state.resume_skills:
            skill_chips = " ".join(
                f"<span style='display:inline-block;background:#1a1a2e;color:#e2ff5d;font-size:0.7rem;padding:3px 9px;border-radius:2px;margin:2px;font-family:IBM Plex Mono,monospace'>{v['name']}</span>"
                for v in sorted(st.session_state.resume_skills.values(), key=lambda x: -x["confidence"])
            )
            st.markdown(
                f"<div style='margin-top:8px'><span style='font-size:0.7rem;color:#666;text-transform:uppercase;letter-spacing:0.1em'>Extracted skills:</span><br>{skill_chips}</div>",
                unsafe_allow_html=True,
            )

# ── FILTERS ───────────────────────────────────────────────────────────────────
# REMOVE_SECTOR_FILTER_v1: dropped Sector multiselect (recruiter-side concern, not job-seeker)
sector_filter = []  # kept as empty list so downstream WHERE-clause logic remains a no-op

f1, f2, f3 = st.columns([2, 1, 1])

with f1:
    role_types = ["Data Engineer", "Data Scientist", "ML Engineer", "AI Engineer",
                  "Data Analyst", "Analytics Engineer", "Leadership", "Revenue/Ops"]
    role_filter = st.multiselect("Role Type", role_types, placeholder="All roles", key="rf_role")

with f2:
    workplace_filter = st.selectbox("Workplace", ["All", "Remote", "Hybrid", "Onsite"], key="rf_workplace")

with f3:
    days_filter = st.selectbox("Posted within", ["3 days", "24 hours", "7 days", "15 days", "30 days"], key="rf_days")

f5, f6, f7, f8 = st.columns([2, 1, 1, 1])

with f5:
    # RECRUITER_RESUME_PATCH_v1: pre-populate from resume if loaded
    _default_skills = ""
    if st.session_state.get("resume_skills"):
        _default_skills = ", ".join(
            v["name"] for v in sorted(
                st.session_state.resume_skills.values(),
                key=lambda x: -x["confidence"],
            )
        )
    skills_input = st.text_input(
        "Your skills (comma-separated)",
        value=_default_skills,
        placeholder="e.g. Python, SQL, PyTorch",
        key="rf_skills",
    )

with f6:
    exp_filter = st.multiselect("Experience", ["Entry", "Associate", "Mid", "Senior"], placeholder="All levels", key="rf_exp")

with f7:
    # RECRUITER_RESUME_PATCH_v1: add Best Match option
    _sort_options = ["Date", "Salary (high)", "Signal", "Skills match"]
    if st.session_state.get("resume_skills"):
        _sort_options = ["Best Match"] + _sort_options
    sort_by = st.selectbox("Sort by", _sort_options, key="rf_sort")

with f8:
    signal_filter = st.selectbox("Min Signal", ["All", "⚡ Fresh", "● Strong", "● Moderate"], key="rf_signal")

# RECRUITER_LOC_PATCH_v1: state + city filters
f9, f10, f11, f12 = st.columns([2, 2, 1, 1])
with f9:
    state_filter = st.multiselect("State", [
        "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
        "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
        "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
        "VA","WA","WV","WI","WY","DC"
    ], placeholder="All states", key="rf_state")
with f10:
    city_filter = st.text_input("City contains", placeholder="e.g. New York, Austin", key="rf_city")
with f11:
    pass
with f12:
    pass
    salary_only = st.checkbox("Salary only", key="rf_salary")

days_map = {"24 hours": 1, "3 days": 3, "7 days": 7, "15 days": 15, "30 days": 30}
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

# REMOVE_STALE_WORKDAY_FILTERS_v1: dropped two stale defensive filters
# - workday-must-have-posted-date (195 legit jobs were being hidden)
# - same-day workday < 300 cap (Apr 19 had 948 jobs all filtered)
where_clauses = [
    "jp.data_tier = 1",
    "jp.status = 'raw'",
    "(jp.role_category IS NULL OR jp.role_category != 'non_data')",
    # LOC_COUNTRY_FILTER_v1: drop misclassified foreign + junk strings
    "(jp.loc_country = 'US' OR (jp.loc_country = 'unknown' AND jp.loc_city IS NULL))",
    f"(CASE WHEN jp.posted_date IS NOT NULL THEN jp.posted_date::timestamp ELSE jp.date_found END) > NOW() - INTERVAL '{days_back} days'",
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
        jp.job_url,
        cc.full_name as contact_name,
        cc.title as contact_title,
        cc.email as contact_email,
        cc.linkedin_url as contact_linkedin,
        jp.loc_city,
        jp.loc_state,
        jp.loc_country,
        jh.honesty_score,
        gi.ghost_probability,
        EXTRACT(EPOCH FROM (NOW() - CASE WHEN jp.posted_date IS NOT NULL THEN jp.posted_date::timestamp ELSE jp.date_found END))/3600 as hours_old,
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
    -- RECRUITER_LOC_PATCH_v1: dropped LEFT JOIN locations (use jp.loc_* instead)
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
             jp.workplace_type, jp.experience_level, jp.job_url, jh.honesty_score,
             jp.loc_city, jp.loc_state, jp.loc_country,
             cc.full_name, cc.title, cc.email, cc.linkedin_url,
             gi.ghost_probability, ch.employee_count
    ORDER BY (CASE WHEN jp.posted_date IS NOT NULL
                THEN jp.posted_date::timestamp
                ELSE jp.date_found::timestamp END) DESC
    LIMIT 2000
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

    # Apply workplace filter
    if workplace_filter != "All":
        wp_map = {"Remote": "remote", "Hybrid": "hybrid", "Onsite": "onsite"}
        fresh_jobs = fresh_jobs[fresh_jobs["workplace_type"] == wp_map[workplace_filter]]

    # Experience filter
    if exp_filter:
        exp_map = {"Entry": "entry", "Associate": "associate", "Mid": "mid", "Senior": "senior"}
        selected_levels = [exp_map[e] for e in exp_filter]
        fresh_jobs = fresh_jobs[fresh_jobs["experience_level"].isin(selected_levels)]

    # RECRUITER_LOC_PATCH_v1: state + city filters
    if state_filter:
        fresh_jobs = fresh_jobs[fresh_jobs["loc_state"].isin(state_filter)]
    if city_filter:
        city_lower = city_filter.strip().lower()
        fresh_jobs = fresh_jobs[
            fresh_jobs["loc_city"].fillna("").str.lower().str.contains(city_lower, na=False)
        ]

    # Compute skills match score
    user_skills = [s.strip().lower() for s in skills_input.split(",") if s.strip()] if skills_input else []
    if user_skills:
        def _match_score(skills_str):
            if not skills_str or pd.isna(skills_str):
                return 0
            job_skills = [s.strip().lower() for s in str(skills_str).split(",")]
            matches = sum(1 for us in user_skills if any(us in js or js in us for js in job_skills))
            return int((matches / max(len(user_skills), 1)) * 100)
        fresh_jobs["skills_match"] = fresh_jobs["skills"].apply(_match_score)
    else:
        fresh_jobs["skills_match"] = 0

    # RECRUITER_RESUME_PATCH_v1: full match_score using resume + exp + salary + freshness
    if st.session_state.get("resume_skills"):
        _resume_skill_names = st.session_state.resume_skills_names
        _resume_exp = st.session_state.resume_exp
        _salary_floor = st.session_state.get("resume_salary_floor", 0) or 0
        _exp_order = {"entry": 0, "associate": 1, "mid": 2, "senior": 3}

        def _exp_fit(row_exp):
            if not row_exp or row_exp not in _exp_order:
                return 0.7
            if _resume_exp not in _exp_order:
                return 0.5
            d = abs(_exp_order[row_exp] - _exp_order[_resume_exp])
            if d == 0: return 1.0
            if d == 1: return 0.5
            return 0.0

        def _salary_fit(smin, smax):
            if _salary_floor <= 0:
                return 1.0
            high = smax if pd.notna(smax) and smax else (smin if pd.notna(smin) else None)
            if high is None:
                return 0.7
            if high >= _salary_floor:
                return 1.0
            if high >= _salary_floor * 0.9:
                return 0.5
            return 0.0

        def _full_match_score(row):
            skills_str = row.get("skills") or ""
            if pd.isna(skills_str) or not skills_str:
                job_skill_names = set()
            else:
                job_skill_names = {s.strip().lower() for s in str(skills_str).split(",") if s.strip()}
            n_job = len(job_skill_names)
            if n_job == 0:
                return 0
            overlap = _resume_skill_names & job_skill_names
            raw = len(overlap) / n_job
            if n_job == 1: raw = min(raw, 0.40)
            elif n_job == 2: raw = min(raw, 0.55)
            elif n_job == 3: raw = min(raw, 0.75)

            ef = _exp_fit(row.get("experience_level"))
            sf = _salary_fit(row.get("salary_min_annual"), row.get("salary_max_annual"))

            q = row.get("quality_score")
            if pd.notna(q) and q is not None:
                qn = float(q) / 100.0 if float(q) > 1.0 else float(q)
                qn = max(0.0, min(1.0, qn))
            else:
                qn = 0.5
            hours = float(row.get("hours_old") or 0)
            days_old = hours / 24.0
            if days_old <= 3: decay = 1.0
            elif days_old >= 30: decay = 0.5
            else: decay = 1.0 - (0.5 * (days_old - 3) / 27)
            fq = qn * decay

            score = 0.50 * raw + 0.20 * ef + 0.20 * sf + 0.10 * fq
            return int(round(score * 100))

        fresh_jobs["match_score"] = fresh_jobs.apply(_full_match_score, axis=1)

        # Compute matched/missing skills per row for the expander
        def _matched(skills_str):
            if not skills_str or pd.isna(skills_str): return ""
            job_set = {s.strip().lower() for s in str(skills_str).split(",") if s.strip()}
            return ", ".join(sorted(s.title() for s in (_resume_skill_names & job_set)))
        def _missing(skills_str):
            if not skills_str or pd.isna(skills_str): return ""
            job_set = {s.strip().lower() for s in str(skills_str).split(",") if s.strip()}
            return ", ".join(sorted(s.title() for s in (job_set - _resume_skill_names)))
        fresh_jobs["_matched_skills_str"] = fresh_jobs["skills"].apply(_matched)
        fresh_jobs["_missing_skills_str"] = fresh_jobs["skills"].apply(_missing)
    else:
        fresh_jobs["match_score"] = 0
        fresh_jobs["_matched_skills_str"] = ""
        fresh_jobs["_missing_skills_str"] = ""

    # Sort
    if sort_by == "Salary (high)":
        fresh_jobs = fresh_jobs.sort_values(by="salary_max_annual", ascending=False, na_position="last")
    elif sort_by == "Signal":
        signal_rank = {"fresh": 0, "strong": 1, "moderate": 2, "weak": 3}
        fresh_jobs["_sig_rank"] = fresh_jobs["signal_label"].map(lambda s: signal_rank.get(s, 99))
        fresh_jobs = fresh_jobs.sort_values(by=["_sig_rank", "urgency_score"], ascending=[True, False])
        fresh_jobs = fresh_jobs.drop(columns=["_sig_rank"])
    elif sort_by == "Skills match" and user_skills:
        fresh_jobs = fresh_jobs.sort_values(by="skills_match", ascending=False)
    elif sort_by == "Best Match" and st.session_state.get("resume_skills"):
        # RECRUITER_RESUME_PATCH_v1
        fresh_jobs = fresh_jobs.sort_values(by="match_score", ascending=False)

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
# GAPS_v2_PATCH: skill gaps query a wider slice (respects role_filter, ignores exp_filter)
if st.session_state.get("resume_skills"):
    from collections import Counter
    import statistics as _stats

    # Build SQL filter for the gap query: same as main feed, but ignore exp_filter
    # so users see aspirational skills at all levels
    _gap_where = [
        "jp.data_tier = 1",
        "jp.status = 'raw'",
        "(jp.role_category IS NULL OR jp.role_category != 'non_data')",
        "(jp.loc_country = 'US' OR (jp.loc_country = 'unknown' AND jp.loc_city IS NULL))",
    ]
    # Inherit role filter from existing UI (role_filter is the multiselect)
    if role_filter:
        _role_categories_for_gaps = []
        _role_map = {
            "Data Engineer": "data_engineering",
            "Data Scientist": "data_science",
            "ML Engineer": "ml_engineering",
            "AI Engineer": "ai_research",
            "Data Analyst": "data_analytics",
            "Analytics Engineer": "analytics_engineering",
        }
        _selected_categories = [_role_map[r] for r in role_filter if r in _role_map]
        if _selected_categories:
            _cats_str = ",".join([f"'{c}'" for c in _selected_categories])
            _gap_where.append(f"jp.role_category IN ({_cats_str})")

    _gap_sql = f'''
        SELECT
            jp.job_id,
            COALESCE(jp.salary_max_annual, jp.salary_min_annual) AS salary,
            STRING_AGG(DISTINCT s.skill_name, ', ' ORDER BY s.skill_name) AS skills
        FROM job_postings jp
        LEFT JOIN job_skills js ON js.job_id = jp.job_id
        LEFT JOIN skills s ON s.skill_id = js.skill_id
        WHERE {' AND '.join(_gap_where)}
        GROUP BY jp.job_id, jp.salary_min_annual, jp.salary_max_annual
        HAVING COUNT(DISTINCT js.skill_id) > 0
    '''
    _gap_jobs = query(_gap_sql)

    _resume_names = st.session_state.resume_skills_names
    _gap_counter = Counter()
    _gap_salaries = {}
    _strong_match_salaries = []
    for _, _row in _gap_jobs.iterrows():
        _job_skills_str = _row.get("skills") or ""
        if pd.isna(_job_skills_str) or not _job_skills_str:
            continue
        _job_set = {s.strip().lower() for s in str(_job_skills_str).split(",") if s.strip()}
        if not _job_set:
            continue
        _overlap = _resume_names & _job_set
        _overlap_pct = len(_overlap) / len(_job_set)
        _missing_in_job = _job_set - _resume_names
        _smax = _row.get("salary")
        if _overlap_pct >= 0.70 and _smax and pd.notna(_smax):
            _strong_match_salaries.append(float(_smax))
        for _msk in _missing_in_job:
            _other = _job_set - {_msk}
            if not _other: continue
            _other_pct = len(_resume_names & _other) / len(_other)
            if _other_pct >= 0.70:
                _gap_counter[_msk] += 1
                if _smax and pd.notna(_smax):
                    _gap_salaries.setdefault(_msk, []).append(float(_smax))

    if _gap_counter and _strong_match_salaries:
        _current_median = _stats.median(_strong_match_salaries)
        _all_gaps = []
        for _sk, _cnt in _gap_counter.most_common(30):
            if _cnt < 3: continue
            _sals = _gap_salaries.get(_sk, [])
            if not _sals: continue
            _delta = _stats.median(_sals) - _current_median
            _all_gaps.append((_sk, _cnt, _delta))

        # Split into positive (high-value) and negative (saturated) buckets
        _positive_gaps = sorted([g for g in _all_gaps if g[2] > 0], key=lambda t: (-t[2], -t[1]))[:5]
        _negative_gaps = sorted([g for g in _all_gaps if g[2] <= 0], key=lambda t: (-t[1], t[2]))[:3]

        def _fmt_delta(d):
            # Fix formatting: -$24,750 not $-24,750
            if d > 0:
                return f"+${int(d):,}"
            elif d < 0:
                return f"-${int(abs(d)):,}"
            else:
                return "$0"

        if _positive_gaps or _negative_gaps:
            _gap_html = "<div style='background:#0f0f1a;border:1px solid #1a1a2e;border-radius:4px;padding:14px 18px;margin:18px 0'>"

            if _positive_gaps:
                _gap_html += "<div style='font-size:0.65rem;color:#666;text-transform:uppercase;letter-spacing:0.15em;margin-bottom:8px'>💡 High-value skills to add</div>"
                _gap_html += "<div style='display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px'>"
                for _sk, _cnt, _delta in _positive_gaps:
                    _gap_html += "<div style='background:#1a1a2e;border-radius:2px;padding:6px 12px;font-family:IBM Plex Mono,monospace;font-size:0.7rem'>"
                    _gap_html += f"<span style='color:#e2ff5d'>{_sk.title()}</span> "
                    _gap_html += f"<span style='color:#888'>· {_cnt} jobs</span> "
                    _gap_html += f"<span style='color:#22c55e'>· {_fmt_delta(_delta)}</span>"
                    _gap_html += "</div>"
                _gap_html += "</div>"

            if _negative_gaps:
                _gap_html += "<div style='font-size:0.65rem;color:#666;text-transform:uppercase;letter-spacing:0.15em;margin-bottom:6px;margin-top:6px'>"
                _gap_html += "⚠ Required-but-saturated skills</div>"
                _gap_html += "<div style='font-size:0.65rem;color:#555;margin-bottom:8px;font-style:italic'>"
                _gap_html += "These skills appear in many jobs but median pay is below your current match band. They likely won't lift your salary, though they may broaden options.</div>"
                _gap_html += "<div style='display:flex;flex-wrap:wrap;gap:8px'>"
                for _sk, _cnt, _delta in _negative_gaps:
                    _gap_html += "<div style='background:#15151f;border:1px dashed #2a2a3a;border-radius:2px;padding:6px 12px;font-family:IBM Plex Mono,monospace;font-size:0.7rem'>"
                    _gap_html += f"<span style='color:#a8a8b8'>{_sk.title()}</span> "
                    _gap_html += f"<span style='color:#666'>· {_cnt} jobs</span> "
                    _gap_html += f"<span style='color:#71717a'>· {_fmt_delta(_delta)}</span>"
                    _gap_html += "</div>"
                _gap_html += "</div>"

            _gap_html += "</div>"

            # BLUR_SKILL_GAPS_v1 + ANON_LANDING_POLISH_v1: blur for free OR anonymous users
            if is_free or is_anonymous:
                import urllib.parse as _urlparse_g
                _free_email_g = (client.get("email") if client else "") or ""
                _stripe_url_g = "https://buy.stripe.com/3cI4gs9hugN14obehifnO02"
                if _free_email_g:
                    _stripe_url_g = f"{_stripe_url_g}?prefilled_email={_urlparse_g.quote(_free_email_g)}"
                _gap_html = (
                    '<div style="position:relative;margin:18px 0">'
                    '<div style="filter:blur(5px);user-select:none;pointer-events:none">'
                    + _gap_html +
                    '</div>'
                    '<div style="position:absolute;top:50%;left:50%;'
                    'transform:translate(-50%,-50%);text-align:center;'
                    'background:rgba(8,8,16,0.92);padding:18px 24px;border-radius:6px;'
                    'border:1px solid #e2ff5d33;min-width:280px">'
                    '<div style="font-size:1.4rem;margin-bottom:6px">🔒</div>'
                    '<div style="font-size:0.78rem;color:#e2ff5d;font-weight:600;'
                    'margin-bottom:4px;text-transform:uppercase;letter-spacing:0.05em">'
                    'Upgrade to see skill gaps'
                    '</div>'
                    '<div style="font-size:0.66rem;color:#888;margin-bottom:12px;line-height:1.5">'
                    'See which skills lift your salary band — and which ones won\'t.'
                    '</div>'
                    f'<a href="{_stripe_url_g}" target="_blank" '
                    'style="background:#e2ff5d;color:#080810;font-family:IBM Plex Mono,monospace;'
                    'font-size:0.68rem;font-weight:600;padding:6px 14px;border-radius:3px;'
                    'text-decoration:none;letter-spacing:0.05em;text-transform:uppercase;'
                    'display:inline-block">Upgrade $19/mo &rarr;</a>'
                    '</div>'
                    '</div>'
                )

            st.markdown(_gap_html, unsafe_allow_html=True)

# GAPS_v2_PATCH: empty state when no results
if fresh_jobs.empty:
    st.markdown('''
    <div style="text-align:center;padding:60px 20px;background:#0f0f1a;border:1px dashed #1a1a2e;border-radius:4px;margin:24px 0">
        <div style="font-size:2rem;color:#444;margin-bottom:12px">🔍</div>
        <div style="font-family:'Bebas Neue',sans-serif;font-size:1.4rem;color:#888;letter-spacing:0.05em;margin-bottom:8px">No matches found</div>
        <div style="font-size:0.8rem;color:#666;line-height:1.6;max-width:500px;margin:0 auto">
            Try loosening your filters: extend the date range, remove sector/role restrictions, or lower your salary floor. Check the active filters above.
        </div>
    </div>
    ''', unsafe_allow_html=True)
# FEED_CAP_FREE_v1: free tier gets 500 freshest jobs (paid gets full 2000)
if is_free and len(fresh_jobs) > 500:
    fresh_jobs = fresh_jobs.head(500).reset_index(drop=True)

# FREEMIUM_LANDING_v1: anonymous gets 25 random jobs only
if is_anonymous and len(fresh_jobs) > 25:
    fresh_jobs = fresh_jobs.sample(n=25, random_state=None).reset_index(drop=True)

st.markdown(f"<div class='section-divider'>{len(fresh_jobs):,} roles — sorted by most recent</div>",
            unsafe_allow_html=True)

if fresh_jobs.empty:
    st.markdown("<div style='color:#444;font-size:0.8rem;padding:40px;text-align:center'>No roles match current filters.</div>",
                unsafe_allow_html=True)
else:
    # BLUR_MATCH_v1: free-tier users see top 5 matches, then single CTA card
    _show_match_cta = (
        is_free
        and bool(st.session_state.get("resume_skills"))
        and len(fresh_jobs) > 5
    )
    _hidden_match_count = max(0, len(fresh_jobs) - 5) if _show_match_cta else 0

    for _idx, (_, row) in enumerate(fresh_jobs.iterrows()):
        # Break out at rank 6+ for free users with resume → render CTA card and stop
        if _show_match_cta and _idx >= 5:
            import urllib.parse as _urlparse
            _free_email_2 = (client.get("email") if client else "") or ""
            _stripe_url_2 = "https://buy.stripe.com/3cI4gs9hugN14obehifnO02"
            if _free_email_2:
                _stripe_url_2 = f"{_stripe_url_2}?prefilled_email={_urlparse.quote(_free_email_2)}"
            st.markdown(f"""
            <div style="background:linear-gradient(135deg,#0f0f1a 0%,#1a1a2e 100%);
                border:1px dashed #e2ff5d55;border-radius:6px;padding:32px 24px;
                margin-top:8px;text-align:center">
                <div style="font-size:2rem;margin-bottom:8px">🔒</div>
                <div style="font-family:'Bebas Neue',sans-serif;font-size:1.6rem;
                    color:#e2ff5d;letter-spacing:0.05em;margin-bottom:6px">
                    {_hidden_match_count} more matches available
                </div>
                <div style="font-size:0.78rem;color:#aaa;line-height:1.6;
                    max-width:520px;margin:0 auto 20px auto">
                    Upgrade to Pro to see your full match top-50 — ranked by your resume,
                    experience level, and salary floor. Plus hiring manager LinkedIn on
                    every job.
                </div>
                <a href="{_stripe_url_2}" target="_blank"
                   style="background:#e2ff5d;color:#080810;font-family:'IBM Plex Mono',monospace;
                       font-size:0.78rem;font-weight:600;padding:10px 24px;border-radius:3px;
                       text-decoration:none;letter-spacing:0.05em;text-transform:uppercase;
                       display:inline-block">
                    Upgrade $19/mo →
                </a>
            </div>
            """, unsafe_allow_html=True)
            break
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
        # RECRUITER_LOC_PATCH_v1: clean loc_city + loc_state usage
        loc_str = ""
        loc_city = row.get("loc_city")
        loc_state = row.get("loc_state")
        if pd.notna(loc_city) and loc_city and pd.notna(loc_state) and loc_state:
            loc_str = f"{loc_city}, {loc_state}"
        elif pd.notna(loc_state) and loc_state:
            loc_str = str(loc_state)
        elif pd.notna(loc_city) and loc_city:
            loc_str = str(loc_city)
        loc_badge = ("<span class=\"badge badge-location\">" + loc_str + "</span>") if loc_str else ""
        exp_badge = ("<span class=\"badge badge-sector\">" + exp + "</span>") if exp else ""
        age_badge = "<span class=\"badge badge-age\">" + icon + " " + age_str + "</span>"
        # TRAFFIC_LIGHT_v1: replace numeric Quality/Urgency with signal traffic light
        # Compute score from agreed ruleset
        # TRAFFIC_LIGHT_NAN_FIX_v1: pd.notna handles NaN correctly (bool(NaN)==True is wrong)
        _smin = row.get("salary_min_annual")
        _smax = row.get("salary_max_annual")
        _has_salary = (pd.notna(_smin) and _smin) or (pd.notna(_smax) and _smax)
        _contact = row.get("contact_linkedin")
        _has_contact = pd.notna(_contact) and bool(_contact)
        _hscore = row.get("honesty_score")
        _days_old_val = row.get("days_old") or 0
        _signal_score = 0
        _reasons_pos = []
        _reasons_neg = []
        # TRAFFIC_LIGHT_RECAL_v1: weight tuned down +2 -> +1
        if _has_salary:
            _signal_score += 1
            _reasons_pos.append("Salary disclosed")
        else:
            _signal_score -= 1
            _reasons_neg.append("No salary")
        if _has_contact:
            _signal_score += 1
            _reasons_pos.append("Hiring contact")
        else:
            _signal_score -= 1
            _reasons_neg.append("No contact")
        if _days_old_val <= 3:
            _signal_score += 1
            _reasons_pos.append(f"Posted {int(_days_old_val)}d ago" if _days_old_val >= 1 else "Posted today")
        elif _days_old_val >= 7 and _days_old_val < 14:
            _signal_score -= 1
            _reasons_neg.append(f"Posted {int(_days_old_val)}d ago")
        if _hscore is not None:
            try:
                _hs = int(_hscore)
                if _hs >= 85:
                    _signal_score += 2
                    _reasons_pos.append("High honesty score")
                elif _hs < 50:
                    _signal_score -= 2
                    _reasons_neg.append("Low honesty score")
                elif _hs < 70:
                    _signal_score -= 1
                    _reasons_neg.append("Mixed honesty signals")
            except (ValueError, TypeError):
                pass

        if _signal_score >= 3:
            _signal_emoji = "🟢"
            _signal_label = "Worth applying"
            _signal_color = "#22c55e"
            _signal_bg = "#0d1f0d"
        elif _signal_score >= -1:
            _signal_emoji = "🟡"
            _signal_label = "Mixed signals"
            _signal_color = "#facc15"
            _signal_bg = "#1f1a00"
        else:
            _signal_emoji = "🔴"
            _signal_label = "Likely skip"
            _signal_color = "#ef4444"
            _signal_bg = "#1f0d0d"

        signal_badge = f'<span style="display:inline-block;font-size:0.62rem;padding:2px 8px;border-radius:2px;margin-right:4px;font-family:IBM Plex Mono,monospace;font-weight:600;background:{_signal_bg};color:{_signal_color};border:1px solid {_signal_color}55;text-transform:uppercase;letter-spacing:0.05em">{_signal_emoji} {_signal_label}</span>'
        # Kept as empty for backward compat; do not display old numeric badges
        q_badge = ""
        u_badge = ""
        src_badge = f'<span style="display:inline-block;font-size:0.62rem;padding:2px 8px;border-radius:2px;margin-right:4px;font-family:IBM Plex Mono,monospace;text-transform:uppercase;letter-spacing:0.05em;background:{src_bg};color:{src_color};border:1px solid {src_color}33">{source_display}</span>'
        # RECRUITER_RESUME_PATCH_v1: match badge prepended (only when resume loaded)
        match_badge = ""
        if st.session_state.get("resume_skills"):
            _ms = int(row.get("match_score") or 0)
            if _ms > 0:
                _color = "#22c55e" if _ms >= 80 else ("#facc15" if _ms >= 60 else "#71717a")
                _bg = "#0d1f0d" if _ms >= 80 else ("#1f1a00" if _ms >= 60 else "#15151f")
                match_badge = f'<span style="display:inline-block;font-size:0.62rem;padding:2px 8px;border-radius:2px;margin-right:4px;font-family:IBM Plex Mono,monospace;font-weight:600;background:{_bg};color:{_color};border:1px solid {_color}55">★ {_ms}% MATCH</span>'
        badges = match_badge + age_badge + signal_badge + sal_badge + loc_badge + sector_badge + wp_badge + exp_badge + src_badge  # TRAFFIC_LIGHT_v1
        # TRAFFIC_LIGHT_v1: build reasons row HTML
        _reasons_parts = []
        for _r in _reasons_pos:
            _reasons_parts.append(f'<span style="color:#22c55e;margin-right:10px">✓ {_r}</span>')
        for _r in _reasons_neg:
            _reasons_parts.append(f'<span style="color:#a1a1aa;margin-right:10px">⚠ {_r}</span>')
        reasons_html = (
            f'<div style="font-size:0.62rem;font-family:IBM Plex Mono,monospace;margin-top:6px;color:#666;line-height:1.6">'
            + "".join(_reasons_parts)
            + '</div>'
        ) if _reasons_parts else ""

        # Build Apply button
        job_url = row.get('job_url', '') or ''
        if job_url:
            apply_html = (
                f'<div style="margin-top:12px">'
                f'<a href="{job_url}" target="_blank" '
                f'style="display:inline-block;background:#e2ff5d;color:#080810;'
                f'font-family:IBM Plex Mono,monospace;font-size:0.7rem;font-weight:600;'
                f'padding:8px 18px;text-decoration:none;letter-spacing:0.05em;'
                f'text-transform:uppercase;border-radius:2px">Apply →</a>'
                f'</div>'
            )
        else:
            apply_html = ''

        # Build contact HTML - LinkedIn only (no email)
        if pd.notna(row.get('contact_name')) and row.get('contact_name'):
            li_url = row.get('contact_linkedin', '')
            contact_html = (
                f'<div style="font-size:0.72rem;color:#e2ff5d;font-weight:500">{row["contact_name"]}</div>'
                f'<div style="font-size:0.62rem;color:#666;margin:2px 0">{(row.get("contact_title") or "")[:35]}</div>'
                f'<div style="margin-top:4px;display:flex;gap:6px;justify-content:flex-end">'
            )
            if li_url:
                contact_html += f'<a href="{li_url}" target="_blank" style="font-size:0.6rem;color:#38bdf8;text-decoration:none;border:1px solid #38bdf844;padding:2px 6px;border-radius:2px">View on LinkedIn</a>'
            contact_html += '</div>'
        else:
            contact_html = '<div style="font-size:0.65rem;color:#333;font-style:italic">No contact found</div>'

        # BLUR_CONTACT_v1 + ANON_LANDING_POLISH_v1: wrap for free OR anonymous users
        if is_free or is_anonymous:
            contact_html = (
                '<div style="position:relative;min-height:60px">'
                '<div style="filter:blur(4px);user-select:none;pointer-events:none">'
                + contact_html +
                '</div>'
                '<div style="position:absolute;top:50%;left:50%;'
                'transform:translate(-50%,-50%);'
                'font-size:0.6rem;color:#e2ff5d;font-weight:600;'
                'background:rgba(8,8,16,0.85);padding:4px 8px;border-radius:3px;'
                'border:1px solid #e2ff5d33;white-space:nowrap;'
                'text-transform:uppercase;letter-spacing:0.05em">'
                '🔒 Upgrade to see contact'
                '</div>'
                '</div>'
            )

        company_display = row['company_name'] or '—'
        sector_sub = f" · {row['sector']}" if pd.notna(row['sector']) and row['sector'] else ""
        emp_sub = f" · {int(row['employee_count']):,} employees" if pd.notna(row.get('employee_count')) else ""
        card_html = (
            f"<div class=\"job-card signal-{signal}\">"
            f"<div style=\"display:flex;justify-content:space-between;align-items:flex-start\">"
            f"<div style=\"flex:1\">"
            f"<div class=\"job-title\">{row['role_name']}</div>"
            f"<div class=\"job-company\">{company_display}{sector_sub}{emp_sub}</div>"
            f"<div style=\"margin-bottom:8px\">{badges}</div>{reasons_html}"
            f"<div>{skills_html}</div>"
            f"</div>"
            f"<div style=\"text-align:right;min-width:140px;padding-left:16px\">"
            f"<div style=\"font-size:0.6rem;color:#333;text-transform:uppercase;"
            f"letter-spacing:0.1em;margin-bottom:4px\">Hiring Contact</div>"
            f"<div>{contact_html}</div>"
            f"{apply_html}"
            f"</div></div></div>"
        )
        st.markdown(card_html, unsafe_allow_html=True)

# ── FOOTER ────────────────────────────────────────────────────────────────────
st.markdown("""
<div style="margin-top:48px;padding-top:16px;border-top:1px solid #131320;
    font-size:0.6rem;color:#2a2a3a;display:flex;justify-content:space-between">
    <span>DataHiringIQ · Data & ML Job Search</span>
    <span>datahiringiq.com · jones31luke@gmail.com</span>
</div>
""", unsafe_allow_html=True)
