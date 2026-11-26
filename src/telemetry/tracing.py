import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from opentelemetry import trace
    from azure.monitor.opentelemetry import configure_azure_monitor
    HAS_AZURE_MONITOR = True
except ImportError:
    HAS_AZURE_MONITOR = False

_AZURE_MONITOR_CONFIGURED = False


def init_azure_app_insights() -> bool:
    """Configure Azure Application Insights OpenTelemetry exporter once when connection string is set."""
    global _AZURE_MONITOR_CONFIGURED
    if _AZURE_MONITOR_CONFIGURED:
        return True
    conn_str = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if HAS_AZURE_MONITOR and conn_str:
        try:
            configure_azure_monitor(connection_string=conn_str)
            _AZURE_MONITOR_CONFIGURED = True
            return True
        except Exception:
            return False
    return False


class PipelineTracer:
    """
    Records timing for every step in the pipeline and sends OpenTelemetry traces
    directly to Azure Application Insights.
    """

    def __init__(self, complaint_id: str):
        self.complaint_id = str(complaint_id)
        self.spans: List[Dict[str, Any]] = []
        self.start_time = time.time()
        self.current_span_start: Optional[float] = None
        self._otel_span = None
        self._otel_tracer = trace.get_tracer("complaint_resolution_copilot", "1.2.0") if init_azure_app_insights() else None

    def start_span(self, name: str, attributes: Optional[Dict[str, Any]] = None):
        self.current_span_start = time.time()
        attrs = attributes or {}
        if self._otel_tracer:
            self._otel_span = self._otel_tracer.start_span(name)
            self._otel_span.set_attribute("complaint_id", self.complaint_id)
            for k, v in attrs.items():
                self._otel_span.set_attribute(str(k), str(v))

        self.spans.append({
            "step_name": name,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "attributes": attrs,
        })

    def end_span(self, status: str = "SUCCESS", output: Optional[Dict[str, Any]] = None, error: Optional[str] = None):
        duration_ms = round((time.time() - (self.current_span_start or time.time())) * 1000, 2)
        if self._otel_span:
            self._otel_span.set_attribute("status", status)
            self._otel_span.set_attribute("duration_ms", duration_ms)
            if output:
                for k, v in output.items():
                    self._otel_span.set_attribute(f"output.{k}", str(v))
            if error:
                self._otel_span.set_attribute("error", error)
            self._otel_span.end()
            self._otel_span = None

        if self.spans:
            last_span = self.spans[-1]
            last_span["status"] = status
            last_span["duration_ms"] = duration_ms
            last_span["end_time"] = datetime.now(timezone.utc).isoformat()
            if output:
                last_span["output_summary"] = output
            if error:
                last_span["error"] = error

    def get_full_trace(self) -> List[Dict[str, Any]]:
        return self.spans

    def get_total_latency_ms(self) -> float:
        return round((time.time() - self.start_time) * 1000, 2)
