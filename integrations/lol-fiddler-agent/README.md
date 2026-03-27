# LoL Fiddler Agent

**League of Legends AI Strategy System with Fiddler MCP Network Capture + AgentOS Integration**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## 概述

这是一个基于 **Fiddler MCP网络捕获** 的英雄联盟AI策略系统，整合了：

- **Fiddler Everywhere MCP Server** - 网络流量捕获（零幻觉，原始HTTP数据）
- **LoL Live Client Data API** - 实时游戏状态解析
- **AgentOS** - 策略治理和审计日志
- **ML预测模型** - 基于 [leagueoflegends-optimizer](https://github.com/oracle-devrel/leagueoflegends-optimizer) 方法论

## 为什么选择网络捕获而非视觉识别？

| 对比项 | Fiddler网络捕获 | 视觉识别 |
|--------|----------------|----------|
| 幻觉风险 | 极低（原始HTTP数据） | 高（OCR/图像识别误差） |
| 延迟 | 低（毫秒级） | 高（图像处理开销） |
| 数据结构 | 原生JSON | 需要复杂解析 |
| 技术方向 | 符合逆向工程 | 偏向CV |
| 资源消耗 | 低 | 高（GPU） |

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    LoL Strategy Agent (AgentOS)                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │ Lane Phase   │  │ Objective    │  │ Win Prediction       │   │
│  │ Evaluator    │  │ Evaluator    │  │ Evaluator (ML)       │   │
│  └──────────────┘  └──────────────┘  └──────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Live Client Data Parser                        │
│  - Game State Extraction                                         │
│  - Performance Features (f1, f2, f3)                             │
│  - Team Analysis                                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Fiddler MCP Client                            │
│  - HTTP Traffic Capture                                          │
│  - Session Management                                            │
│  - Reverse Proxy (for LoL Live Client API)                       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│              Fiddler Everywhere (localhost:8868)                 │
│  - Captures traffic to 127.0.0.1:2999 (LoL Live Client API)     │
│  - Provides MCP Server interface                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                League of Legends Client                          │
│  - Live Client Data API (127.0.0.1:2999)                        │
│  - Real-time game state                                          │
└─────────────────────────────────────────────────────────────────┘
```

## 安装

### 前置要求

1. **Python 3.10+**
2. **Fiddler Everywhere Pro** (或更高版本)
   - 下载: https://www.telerik.com/fiddler/fiddler-everywhere
   - 需要配置 MCP Server 并生成 API Key
3. **League of Legends** 客户端

### 安装步骤

```bash
# 克隆项目
git clone https://github.com/dylanyunlon/operatorRL.git
cd operatorRL/integrations/lol-fiddler-agent

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装依赖
pip install -e .

# 运行测试
pytest tests/ -v
```

### 配置 Fiddler MCP

1. 启动 Fiddler Everywhere
2. 进入 **Settings > MCP Server**
3. 设置端口 (默认 8868)
4. 生成 API Key
5. 复制 API Key 到配置文件

```yaml
# config/settings.yaml
fiddler:
  host: localhost
  port: 8868
  api_key: YOUR_FIDDLER_API_KEY_HERE

agent:
  poll_interval: 2.0
  advice_cooldown: 10.0
```

## 使用

### 启动策略Agent

```bash
# 使用CLI
lol-agent --api-key YOUR_API_KEY

# 或者Python
python -c "
import asyncio
from lol_fiddler_agent import LoLStrategyAgent, StrategyAgentConfig

async def main():
    config = StrategyAgentConfig(
        fiddler_api_key='YOUR_API_KEY',
    )
    agent = LoLStrategyAgent(config)
    await agent.run('monitor')
    
    # 保持运行直到手动停止
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        await agent.run('stop')

asyncio.run(main())
"
```

### 输出示例

```
⚠️ [RECALL] Health below 30%, recall to avoid dying (confidence: 85%)
💡 [OBJECTIVE] Dragon spawning soon and we have numbers advantage (confidence: 80%)
🚨 [ALL_IN] Numbers advantage (5v3) - ENGAGE! (confidence: 85%)
```

## ML特征工程

基于 [leagueoflegends-optimizer](https://github.com/oracle-devrel/leagueoflegends-optimizer) 的特征：

- **f1**: 每分钟死亡数 (deaths_per_min)
- **f2**: 每分钟击杀+助攻数 (k_a_per_min)
- **f3**: 每分钟等级 (level_per_min)

阈值统计 (来自427,984场比赛数据):

| 特征 | 中位数 | Q1 (差) | Q3 (好) |
|------|--------|---------|---------|
| f1 (死亡/分) | 0.195 | 0.126 | 0.267 |
| f2 (击杀+助攻/分) | 0.466 | 0.307 | 0.639 |
| f3 (等级/分) | 0.505 | 0.462 | 0.555 |

## 项目结构

```
lol-fiddler-agent/
├── src/
│   └── lol_fiddler_agent/
│       ├── __init__.py
│       ├── network/
│       │   ├── fiddler_client.py    # Fiddler MCP客户端
│       │   └── live_client_data.py  # LoL数据解析
│       ├── agents/
│       │   └── strategy_agent.py    # AgentOS集成策略Agent
│       ├── strategies/              # 策略评估器
│       └── models/                  # ML模型
├── tests/
│   ├── test_fiddler_client.py       # 32个测试
│   └── test_live_client_data.py     # 42个测试
├── config/
│   └── settings.yaml
├── pyproject.toml
└── README.md
```

## 开发

### TDD流程

本项目遵循测试驱动开发 (TDD):

1. 编写测试 (预期50%失败)
2. 运行测试确认失败
3. 编写实现代码
4. 运行测试直到全部通过
5. 重构

```bash
# 运行所有测试
pytest tests/ -v

# 运行特定测试
pytest tests/test_fiddler_client.py -v

# 带覆盖率
pytest tests/ --cov=lol_fiddler_agent
```

### 贡献

1. Fork 仓库
2. 创建特性分支 (`git checkout -b feature/amazing`)
3. 提交更改 (`git commit -m 'feat: add amazing feature'`)
4. 推送分支 (`git push origin feature/amazing`)
5. 创建 Pull Request

## 参考

- [Fiddler MCP Server 文档](https://www.telerik.com/fiddler/fiddler-everywhere/documentation/mcp-server/fiddler-mcp-server)
- [LoL Live Client Data API](https://developer.riotgames.com/docs/lol#league-client-api_live-client-data-api)
- [leagueoflegends-optimizer](https://github.com/oracle-devrel/leagueoflegends-optimizer)
- [AgentOS (operatorRL)](https://github.com/dylanyunlon/operatorRL)

## License

MIT License - see [LICENSE](LICENSE) for details.

## 作者

- **dylanyunlong** - dylanyunlong@gmail.com
