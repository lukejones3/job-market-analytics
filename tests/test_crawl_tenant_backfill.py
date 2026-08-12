import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from backfill_crawl_tenants import infer_crawl_tenant, tenant_from_board_token


def test_source_urls_and_board_tokens_resolve_lifecycle_owner():
    assert infer_crawl_tenant(
        "greenhouse", None, "https://job-boards.greenhouse.io/example/jobs/123"
    ) == "example"
    assert tenant_from_board_token("greenhouse", "example") == "example"
    assert tenant_from_board_token("workday", "example/wd5/External") == "example"
    assert tenant_from_board_token("eightfold", "example/example.com") == "example"
