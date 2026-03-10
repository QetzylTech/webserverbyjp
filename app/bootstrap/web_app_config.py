"""Bootstrap configuration helpers for the web app."""

from dataclasses import dataclass
from pathlib import Path
import os
import secrets
import time
from zoneinfo import ZoneInfo

from app.bootstrap.config_loader import load_web_config
from app.core.config import apply_default_flask_config, resolve_secret_key
from app.infrastructure.adapters import PlatformServiceControlAdapter
from app.services import setup_service


@dataclass(frozen=True)
class WebAppBootstrapConfig:
    app_dir: Path
    app_config: object
    web_conf_path: Path
    raw_values: dict
    setup_required_state: dict
    display_tz: ZoneInfo


def load_bootstrap_config():
    app_dir = Path(__file__).resolve().parent.parent.parent
    platform_service_control = PlatformServiceControlAdapter()
    app_config = load_web_config(
        app_dir,
        default_backup_dir=Path(platform_service_control.default_backup_dir()),
        default_minecraft_root=Path(platform_service_control.default_minecraft_root()),
    )
    web_conf_path = app_config.web_conf_path
    raw_values = app_config.raw_values

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

    os.environ["TZ"] = display_tz_name
    if hasattr(time, "tzset"):
        try:
            time.tzset()
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


def configure_flask_app(app, *, app_config, setup_required_state):
    if setup_required_state.get("required"):
        app.config["SECRET_KEY"] = secrets.token_hex(32)
    else:
        app.config["SECRET_KEY"] = resolve_secret_key(
            lambda _k, _d="": app_config.secret_key_value or _d,
            "MCWEB_SECRET_KEY",
        )
    apply_default_flask_config(app)
