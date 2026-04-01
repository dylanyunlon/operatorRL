"""
M1006-M1025 Logging System — 历史战斗数据获取 + Fiddler网络捕获 + 实时情报融合
==============================================================================
查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
从 M906 SeraphineConnectorBridge 的 retry + PastRequest 这个好例子开始。

本日志系统负责:
1. 收集所有模块的初始化/运行/错误日志
2. 生成诊断报告 (JSON)
3. 输出结构化日志供后续模块改进
"""

import asyncio
import json
import logging
import os
import sys
import time
import traceback
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ─── 常量 ────────────────────────────────────────────────────────────────────

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

DIAGNOSTIC_REPORT_PATH = LOG_DIR / "M1006-M1025_diagnostic_report.json"
RUNTIME_LOG_PATH = LOG_DIR / "M1006-M1025.log"

MODULE_REGISTRY = {
    "M1006": "HistoricalMatchCrawler",
    "M1007": "FiddlerNetworkBridge",
    "M1008": "MatchTimelineDeserializer",
    "M1009": "PlayerProfileAggregator",
    "M1010": "ChampionMasteryIndexer",
    "M1011": "RankTierClassifier",
    "M1012": "MatchOutcomeCorrelator",
    "M1013": "LaneMatchupStatEngine",
    "M1014": "ItemBuildPathAnalyzer",
    "M1015": "GoldDiffTrendTracker",
    "M1016": "ObjectiveControlAnalyzer",
    "M1017": "TeamfightDetector",
    "M1018": "VisionScoreAnalyzer",
    "M1019": "DeathHeatmapGenerator",
    "M1020": "FiddlerPacketDecoder",
    "M1021": "LiveFeedHistoricalMerger",
    "M1022": "PredictiveFeatureExtractor",
    "M1023": "HistoricalCoachReportGen",
    "M1024": "CrossMatchPatternMiner",
    "M1025": "UnifiedHistoricalGateway",
}

# ─── 日志格式器 ───────────────────────────────────────────────────────────────

