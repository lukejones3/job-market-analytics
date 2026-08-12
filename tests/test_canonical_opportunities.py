from python.canonicalize_opportunities import build_updates, near_identical, normalized_description, shingles


def row(job_id: str, description: str, source_id: str = "source") -> tuple:
    return (job_id, "company", "Data Analyst", description, source_id, "us", "IL", "Chicago", "hybrid")


def test_normalization_ignores_tracking_noise():
    assert normalized_description("Apply at https://x.test/1 — Req 1234") == "apply at req"


def test_exact_normalized_duplicates_share_opportunity():
    updates = dict(build_updates([
        row("one", "Build reliable systems. Req 123"),
        row("two", "Build reliable systems! Req 987"),
    ]))
    assert updates["one"] == updates["two"]


def test_exact_signature_ignores_word_boundary_punctuation():
    updates = dict(build_updates([
        row("one", "Build Salesforce integrations"),
        row("two", "Build sales-force integrations"),
    ]))
    assert updates["one"] == updates["two"]


def test_near_duplicate_descriptions_cluster_but_real_differences_do_not():
    def token(index: int) -> str:
        return "word" + "".join(chr(97 + ((index // power) % 26)) for power in (676, 26, 1))

    base = " ".join(token(index) for index in range(1200))
    almost = base + " tracking"
    unrelated = "sales customer quota pipeline " * 300
    assert near_identical(shingles(normalized_description(base)), shingles(normalized_description(almost)))
    updates = dict(build_updates([row("one", base), row("two", almost), row("three", unrelated)]))
    assert updates["one"] == updates["two"]
    assert updates["one"] != updates["three"]


def test_location_and_workplace_prevent_false_merge():
    first = row("one", "same description " * 100)
    second = list(row("two", "same description " * 100))
    second[-1] = "remote"
    updates = dict(build_updates([first, tuple(second)]))
    assert updates["one"] != updates["two"]
