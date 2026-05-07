"""
Lander transactional email templates.

Usage in api.py:
    from email_templates import free_signup_html, free_signup_plain, pro_welcome_html, pro_welcome_plain

    resend_client.emails.send({
        "from": os.environ["RESEND_FROM"],
        "to": [email],
        "subject": "Your Lander access link",
        "html": free_signup_html(magic_link_url),
        "text": free_signup_plain(magic_link_url),
    })
"""

import html as _h


def _base(content: str) -> str:
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
</head>
<body style="margin:0;padding:0;background-color:#0a0a0a;font-family:Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#0a0a0a;">
    <tr>
      <td align="center" style="padding:40px 20px;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0"
               style="max-width:600px;background-color:#111111;border-radius:8px;border:1px solid #1e1e1e;">
{content}
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _logo_row(badge: str = "") -> str:
    badge_html = (
        f'<span style="margin-left:10px;font-size:11px;font-weight:700;color:#0a0a0a;'
        f'background-color:#d4ff3a;padding:3px 8px;border-radius:3px;letter-spacing:0.6px;">'
        f'{_h.escape(badge)}</span>'
        if badge else ""
    )
    return f"""\
          <tr>
            <td style="padding:40px 48px 0;">
              <span style="font-size:30px;font-weight:800;letter-spacing:-0.5px;line-height:1;"><span style="color:#d4ff3a;">L</span><span style="color:#ffffff;">ander</span></span>{badge_html}
            </td>
          </tr>
          <tr>
            <td style="padding:24px 48px 0;">
              <div style="height:1px;background-color:#1e1e1e;"></div>
            </td>
          </tr>"""


def _divider() -> str:
    return """\
          <tr>
            <td style="padding:32px 48px 0;">
              <div style="height:1px;background-color:#1e1e1e;"></div>
            </td>
          </tr>"""


def _footer(note: str = "If you didn't request this, you can safely ignore this email.") -> str:
    return f"""\
{_divider()}
          <tr>
            <td style="padding:24px 48px 40px;">
              <p style="margin:0 0 12px;font-size:13px;color:#555555;line-height:1.5;">
                Lander &mdash; Job intelligence built for seekers, not employers
              </p>
              <p style="margin:0;font-size:12px;color:#444444;line-height:1.5;">{_h.escape(note)}</p>
            </td>
          </tr>"""


def _feature_block(label: str, items: str) -> str:
    return f"""\
          <tr>
            <td style="padding:20px 48px 0;">
              <p style="margin:0 0 6px;font-size:11px;font-weight:700;color:#555555;text-transform:uppercase;letter-spacing:0.8px;">{_h.escape(label)}</p>
              <p style="margin:0;font-size:14px;color:#888888;line-height:1.7;">{_h.escape(items)}</p>
            </td>
          </tr>"""


# ---------------------------------------------------------------------------
# Free signup
# ---------------------------------------------------------------------------

FREE_SIGNUP_SUBJECT = "Your Lander access link"

FREE_FEATURES = "Full job feed · Traffic-light signals · Mark applied · Outcome capture · Top 3 resume matches"
PRO_UPSELL = "Upgrade to Pro ($19/mo) for top 50 resume matches, hiring manager LinkedIn for all roles, and the Insights dashboard"


def free_signup_html(magic_link_url: str) -> str:
    url_escaped = _h.escape(magic_link_url)
    content = f"""\
{_logo_row()}
          <tr>
            <td style="padding:40px 48px 0;">
              <h1 style="margin:0 0 10px;font-size:32px;font-weight:800;color:#ffffff;letter-spacing:-1px;line-height:1.1;">
                Your access link
              </h1>
              <p style="margin:0;font-size:16px;color:#a0a0a0;line-height:1.5;">
                Welcome to Lander. Click below to enter your dashboard.
              </p>
            </td>
          </tr>
          <tr>
            <td style="padding:32px 48px 0;">
              <table cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td style="border-radius:6px;background-color:#d4ff3a;">
                    <a href="{url_escaped}"
                       style="display:inline-block;padding:16px 32px;font-size:16px;font-weight:700;color:#0a0a0a;text-decoration:none;letter-spacing:-0.2px;">
                      Open Dashboard &rarr;
                    </a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 48px 0;">
              <p style="margin:0;font-size:13px;color:#555555;line-height:1.6;">
                Or paste this URL into your browser:<br>
                <a href="{url_escaped}" style="color:#d4ff3a;text-decoration:none;word-break:break-all;">{url_escaped}</a>
              </p>
            </td>
          </tr>
{_divider()}
{_feature_block("What's free", FREE_FEATURES)}
          <tr>
            <td style="padding:16px 48px 0;">
              <p style="margin:0;font-size:14px;color:#666666;line-height:1.7;">{_h.escape(PRO_UPSELL)}</p>
            </td>
          </tr>
{_footer()}"""
    return _base(content)


def free_signup_plain(magic_link_url: str) -> str:
    return f"""\
Welcome to Lander.

Click to open your dashboard:
{magic_link_url}

If the link doesn't work, paste the URL above into your browser.

---
What's free: {FREE_FEATURES}

{PRO_UPSELL}

Lander — Job intelligence built for seekers, not employers

If you didn't request this, you can safely ignore this email.
"""


# ---------------------------------------------------------------------------
# Pro welcome
# ---------------------------------------------------------------------------

PRO_WELCOME_SUBJECT = "Welcome to Lander Pro"

PRO_UNLOCKED = "Top 50 resume matches · Hiring manager LinkedIn for all roles · Insights dashboard"


def pro_welcome_html(dashboard_url: str) -> str:
    url_escaped = _h.escape(dashboard_url)
    content = f"""\
{_logo_row(badge="PRO")}
          <tr>
            <td style="padding:40px 48px 0;">
              <h1 style="margin:0 0 10px;font-size:32px;font-weight:800;color:#ffffff;letter-spacing:-1px;line-height:1.1;">
                You&rsquo;re on Pro.
              </h1>
              <p style="margin:0;font-size:16px;color:#a0a0a0;line-height:1.5;">
                Your subscription is active. The complete intelligence layer is ready.
              </p>
            </td>
          </tr>
          <tr>
            <td style="padding:32px 48px 0;">
              <table cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td style="border-radius:6px;background-color:#d4ff3a;">
                    <a href="{url_escaped}"
                       style="display:inline-block;padding:16px 32px;font-size:16px;font-weight:700;color:#0a0a0a;text-decoration:none;letter-spacing:-0.2px;">
                      Open Dashboard &rarr;
                    </a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
{_divider()}
{_feature_block("What's unlocked", PRO_UNLOCKED)}
{_feature_block("Plus everything free includes", FREE_FEATURES)}
{_footer(note="Questions? Reply to this email.")}"""
    return _base(content)


def pro_welcome_plain(dashboard_url: str) -> str:
    return f"""\
You're on Lander Pro.

Your subscription is active. The complete intelligence layer is ready.

Open your dashboard:
{dashboard_url}

---
What's unlocked: {PRO_UNLOCKED}

Plus everything free includes: {FREE_FEATURES}

Lander — Job intelligence built for seekers, not employers

Questions? Reply to this email.
"""
