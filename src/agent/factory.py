from __future__ import annotations

from pathlib import Path

from src.agent.audit import AuditLog
from src.agent.computer import ComputerController
from src.agent.computer.tools import build_computer_tools
from src.agent.emergency import GLOBAL_EMERGENCY_STOP
from src.agent.orchestrator import AgentOrchestrator
from src.agent.permissions import AutonomyMode, PermissionEngine
from src.agent.providers.ollama import LocalOllamaProvider
from src.agent.screen import ScreenUnderstandingService
from src.agent.screen.tools import build_screen_tools
from src.agent.task_store import TaskStore
from src.agent.tools import build_phase1_tools
from src.agent.tools.base import ToolRegistry
from src.agent.files import FilePathPolicy, FileSystemService, build_file_tools
from src.agent.files.index import DocumentIndex
from src.agent.browser import BrowserSession, UrlPolicy, build_browser_tools
from src.agent.browser.existing import ExistingBrowserAdapter
from src.agent.browser.resolver import BrowserTargetResolver

# Phase 7 imports
from src.agent.phase7.context import PermissionContext
from src.agent.phase7.policy import PermissionPolicyEngine
from src.agent.phase7.approval import ApprovalManager
from src.agent.phase7.audit import Phase7AuditLog
from src.agent.phase7.gate import ExecutionGate
from src.agent.phase7.guards import DuplicateActionGuard, RateProtection


