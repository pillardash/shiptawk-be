from collections.abc import Sequence
from dataclasses import dataclass
from html import escape

from app.services.email.base import EmailContent


@dataclass(frozen=True, slots=True)
class EmailSection:
    heading: str
    body: str


def render_email(
    *,
    subject: str,
    preheader: str,
    heading: str,
    body: str,
    action_label: str,
    action_url: str,
    footer: str | None = None,
    sections: Sequence[EmailSection] = (),
) -> EmailContent:
    text_parts = [heading, "", body]
    for section in sections:
        text_parts.extend(("", section.heading, section.body))
    text_parts.extend(("", f"{action_label}: {action_url}"))
    if footer:
        text_parts.extend(("", footer))

    escaped_url = escape(action_url, quote=True)
    footer_html = (
        f'<p style="margin:24px 0 0;color:#455A64;font-size:14px;line-height:1.5">'
        f"{escape(footer)}</p>"
        if footer
        else ""
    )
    sections_html = "".join(
        '<section style="margin:24px 0 0;">'
        f'<h2 style="margin:0 0 8px;color:#102A33;font-size:20px;line-height:1.3;">'
        f"{escape(section.heading)}</h2>"
        f'<p style="margin:0;color:#263F47;font-size:16px;line-height:1.6;white-space:pre-line;">'
        f"{escape(section.body)}</p></section>"
        for section in sections
    )
    card_style = (
        "box-sizing:border-box;max-width:600px;margin:0 auto;padding:40px;"
        "background-color:#FFFFFF;border-radius:12px;"
    )
    action_style = (
        "display:inline-block;padding:14px 22px;background-color: #45B9DF;"
        "color: #102A33;font-weight:700;text-decoration:none;border-radius:8px;"
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(subject)}</title>
  <style>
    @media only screen and (max-width: 600px) {{
      .email-card {{ padding: 24px 20px !important; }}
      .email-action {{ display: block !important; text-align: center !important; }}
    }}
  </style>
</head>
<body style="margin:0;background-color:#F4F8FA;color:#102A33;font-family:Arial,sans-serif;">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">
    {escape(preheader)}
  </div>
  <main role="main" aria-label="{escape(subject, quote=True)}" style="padding:32px 12px;">
    <section class="email-card" style="{card_style}">
      <h1 style="margin:0 0 16px;color:#102A33;font-size:28px;line-height:1.25;">
        {escape(heading)}
      </h1>
      <p style="margin:0;color:#263F47;font-size:16px;line-height:1.6;">{escape(body)}</p>
      {sections_html}
      <p style="margin:28px 0 0;">
        <a class="email-action"
           href="{escaped_url}"
           aria-label="{escape(action_label, quote=True)}"
           style="{action_style}">{escape(action_label)}</a>
      </p>
      {footer_html}
    </section>
  </main>
</body>
</html>"""
    return EmailContent(subject=subject, text="\n".join(text_parts), html=html)
