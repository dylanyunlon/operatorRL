# Claude25 — Apollo Code/Interface Separation

## Design Spec

从 Apollo canbus_component.cc (280行纯调度) 这个好例子开始。
遵循该模式将 LCUClient/FiddlerMCPClient 提取到 vehicle/ 独立文件，
让 canbus_component.py 只做调度。接着在 prediction_component.py 引入
同样的提取模式，使 PredictionFeatures/WinPredictor/TeamfightAnalyzer
能独立测试。随后在 planning_component.py 整合提取 MacroDecisionEngine。
确保全部核心组件兼容 Apollo thin-dispatcher 设计。

## Based on: Claude24 commit 70e66175

New files (8): vehicle/lcu_client.py, vehicle/fiddler_client.py,
  features/prediction_features.py, features/win_predictor_legacy.py,
  features/teamfight_analyzer_legacy.py, engine/macro_decision_engine.py,
  features/__init__.py, engine/__init__.py

Modified (3): canbus 751→596, prediction 901→647, planning 801→666

Claude23 methods preserved: _check_communication_fault, _update_heartbeat,
  _check_stale_by_time, _check_features_fresh, _clamp_confidence,
  _safe_mode_prediction, _check_input, _safe_fallback_advice
