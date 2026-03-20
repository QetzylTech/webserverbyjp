"""Bootstrap configuration helpers for the web app."""

from dataclasses import dataclass
from pathlib import Path
import secrets
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from app.bootstrap.config_loader import load_web_config
from app.core.config import apply_default_flask_config, resolve_secret_key
from app.ports import ports
from app.services import setup_service


@dataclass(frozen=True)
class WebAppBootstrapConfig:
    app_dir: Path
    app_config: object
    web_conf_path: Path
    raw_values: Mapping[str, object]
    setup_required_state: dict[str, object]
    display_tz: ZoneInfo


def load_bootstrap_config() -> WebAppBootstrapConfig:
    app_dir = Path(__file__).resolve().parent.parent.parent
    app_config = load_web_config(
        app_dir,
        default_backup_dir=Path(ports.service_control.default_backup_dir()),
        default_minecraft_root=Path(ports.service_control.default_minecraft_root()),
    )
    web_conf_path = app_config.web_conf_path
    raw_values = dict(app_config.raw_values)

    setup_status = setup_service.assess_setup_requirement(web_conf_path, raw_values)
    setup_required_state = {
        "required": bool(setup_status.get("required")),
        "reasons": list(setup_status.get("reasons", [])),
        "mode": str(setup_status.get("mode", "full") or "full"),
    }

    # "PST" here refers to Philippines Standard Time (UTC+8), not Pacific Time.
    display_tz_name = app_config.display_tz_name
    try:
        display_tz = ZoneInfo(display_tz_name)
    except Exception:
        display_tz_name = "Asia/Manila"
        display_tz = ZoneInfo("Asia/Manila")

    try:
        ports.service_control.apply_process_timezone(display_tz_name)
    except Exception:
        pass

    return WebAppBootstrapConfig(
        app_dir=app_dir,
        app_config=app_config,
        web_conf_path=web_conf_path,
        raw_values=raw_values,
        setup_required_state=setup_required_state,
        display_tz=display_tz,
    )


def configure_flask_app(app: Any, *, app_config: Any, setup_required_state: Mapping[str, object]) -> None:
    if setup_required_state.get("required"):
        app.config["SECRET_KEY"] = secrets.token_hex(32)
    else:
        def _secret_key_getter(_key: str, default: str = "") -> str:
            return str(app_config.secret_key_value or default)

        app.config["SECRET_KEY"] = resolve_secret_key(
            _secret_key_getter,
            "MCWEB_SECRET_KEY",
        )
    apply_default_flask_config(app)
