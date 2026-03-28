import unittest
from pathlib import Path
import re


class TemplateContractsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parents[1]
        cls.templates = cls.repo_root / "templates"
        cls.static = cls.repo_root / "static"

    def _read(self, rel_path):
        return (self.repo_root / rel_path).read_text(encoding="utf-8", errors="ignore")

    def test_home_fragment_has_required_panes_stats_inputs_and_modals(self):
        text = self._read("templates/fragments/home_fragment.html")
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
            '"initialLogs": initial_logs or {}',
        ]
        for token in required_tokens:
            self.assertIn(token, text)

    def test_maintenance_fragment_has_scopes_views_actions_and_modals(self):
        text = self._read("templates/fragments/maintenance_fragment.html")
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

    def test_files_fragment_has_backup_restore_controls_and_modals(self):
        text = self._read("templates/fragments/files_fragment.html")
        required_tokens = [
            'id="backup-sort"',
            'class="backup-filter"',
            "file-download-btn",
            "file-restore-btn",
            'id="backup-restore-controls"',
            'id="backup-restore-start"',
            'id="backup-restore-cancel"',
            'id="download-password-modal"',
            'id="download-password-image"',
            'id="download-password-error"',
            'id="success-modal"',
            'id="error-modal"',
            'data-log-source="crash"',
        ]
        for token in required_tokens:
            self.assertIn(token, text)
        self.assertNotIn('id="message-modal"', text)

    def test_documentation_fragment_has_main_content_and_toc(self):
        text = self._read("templates/fragments/documentation_fragment.html")
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

    def test_home_error_modal_close_handler_restores_hidden_state(self):
        home_js = self._read("static/dashboard_home_page.js")
        self.assertRegex(
            home_js,
            re.compile(
                r"function closeErrorModal\(\)\s*\{[\s\S]*?setAttribute\(\"aria-hidden\", \"true\"\)",
                re.MULTILINE,
            ),
        )

    def test_app_shell_restores_preferred_home_log_stream_from_shell_state(self):
        shell_js = self._read("static/app_shell.js")
        self.assertIn("resolvePreferredHomeLogSource", shell_js)
        self.assertIn("activateHomeLogStream(resolvePreferredHomeLogSource())", shell_js)
        self.assertIn('shellState.homeView?.selectedLogSource', shell_js)

    def test_file_page_password_rejection_reuses_password_modal(self):
        files_js = self._read("static/file_page_modals.js")
        self.assertIn("showPasswordError", files_js)
        self.assertIn('dom.passwordTitle.textContent = "Action Rejected"', files_js)
        self.assertIn("dom.passwordImage.hidden = false", files_js)

    def test_panel_settings_device_cards_edit_inline_without_expanded_editor(self):
        panel_js = self._read("static/panel_settings.js")
        panel_css = self._read("static/panel_settings.css")
        self.assertIn("device-machine-edit-field", panel_js)
        self.assertIn('el.hasAttribute("data-device-last-seen")', panel_js)
        self.assertNotIn("data-device-edit-last-seen", panel_js)
        self.assertIn('card.classList.toggle("is-editing", isEditing)', panel_js)
        self.assertNotIn("data-device-editor", panel_js)
        self.assertNotIn("deviceInlineInputStyle", panel_js)
        self.assertNotIn("device-machine-address-input", panel_js)
        self.assertNotIn("device-machine-edit-readonly", panel_js)
        self.assertIn(".device-machine-edit-field[hidden]", panel_css)

    def test_panel_settings_security_paths_timezone_and_csv_layout_use_cards(self):
        fragment = self._read("templates/fragments/panel_settings_fragment.html")
        panel_js = self._read("static/panel_settings.js")
        self.assertIn('class="settings-card-grid"', fragment)
        self.assertIn('class="settings-card"', fragment)
        self.assertIn('class="settings-dropzone settings-dropzone--full"', fragment)
        self.assertIn('class="device-map-upload-row"', fragment)
        self.assertIn("syncCsvDropzoneState", panel_js)
        self.assertIn("selectedCsvFile = file", panel_js)
        self.assertIn("if (assignedToInput)", panel_js)

    def test_cleanup_rules_only_style_inputs_in_edit_mode(self):
        rules_js = self._read("static/maintenance_page_rules.js")
        rules_css = self._read("static/maintenance_page.css")
        self.assertIn('class="ui-card-input rule-inline-edit-input"', rules_js)
        self.assertIn('class="rule-inline-control" data-rule-field="time_based.repeat_mode"', rules_js)
        self.assertIn("MCWebEnhanceCustomSelects(dom.rulesCardList)", rules_js)
        self.assertIn("#rules-card-list .rule-inline-sentence .ui-select", rules_css)
        self.assertNotIn('class="ui-card-input rule-inline-control" data-rule-field="time_based.repeat_mode"', rules_js)

    def test_offline_recovery_ignores_aborted_fetches_and_probes_before_banner(self):
        offline_js = self._read("static/offline_recovery.js")
        self.assertIn('if (err && err.name === "AbortError")', offline_js)
        self.assertIn('setOfflineIfUnreachable("fetch_failed")', offline_js)
        self.assertNotIn('setOfflineActive("fetch_failed")', offline_js)

    def test_service_worker_respects_static_asset_versions_and_prefers_network(self):
        sw_js = self._read("static/service_worker.js")
        self.assertNotIn("stripSearch(", sw_js)
        self.assertNotIn("ignoreSearch: true", sw_js.split("async function matchStatic", 1)[1].split("async function handleNavigate", 1)[0])
        self.assertRegex(
            sw_js,
            re.compile(
                r"async function handleStatic\(request\)\s*\{\s*try\s*\{\s*const response = await fetch\(request\);",
                re.MULTILINE,
            ),
        )
        self.assertIn('await cache.put(request, response.clone())', sw_js)

    def test_global_input_classes_override_generic_input_reset(self):
        global_css = self._read("static/global.css")
        self.assertIn('input.ui-text-input:not([type="checkbox"])', global_css)
        self.assertIn('input.ui-card-input:not([type="checkbox"])', global_css)
        self.assertIn("textarea.ui-card-input", global_css)

    def test_shell_and_pages_register_unsaved_changes_guard(self):
        shell_js = self._read("static/app_shell.js")
        maintenance_js = self._read("static/maintenance_page.js")
        panel_js = self._read("static/panel_settings.js")
        self.assertIn("setUnsavedChangesGuard", shell_js)
        self.assertIn("Discard Changes", shell_js)
        self.assertIn("Save Changes", shell_js)
        self.assertIn("Go Back to Editing", shell_js)
        self.assertIn('pageKey: "maintenance"', maintenance_js)
        self.assertIn("hasUnsavedRuleChanges", maintenance_js)
        self.assertIn('pageKey: "panel_settings"', panel_js)
        self.assertIn("saveAllUnsavedChanges", panel_js)

    def test_shell_uses_metrics_sse_without_http_poll_fallback(self):
        shell_js = self._read("static/app_shell.js")
        self.assertIn('new EventSource(metricsStreamPath())', shell_js)
        self.assertIn("function subscribeMetrics(listener)", shell_js)
        self.assertNotIn('fetchJson("/metrics")', shell_js)
        self.assertNotIn("startMetricsFallbackPoll", shell_js)
        self.assertIn('nextStatus === "running"', shell_js)
        self.assertIn('nextStatus === "off"', shell_js)
        self.assertIn('previousStatus === "running" || previousStatus === "shutting down"', shell_js)
        self.assertIn('stream.addEventListener("batch"', shell_js)
        self.assertIn('stream.addEventListener("snapshot"', shell_js)
        self.assertIn('broadcast.send({ type: "log_lines"', shell_js)

    def test_home_page_consumes_shell_metrics_stream_without_direct_poll(self):
        home_js = self._read("static/dashboard_home_page.js")
        self.assertIn('homeMetricsUnsubscribe = shell.subscribeMetrics((payload) => {', home_js)
        self.assertIn('operationUpdatesUnsubscribe = shell.subscribeOperationUpdates((operation) => {', home_js)
        self.assertIn("cacheMetricsSnapshot(payload)", home_js)
        self.assertIn("applyMetricsData(payload)", home_js)
        self.assertNotIn('fetch("/metrics"', home_js)
        self.assertNotIn("/home-heartbeat", home_js)
        self.assertNotIn("/operation-status/", home_js)
        self.assertNotIn("scheduleLiveMetricsPoll({ immediate: true })", home_js)

    def test_file_page_uses_shell_operation_stream_and_restore_sse(self):
        file_js = self._read("static/file_browser_page.js")
        data_js = self._read("static/file_page_data_runtime.js")
        shell_js = self._read("static/app_shell.js")
        self.assertIn('new EventSource("/operation-stream")', shell_js)
        self.assertIn("subscribeOperationUpdates", shell_js)
        self.assertIn('stream.addEventListener("status", handleRestoreStreamStatus);', file_js)
        self.assertIn("/file-page-stream/", file_js)
        self.assertIn("/log-files-stream/", file_js)
        self.assertNotIn("/file-page-heartbeat", file_js)
        self.assertNotIn("/restore-status", file_js)
        self.assertNotIn("/operation-status/", file_js)
        self.assertIn('const url = new URL("/stream/restore_logs", window.location.origin);', data_js)

    def test_maintenance_page_uses_state_stream_without_interval_polling(self):
        maint_js = self._read("static/maintenance_page.js")
        self.assertIn('const streamUrl = new URL("/maintenance-stream", window.location.origin);', maint_js)
        self.assertIn('const stream = new EventSource(streamUrl.toString());', maint_js)
        self.assertNotIn("REFRESH_INTERVAL_MS", maint_js)
        self.assertNotIn("startAutoRefresh", maint_js)
        self.assertNotIn("stopAutoRefresh", maint_js)

if __name__ == "__main__":
    unittest.main()
