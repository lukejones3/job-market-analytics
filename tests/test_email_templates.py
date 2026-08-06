from python.email_templates import free_signup_html, free_signup_plain, pro_welcome_html


def test_access_email_uses_current_product_language_and_escapes_url() -> None:
    url = "https://www.landerjob.com/auth/verify?token=a&next=/jobs"
    rendered = free_signup_html(url)

    assert "The job market,<br>with the lights on." in rendered
    assert "Live roles" in rendered
    assert "Resume fit" in rendered
    assert "One workspace" in rendered
    assert "Upgrade to Pro" not in rendered
    assert "token=a&amp;next=/jobs" in rendered


def test_access_email_plain_text_has_a_complete_fallback() -> None:
    url = "https://www.landerjob.com/auth/verify?token=test"
    rendered = free_signup_plain(url)

    assert url in rendered
    assert "live company-source roles" in rendered
    assert "If you didn't request this" in rendered


def test_pro_email_uses_current_pro_language() -> None:
    rendered = pro_welcome_html("https://www.landerjob.com/jobs")

    assert "Lander Pro is ready." in rendered
    assert "Expanded Career Agent access" in rendered
    assert "Open your workspace" in rendered
