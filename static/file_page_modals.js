(function (global) {
    function createModalsController(options) {
        const dom = options?.dom || {};
        const actions = options?.actions || {};

        let pendingAction = null;
        let reloadAfterMessageClose = false;

        function openPasswordModal(actionRequest) {
            if (!dom.passwordModal || !dom.passwordInput) return;
            pendingAction = actionRequest || null;
            if (dom.passwordTitle) {
                dom.passwordTitle.textContent = actionRequest?.kind === "restore" ? "Confirm Restore" : "Enter Password";
            }
            if (dom.passwordText) {
                if (actionRequest?.kind === "restore") {
                    const restoreDisplay = actionRequest.displayName || actionRequest.filename || "selected backup";
                    dom.passwordText.textContent = `Enter sudo password to restore ${restoreDisplay}. This will create a new world folder and switch level-name.`;
                } else {
                    dom.passwordText.textContent = "Enter sudo password to download this backup.";
                }
            }
            if (dom.passwordSubmit) {
                dom.passwordSubmit.textContent = actionRequest?.kind === "restore" ? "Restore" : "Continue";
            }
            dom.passwordInput.value = actionRequest?.prefillPassword || "";
            dom.passwordModal.classList.add("open");
            dom.passwordModal.setAttribute("aria-hidden", "false");
            dom.passwordInput.focus();
        }

        function closePasswordModal() {
            if (!dom.passwordModal) return;
            dom.passwordModal.classList.remove("open");
            dom.passwordModal.setAttribute("aria-hidden", "true");
            if (dom.passwordInput) dom.passwordInput.value = "";
            if (dom.passwordSubmit) dom.passwordSubmit.textContent = "Continue";
            pendingAction = null;
        }

        function popPendingAction() {
            const action = pendingAction;
            pendingAction = null;
            return action;
        }

        function showMessageModal(message, options = {}) {
            closePasswordModal();
            closeSuccessModal();
            closeErrorModal();
            if (!dom.messageModal || !dom.messageModalText) return;
            reloadAfterMessageClose = !!options.reloadAfterClose;
            dom.messageModalText.textContent = message || "";
            dom.messageModal.classList.add("open");
            dom.messageModal.setAttribute("aria-hidden", "false");
        }

        function closeMessageModal() {
            if (!dom.messageModal) return;
            dom.messageModal.classList.remove("open");
            dom.messageModal.setAttribute("aria-hidden", "true");
            if (reloadAfterMessageClose) {
                reloadAfterMessageClose = false;
                global.location.reload();
            }
        }

        function showSuccessModal(message) {
            closePasswordModal();
            closeMessageModal();
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
                    const action = popPendingAction();
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

            if (dom.messageModal) {
                listen(dom.messageModal, "click", (event) => {
                    if (event.target === dom.messageModal) {
                        closeMessageModal();
                    }
                });
            }
            if (dom.messageModalOk) {
                listen(dom.messageModalOk, "click", () => closeMessageModal());
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
            showMessageModal,
            showSuccessModal,
            showErrorModal,
            closeMessageModal,
            closeSuccessModal,
            closeErrorModal,
            bindEvents,
        };
    }

    global.MCWebFilePageModals = Object.assign({}, global.MCWebFilePageModals || {}, {
        createModalsController,
    });
})(window);
