from django.template.loader import get_template
from django.test import Client, TestCase, override_settings


class NotFoundPageTests(TestCase):
    """Django only renders templates/404.html when DEBUG=False (with DEBUG=True
    it shows its own technical debug page instead), so this has to force
    DEBUG off to actually exercise the custom template."""

    @override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"])
    def test_unknown_url_renders_custom_404_page(self):
        resp = Client().get("/this-page-does-not-exist/")
        self.assertEqual(resp.status_code, 404)
        self.assertContains(resp, "404", status_code=404)
        self.assertContains(resp, "This page doesn't exist", status_code=404)
        self.assertContains(resp, "Go to Homepage", status_code=404)


class ServerErrorPageTests(TestCase):
    """Django's server_error view renders 500.html with template.render() —
    no context, no request — so the template can't lean on context
    processors (SITE_NAME, request.user, etc.) or {% url %} names that
    happen to need a request. Render it exactly that way here to catch a
    template that only works by accident when a request happens to be
    available (e.g. in a dev-server sanity check)."""

    def test_renders_with_no_context_or_request(self):
        html = get_template("500.html").render()
        self.assertIn("500", html)
        self.assertIn("Something went wrong", html)
        self.assertIn("Try again", html)
        self.assertIn("Go to Homepage", html)
        # Context processors didn't run, so any accidental {{ SITE_NAME }} or
        # similar would render empty rather than raise — this string is
        # meaningless in a template and would only appear on such a bug.
        self.assertNotIn("{{", html)
