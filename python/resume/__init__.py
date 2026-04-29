"""resume — pure functions for resume parsing, skill extraction, and job matching.

Public API:
    parse_resume(file_bytes, filename) -> str           # raw text
    extract_skills(text, db_conn) -> dict               # {skill_id: {name, confidence, evidence, source}}
    infer_experience_level(text) -> str                 # entry/associate/mid/senior
    match_jobs(skills, exp_level, salary_floor, db_conn, top_n=50) -> list[dict]
    find_skill_gaps(skills, exp_level, db_conn, top_n=5) -> list[dict]
"""

from .parser import parse_resume
from .skill_extractor import extract_skills
from .exp_inferrer import infer_experience_level
from .matcher import match_jobs, find_skill_gaps

__all__ = [
    "parse_resume",
    "extract_skills",
    "infer_experience_level",
    "match_jobs",
    "find_skill_gaps",
]
