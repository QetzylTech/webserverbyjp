(function (global) {
    function createModalsController(options) {
        const dom = options?.dom || {};
        const actions = options?.actions || {};
        const DEFAULT_PASSWORD_TEXT = "Enter sudo password to continue.";

        let pendingAction = null;

        function defaultPasswordText(actionRequest) {
            if (actionRequest?.kind === "restore") {
                return "Enter sudo password to restore this backup.";
            }
            return "Enter sudo password to download this backup.";
        }

        function resetPasswordModal(actionRequest) {
            if (dom.passwordTitle) {
                dom.passwordTitle.textContent = actionRequest?.kind === "restore" ? "Confirm Restore" : "Enter Password";
            }
            if (dom.passwordText) {
                dom.passwordText.textContent = defaultPasswordText(actionRequest) || DEFAULT_PASSWORD_TEXT;
            }
            if (dom.passwordSubmit) {
                dom.passwordSubmit.textContent = actionRequest?.kind === "restore" ? "Restore" : "Continue";
            }
            if (dom.passwordImage) {
                dom.passwordImage.hidden = true;
            }
            if (dom.passwordError) {
                dom.passwordError.textContent = "";
                dom.passwordError.hidden = true;
            }
        }

        function openPasswordModal(actionRequest) {
            if (!dom.passwordModal || !dom.passwordInput) return;
            pendingAction = actionRequest || null;
            resetPasswordModal(actionRequest);
            dom.passwordInput.value = "";
            dom.passwordModal.classList.add("open");
            dom.passwordModal.setAttribute("aria-hidden", "false");
            dom.passwordInput.focus();
        }

        function showPasswordError(actionRequest, message) {
            if (!dom.passwordModal || !dom.passwordInput) return;
            pendingAction = actionRequest || null;
            closeSuccessModal();
            closeErrorModal();
            if (dom.passwordTitle) {
                dom.passwordTitle.textContent = "Action Rejected";
            }
            if (dom.passwordText) {
                dom.passwordText.textContent = "Password incorrect. Enter sudo password to try again.";
            }
            if (dom.passwordSubmit) {
                dom.passwordSubmit.textContent = actionRequest?.kind === "restore" ? "Restore" : "Continue";
            }
            if (dom.passwordImage) {
                dom.passwordImage.hidden = false;
            }
            if (dom.passwordError) {
                dom.passwordError.textContent = message || "Password incorrect.";
                dom.passwordError.hidden = false;
            }
            dom.passwordInput.value = "";
            dom.passwordModal.classList.add("open");
            dom.passwordModal.setAttribute("aria-hidden", "false");
            dom.passwordInput.focus();
        }

        function closePasswordModal() {
            if (!dom.passwordModal) return;
            dom.passwordModal.classList.remove("open");
            dom.passwordModal.setAttribute("aria-hidden", "true");
            if (dom.passwordInput) dom.passwordInput.value = "";
            resetPasswordModal(pendingAction);
            pendingAction = null;
        }

        function showSuccessModal(message) {
            closePasswordModal();
            closeErrorModal();
            if (!dom.successModal || !dom.successModalText) return;
            dom.successModalText.textContent = message || "Action completed successfully.";
            dom.successModal.classList.add("open");
            dom.successModal.setAttribute("aria-hidden", "false");
        }

        function closeSuccessModal() {
            if (!dom.successModal) return;
            dom.successModal.classList.remove("open");
            dom.successModal.setAttribute("aria-hidden", "true");
        }

        function showErrorModal(message, options = {}) {
            closePasswordModal();
            closeSuccessModal();
            const code = String(options.errorCode || "").trim();
            if (!dom.errorModal || !dom.errorModalText) {
                actions.setDownloadError?.(message || "Action failed.");
                return;
            }
            const detail = code ? `${message || "Action failed."} (error: ${code})` : (message || "Action failed.");
            dom.errorModalText.textContent = detail;
            dom.errorModal.classList.add("open");
            dom.errorModal.setAttribute("aria-hidden", "false");
        }

        function closeErrorModal() {
            if (!dom.errorModal) return;
            dom.errorModal.classList.remove("open");
            dom.errorModal.setAttribute("aria-hidden", "true");
        }

        function bindEvents(listen, handlers = {}) {
            if (dom.passwordCancel) {
                listen(dom.passwordCancel, "click", () => closePasswordModal());
            }
            if (dom.passwordModal) {
                listen(dom.passwordModal, "click", (event) => {
                    if (event.target === dom.passwordModal) {
                        closePasswordModal();
                    }
                });
            }
            if (dom.passwordSubmit) {
                listen(dom.passwordSubmit, "click", () => {
                    if (!dom.passwordInput) return;
                    const password = (dom.passwordInput.value || "").trim();
                    if (!password) return;
                    const action = pendingAction;
                    closePasswordModal();
                    if (typeof handlers.onPasswordSubmit === "function") {
                        handlers.onPasswordSubmit(action, password);
                    }
                });
            }
            if (dom.passwordInput) {
                listen(dom.passwordInput, "keydown", (event) => {
                    if (event.key === "Enter" && dom.passwordSubmit) {
                        event.preventDefault();
                        dom.passwordSubmit.click();
                    }
                });
            }

            if (dom.successModal) {
                listen(dom.successModal, "click", (event) => {
                    if (event.target === dom.successModal) {
                        closeSuccessModal();
                    }
                });
            }
            if (dom.successModalOk) {
                listen(dom.successModalOk, "click", () => closeSuccessModal());
            }
            if (dom.errorModal) {
                listen(dom.errorModal, "click", (event) => {
                    if (event.target === dom.errorModal) {
                        closeErrorModal();
                    }
                });
            }
            if (dom.errorModalOk) {
                listen(dom.errorModalOk, "click", () => closeErrorModal());
            }
        }

        return {
            openPasswordModal,
            showPasswordError,
            showSuccessModal,
            showErrorModal,
            closeSuccessModal,
            closeErrorModal,
            bindEvents,
        };
    }

    global.MCWebFilePageModals = Object.assign({}, global.MCWebFilePageModals || {}, {
        createModalsController,
    });
})(window);
