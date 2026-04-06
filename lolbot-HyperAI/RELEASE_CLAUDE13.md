# RELEASE_CLAUDE13.md — Bug Fixes + Replay + Metrics

> Claude13 · 7 files (4 modified, 3 new), 1 fatal runtime bug fixed
> Base: Claude14 commit 73d09e16 (Thread-per-Component architecture)
> Author: dylanyunlong <dylanyunlong@gmail.com>

---

## Critical Bug Fixed

### `modules/common/status/error_code.py` — @property ok shadows @staticmethod ok()

`Status` 类同时定义了 `@staticmethod ok()` (line 179) 和 `@property ok` (line 216)。
Python 中后定义的 descriptor 覆盖前者。`Status.ok()` 在 12+ 处被调用，全部会
在运行时抛出 `TypeError: 'property' object is not callable`。

修复: `@property ok` → `@property is_ok`，更新 error_code.py 内部 5 处 + test_integration.py 2 处。

---

## Modified Files (4)

### 1. `modules/common/status/error_code.py`
- `@property ok` → `@property is_ok`
- 内部 self.ok → self.is_ok (5 处: to_dict, __str__, __bool__, docstring, StatusMessage)

### 2. `modules/canbus/canbus_component.py`
- `Proc()` 包裹在 `with self.measure_proc() as m:` 中, 自动采集延迟/成功率到 ProcMetrics
- 新增 `_validate_lcu_response()`: 验证 allPlayers + gameData.gameTime 存在
- 无效响应记录 `m.failure_reason = "invalid_lcu_response"` 并返回 False

### 3. `conf/default_config.py`
- 新增 `ReplayConfig` dataclass (enabled, recording_path, speed_factor, loop, start_game_time)
- `LolBotConfig` 新增 `replay: ReplayConfig` 字段

### 4. `tests/test_integration.py`
- `s.ok` → `s.is_ok` (2 处)

## New Files (3)

### 5. `modules/common/adapters/replay_data_source.py`
LCUClient 兼容的回放数据源。from_file() 加载 JSONL, set_speed/seek/pause/resume/loop。

### 6. `launch/structured_logger.py`
后台线程 JSONL 指标采集器。定时从 ComponentRegistry 收集健康状态, gzip rotate。

### 7. `RELEASE_CLAUDE13.md`
本文件。
