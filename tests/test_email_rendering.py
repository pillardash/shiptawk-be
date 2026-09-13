from app.services.email.rendering import render_email
from app.services.email.templates import password_reset_template


def test_render_email_builds_semantic_accessible_responsive_document() -> None:
    content = render_email(
        subject="Review your account",
        preheader="A short summary for inbox previews.",
        heading="Review your account",
        body="Use the button below to continue.",
        action_label="Continue",
        action_url='https://app.example.com/review?token="x"&next=<home>',
        footer="You can ignore this message.",
    )

    assert content.html is not None
    assert '<html lang="en">' in content.html
    assert '<main role="main"' in content.html
    assert "<h1" in content.html
    assert 'aria-label="Continue"' in content.html
    assert "@media only screen and (max-width: 600px)" in content.html
    assert "background-color: #45B9DF" in content.html
    assert "color: #102A33" in content.html
    assert "&quot;x&quot;" in content.html
    assert "&lt;home&gt;" in content.html
    assert "https://app.example.com/review" in content.text


def test_password_reset_template_uses_shared_renderer() -> None:
    content = password_reset_template("https://app.example.com/reset")

    assert content.html is not None
    assert '<html lang="en">' in content.html
    assert "Reset password" in content.html
    assert "background-color: #45B9DF" in content.html
