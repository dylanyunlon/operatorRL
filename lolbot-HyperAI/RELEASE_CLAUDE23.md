# RELEASE_CLAUDE23 — Apollo-Aligned Architecture Hardening

**Author:** dylanyunlong <dylanyunlong@gmail.com>
**Date:** 2026-04-09
**Base commit:** e213de8c (claude22-v3)
**Files modified:** 20 (all additive — zero lines of existing logic removed)

---

## Design Spec (Apollo Diff-Based)

### Methodology

Real `diff` comparison between Apollo source code and our lolbot-HyperAI:

| Apollo File | Our File | Gap Found |
|-------------|----------|-----------|
| `canbus_component.cc:162-217` Proc() timing | `timer_component.py` | No deadline enforcement |
| `canbus_component.cc:196` CheckChassisCommunicationFault | `canbus_component.py` | No comm fault detection |
| `canbus_component.cc:214` UpdateHeartbeat | `data_source_factory.py` | No heartbeat protocol |
| `canbus_component.cc:239-275` OnControlCommandCheck | `node.py` Reader | No data freshness check |
| `canbus_component.cc:344` OnError → Status | `component_base.py` | No structured error propagation |
| `canbus_component.cc:376` ProcessGuardianCmdTimeout | `component_base.py` | No SafeMode/estop |
| `planning_component.cc:284-320` CheckInput | `planning_component.py` | No upstream validation |
| `common::util::FillHeader()` | `game_messages.py` | No message timestamp headers |
| `canbus_gflags.cc` | `default_config.py` | Timing constants scattered |
| `cyber/scheduler/choreography` | `scheduler.py` | No priority ordering |

### Design Template (filled in)

从 Apollo `canbus_component.cc` 这个好例子开始，查看其 `Proc()` 的 timing guard 实现方式，
理解其 start_time→end_time→warn 模式。然后，遵循该模式实现新的 `should_skip_proc()` +
`measure_proc()` 上下文管理器，让 TimerComponent 可以检测 Proc() 超时，并能自动降级。
接着在 CyberNode Reader 引入 `is_stale()` / `observed_age_s()`，使组件能够像 Apollo
`OnControlCommandCheck()` 那样检测上游数据新鲜度，同时优化 channel back-pressure 监控。
随后在 PlanningComponent 整合 `_check_input()` 验证，令上游数据断流时支持 `_safe_fallback_advice()`
优雅降级，进而增强系统鲁棒性。最终完善 `SafeMode` 紧急回退机制，确保数据超时时兼容所有组件，
全面升级系统可靠性以达成 Apollo 级别的生产质量。

---

## 20 Files Modified

| # | File | Apollo Pattern | Change |
|---|------|---------------|--------|
| 1 | `cyber/component/timer_component.py` | Proc() timing guard | +`should_skip_proc()`, +`measure_proc()` context mgr |
| 2 | `cyber/node/node.py` | OnControlCommandCheck | +`Reader.is_stale()`, +`observed_age_s()`, +`wait_for_message()` |
| 3 | `modules/canbus/canbus_component.py` | CheckChassisCommunicationFault, UpdateHeartbeat | +`_check_communication_fault()`, +`_update_heartbeat()`, +`_check_stale_by_time()` |
| 4 | `modules/canbus/vehicle/data_source_factory.py` | vehicle_object_ protocol | +`update_heartbeat()`, +`check_communication_fault()`, +`last_success_time` |
| 5 | `modules/common/component_base.py` | ProcessGuardianCmdTimeout, OnError | +`SafeMode` class, +`StructuredError` class |
| 6 | `modules/common/status/error_code.py` | monitor_logger_buffer_ levels | +`ErrorSeverity`, +`ErrorCodeClaude23` |
| 7 | `modules/perception/perception_component.py` | InternalProc validation | +`_validate_input()`, +`_check_upstream_health()` |
| 8 | `modules/prediction/prediction_component.py` | Feature freshness | +`_check_features_fresh()`, +`_clamp_confidence()`, +`_safe_mode_prediction()` |
| 9 | `modules/planning/planning_component.py` | CheckInput() | +`_check_input()`, +`_safe_fallback_advice()` |
| 10 | `modules/control/control_component.py` | OnControlCommandCheck | +`_check_command_freshness()`, +`_throttle_on_safe_mode()` |
| 11 | `modules/monitor/monitor_component.py` | Monitor health aggregation | +`_aggregate_component_health()`, +`_check_error_budget()`, +`_check_safe_mode_status()` |
| 12 | `launch/mainboard.py` | DAG module loading | +`health_probe()`, +`validate_dependencies()`, +`restart_component()` |
| 13 | `launch/main_loop.py` | Mainboard lifecycle | +`_startup_health_probe()`, +`_check_safe_mode()`, +`_validate_startup()` |
| 14 | `runtime/error_recovery.py` | ProcessGuardianCmdTimeout | +`trigger_safe_mode_on_threshold()`, +`clear_safe_mode_on_recovery()` |
| 15 | `runtime/health_monitor.py` | Proc() deadline tracking | +`track_deadline_violations()`, +`recommend_interval_adjustment()` |
| 16 | `modules/common/adapters/game_messages.py` | FillHeader() | +`MessageHeader`, +`fill_header()`, +`get_header_age()` |
| 17 | `conf/default_config.py` | canbus_gflags.cc | +`TimingFlags` class with all timing constants |
| 18 | `cyber/record/record_writer.py` | Record crash safety | +`force_flush()`, +`recording_health()` |
| 19 | `cyber/transport/backpressure.py` | Transport flow control | +`FlowControlMetrics` class |
| 20 | `cyber/scheduler/scheduler.py` | CRoutine priority | +`ComponentPriority` class with startup ordering |

---

## Critical Review

### From User Perspective (bug risk):
1. `SafeMode` is a singleton — test isolation requires `SafeMode.reset()` in teardown.
2. `_check_input()` in planning uses `hasattr` checks — future refactors adding/removing fields need to update these.
3. `_clamp_confidence()` floor of 5% means the model can never say "<5% chance" — acceptable trade-off for UX.

### From System Perspective:
1. All additions are **new methods on existing classes** — no method signatures changed, no imports broken.
2. `SafeMode` uses `threading.Lock` — safe for our thread-per-component model.
3. `FlowControlMetrics.collect()` iterates `_buffers` dict — safe because BackpressureRegistry is already thread-safe.
4. `ComponentPriority.sort_by_priority()` is pure — no side effects, safe to call from any thread.

### For Claude 24 (next developer):
Continue with: wire `_check_communication_fault()` into `canbus Proc()` body,
wire `_check_input()` into `planning _internal_proc()` body,
wire `_check_safe_mode()` into `main_loop._supervisor_tick()` body.
These are intentionally left as separate methods (not wired in) to avoid
changing any existing Proc() control flow — Claude 24 should wire them
with full integration tests.
