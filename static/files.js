    (function () {
        const darkModeQuery = window.matchMedia("(prefers-color-scheme: dark)");
        function applyThemePreference() {
            document.documentElement.classList.toggle("theme-dark", darkModeQuery.matches);
        }
        applyThemePreference();
        if (darkModeQuery.addEventListener) {
            darkModeQuery.addEventListener("change", applyThemePreference);
        } else if (darkModeQuery.addListener) {
            darkModeQuery.addListener(applyThemePreference);
        }

        const __MCWEB_FILES_CONFIG = window.__MCWEB_FILES_CONFIG || {};
        const csrfToken = __MCWEB_FILES_CONFIG.csrfToken ?? "";
        const FILE_PAGE_HEARTBEAT_INTERVAL_MS = Number(__MCWEB_FILES_CONFIG.heartbeatIntervalMs || 10000);
        function sendFilePageHeartbeat() {
            fetch("/file-page-heartbeat", {
                method: "POST",
                headers: {
                    "X-Requested-With": "XMLHttpRequest",
                    "X-CSRF-Token": csrfToken || "",
                },
                cache: "no-store",
                keepalive: true,
            }).catch(() => {});
        }
        sendFilePageHeartbeat();
        window.setInterval(sendFilePageHeartbeat, FILE_PAGE_HEARTBEAT_INTERVAL_MS);

        const toggle = document.getElementById("nav-toggle");
        const sidebar = document.getElementById("side-nav");
        const backdrop = document.getElementById("nav-backdrop");
        if (!toggle || !sidebar || !backdrop) return;

        function closeNav() {
            sidebar.classList.remove("open");
            backdrop.classList.remove("open");
            toggle.classList.remove("nav-open");
            toggle.setAttribute("aria-expanded", "false");
        }

        function toggleNav() {
            const nextOpen = !sidebar.classList.contains("open");
            sidebar.classList.toggle("open", nextOpen);
            backdrop.classList.toggle("open", nextOpen);
            toggle.classList.toggle("nav-open", nextOpen);
            toggle.setAttribute("aria-expanded", nextOpen ? "true" : "false");
        }

        toggle.addEventListener("click", toggleNav);
        backdrop.addEventListener("click", closeNav);
        window.addEventListener("resize", function () {
            if (window.innerWidth > 1100) closeNav();
        });

        const errorBox = document.getElementById("download-error");
        const passwordModal = document.getElementById("download-password-modal");
        const passwordInput = document.getElementById("download-password-input");
        const passwordCancel = document.getElementById("download-password-cancel");
        const passwordSubmit = document.getElementById("download-password-submit");
        const messageModal = document.getElementById("message-modal");
        const messageModalText = document.getElementById("message-modal-text");
        const messageModalOk = document.getElementById("message-modal-ok");
        let pendingDownload = null;

        function setDownloadError(text) {
            if (!errorBox) return;
            if (!text) {
                errorBox.textContent = "";
                errorBox.classList.remove("open");
                return;
            }
            errorBox.textContent = text;
            errorBox.classList.add("open");
        }

        function closePasswordModal() {
            if (!passwordModal) return;
            passwordModal.classList.remove("open");
            passwordModal.setAttribute("aria-hidden", "true");
            if (passwordInput) passwordInput.value = "";
            pendingDownload = null;
        }

        function openPasswordModal(downloadRequest) {
            if (!passwordModal || !passwordInput) return;
            pendingDownload = downloadRequest;
            passwordInput.value = "";
            passwordModal.classList.add("open");
            passwordModal.setAttribute("aria-hidden", "false");
            passwordInput.focus();
        }

        function showMessageModal(message) {
            closePasswordModal();
            if (!messageModal || !messageModalText) return;
            messageModalText.textContent = message || "";
            messageModal.classList.add("open");
            messageModal.setAttribute("aria-hidden", "false");
        }

        async function runBackupDownload(downloadRequest, password) {
            const body = new URLSearchParams();
            body.set("csrf_token", csrfToken || "");
            body.set("sudo_password", password);

            let response;
            try {
                response = await fetch(downloadRequest.url, {
                    method: "POST",
                    headers: {
                        "X-Requested-With": "XMLHttpRequest",
                        "X-CSRF-Token": csrfToken || "",
                        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                    },
                    body: body.toString(),
                });
            } catch (err) {
                setDownloadError("Download failed. Please try again.");
                return;
            }

            if (!response.ok) {
                let message = "Password incorrect. Download cancelled.";
                let errorCode = "";
                try {
                    const payload = await response.json();
                    if (payload && payload.message) message = payload.message;
                    if (payload && payload.error) errorCode = payload.error;
                } catch (_) {
                    // Keep default message on non-JSON responses.
                }
                if (errorCode === "password_incorrect") {
                    showMessageModal(message);
                } else {
                    setDownloadError(message);
                }
                return;
            }

            const blob = await response.blob();
            const fileUrl = URL.createObjectURL(blob);
            const anchor = document.createElement("a");
            anchor.href = fileUrl;
            anchor.download = downloadRequest.filename;
            document.body.appendChild(anchor);
            anchor.click();
            anchor.remove();
            URL.revokeObjectURL(fileUrl);
        }

        if (passwordCancel) {
            passwordCancel.addEventListener("click", () => {
                closePasswordModal();
            });
        }
        if (passwordModal) {
            passwordModal.addEventListener("click", (event) => {
                if (event.target === passwordModal) {
                    closePasswordModal();
                }
            });
        }
        if (messageModal) {
            messageModal.addEventListener("click", (event) => {
                if (event.target === messageModal) {
                    messageModal.classList.remove("open");
                    messageModal.setAttribute("aria-hidden", "true");
                }
            });
        }
        if (messageModalOk) {
            messageModalOk.addEventListener("click", () => {
                if (!messageModal) return;
                messageModal.classList.remove("open");
                messageModal.setAttribute("aria-hidden", "true");
            });
        }
        if (passwordSubmit) {
            passwordSubmit.addEventListener("click", async () => {
                if (!passwordInput || !pendingDownload) return;
                const password = (passwordInput.value || "").trim();
                if (!password) return;
                const downloadRequest = pendingDownload;
                closePasswordModal();
                await runBackupDownload(downloadRequest, password);
            });
        }
        if (passwordInput) {
            passwordInput.addEventListener("keydown", (event) => {
                if (event.key === "Enter" && passwordSubmit) {
                    event.preventDefault();
                    passwordSubmit.click();
                }
            });
        }

        document.querySelectorAll(".file-download-btn").forEach((btn) => {
            btn.addEventListener("click", async () => {
                setDownloadError("");
                const url = btn.getAttribute("data-download-url") || "";
                const filename = btn.getAttribute("data-filename") || "backup.zip";
                if (!url) return;
                openPasswordModal({ url, filename });
            });
        });
    })();
