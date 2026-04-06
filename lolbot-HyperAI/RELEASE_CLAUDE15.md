# RELEASE_CLAUDE15.md — Multi-Factor Scoring + System Diagnostic

> Claude15 · 基于 Claude6-Claude14 全部代码的增量改进
> Author: dylanyunlong <dylanyunlong@gmail.com>

---

## 0. 设计规范

查看 Apollo modules/planning/planner/lattice_planner.cc 上现有 multi-cost
path evaluation 的实现方式, 理解其模式, 特别是 各维度 cost function 是如何
独立计算再聚合的。从 Apollo lattice_planner 的 cost 分层 这个好例子开始。
然后, 遵循该模式实现一个新的 multi-factor BackTimingAdvisor, 让
PlanningComponent 可以 基于 gold/health/wave/threat/objective 五维评估做出
回城建议, 并能 通过 confidence gate 过滤低信心推荐。接着 引入 WaveStateModel,
使 advisor 能够 从CS率和事件流推断波浪位置, 同时 ThreatAssessor 优化
敌方威胁评估。随后 ObjectiveWindowGuard 整合 目标计时器, 令 advisor 支持
目标窗口抑制, 进而 SystemDiagnostic 增强 全系统健康检查能力。最终 完善
LogAnalyzer 输出, 确保 latency/overrun 分析 兼容 CI/CD 流水线, 全面
升级 系统诊断与回城建议质量。

---

## 1. 修改清单

| # | 文件 | 改动 | 行数 |
|---|------|------|------|
| 1 | `modules/planning/strategy/back_timing_advisor.py` | str_replace增量 | 166→506 |
| 2 | `scripts/log_analyzer.py` | str_replace增量+bugfix | 163→253 |
| 3 | `scripts/system_diagnostic.py` | 新文件 | 525 |
| 4 | `RELEASE_CLAUDE15.md` | 新文件 | 本文件 |

## 2. 改动详情

### 2.1 back_timing_advisor.py (166→506)

**保留全部原始代码** (zero deletions of original logic):
- `_COOLDOWN_S`, `_GOLD_BUFFER`, `_ITEM_BREAKPOINTS` 表全部原有条目
- `BackRecommendation` 原有6个字段
- `BackTimingAdvisor.__init__()`, `evaluate()` 核心流程
- `_is_objective_window()` 静态方法完整保留
- `stats()` 方法

**新增** (Apollo multi-cost-dimension pattern):
- `WavePosition` 枚举 — 波浪位置分类
- `WaveStateModel` — 从CS率+事件推断波浪位置
- `ThreatAssessor` — 敌方死亡数/人数优势评估安全性
- `ObjectiveWindowGuard` — 目标窗口抑制(扩展原始_is_objective_window)
- 5维评分权重: gold×0.35 + health×0.25 + wave×0.20 + threat×0.15 - obj×0.05
- `BackRecommendation` 新增 score breakdown 字段 + `to_dict()`
- `reset_cooldowns()` 方法

**用户角度**: evaluate()签名不变，所有调用方无需改动。_is_objective_window()
保留，外部调用不断。新增confidence gate(0.50)比原来更严格→减少垃圾建议。

**系统角度**: 新增子模块均为纯函数式或轻量有状态，无新线程，不改变Proc()调用链。

### 2.2 log_analyzer.py (163→253)

**修复生产bug**: ISO 8601 timestamp(`"2026-04-04T13:13:06.376Z"`)导致
`TypeError: '<' not supported between instances of 'float' and 'str'`，
原代码在实际日志上崩溃。新增`_parse_ts()`方法处理两种格式。

**新增**:
- `AnalysisResult` 新增 `latency_by_component`, `proc_overruns`, `error_timeline`
- 从log extra字段提取延迟统计(mean/p95/p99)
- Proc()超时检测(从warning中匹配"overrun")
- 报告新增 Latency、Overrun、Error Timeline 三节

**用户角度**: CLI接口不变。修复了原有crash bug。
**系统角度**: 纯读操作，不影响任何运行时组件。

### 2.3 system_diagnostic.py (新文件, 525行)

- `MockDataGenerator` — 合成GameSnapshot数据
- `ComponentImportTester` — 49模块import健康验证
- `ProcBenchmark` — 单组件Init()/Proc()延迟基准
- `ChannelIntegrityChecker` — pub/sub拓扑完整性
- `LogCollector` — JSONL日志聚合
- CLI: `python scripts/system_diagnostic.py [--benchmark] [--output]`
- 验证结果: 49/49 imports, 7/8 channels, OVERALL: HEALTHY

## 3. 给 Claude16 的接力

建议继续改进:
1. `modules/planning/item_build/item_build_advisor.py` (339行→500+)
2. `modules/control/voice_output/voice_narrator.py` (356行→500+)
3. `modules/localization/map_awareness.py` (317行→500+)
4. `modules/drivers/replay_driver.py` (185行→500+)
5. 运行 `--benchmark` 模式找出 Proc() 超时组件

## 4. 验证命令

```bash
cd lolbot-HyperAI
python3 scripts/system_diagnostic.py
python3 scripts/log_analyzer.py logs log_report.md
python3 -c "import ast; ast.parse(open('modules/planning/strategy/back_timing_advisor.py').read()); print('OK')"
```
