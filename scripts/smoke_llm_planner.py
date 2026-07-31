"""Opt-in real DeepSeek smoke test through understanding, planning, and validation."""

from __future__ import annotations

import json
from contextlib import suppress

from copilot.agent.graph import WorkflowInterrupted
from copilot.bootstrap.container import build_workflow_container
from copilot.config import ConfigurationError, Settings
from copilot.llm.deepseek import DeepSeekProvider
from copilot.services.workflows.models import SupplierQualityCommand


def main() -> int:
    """Run no tools; stop after the real LLM candidate passes deterministic validation."""
    settings = Settings(database_url="sqlite:///data/database/enterprise_demo.db")
    if settings.llm_provider != "deepseek":
        print("SKIP: set LLM_PROVIDER=deepseek to run the real planner smoke test")
        return 0
    try:
        api_key = settings.require_llm_api_key().get_secret_value()
    except ConfigurationError as exc:
        print(f"SKIP: {exc}")
        return 0
    provider = DeepSeekProvider(
        api_key=api_key,
        model=settings.llm_model,
        base_url=str(settings.llm_base_url),
        connect_timeout_seconds=settings.llm_connect_timeout_seconds,
        read_timeout_seconds=settings.llm_read_timeout_seconds,
        max_retries=settings.llm_max_retries,
        retry_base_delay_seconds=settings.llm_retry_base_delay_seconds,
        user_agent=settings.llm_user_agent,
        trace_header=settings.llm_trace_header,
    )
    try:
        with build_workflow_container(
            settings,
            llm_provider=provider,
            interrupt_after=("validate_plan",),
        ) as container:
            command = SupplierQualityCommand(
                supplier_id="SUP-001",
                material_id="MAT-001",
                time_range="2026-Q1",
                user_id="U-LLM-SMOKE",
                tenant_id="TENANT-LLM-SMOKE",
                report_format="JSON",
            )
            with suppress(WorkflowInterrupted):
                container.service.execute(command)
            task_id = next(
                record.task_id
                for record in container.workflow_audit.list()
                if record.event == "workflow_started"
            )
            state = container.engine.get_state(task_id, command.tenant_id)
            if state["route"] != "plan_valid":
                print(f"FAIL: planner stopped at route {state['route']}")
                return 1
            print(
                json.dumps(
                    {
                        "task_contract": state["contract"].model_dump(mode="json"),
                        "task_plan": state["plan"].model_dump(mode="json"),
                        "plan_validation": "PASSED",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
    finally:
        provider.close()


if __name__ == "__main__":
    raise SystemExit(main())
