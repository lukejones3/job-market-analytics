#!/usr/bin/env python3
from resume.matcher import _skill_overlap_pct


def main():
    resume = {"python", "sql", "dbt", "airflow", "postgres", "git", "bash", "fastapi"}
    generic_four = {"python", "sql", "git", "bash"}
    focused_six = {"python", "sql", "dbt", "airflow", "postgres", "git"}

    generic_score = _skill_overlap_pct(resume, generic_four)
    focused_score = _skill_overlap_pct(resume, focused_six)
    assert generic_score < 0.70, generic_score
    assert focused_score > generic_score, (focused_score, generic_score)
    assert _skill_overlap_pct(resume, set()) == 0.0
    assert _skill_overlap_pct(set(), focused_six) == 0.0
    print("resume matcher regression: 4/4")


if __name__ == "__main__":
    main()
