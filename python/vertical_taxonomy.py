"""
Single source of truth for all 8 Lander verticals.

Structure per vertical:
  display_name   — UI label
  description    — one-sentence summary
  patterns       — regex fragments matched against job titles (case-insensitive, word-boundary)
  overlaps       — list of {pattern, primary, secondary} for ambiguous title segments

NOTE: `skills` and `subcategories` were migrated out of this file:
  - skills      -> config/skill_taxonomy.json   (loaded via python/skill_taxonomy.py)
  - role_category vocab / subcategories -> config/role_taxonomy.json (python/role_taxonomy.py)
This file now holds only the DOMAIN-classification patterns (single source for `domain`).
"""

VERTICALS = {

    # ─────────────────────────────────────────────────────────────────────────
    "data_ml": {
        "display_name": "Data & AI",
        "description": "Roles focused on data pipelines, analytics, machine learning, and AI systems.",
        "patterns": [
            r"data engineer",
            r"data scientist",
            r"\bml engineer",
            r"machine learning",
            r"analytics engineer",
            r"\bai engineer",
            r"\bmlops\b",
            r"\bllmops\b",
            r"data analyst",
            r"applied scientist",
            r"research scientist",
            r"decision scientist",
            r"\bbi analyst",
            r"\bbi developer",
            r"business intelligence",
            r"quantitative analyst",
            r"quant analyst",
            r"data platform",
            r"data infrastructure",
            r"data architect",
            r"data manager",
            r"data lead",
            r"head of data",
            r"vp.*data",
            r"chief data",
            r"data product manager",       # primary data_ml, secondary product
        ],
        "overlaps": [
            {
                "pattern": r"ml engineer|machine learning engineer",
                "primary": "data_ml",
                "secondary": ["engineering"],
                "note": "ML Engineers bridge data science and software engineering.",
            },
            {
                "pattern": r"\bai engineer",
                "primary": "data_ml",
                "secondary": ["engineering"],
                "note": "AI Engineers often build infrastructure as well as models.",
            },
            {
                "pattern": r"data product manager",
                "primary": "data_ml",
                "secondary": ["product"],
            },
            {
                "pattern": r"quant analyst|quantitative analyst",
                "primary": "data_ml",
                "secondary": ["finance"],
            },
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    "engineering": {
        "display_name": "Engineering",
        "description": "Software, infrastructure, hardware, and quality engineering roles.",
        "patterns": [
            r"software engineer",
            r"software developer",
            r"\bsde\b",
            r"\bswe\b",
            r"backend engineer",
            r"frontend engineer",
            r"front-end engineer",
            r"back-end engineer",
            r"fullstack engineer",
            r"full.stack engineer",
            r"full.stack developer",
            r"mobile engineer",
            r"\bios engineer",
            r"\bios developer",
            r"android engineer",
            r"android developer",
            r"\bdevops\b",
            r"site reliability",
            r"\bsre\b",
            r"platform engineer",
            r"infrastructure engineer",
            r"cloud engineer",
            r"security engineer",
            r"embedded engineer",
            r"embedded software",
            r"firmware engineer",
            r"hardware engineer",
            r"electrical engineer",
            r"mechanical engineer",
            r"robotics engineer",
            r"systems engineer",
            r"network engineer",
            r"distributed systems",
            r"\bqa engineer",
            r"test engineer",
            r"automation engineer",
            r"staff engineer",
            r"principal engineer",
            r"engineering manager",
            r"head of engineering",
            r"vp.*engineering",
            r"chief.*engineer",
            r"productivity engineer",
        ],
        "overlaps": [
            {
                "pattern": r"security engineer|application security|appsec",
                "primary": "engineering",
                "secondary": ["ops"],
                "note": "Security engineers are counted as engineering; security specialists in ops.",
            },
            {
                "pattern": r"platform engineer",
                "primary": "engineering",
                "secondary": [],
                "note": "Platform engineers are squarely engineering.",
            },
            {
                "pattern": r"growth engineer",
                "primary": "engineering",
                "secondary": ["marketing"],
            },
            {
                "pattern": r"ml engineer|machine learning engineer",
                "primary": "data_ml",
                "secondary": ["engineering"],
            },
            {
                "pattern": r"\bai engineer",
                "primary": "data_ml",
                "secondary": ["engineering"],
            },
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    "finance": {
        "display_name": "Finance",
        "description": "Financial planning, accounting, investment, risk, and treasury roles.",
        "patterns": [
            r"\bfp&a\b",
            r"financial analyst",
            r"financial planning",
            r"financial reporting",
            r"\baccounti",          # accounting, accountant
            r"\baccountant\b",
            r"\bcontroller\b",
            r"treasury analyst",
            r"treasurer",
            r"\baudit",            # audit, auditor
            r"\bauditor\b",
            r"investment banker",
            r"investment banking",
            r"equity research",
            r"credit analyst",
            r"risk analyst",
            r"risk manager",
            r"\btax analyst",
            r"\btax manager",
            r"\btax director",
            r"director[\s,]+tax",   # "Director, Tax" or "Director Tax"
            r"\bhead of tax\b",
            r"\bvp[\s,]+tax\b",
            r"\bfp&a director\b",
            r"\bactuary\b",
            r"\bactuarial\b",
            r"underwriter",
            r"corporate finance",
            r"chief financial",
            r"\bcfo\b",
            r"vp.*finance",
            r"head of finance",
            r"finance manager",
            r"finance director",
            r"\bcpa\b",
            r"\bcfa\b",
            r"wealth management",
            # ── Pricing / Monetization (strategy/finance roles, not data roles) ──
            # Note: "Pricing Data Scientist", "Sr. Data Analyst, Pricing" are data_ml
            # because data_ml title patterns fire first (data scientist / data analyst).
            # These patterns only catch titles where pricing IS the role, not a qualifier.
            r"pricing.*analyst",    # Pricing Analyst, Pricing & Monetization Analyst
            r"pricing.*manager",    # Pricing Manager, Senior Manager Pricing Strategy
            r"pricing.*director",   # Pricing Director, Director of Pricing
            r"pricing.*strategy",   # Pricing Strategy Manager, Head of Pricing Strategy
            r"pricing.*specialist",
            r"pricing.*operations",
            r"director.*pricing",   # Director of Pricing & Monetization
            r"head.*pricing",       # Head of Pricing
            r"vp.*pricing",
            r"monetization.*analyst",
            r"monetization.*manager",
            r"monetization.*strategy",
            r"price analyst",
            r"price manager",
        ],
        "overlaps": [
            {
                "pattern": r"quant analyst|quantitative analyst|quantitative researcher",
                "primary": "data_ml",
                "secondary": ["finance"],
                "note": "Quant roles are primarily data_ml but live in finance contexts.",
            },
            {
                "pattern": r"financial data analyst|finance data",
                "primary": "data_ml",
                "secondary": ["finance"],
            },
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    "marketing": {
        "display_name": "Marketing",
        "description": "Growth, performance, content, brand, and marketing operations roles.",
        "patterns": [
            r"marketing manager",
            r"marketing director",
            r"marketing specialist",
            r"marketing analyst",
            r"marketing coordinator",
            r"growth manager",
            r"growth marketing",
            r"demand gen",
            r"demand generation",
            r"lifecycle marketing",
            r"lifecycle manager",
            r"performance marketing",
            r"paid acquisition",
            r"paid media",
            r"paid social",
            r"\bseo\b",
            r"\bsem\b",
            r"\bsearch engine",      # word boundary required — prevents "reSearch Engine er" false match
            r"content marketing",
            r"content strategist",
            r"content manager",
            r"content writer",
            r"copywriter",
            r"\bbrand manager",
            r"\bbrand strategist",
            r"communications manager",
            r"public relations",
            r"\bpr manager",
            r"social media manager",
            r"social media specialist",
            r"email marketing",
            r"product marketing",
            r"\bpmm\b",
            r"marketing ops",
            r"\bmarops\b",
            r"creative director",
            r"vp.*marketing",
            r"chief marketing",
            r"\bcmo\b",
            r"head of marketing",
            r"head of growth",
            r"digital experience manager",
            r"digital experience director",
            r"director.*digital experience",
            r"head of digital experience",
            r"vp.*digital experience",
            r"brand experience",
            r"customer experience(?!\s+(?:manager|director)\b)",
            r"marketing scientist",
            r"growth scientist",
            r"marketing lead",
            r"growth marketer",
            r"growth lead",
            r"brand lead",
            r"content lead",
            r"marketing strategist",
            r"demand gen lead",
            r"director.*marketing",  # catches "Director, Marketing", "Sr Director, Marketing"
        ],
        "overlaps": [
            {
                "pattern": r"marketing analyst|marketing data",
                "primary": "data_ml",
                "secondary": ["marketing"],
                "note": "Analytics-heavy marketing roles lean data_ml.",
            },
            {
                "pattern": r"growth engineer",
                "primary": "engineering",
                "secondary": ["marketing"],
            },
            {
                "pattern": r"product marketing",
                "primary": "marketing",
                "secondary": ["product"],
            },
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    "product": {
        "display_name": "Product",
        "description": "Product management, product operations, and product analytics roles.",
        "patterns": [
            r"product manager",
            r"\bpm\b",
            r"product owner",
            r"technical product manager",
            r"\btpm\b",
            r"product ops",
            r"product analyst",
            r"group product manager",
            r"\bgpm\b",
            r"principal pm",
            r"head of product",
            r"vp.*product",
            r"chief product",
            r"\bcpo\b",
            r"director of product",
            r"sr\. product",
            r"senior product manager",
            r"product lead",
            r"senior product lead",
            r"associate product manager",
            r"\bapm\b",
            r"(?<!customer )experience manager",
            r"(?<!customer )experience director",
            r"platform manager",
            r"\bproduct management\b",  # catches "Director Product Management", "VP Product Management"
        ],
        "overlaps": [
            {
                "pattern": r"product analyst",
                "primary": "product",
                "secondary": ["data_ml"],
            },
            {
                "pattern": r"technical product manager|technical pm",
                "primary": "product",
                "secondary": ["engineering"],
            },
            {
                "pattern": r"data product manager",
                "primary": "data_ml",
                "secondary": ["product"],
            },
            {
                "pattern": r"product marketing",
                "primary": "marketing",
                "secondary": ["product"],
            },
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    "sales": {
        "display_name": "Sales",
        "description": "Account executives, SDRs, customer success, revenue ops, and sales engineering roles.",
        "patterns": [
            r"account executive",
            r"\bae\b",
            r"\bsdr\b",
            r"\bbdr\b",
            r"business development rep",
            r"business development manager",
            r"sales engineer",
            r"solutions engineer",
            r"solutions architect",  # often sales-adjacent
            r"customer success",
            r"\bcsm\b",
            r"account manager",
            r"\bam\b",
            r"revenue ops",
            r"\brevops\b",
            r"sales ops",
            r"sales operations",
            r"channel sales",
            r"field sales",
            r"inside sales",
            r"enterprise sales",
            r"sales manager",
            r"sales director",
            r"sales executive",
            r"senior sales executive",
            r"enterprise sales executive",
            r"vp.*sales",
            r"chief revenue",
            r"\bcro\b",
            r"head of sales",
            r"head of.*\bsales\b",
            r"\bad sales\b",
            r"sales.*monetization",
            r"monetization.*sales",
            r"partnership manager",
            r"partner management",
            r"alliances manager",
            r"sales development",
            r"sales representative",
            r"\bsales rep\b",
            r"relationship manager",
            r"relationship management",
            r"account director",
            r"solutions director",
            r"solutions manager",
            r"pharmaceutical sales",
            r"\bpharma sales\b",
            r"medical device sales",
            r"\bmed device sales\b",
            r"renewal manager",
            r"renewal management",
            r"account management",
            r"client success",
            r"client manager",
            r"partner success",
            r"customer experience manager",
            r"customer experience director",
            r"sales compensation",
            r"sales comp\b",
            r"comp planning",
            r"compensation design",
            r"go[\-]to[\-]market",   # hyphens only — "go-to-market" role titles; "Go To Market" (org label) excluded
            r"gtm lead",
            r"gtm director",
            r"gtm program",
            r"gtm strategy",
            r"gtm manager",
            r"gtm enablement",
            r"gtm engineer",         # sales engineering in GTM org
            r"sales lead",
        ],
        "overlaps": [
            {
                "pattern": r"sales engineer|solutions engineer",
                "primary": "sales",
                "secondary": ["engineering"],
                "note": "Sales engineers sit in sales org but need deep technical knowledge.",
            },
            {
                "pattern": r"revenue ops|revops|sales ops",
                "primary": "sales",
                "secondary": ["ops"],
            },
            {
                "pattern": r"solutions architect",
                "primary": "sales",
                "secondary": ["engineering"],
            },
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    "design": {
        "display_name": "Design",
        "description": "Product design, UX research, brand, motion, and design operations roles.",
        "patterns": [
            r"product designer",
            r"ux designer",
            r"ui designer",
            r"ux/ui designer",
            r"ui/ux designer",
            r"cx[\s/]+ux\s+designer",
            r"ux[\s/]+cx\s+designer",
            r"\bcx\s+designer\b",
            r"experience designer",
            r"service designer",
            r"user experience designer",
            r"user interface designer",
            r"visual designer",
            r"brand designer",
            r"graphic designer",
            r"graphic design",      # catches "Manager Graphic Design" (no -er)
            r"\bdesigner\b",        # catches content designer, AI designer, solution designer, etc.
            r"motion designer",
            r"creative director",
            r"design director",
            r"design ops",
            r"interaction designer",
            r"industrial designer",
            r"ux researcher",
            r"design researcher",
            r"user researcher",
            r"head of design",
            r"vp.*design",
            r"chief design",
            r"\bcdo\b",             # chief design officer
            r"design lead",
            r"lead designer",
            r"senior designer",
            r"staff designer",
        ],
        "overlaps": [
            {
                "pattern": r"ux researcher|design researcher|user researcher",
                "primary": "design",
                "secondary": ["product"],
                "note": "UX researchers sit in design orgs but closely tied to product.",
            },
            {
                "pattern": r"motion designer",
                "primary": "design",
                "secondary": [],
            },
            {
                "pattern": r"creative director",
                "primary": "design",
                "secondary": ["marketing"],
                "note": "Creative directors in brand/agency contexts lean marketing.",
            },
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    "ops": {
        "display_name": "Operations",
        "description": "Business operations, supply chain, people ops, talent acquisition, and executive support roles.",
        "patterns": [
            r"business operations",
            r"\bbizops\b",
            r"strategic ops",
            r"strategy & operations",
            r"supply chain",
            r"logistics manager",
            r"logistics coordinator",
            r"procurement manager",
            r"vendor management",
            r"people ops",
            r"people operations",
            r"talent ops",
            r"hr ops",
            r"hr operations",
            r"\brecruiter\b",
            r"talent acquisition",
            r"\bta \b",
            r"recruiting manager",
            r"head of recruiting",
            r"executive assistant",
            r"office manager",
            r"facilities manager",
            r"chief of staff",
            r"vp.*operations",
            r"head of operations",
            r"coo",
            r"director of operations",
            r"program manager",     # can overlap with product; ops is primary without "product" in title
            r"corporate development",
            r"fleet planning",
            r"fleet management",
            r"regional operations",
            r"operations director",
            r"supply chain director",
            r"strategy.*operations",  # Strategy Operations Manager, Strategy & Ops Lead
            r"operations manager",    # general ops manager; tiebreak ensures specific verticals win
        ],
        "overlaps": [
            {
                "pattern": r"recruiting ops|talent ops",
                "primary": "ops",
                "secondary": [],
                "note": "Recruiting ops is firmly ops; pure recruiter roles also ops.",
            },
            {
                "pattern": r"strategic ops|strategy.*operations",
                "primary": "ops",
                "secondary": ["product"],
                "note": "Strategic ops at startups often adjacent to product/strategy.",
            },
            {
                "pattern": r"revenue ops|revops",
                "primary": "sales",
                "secondary": ["ops"],
            },
            {
                "pattern": r"program manager",
                "primary": "ops",
                "secondary": ["product", "engineering"],
                "note": "Program manager is ops by default unless 'product' or 'technical' appears in title.",
            },
        ],
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Convenience helpers used by classify_domain.py and seed_skills_taxonomy.py
# ─────────────────────────────────────────────────────────────────────────────

VERTICAL_KEYS: list[str] = list(VERTICALS.keys())
VERTICAL_DISPLAY: dict[str, str] = {k: v["display_name"] for k, v in VERTICALS.items()}


def all_patterns_for(vertical: str) -> list[str]:
    """Return the raw pattern strings for a given vertical."""
    return VERTICALS[vertical]["patterns"]


def all_skills_for(vertical: str) -> dict:
    """Return the skills dict for a given vertical.
    Source: config/skill_taxonomy.json (skills migrated out of VERTICALS)."""
    import skill_taxonomy
    return skill_taxonomy.skills_by_vertical().get(vertical, {})


def overlap_rules() -> list[dict]:
    """Return all overlap rules across every vertical (deduplicated by pattern)."""
    seen: set[str] = set()
    rules: list[dict] = []
    for vertical, data in VERTICALS.items():
        for rule in data.get("overlaps", []):
            if rule["pattern"] not in seen:
                seen.add(rule["pattern"])
                rules.append({**rule, "_defined_in": vertical})
    return rules


if __name__ == "__main__":
    # Quick sanity check
    total_skills = sum(len(v["skills"]) for v in VERTICALS.values())
    print(f"Verticals: {len(VERTICALS)}")
    print(f"Total skill definitions: {total_skills}")
    for key, v in VERTICALS.items():
        print(f"  {key:15s} — {len(v['patterns']):2d} patterns, {len(v['skills']):2d} skills, {len(v['subcategories'])} subcategories")
