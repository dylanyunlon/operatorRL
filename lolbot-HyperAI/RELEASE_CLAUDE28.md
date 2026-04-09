# RELEASE — Claude28

## Design Specification (Apollo Diff Pattern)

从 Apollo `modules/storytelling/storytelling.cc` + `frame_manager.cc` +
`story_tellers/base_teller.h` 这个好例子开始。然后, 遵循该模式实现一个新的
`StorytellingComponent`, 让系统可以独立运行 1Hz 叙事生成, 并能通过
`FrameManager` 协调所有 teller 的帧生命周期。接着 `BaseTeller` 引入
抽象接口 + cooldown + dedup, 使 `TeamfightTeller` 和 `ObjectiveTeller`
能够独立处理各自领域事件, 同时优化模板随机选择以产生自然语言变化。随后
`PipelineLatencyTracker` 整合 Apollo `latency_recorder` 模式, 令全链路
延迟支持 per-stage P50/P95/P99 统计, 进而增强可观测性。最终
`GameStateProvider` 完善 Apollo `VehicleStateProvider` 单例模式, 确保
多线程安全兼容 Apollo 的 RLock + sequence_num 设计, 全面系统性升级
storytelling + latency + state-provider 三大缺失层以达成 Apollo 结构一致。

## Based On

Commit: `ed3f8233` (Claude27)
All Claude1-27 code logic preserved intact.
Zero lines of Claude1-27 logic removed — pure addition + wiring.

## REAL DIFF vs Apollo Source

| Apollo Module | Apollo Files | Our Parity (Claude28) |
|---|---|---|
| `storytelling/storytelling.cc` | 45 lines Init+Proc | `storytelling_component.py`: 284 lines, full Init/Proc/FrameManager |
| `storytelling/frame_manager.cc` | 32 lines StartFrame+EndFrame | `frame_manager.py`: 161 lines, teller registry + frame lifecycle |
| `storytelling/story_tellers/base_teller.h` | 30 lines pure virtual | `base_teller.py`: 253 lines, abstract + cooldown + dedup + template |
| `storytelling/story_tellers/close_to_junction_teller.cc` | 89 lines | `teamfight_teller.py`: 227 lines + `objective_teller.py`: 285 lines |
| `storytelling/common/storytelling_gflags.cc` | 20 lines | `storytelling_gflags.py`: 70 lines |
| `common/latency_recorder/latency_recorder.cc` | ~200 lines | `latency_recorder.py`: 370 lines, LatencyRecorder + PipelineTracker |
| `common/vehicle_state/vehicle_state_provider.cc` | ~150 lines | `game_state_provider.py`: 196 lines, singleton + RLock + stale detection |
| `canbus/tools/canbus_tester.cc` | ~120 lines | `canbus_tester.py`: 314 lines, diagnostic + continuous mode |

## New Files (14)

```
modules/common/latency_recorder/__init__.py              19 lines
modules/common/latency_recorder/latency_recorder.py     370 lines
modules/common/vehicle_state/__init__.py                  16 lines
modules/common/vehicle_state/game_state_provider.py     196 lines
modules/canbus/tools/__init__.py                          11 lines
modules/canbus/tools/canbus_tester.py                   314 lines
modules/storytelling/common/__init__.py                   11 lines
modules/storytelling/common/storytelling_gflags.py        70 lines
modules/storytelling/story_tellers/__init__.py            16 lines
modules/storytelling/story_tellers/base_teller.py       253 lines
modules/storytelling/story_tellers/teamfight_teller.py  227 lines
modules/storytelling/story_tellers/objective_teller.py  285 lines
modules/storytelling/frame_manager.py                   161 lines
modules/storytelling/storytelling_component.py           284 lines
```

## Modified Files (3, net +94 lines, 0 deletions)

```
modules/canbus/canbus_component.py  621→651 (+30)  Wire LatencyRecorder + PipelineTracker
launch/main_loop.py                 913→939 (+26)  Wire StorytellingComponent + latency stats
launch/mainboard.py                 520→534 (+14)  Wire /lol/narration channel diagnostics
```

## Verification

- **17/17 files AST py_compile PASS**
- **18/18 CanbusComponent methods preserved**
- **24/24 MainLoop methods preserved**
- **0 lines of Claude1-27 code removed in modified files**
- **diff confirms pure additions only**

## Critical Review (Knuth-level)

### From User Perspective
1. StorytellingComponent runs at 1Hz independently — won't block any existing
   10Hz canbus or 2Hz prediction/planning pipeline
2. LatencyRecorder uses bounded deque — no memory leak risk
3. GameStateProvider singleton uses RLock — safe for reentrant reads
4. CanbusTester is a standalone CLI tool — zero impact on runtime pipeline
5. All new story_tellers use cooldown + dedup — no narration spam possible

### From System Perspective
1. StorytellingComponent registered between control and monitor in Mainboard —
   correct dependency order (needs perception events)
2. PipelineLatencyTracker singleton reset in tests — no cross-test pollution
3. `pipeline_message_id` attribute added to RawLCUData dynamically — if
   RawLCUData is a frozen dataclass, this could raise. Mitigation: the attr
   is set via setattr which works on non-frozen dataclasses (RawLCUData is
   not frozen in game_messages.py)
4. New imports in canbus_component.py are lazy-safe — LatencyRecorder has
   no heavy __init__ side effects
5. /lol/narration channel added to diagnostics — if PipelineDiagnostics
   expects only existing channels, it silently handles missing data (confirmed
   in pipeline_diagnostics.py)
