import unittest
from pathlib import Path


class TemplateContractsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parents[1]
        cls.templates = cls.repo_root / "templates"
        cls.static = cls.repo_root / "static"

    def _read(self, rel_path):
        return (self.repo_root / rel_path).read_text(encoding="utf-8", errors="ignore")

    def test_home_template_has_required_panes_stats_inputs_and_modals(self):
        text = self._read("templates/home.html")
        required_tokens = [
            'id="control-panel-title"',
            'id="start-btn"',
            'id="stop-btn"',
            'id="backup-btn"',
            'id="log-source"',
            'id="rcon-command"',
            'id="rcon-submit"',
            'id="service-status"',
            'id="players-online"',
            'id="tick-rate"',
            'id="backup-status"',
            'id="last-backup-time"',
            'id="next-backup-time"',
            'id="backups-status"',
            'id="sudo-modal"',
            'id="message-modal"',
            'id="success-modal"',
            'id="error-modal"',
            'name="csrf_token"',
        ]
        for token in required_tokens:
            self.assertIn(token, text)

    def test_maintenance_template_has_scopes_views_actions_and_modals(self):
        text = self._read("templates/maintenance.html")
        required_tokens = [
            'id="maint-scope-backups"',
            'id="maint-scope-stale"',
            'id="maint-open-rules"',
            'id="maint-open-history"',
            'id="maint-open-manual"',
            'id="cleanup-file-list"',
            'id="maintenance-view-rules"',
            'id="maintenance-view-history"',
            'id="maintenance-view-manual"',
            'id="run-rule-delete-btn"',
            'id="run-manual-delete-btn"',
            'id="maintenance-password-modal"',
            'id="maintenance-dry-run-modal"',
            'id="maintenance-complete-modal"',
            'id="maintenance-error-modal"',
            'id="maintenance-ack-suggest-modal"',
            'id="maintenance-csrf-token"',
        ]
        for token in required_tokens:
            self.assertIn(token, text)

    def test_files_template_has_backup_restore_controls_and_modals(self):
        text = self._read("templates/files.html")
        required_tokens = [
            'id="backup-sort"',
            'class="backup-filter"',
            "file-download-btn",
            "file-restore-btn",
            'id="backup-restore-controls"',
            'id="backup-restore-start"',
            'id="backup-restore-cancel"',
            'id="download-password-modal"',
            'id="message-modal"',
            'id="success-modal"',
            'id="error-modal"',
            'data-log-source="crash"',
        ]
        for token in required_tokens:
            self.assertIn(token, text)

    def test_documentation_template_has_main_content_and_toc(self):
        text = self._read("templates/documentation.html")
        required_tokens = [
            'id="tocSidebar"',
            'id="tocSidebarBody"',
            'id="content"',
            'id="stickyHeader"',
            'id="stickyMenu"',
            'id="backToTop"',
        ]
        for token in required_tokens:
            self.assertIn(token, text)

    def test_nav_template_has_navigation_and_github_link(self):
        text = self._read("templates/partials/nav.html")
        required_tokens = [
            'id="sidebar-title"',
            'id="nav-home-link"',
            'id="nav-backups-link"',
            'id="nav-maintenance-link"',
            'href="/readme"',
            'href="https://github.com/QetzylTech/Marites"',
        ]
        for token in required_tokens:
            self.assertIn(token, text)

    def test_app_shell_template_preserves_content_container_class(self):
        text = self._read("templates/app_shell.html")
        self.assertIn('id="mcweb-app-content" class="content"', text)
    def test_frontend_scripts_send_csrf_headers_for_sensitive_calls(self):
        files_js = self._read("static/file_browser_page.js")
        home_js = self._read("static/dashboard_home_page.js")
        maint_js = self._read("static/maintenance_page.js")
        self.assertIn("X-CSRF-Token", files_js)
        self.assertIn("X-CSRF-Token", home_js)
        self.assertIn("X-CSRF-Token", maint_js)


if __name__ == "__main__":
    unittest.main()