def build_agent_stack(
    config: dict,
    *,
    screen_capture,
    clipboard_out,
    speech_out_getter,
) -> AgentOrchestrator:
    """Factory: wire agent from app config and existing capture/output objects."""
    agent_cfg = config.get("agent") or {}
    data_dir = Path(agent_cfg.get("data_dir") or "data")
    if not data_dir.is_absolute():
        data_dir = Path(__file__).resolve().parents[2] / data_dir

    store = TaskStore(data_dir / "tasks.db")
    audit = AuditLog()
    computer_control_enabled = bool(agent_cfg.get("computer_control_enabled", True))
    permissions = PermissionEngine(
        autonomy_mode=agent_cfg.get("autonomy_mode", AutonomyMode.ASSIST),
        computer_control_enabled=computer_control_enabled,
        blocked_directories=agent_cfg.get("blocked_directories") or [],
        allowed_directories=agent_cfg.get("allowed_directories") or [],
    )

    import os

    local_provider = LocalOllamaProvider(
        base_url=agent_cfg.get("ollama_base_url", "http://127.0.0.1:11434/v1"),
        model=agent_cfg.get("model", "qwen3:8b"),
        vision_model=agent_cfg.get("vision_model", "llava"),
        timeout_s=float(agent_cfg.get("timeout_s", 25)),
        auto_start=bool(agent_cfg.get("auto_start_ollama", True)),
        startup_timeout_s=float(agent_cfg.get("ollama_startup_timeout_s", 60)),
    )
    ollama_status = local_provider.ensure_ready()
    if ollama_status.get("ok"):
        print(f"[Jarvis] Ollama ready ({ollama_status.get('message')})")
        if ollama_status.get("model_missing"):
            print(
                f"[Jarvis] WARNING: missing model '{ollama_status['model_missing']}'. "
                f"Run: ollama pull {ollama_status['model_missing']}"
            )
    else:
        print(f"[Jarvis] WARNING: Ollama not ready — {ollama_status.get('error')}")

    # Build hybrid provider if a cloud API key is available
    cloud_api_key = (
        agent_cfg.get("cloud_api_key")
        or os.environ.get("OPENAI_API_KEY", "")
    )
    cloud_provider_name = agent_cfg.get("cloud_provider", "openai")

    if cloud_api_key:
        from src.agent.providers.openai import OpenAIProvider
        from src.agent.providers.hybrid import HybridProvider

        cloud = OpenAIProvider(
            api_key=cloud_api_key,
            model=agent_cfg.get("cloud_model", "gpt-4o"),
            timeout_s=float(agent_cfg.get("cloud_timeout_s", 30)),
        )
        provider = HybridProvider(local=local_provider, cloud=cloud)
        print(
            f"[Jarvis] Hybrid mode: cloud={cloud.model} (planning) "
            f"+ local={local_provider.model} (chat)"
        )
    else:
        provider = local_provider
        print(f"[Jarvis] Local-only mode: {local_provider.model}")

    GLOBAL_EMERGENCY_STOP.on_engage(provider.cancel_inflight)

    registry = ToolRegistry(
        permission_engine=permissions,
        audit_log=audit,
        emergency_stop=GLOBAL_EMERGENCY_STOP,
    )

    # --- Phase 7: Permission / Approval / Audit gate ---
    p7_context = PermissionContext(session_id="jarvis")
    p7_policy = PermissionPolicyEngine(
        p7_context,
        autonomy_mode=agent_cfg.get("autonomy_mode", "assist"),
        computer_control_enabled=computer_control_enabled,
    )
    p7_approvals = ApprovalManager()
    p7_audit = Phase7AuditLog(
        db_path=str(data_dir / "audit.db"),
    )
    p7_gate = ExecutionGate(
        policy_engine=p7_policy,
        approval_manager=p7_approvals,
        audit_log=p7_audit,
        duplicate_guard=DuplicateActionGuard(),
        rate_protection=RateProtection(),
        emergency_check=lambda: GLOBAL_EMERGENCY_STOP.is_engaged,
    )
    GLOBAL_EMERGENCY_STOP.on_engage(lambda: p7_approvals.cancel_all())
    print("[Jarvis] Phase 7 security gate initialized")

    orchestrator = AgentOrchestrator(
        provider=provider,
        registry=registry,
        task_store=store,
        permission_engine=permissions,
        audit_log=audit,
        emergency_stop=GLOBAL_EMERGENCY_STOP,
        max_steps=int(agent_cfg.get("max_steps", 20)),
        cloud_fallback_enabled=bool(agent_cfg.get("cloud_fallback", False)),
        hud_tasks_path=str(data_dir / "daily_tasks.json"),
        planner_timeout_s=float(agent_cfg.get("planner_timeout_s", 45)),
    )
    # Attach Phase 7 components to orchestrator
    orchestrator.p7_gate = p7_gate          # type: ignore[attr-defined]
    orchestrator.p7_approvals = p7_approvals  # type: ignore[attr-defined]
    orchestrator.p7_audit = p7_audit        # type: ignore[attr-defined]
    orchestrator.p7_context = p7_context    # type: ignore[attr-defined]

    tools = build_phase1_tools(
        screen_capture=screen_capture,
        clipboard_out=clipboard_out,
        speech_out_getter=speech_out_getter,
        registry_getter=lambda: registry,
        status_getter=orchestrator.status_snapshot,
    )
    for tool in tools:
        registry.register(tool)

    computer = None
    screen_service = None
    file_policy = None
    if computer_control_enabled:
        try:
            computer = ComputerController()
            for tool in build_computer_tools(computer):
                registry.register(tool)
            orchestrator.computer = computer  # type: ignore[attr-defined]
            computer.on_event = orchestrator.world.on_computer_event
            print("[Jarvis] Computer control tools registered")
        except Exception as e:
            print(f"[Jarvis] Computer control unavailable: {e}")

    # Phase 3 screen understanding (works even if only window APIs available)
    try:
        screen_service = ScreenUnderstandingService(screen_capture, computer)
        screen_service.attach_vision(local_provider)
        for tool in build_screen_tools(screen_service):
            registry.register(tool)
        orchestrator.screen_service = screen_service  # type: ignore[attr-defined]
        orchestrator.screen_capture = screen_capture  # type: ignore[attr-defined]
        if computer is not None:
            prev = computer.on_event

            def _on_computer(name, data=None):
                if callable(prev):
                    prev(name, data)
                screen_service.invalidate(name)

            computer.on_event = _on_computer
        print("[Jarvis] Screen understanding ready")
        import threading

        def _warm_vision() -> None:
            try:
                vp = screen_service.vision
                if vp is not None and hasattr(vp, "warmup"):
                    stats = vp.warmup(timeout_s=40.0)
                    print(
                        f"[Jarvis] Vision warmup {stats.get('elapsed_ms')} ms "
                        f"model={stats.get('model')} load={((stats.get('stats') or {}).get('load_duration_ms'))}"
                    )
            except Exception as e:
                print(f"[Jarvis] Vision warmup skipped: {e}")

        threading.Thread(target=_warm_vision, daemon=True, name="vision-warmup").start()
    except Exception as e:
        print(f"[Jarvis] Screen understanding unavailable: {e}")

    files_cfg = config.get("files") or {}
    if files_cfg.get("enabled", True):
        try:
            allowed = files_cfg.get("allowed_directories") or []
            blocked = files_cfg.get("blocked_directories") or []
            readonly = files_cfg.get("read_only_directories") or []
            file_policy = FilePathPolicy(
                allowed_directories=allowed,
                blocked_directories=blocked,
                read_only_directories=readonly,
            )
            index = DocumentIndex(
                data_dir / "documents.db",
                file_policy,
                max_text_chars=int(files_cfg.get("max_index_text_chars", 50000)),
            )
            fs = FileSystemService(
                file_policy,
                index=index,
                max_read_bytes=int(files_cfg.get("max_read_bytes", 2_000_000)),
            )
            for tool in build_file_tools(fs):
                registry.register(tool)
            orchestrator.files = fs  # type: ignore[attr-defined]
            print(f"[Jarvis] File tools ready ({len(file_policy.allowed_roots)} allowed roots)")
            if files_cfg.get("index_on_startup"):
                print("[Jarvis] Indexing authorized folders…")
                print("[Jarvis]", fs.index_now())
        except Exception as e:
            print(f"[Jarvis] File tools unavailable: {e}")

    # --- Browser Target Resolver: prefer existing desktop Chrome ---
    browser_resolver = None
    existing_adapter = None
    if computer is not None:
        try:
            from src.agent.browser.discovery import BrowserWindowRegistry

            window_registry = BrowserWindowRegistry()
            browser_resolver = BrowserTargetResolver(
                computer=computer,
                registry=window_registry,
                use_native=True,
            )
            existing_adapter = ExistingBrowserAdapter(computer, browser_resolver, screen=screen_service)
            orchestrator.browser_resolver = browser_resolver   # type: ignore[attr-defined]
            orchestrator.existing_browser = existing_adapter   # type: ignore[attr-defined]
            orchestrator.browser_registry = window_registry    # type: ignore[attr-defined]
            browser_resolver.seed_from_desktop()
            window_registry.start_foreground_hook()
            print("[Jarvis] Browser target resolver ready (FOLLOW_USER_BROWSER mode)")
        except Exception as e:
            print(f"[Jarvis] Browser target resolver unavailable: {e}")

    browser_cfg = config.get("browser") or {}
    if browser_cfg.get("enabled", True):
        try:
            channel = str(browser_cfg.get("channel") or "chrome")

            def _browser_status(msg: str) -> None:
                print(f"[Browser] {msg}")
                hearing = getattr(orchestrator, "on_hearing", None)
                if callable(hearing):
                    hearing(msg)

            session = BrowserSession(
                policy=UrlPolicy(
                    allowed_hosts=browser_cfg.get("allowed_hosts") or [],
                    blocked_hosts=browser_cfg.get("blocked_hosts") or [],
                ),
                profile_dir=data_dir / "browser_profile",
                headed=bool(browser_cfg.get("headed", True)),
                persist_profile=bool(browser_cfg.get("persist_profile", True)),
                navigation_timeout_ms=int(browser_cfg.get("navigation_timeout_ms", 20000)),
                channel=channel,
                file_policy=file_policy,
                computer=computer,
                screen=screen_service,
                on_status=_browser_status,
            )
            for tool in build_browser_tools(
                session,
                resolver=browser_resolver,
                existing_adapter=existing_adapter,
            ):
                registry.register(tool)
            orchestrator.browser = session  # type: ignore[attr-defined]
            session.agent.on_event = orchestrator.world.on_browser_event
            GLOBAL_EMERGENCY_STOP.on_engage(session.close)
            print(f"[Jarvis] Browser tools ready (Playwright {channel or 'chromium'})")
        except Exception as e:
            print(f"[Jarvis] Browser tools unavailable: {e}")

    import threading

    warm = getattr(provider, "warmup", None)
    if callable(warm):
        threading.Thread(target=warm, daemon=True, name="ollama-warm").start()
    return orchestrator
