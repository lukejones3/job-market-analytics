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
  <meta name="color-scheme" content="dark">
  <meta name="supported-color-schemes" content="dark">
  <style>
    @media only screen and (max-width: 620px) {{
      .email-shell {{ padding:18px 10px !important; }}
      .email-card {{ border-radius:14px !important; }}
      .email-pad {{ padding-left:24px !important; padding-right:24px !important; }}
      .email-title {{ font-size:30px !important; }}
      .email-button {{ display:block !important; text-align:center !important; }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background-color:#0a0a0a;font-family:Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#0a0a0a;">
    <tr>
      <td class="email-shell" align="center" style="padding:44px 20px;">
        <table class="email-card" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="max-width:560px;background-color:#10110f;border-radius:18px;border:1px solid #252720;overflow:hidden;box-shadow:0 24px 80px rgba(0,0,0,0.32);">
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
            <td class="email-pad" style="padding:30px 40px 0;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
                <td><span style="font-size:24px;font-weight:800;color:#f5f5f5;letter-spacing:-0.8px;line-height:1;">lander</span>{badge_html}</td>
                <td align="right"><span style="display:inline-block;width:8px;height:8px;border-radius:8px;background:#d4ff3a;box-shadow:0 0 14px rgba(212,255,58,0.65);"></span></td>
              </tr></table>
            </td>
          </tr>
          <tr>
            <td class="email-pad" style="padding:24px 40px 0;">
              <div style="height:1px;background-color:#252720;"></div>
            </td>
          </tr>"""


def _divider() -> str:
    return """\
          <tr>
            <td class="email-pad" style="padding:30px 40px 0;">
              <div style="height:1px;background-color:#252720;"></div>
            </td>
          </tr>"""


def _footer(note: str = "If you didn't request this, you can safely ignore this email.") -> str:
    return f"""\
{_divider()}
          <tr>
            <td class="email-pad" style="padding:22px 40px 30px;">
              <p style="margin:0 0 8px;font-size:12px;color:#74766f;line-height:1.5;">
                Lander &mdash; a clearer way through the job market
              </p>
              <p style="margin:0;font-size:11px;color:#555750;line-height:1.5;">{_h.escape(note)}</p>
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

FREE_FEATURES = "Live company-source jobs · Resume matching · Saved and applied workspaces · Career Agent"


def free_signup_html(magic_link_url: str) -> str:
    url_escaped = _h.escape(magic_link_url)
    content = f"""\
{_logo_row()}
          <tr>
            <td class="email-pad" style="padding:34px 40px 0;">
              <p style="margin:0 0 14px;font-size:10px;font-weight:700;color:#b9dd37;text-transform:uppercase;letter-spacing:1.5px;">
                Your Lander workspace
              </p>
              <h1 class="email-title" style="margin:0 0 12px;font-size:38px;font-weight:800;color:#f5f5f5;letter-spacing:-1.5px;line-height:1.04;">
                The job market,<br>with the lights on.
              </h1>
              <p style="margin:0;max-width:430px;font-size:15px;color:#9b9d96;line-height:1.65;">
                Your secure sign-in link is ready. Open Lander to search live roles, compare your resume and keep your search in one place.
              </p>
            </td>
          </tr>
          <tr>
            <td class="email-pad" style="padding:28px 40px 0;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td align="center" style="border-radius:10px;background-color:#d4ff3a;">
                    <a class="email-button" href="{url_escaped}"
                       style="display:block;padding:16px 24px;font-size:15px;font-weight:800;color:#0a0a0a;text-align:center;text-decoration:none;letter-spacing:-0.2px;">
                      Enter Lander &nbsp;&rarr;
                    </a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td class="email-pad" style="padding:16px 40px 0;">
              <p style="margin:0;font-size:11px;color:#5e605a;line-height:1.55;">
                This private link signs you in directly. It expires automatically and can only be used once.
              </p>
            </td>
          </tr>
{_divider()}
          <tr>
            <td class="email-pad" style="padding:22px 40px 0;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td width="33%" valign="top" style="padding-right:10px;">
                    <p style="margin:0 0 5px;font-size:11px;font-weight:700;color:#d6d7d2;">Live roles</p>
                    <p style="margin:0;font-size:11px;color:#696b65;line-height:1.5;">Direct company sources</p>
                  </td>
                  <td width="33%" valign="top" style="padding:0 10px;border-left:1px solid #252720;">
                    <p style="margin:0 0 5px;font-size:11px;font-weight:700;color:#d6d7d2;">Resume fit</p>
                    <p style="margin:0;font-size:11px;color:#696b65;line-height:1.5;">Evidence, not guesses</p>
                  </td>
                  <td width="33%" valign="top" style="padding-left:10px;border-left:1px solid #252720;">
                    <p style="margin:0 0 5px;font-size:11px;font-weight:700;color:#d6d7d2;">One workspace</p>
                    <p style="margin:0;font-size:11px;color:#696b65;line-height:1.5;">Save, apply and move</p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
{_footer()}"""
    return _base(content)


def free_signup_plain(magic_link_url: str) -> str:
    return f"""\
Welcome to Lander.

Your secure sign-in link is ready:
{magic_link_url}

Open Lander to search live company-source roles, compare your resume, and keep your search in one place.

Lander — a clearer way through the job market

If you didn't request this, you can safely ignore this email.
"""


# ---------------------------------------------------------------------------
# Pro welcome
# ---------------------------------------------------------------------------

PRO_WELCOME_SUBJECT = "Welcome to Lander Pro"

PRO_UNLOCKED = "Deeper resume matching · Complete job intelligence · Expanded Career Agent access"


def pro_welcome_html(dashboard_url: str) -> str:
    url_escaped = _h.escape(dashboard_url)
    content = f"""\
{_logo_row(badge="PRO")}
          <tr>
            <td class="email-pad" style="padding:34px 40px 0;">
              <p style="margin:0 0 14px;font-size:10px;font-weight:700;color:#b9dd37;text-transform:uppercase;letter-spacing:1.5px;">Subscription active</p>
              <h1 class="email-title" style="margin:0 0 12px;font-size:38px;font-weight:800;color:#f5f5f5;letter-spacing:-1.5px;line-height:1.04;">
                Lander Pro is ready.
              </h1>
              <p style="margin:0;font-size:15px;color:#9b9d96;line-height:1.65;">
                Your workspace now has the complete intelligence layer. Pick up exactly where you left off.
              </p>
            </td>
          </tr>
          <tr>
            <td class="email-pad" style="padding:28px 40px 0;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td align="center" style="border-radius:10px;background-color:#d4ff3a;">
                    <a class="email-button" href="{url_escaped}"
                       style="display:block;padding:16px 24px;font-size:15px;font-weight:800;color:#0a0a0a;text-align:center;text-decoration:none;letter-spacing:-0.2px;">
                      Open your workspace &nbsp;&rarr;
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

Lander — a clearer way through the job market

Questions? Reply to this email.
"""
