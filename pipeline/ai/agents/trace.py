"""Agent trace storage + human-readable reports (PROMPT 1.5).

Never persist API keys, full system prompts, or image base64.
"""

from __future__ import annotations

import html
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline.ai.agents.schema import AgentEnvelope, NextAction, ReasonCode


@dataclass
class TraceEvent:
    seq: int
    agent: str
    task_type: str
    status: str
    provider: str | None
    model: str | None
    message_id: str
    parent_message_id: str | None
    input_artifact_ids: list[str]
    output_artifact_ids: list[str]
    reason_codes: list[str]
    validation_errors: list[str]
    next_action: str | None
    retry_count: int
    escalated: bool
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    latency_ms: float
    created_at: str


@dataclass
class AgentTraceStore:
    project_id: str
    trace_id: str = field(default_factory=lambda: f"tr_{uuid.uuid4().hex[:12]}")
    events: list[TraceEvent] = field(default_factory=list)
    envelopes: list[dict[str, Any]] = field(default_factory=list)

    def record_envelope(
        self,
        envelope: AgentEnvelope,
        *,
        validation_errors: list[str] | None = None,
        next_action: NextAction | str | None = None,
        extra_reason_codes: list[ReasonCode | str] | None = None,
    ) -> TraceEvent:
        codes = [c.value if isinstance(c, ReasonCode) else str(c) for c in (extra_reason_codes or [])]
        for d in envelope.decisions:
            codes.extend(r.value for r in d.reason_codes)
        action = None
        if next_action is not None:
            action = next_action.value if isinstance(next_action, NextAction) else str(next_action)
        elif envelope.recommended_next_action is not None:
            action = envelope.recommended_next_action.value

        ev = TraceEvent(
            seq=len(self.events) + 1,
            agent=envelope.sender_agent,
            task_type=envelope.task_type.value,
            status=envelope.status.value,
            provider=envelope.sender_provider,
            model=envelope.sender_model,
            message_id=envelope.message_id,
            parent_message_id=envelope.parent_message_id,
            input_artifact_ids=[a.artifact_id for a in envelope.input_artifact_refs],
            output_artifact_ids=[a.artifact_id for a in envelope.output_artifact_refs],
            reason_codes=list(dict.fromkeys(codes)),
            validation_errors=list(validation_errors or []),
            next_action=action,
            retry_count=envelope.usage.retry_count,
            escalated=envelope.usage.escalated
            or envelope.status.value == "NEEDS_ESCALATION",
            input_tokens=envelope.usage.input_tokens,
            output_tokens=envelope.usage.output_tokens,
            estimated_cost_usd=envelope.usage.estimated_cost_usd,
            latency_ms=envelope.usage.latency_ms,
            created_at=envelope.created_at,
        )
        self.events.append(ev)
        # Store envelope without any hypothetical base64 blobs
        dump = envelope.model_dump(mode="json")
        self.envelopes.append(_strip_secrets(dump))
        return ev

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "trace_id": self.trace_id,
            "created_at": datetime.now(UTC).isoformat(),
            "events": [asdict(e) for e in self.events],
            "envelopes": self.envelopes,
            "totals": {
                "estimated_cost_usd": round(
                    sum(e.estimated_cost_usd for e in self.events), 6
                ),
                "latency_ms": round(sum(e.latency_ms for e in self.events), 2),
                "retries": sum(e.retry_count for e in self.events),
                "escalations": sum(1 for e in self.events if e.escalated),
            },
        }

    def save(self, project_dir: Path) -> Path:
        project_dir.mkdir(parents=True, exist_ok=True)
        path = project_dir / "agent_trace.json"
        path.write_text(
            json.dumps(self.as_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path


def _strip_secrets(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(x in lk for x in ("api_key", "authorization", "base64", "system_prompt")):
                continue
            if isinstance(v, str) and v.startswith("data:image"):
                out[k] = "[omitted image data url]"
                continue
            out[k] = _strip_secrets(v)
        return out
    if isinstance(obj, list):
        return [_strip_secrets(x) for x in obj]
    return obj


def write_trace_report(project_dir: Path, store: AgentTraceStore | None = None) -> Path:
    """Write JSON + HTML report for dev mode."""
    if store is None:
        path = project_dir / "agent_trace.json"
        if not path.exists():
            raise FileNotFoundError(path)
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        path = store.save(project_dir)
        data = store.as_dict()

    html_path = project_dir / "agent_trace.html"
    rows = []
    for e in data.get("events") or []:
        rows.append(
            "<tr>"
            f"<td>{e.get('seq')}</td>"
            f"<td>{html.escape(str(e.get('agent')))}</td>"
            f"<td>{html.escape(str(e.get('task_type')))}</td>"
            f"<td>{html.escape(str(e.get('status')))}</td>"
            f"<td>{html.escape(str(e.get('provider') or ''))}</td>"
            f"<td>{html.escape(str(e.get('model') or ''))}</td>"
            f"<td>{html.escape(', '.join(e.get('reason_codes') or []))}</td>"
            f"<td>{html.escape('; '.join(e.get('validation_errors') or []))}</td>"
            f"<td>{html.escape(str(e.get('next_action') or ''))}</td>"
            f"<td>{e.get('retry_count')}</td>"
            f"<td>{'yes' if e.get('escalated') else ''}</td>"
            f"<td>{(e.get('input_tokens') or 0) + (e.get('output_tokens') or 0)}</td>"
            f"<td>{e.get('estimated_cost_usd')}</td>"
            f"<td>{e.get('latency_ms')}</td>"
            "</tr>"
        )
    doc = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>zlog agent trace {html.escape(data.get('trace_id',''))}</title>
<style>
body {{ font-family: ui-sans-serif, system-ui, sans-serif; background:#111; color:#ddd; margin:24px; }}
table {{ border-collapse: collapse; width:100%; font-size:12px; }}
th, td {{ border:1px solid #333; padding:6px 8px; text-align:left; vertical-align:top; }}
th {{ color:#888; }}
h1 {{ font-size:18px; }}
.meta {{ color:#888; font-size:12px; margin-bottom:16px; }}
</style></head><body>
<h1>Agent trace</h1>
<p class="meta">project={html.escape(str(data.get('project_id')))}
 · trace={html.escape(str(data.get('trace_id')))}
 ·
cost≈${(data.get('totals') or {}).get('estimated_cost_usd', 0)} ·
No API keys / system prompts / image base64 are stored.</p>
<table>
<thead><tr>
<th>#</th><th>agent</th><th>task</th><th>status</th><th>provider</th><th>model</th>
<th>reason codes</th><th>validation</th><th>next</th><th>retry</th><th>esc</th>
<th>tokens</th><th>cost</th><th>ms</th>
</tr></thead>
<tbody>
{''.join(rows)}
</tbody></table>
</body></html>
"""
    html_path.write_text(doc, encoding="utf-8")
    return html_path