class StructuredFormatter(logging.Formatter):
    """结构化日志格式器，输出 JSON-line 格式便于后续分析"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "module": getattr(record, "module_id", record.module),
            "func": record.funcName,
            "line": record.lineno,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = traceback.format_exception(*record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


class HumanReadableFormatter(logging.Formatter):
    """人类可读格式器，用于控制台输出"""

    LEVEL_COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelname, "")
        module_id = getattr(record, "module_id", "SYSTEM")
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        return (
            f"{color}[{timestamp}] [{record.levelname:>8}] "
            f"[{module_id:>5}] {record.getMessage()}{self.RESET}"
        )


# ─── 日志收集器 ───────────────────────────────────────────────────────────────

class DiagnosticCollector:
    """
    诊断信息收集器 — 汇聚所有模块运行时状态。
    
    设计模式参考 Seraphine/app/lol/connector.py 的 PastRequest:
    每次模块调用都被记录为一个 PastDiagnostic 对象,
    包含调用时间戳、参数摘要、返回状态、耗时。
    """

    def __init__(self):
        self._records: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._module_status: Dict[str, str] = {}
        self._start_time = time.monotonic()
        self._errors: List[Dict[str, Any]] = []
        self._warnings: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock() if asyncio.get_event_loop().is_running() else None

    def record_init(self, module_id: str, status: str, duration_ms: float,
                    details: Optional[Dict] = None):
        """记录模块初始化事件"""
        entry = {
            "event": "init",
            "module_id": module_id,
            "module_name": MODULE_REGISTRY.get(module_id, "Unknown"),
            "status": status,
            "duration_ms": round(duration_ms, 2),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": details or {},
        }
        self._records[module_id].append(entry)
        self._module_status[module_id] = status

    def record_call(self, module_id: str, method: str, args_summary: str,
                    status: str, duration_ms: float, result_summary: str = ""):
        """记录模块方法调用 — 类比 Seraphine PastRequest 的 func + params_dict"""
        entry = {
            "event": "call",
            "module_id": module_id,
            "method": method,
            "args_summary": args_summary[:200],
            "status": status,
            "duration_ms": round(duration_ms, 2),
            "result_summary": result_summary[:500],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._records[module_id].append(entry)
        if status == "error":
            self._errors.append(entry)
        elif status == "warning":
            self._warnings.append(entry)

    def record_error(self, module_id: str, error: Exception, context: str = ""):
        """记录错误详情"""
        entry = {
            "event": "error",
            "module_id": module_id,
            "error_type": type(error).__name__,
            "error_message": str(error)[:500],
            "traceback": traceback.format_exc()[:2000],
            "context": context,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._errors.append(entry)
        self._records[module_id].append(entry)

    def generate_report(self) -> Dict[str, Any]:
        """生成完整诊断报告"""
        elapsed = time.monotonic() - self._start_time
        total_calls = sum(
            len([r for r in records if r["event"] == "call"])
            for records in self._records.values()
        )
        total_errors = len(self._errors)
        total_warnings = len(self._warnings)

        module_summaries = {}
        for mid, records in self._records.items():
            calls = [r for r in records if r["event"] == "call"]
            errors = [r for r in records if r["event"] == "error"]
            avg_duration = (
                sum(r.get("duration_ms", 0) for r in calls) / len(calls)
                if calls else 0
            )
            module_summaries[mid] = {
                "name": MODULE_REGISTRY.get(mid, "Unknown"),
                "status": self._module_status.get(mid, "unknown"),
                "total_calls": len(calls),
                "total_errors": len(errors),
                "avg_duration_ms": round(avg_duration, 2),
                "max_duration_ms": round(
                    max((r.get("duration_ms", 0) for r in calls), default=0), 2
                ),
            }

        report = {
            "report_version": "1.0.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "milestone_range": "M1006-M1025",
            "total_elapsed_seconds": round(elapsed, 2),
            "total_modules": len(MODULE_REGISTRY),
            "initialized_modules": len(self._module_status),
            "total_calls": total_calls,
            "total_errors": total_errors,
            "total_warnings": total_warnings,
            "module_summaries": module_summaries,
            "recent_errors": self._errors[-20:],
            "recent_warnings": self._warnings[-10:],
        }
        return report

    def save_report(self, path: Optional[Path] = None):
        """保存诊断报告到 JSON 文件"""
        target = path or DIAGNOSTIC_REPORT_PATH
        report = self.generate_report()
        with open(target, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        return target


# ─── 全局日志系统 ─────────────────────────────────────────────────────────────

_collector = DiagnosticCollector()
_logger_cache: Dict[str, logging.Logger] = {}


def get_module_logger(module_id: str) -> logging.Logger:
    """
    获取模块专用 logger。
    
    每个 M10xx 模块调用此函数获取自己的 logger 实例,
    日志同时输出到:
    1. 控制台 (人类可读格式)
    2. 文件 (JSON-line 结构化格式)
    """
    if module_id in _logger_cache:
        return _logger_cache[module_id]

    logger = logging.getLogger(f"operatorRL.{module_id}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # 控制台 handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(HumanReadableFormatter())
    logger.addHandler(console_handler)

    # 文件 handler (JSON-line)
    file_handler = logging.FileHandler(RUNTIME_LOG_PATH, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(StructuredFormatter())
    logger.addHandler(file_handler)

    # 注入 module_id 到每条记录
    old_factory = logging.getLogRecordFactory()

    def record_factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.module_id = module_id
        return record

    # 使用 LoggerAdapter 代替全局 factory 以避免冲突
    _logger_cache[module_id] = logger
    return logger


def get_collector() -> DiagnosticCollector:
    """获取全局诊断收集器"""
    return _collector


def reset_collector():
    """重置诊断收集器（用于测试）"""
    global _collector
    _collector = DiagnosticCollector()


# ─── 装饰器 ───────────────────────────────────────────────────────────────────

def traced(module_id: str):
    """
    方法追踪装饰器 — 类比 Seraphine 的 @retry 装饰器。
    
    自动记录方法调用到 DiagnosticCollector:
    - 调用开始时间
    - 参数摘要
    - 返回值摘要
    - 异常信息
    - 总耗时
    """
    def decorator(func):
        import functools
        import inspect

        is_async = inspect.iscoroutinefunction(func)

        if is_async:
            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                logger = get_module_logger(module_id)
                collector = get_collector()
                start = time.monotonic()
                args_str = f"args={len(args)-1}, kwargs={list(kwargs.keys())}"

                try:
                    result = await func(*args, **kwargs)
                    duration_ms = (time.monotonic() - start) * 1000
                    result_str = str(result)[:200] if result is not None else "None"
                    collector.record_call(
                        module_id, func.__name__, args_str,
                        "success", duration_ms, result_str
                    )
                    logger.debug(
                        f"{func.__name__} completed in {duration_ms:.1f}ms"
                    )
                    return result
                except Exception as e:
                    duration_ms = (time.monotonic() - start) * 1000
                    collector.record_call(
                        module_id, func.__name__, args_str,
                        "error", duration_ms, str(e)
                    )
                    collector.record_error(module_id, e, f"in {func.__name__}")
                    logger.error(f"{func.__name__} failed: {e}")
                    raise
        else:
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                logger = get_module_logger(module_id)
                collector = get_collector()
                start = time.monotonic()
                args_str = f"args={len(args)-1}, kwargs={list(kwargs.keys())}"

                try:
                    result = func(*args, **kwargs)
                    duration_ms = (time.monotonic() - start) * 1000
                    result_str = str(result)[:200] if result is not None else "None"
                    collector.record_call(
                        module_id, func.__name__, args_str,
                        "success", duration_ms, result_str
                    )
                    logger.debug(
                        f"{func.__name__} completed in {duration_ms:.1f}ms"
                    )
                    return result
                except Exception as e:
                    duration_ms = (time.monotonic() - start) * 1000
                    collector.record_call(
                        module_id, func.__name__, args_str,
                        "error", duration_ms, str(e)
                    )
                    collector.record_error(module_id, e, f"in {func.__name__}")
                    logger.error(f"{func.__name__} failed: {e}")
                    raise

        return wrapper
    return decorator


# ─── 运行入口 ─────────────────────────────────────────────────────────────────

def run_logging_system():
    """
    运行日志系统自检:
    1. 验证所有模块可导入
    2. 执行初始化检查
    3. 生成诊断报告
    """
    print("=" * 70)
    print("  M1006-M1025 Logging System — 历史战斗数据获取层")
    print("  运行自检与诊断报告生成")
    print("=" * 70)

    collector = get_collector()
    logger = get_module_logger("SYSTEM")

    logger.info("开始 M1006-M1025 模块自检...")

    # 检查每个模块
    for mid, mname in MODULE_REGISTRY.items():
        start = time.monotonic()
        module_file = Path(__file__).parent / f"{mid.lower()}_{_snake_case(mname)}.py"
        
        if module_file.exists():
            try:
                # 尝试语法检查
                with open(module_file, "r", encoding="utf-8") as f:
                    source = f.read()
                compile(source, str(module_file), "exec")
                duration = (time.monotonic() - start) * 1000
                collector.record_init(mid, "ok", duration, {
                    "file": str(module_file.name),
                    "lines": source.count("\n") + 1,
                    "size_bytes": len(source.encode("utf-8")),
                })
                logger.info(f"  ✓ {mid} {mname} — 语法OK, {source.count(chr(10))+1}行")
            except SyntaxError as e:
                duration = (time.monotonic() - start) * 1000
                collector.record_init(mid, "syntax_error", duration, {
                    "error": str(e)
                })
                collector.record_error(mid, e, "syntax check")
                logger.error(f"  ✗ {mid} {mname} — 语法错误: {e}")
        else:
            duration = (time.monotonic() - start) * 1000
            collector.record_init(mid, "missing", duration, {
                "expected_file": str(module_file.name)
            })
            logger.warning(f"  ? {mid} {mname} — 文件不存在: {module_file.name}")

    # 生成报告
    report_path = collector.save_report()
    logger.info(f"诊断报告已保存: {report_path}")

    report = collector.generate_report()
    print("\n" + "=" * 70)
    print("  诊断摘要")
    print("=" * 70)
    print(f"  总模块数:     {report['total_modules']}")
    print(f"  已初始化:     {report['initialized_modules']}")
    print(f"  总调用数:     {report['total_calls']}")
    print(f"  总错误数:     {report['total_errors']}")
    print(f"  总警告数:     {report['total_warnings']}")
    print(f"  运行时间:     {report['total_elapsed_seconds']}s")
    print("=" * 70)

    return report


def _snake_case(name: str) -> str:
    """CamelCase → snake_case"""
    import re
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


if __name__ == "__main__":
    run_logging_system()
