"""Default port registry used by services."""

from __future__ import annotations

from dataclasses import dataclass

from app.infrastructure.adapters import (
    FilesystemAdapter,
    PlatformBackupAdapter,
    PlatformLogAdapter,
    PlatformMetricsAdapter,
    PlatformServiceControlAdapter,
    StateStoreAdapter,
)
from app.ports.interfaces import BackupPort, FilesystemPort, LogPort, MetricsPort, ServiceControlPort, StorePort


@dataclass(frozen=True)
class Ports:
    service_control: ServiceControlPort
    metrics: MetricsPort
    log: LogPort
    backup: BackupPort
    store: StorePort
    filesystem: FilesystemPort


ports = Ports(
    service_control=PlatformServiceControlAdapter(),
    metrics=PlatformMetricsAdapter(),
    log=PlatformLogAdapter(),
    backup=PlatformBackupAdapter(),
    store=StateStoreAdapter(),
    filesystem=FilesystemAdapter(),
)
