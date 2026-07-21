# Title Filter Dry-Run Review
_Generated 2026-05-09_

## Numbers

59,495 raw tier-1 survivors evaluated

| Result | Count | % |
|---|---|---|
| KEPT (knowledge worker) | 37,859 | 63.6% |
| DROP (→ ignored) | 21,636 | 36.4% |

Target range was 30–45K. Currently at 37,859 ✓

---

## All 12 test cases: PASS ✓

| Title | Result | Matched |
|---|---|---|
| Mapping Data Collection Driver | DROP ✓ | exc=`Mapping Data Collection` |
| Senior Software Engineer, Self-Driving | KEPT ✓ | inc=`Software Engin` |
| Mechanical Engineer | DROP ✓ | no_inclusion |
| Data Science Intern | KEPT ✓ | inc=`Data Scienc` |
| PhD Research Scientist | KEPT ✓ | inc=`Research Scientist` |
| Survey Automation & Delivery Analyst | KEPT ✓ | inc=`Analyst` |
| Delivery Solutions Architect | KEPT ✓ | inc=`Solutions Architect` |
| Mental Health Therapist | DROP ✓ | exc=`Mental Health Therapist` |
| Fleet Operations Technician | DROP ✓ | no_inclusion |
| Autonomous Vehicle Test Driver | DROP ✓ | exc=`Test Driver` |
| Director of Engineering, Robotics | KEPT ✓ | inc=`Director of Engineering` |
| Compliance Manager, Banking | KEPT ✓ | inc=`Compliance` |

---

## KEPT spot-check (30 random) — looks clean

- Analytics Engineer ← [Analytics Engineer]
- Cloud Architect - Generalist ← [Architect]
- Data Steward - Senior ← [Data Steward]
- Director of Portfolio Risk ← [Director]
- Director, Decision Science Analytics ← [Director]
- Distinguished Applied Researcher ← [Researcher]
- HR CPS Business Analyst ← [Analyst]
- Information Security Engineer - Insider Risk ← [Security Engineer]
- Lead Data Scientist ← [Scientist]
- Lead Machine Learning Engineer ← [Machine Learning]
- Market Operations Analyst ← [Operations Analyst]
- Motion Graphics Designer ← [Designer]
- Operations Analyst, Mission Engineering, Air Dominance ← [Operations Analyst]
- Product Manager ← [Product Manager]
- Sales Development Representative ← [Sales Development Rep]
- Scientist, Translational IPS (Computational Biologist) ← [Scientist]
- Senior Business Intelligence Analyst ← [Business Intelligence]
- Senior Data Engineer ← [Data Engineer]
- Senior Data Scientist ← [Scientist]
- Senior Machine Learning Engineer, Payments ← [Machine Learning]
- Senior Product Manager, AI / Data Classification ← [Product Manager]
- Senior React Developer ← [Developer]
- Senior SRE/DevOps Engineer ← [SRE]
- Senior User Interaction Test Engineer ← [Test Engineer]
- Senior/Staff Machine Learning Engineer, 3D Simulation ← [Machine Learning]
- Site Reliability Engineering Lead (SRE/DevOps/SF Onsite) ← [Site Reliability]
- Solutions Architect Lead ← [Solutions Architect]
- Staff Software Engineer, Platform ← [Software Engin]
- Systems Security Engineer (Program Protection) ← [Security Engineer]

---

## DROP spot-check (30 random) — correct drops + 9 false positives

### Correct drops
- Housekeeper | Janitor ← [no_inclusion]
- Olympic Sculpture Park Mini Golf Representative ← [no_inclusion]
- Retail Sales Associate (All Positions) - Golf Galaxy ← [no_inclusion]
- Physician Liaison - $5,000 SIGN ON BONUS ← [no_inclusion]
- Process Engineer - Cell Line ← [no_inclusion]
- Construction Project Manager - Transmission & Distribution ← [no_inclusion]
- Global Investigator - Federal Law Enforcement ← [no_inclusion]
- Junior Buyer Wholesale (all genders) ← [no_inclusion]
- Coordinator - Program/Event (West Coast) ← [no_inclusion]
- Regional Security Services Specialist ← [no_inclusion]
- Community Development Manager ← [no_inclusion]
- Story Desk Editor ← [no_inclusion]
- SI/PI Engineer ← [no_inclusion]
- Broadcast Engineer Lead ← [no_inclusion]
- Territory Sales Representative / Restaurant Specialist ← [no_inclusion]

### False positives in DROP list (should be KEPT)

| Title | Why it's wrong | Fix |
|---|---|---|
| `AI Deployment Engineer, Codex \| Sydney` | "AI" + "Engineer" not adjacent → no_inclusion | Add `\bai\b` standalone |
| `Compiler Engineer - Algorithmic Workloads Compilation` | Compiler eng = SW engineering | Add `\bcompiler\s+engineer\b` or `\bfounding\b` |
| `Founding Engineer` | Startup eng role, nothing matches | Add `\bfounding\b` |
| `Discovery Database Administrator (DBA)` | DBA is a tech role | Add `\bdba\b` and `\bdatabase\s+admin` |
| `GTM Engineer, Marketing Operations AI Innovation` | GTM = go-to-market, clearly tech | Add `\bgtm\b` |
| `Sales Manager, Enterprise` | B2B enterprise sales manager | Add `\bsales\s+manager\b` |
| `Manager of Sales Operations & Performance` | "sales operations" ≠ "sales ops" | Add `\bsales\s+operat` |
| `Sr. Validation & Tools Engineer - Dev Tools` | Dev tools eng = SW | Add `\btools\s+engineer\b` |
| `Senior eDiscovery Project Manager` | Legal tech PM | Add `\bproject\s+manager\b` |

### Gray-area drops (might be intentional)
- `Agile Coach` — KW role but not matched. Fix: add `\bagile\b` or `\bscrum\b`
- `Quality Engineer` — could be software QA or manufacturing. Ambiguous.
- `Senior Operations Manager, Implementation` — ops manager, KW in tech context but ambiguous
- `Amazon Connect Production Support Engineer` — support eng, probably fine to drop
- `Mission Operations Engineer` — aerospace ops eng, probably fine to drop

---

## Decision needed before applying

**Option A — Apply as-is (37,859 kept)**
- ~9 known false positives in the drop set
- Numbers are already in range
- Fastest path to restarting batch enrichment

**Option B — Add missing patterns first, re-run dry-run**
Suggested additions:
- `\bai\b` — AI Deployment Engineer, AI Ops, AI anything
- `\bllm\b` — LLM Engineer, LLM Researcher
- `\bdba\b|\bdatabase\s+admin` — Database Administrator
- `\bfounding\b` — Founding Engineer/Designer at startups
- `\bsales\s+manager\b` — enterprise/B2B sales managers
- `\bsales\s+(?:representative|rep)\b` — territory/account sales reps
- `\bproject\s+manager\b` — PMs of all kinds (not just program managers)
- `\bgtm\b` — go-to-market engineers/managers
- `\btools\s+engineer\b` — dev tools, tooling engineers
- Optional: `\bagile\b`, `\bscrum\b` — agile coaches, scrum masters

Estimated impact: +500–1,500 more kept → final count ~38K–39K

---

## After approval

Once you approve (either option), next steps:
1. Apply backfill (mark 21K+ as ignored)
2. Restart batch enrichment with custom_id fix already in place
