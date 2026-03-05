"""Dashboard runtime facade composed from query and metrics services."""

import time

from app.services.dashboard_query_service import *
from app.services.metrics_aggregator import *

from app.services import dashboard_query_service as _query

state_store_service = _query.state_store_service
_OBSERVED_OPS_CACHE = _query._OBSERVED_OPS_CACHE
