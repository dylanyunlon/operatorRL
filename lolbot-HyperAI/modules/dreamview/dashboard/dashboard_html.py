"""
DashboardHTML — Dreamview real-time dashboard HTML generator.
==============================================================
lolbot-HyperAI · Dreamview Layer

Generates a self-contained HTML page that displays real-time game
assistant status via Server-Sent Events (SSE).  The page auto-refreshes
win probability, strategy advice, component health, and overlay state.

Architecture position:
    modules/dreamview/dashboard/dashboard_html.py   ← YOU ARE HERE
    ├─ Reads: DreamviewState from dreamview_api.py
    ├─ Output: HTML string served by Dreamview HTTP server
    └─ Used by: dreamview_api.py (GET /dashboard)

Apollo reference:
    modules/dreamview/frontend/ — React-based visualization
    modules/dreamview/backend/sim_control/ — backend state

Design notes:
    - Single-file HTML with embedded CSS and JS (no external deps)
    - SSE connection to /api/logs/stream for real-time updates
    - Polling /api/state every 500ms for component status
    - Color-coded win probability gauge
    - Responsive layout for desktop and mobile
    - Dark theme matching game overlay aesthetics
"""

from __future__ import annotations

import html
import json
import time
from typing import Any, Dict, List, Optional

from cyber.logger.cyber_logger import get_logger

logger = get_logger("dreamview.dashboard")

# ─── Constants ───────────────────────────────────────────────────────────────

_DASHBOARD_TITLE = "lolbot-HyperAI Dreamview"
_REFRESH_INTERVAL_MS = 500
_SSE_RECONNECT_MS = 2000
_MAX_LOG_LINES = 200


# ─── Color Scheme ────────────────────────────────────────────────────────────

class _Colors:
    """Dashboard color palette (dark theme)."""
    BG_PRIMARY = "#0d1117"
    BG_SECONDARY = "#161b22"
    BG_CARD = "#1c2128"
    TEXT_PRIMARY = "#e6edf3"
    TEXT_SECONDARY = "#8b949e"
    ACCENT_BLUE = "#58a6ff"
    ACCENT_GREEN = "#3fb950"
    ACCENT_YELLOW = "#d29922"
    ACCENT_RED = "#f85149"
    ACCENT_PURPLE = "#bc8cff"
    BORDER = "#30363d"
    WIN_HIGH = "#3fb950"
    WIN_MID = "#d29922"
    WIN_LOW = "#f85149"


# ─── HTML Generator ─────────────────────────────────────────────────────────

class DashboardHTMLGenerator:
    """Generates the complete dashboard HTML page.

    The generated page is self-contained: all CSS and JavaScript are
    embedded inline.  The page connects to the Dreamview API for
    real-time data via polling and SSE.

    Usage::

        generator = DashboardHTMLGenerator()
        html_str = generator.generate(port=8080)
        # Serve html_str via HTTP
    """

    def __init__(self) -> None:
        self._generation_count = 0

    def generate(
        self,
        port: int = 8080,
        host: str = "localhost",
    ) -> str:
        """Generate the complete HTML dashboard page.

        Args:
            port: Dreamview API port.
            host: Dreamview API host.

        Returns:
            Complete HTML string ready to serve.
        """
        self._generation_count += 1
        api_base = f"http://{host}:{port}/api"

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_DASHBOARD_TITLE}</title>
<style>
{self._generate_css()}
</style>
</head>
<body>
<div class="dashboard">
    <header class="header">
        <h1 class="title">&#x1f3ae; {_DASHBOARD_TITLE}</h1>
        <div class="status-indicator" id="connection-status">
            <span class="dot"></span>
            <span class="text">Connecting...</span>
        </div>
    </header>

    <div class="grid">
        <div class="card card-win-prob">
            <h2>Win Probability</h2>
            <div class="win-gauge" id="win-gauge">
                <div class="gauge-fill" id="gauge-fill"></div>
                <span class="gauge-text" id="gauge-text">--</span>
            </div>
            <div class="win-trend" id="win-trend">Waiting for data...</div>
        </div>

        <div class="card card-strategy">
            <h2>Strategy Advice</h2>
            <div class="strategy-content" id="strategy-content">
                <div class="advice-placeholder">No active advice</div>
            </div>
        </div>

        <div class="card card-macro">
            <h2>Macro Decision</h2>
            <div class="macro-content" id="macro-content">
                <div class="macro-action" id="macro-action">IDLE</div>
                <div class="macro-rationale" id="macro-rationale">--</div>
            </div>
        </div>

        <div class="card card-components">
            <h2>Component Health</h2>
            <div class="components-grid" id="components-grid"></div>
        </div>

        <div class="card card-events">
            <h2>Kill Feed</h2>
            <div class="events-list" id="events-list"></div>
        </div>

        <div class="card card-log">
            <h2>System Log</h2>
            <div class="log-container" id="log-container"></div>
        </div>
    </div>

    <footer class="footer">
        <span>lolbot-HyperAI &middot; Apollo-style Game Assistant</span>
        <span id="tick-counter">Tick: 0</span>
        <span id="uptime">Uptime: 0s</span>
    </footer>
</div>

<script>
{self._generate_js(api_base)}
</script>
</body>
</html>"""

    def _generate_css(self) -> str:
        """Generate embedded CSS."""
        c = _Colors
        return f"""
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    background: {c.BG_PRIMARY};
    color: {c.TEXT_PRIMARY};
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    font-size: 14px;
    line-height: 1.5;
}}
.dashboard {{ max-width: 1400px; margin: 0 auto; padding: 16px; }}
.header {{
    display: flex; justify-content: space-between; align-items: center;
    padding: 12px 0; border-bottom: 1px solid {c.BORDER}; margin-bottom: 16px;
}}
.title {{ font-size: 20px; font-weight: 600; }}
.status-indicator {{ display: flex; align-items: center; gap: 8px; }}
.dot {{
    width: 8px; height: 8px; border-radius: 50%;
    background: {c.ACCENT_YELLOW}; display: inline-block;
}}
.dot.connected {{ background: {c.ACCENT_GREEN}; }}
.dot.error {{ background: {c.ACCENT_RED}; }}
.grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    gap: 16px;
}}
.card {{
    background: {c.BG_CARD}; border: 1px solid {c.BORDER};
    border-radius: 8px; padding: 16px;
}}
.card h2 {{
    font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px;
    color: {c.TEXT_SECONDARY}; margin-bottom: 12px;
}}
.win-gauge {{
    position: relative; height: 48px; background: {c.BG_SECONDARY};
    border-radius: 6px; overflow: hidden; margin-bottom: 8px;
}}
.gauge-fill {{
    height: 100%; width: 50%; transition: width 0.5s ease, background 0.5s ease;
    background: {c.ACCENT_YELLOW}; border-radius: 6px;
}}
.gauge-text {{
    position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
    font-size: 22px; font-weight: 700;
}}
.win-trend {{ font-size: 12px; color: {c.TEXT_SECONDARY}; }}
.strategy-content {{ min-height: 60px; }}
.advice-placeholder {{ color: {c.TEXT_SECONDARY}; font-style: italic; }}
.advice-item {{
    padding: 8px; margin-bottom: 6px; border-radius: 4px;
    border-left: 3px solid {c.ACCENT_BLUE};
    background: {c.BG_SECONDARY};
}}
.advice-item.urgent {{ border-left-color: {c.ACCENT_RED}; }}
.macro-action {{
    font-size: 28px; font-weight: 700; text-transform: uppercase;
    color: {c.ACCENT_BLUE}; margin-bottom: 4px;
}}
.macro-rationale {{ font-size: 12px; color: {c.TEXT_SECONDARY}; }}
.components-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
.component-item {{
    padding: 8px; background: {c.BG_SECONDARY}; border-radius: 4px;
    display: flex; justify-content: space-between; align-items: center;
}}
.component-name {{ font-size: 12px; font-weight: 500; }}
.component-status {{ font-size: 11px; padding: 2px 8px; border-radius: 10px; }}
.component-status.ok {{ background: rgba(63,185,80,0.15); color: {c.ACCENT_GREEN}; }}
.component-status.warn {{ background: rgba(210,153,34,0.15); color: {c.ACCENT_YELLOW}; }}
.component-status.err {{ background: rgba(248,81,73,0.15); color: {c.ACCENT_RED}; }}
.events-list {{ max-height: 200px; overflow-y: auto; }}
.event-item {{
    padding: 4px 0; border-bottom: 1px solid {c.BORDER};
    font-size: 12px; display: flex; gap: 8px;
}}
.event-time {{ color: {c.TEXT_SECONDARY}; min-width: 50px; }}
.log-container {{
    max-height: 200px; overflow-y: auto; font-family: 'SFMono-Regular', Consolas, monospace;
    font-size: 11px; line-height: 1.6; color: {c.TEXT_SECONDARY};
}}
.log-line {{ white-space: pre-wrap; word-break: break-all; }}
.log-line.error {{ color: {c.ACCENT_RED}; }}
.log-line.warning {{ color: {c.ACCENT_YELLOW}; }}
.footer {{
    display: flex; justify-content: space-between;
    margin-top: 16px; padding-top: 12px; border-top: 1px solid {c.BORDER};
    font-size: 11px; color: {c.TEXT_SECONDARY};
}}
"""

    def _generate_js(self, api_base: str) -> str:
        """Generate embedded JavaScript."""
        return f"""
(function() {{
    const API = '{api_base}';
    const POLL_MS = {_REFRESH_INTERVAL_MS};
    const MAX_LOGS = {_MAX_LOG_LINES};
    let logs = [];
    let pollTimer = null;

    function setConnectionStatus(status) {{
        const el = document.getElementById('connection-status');
        const dot = el.querySelector('.dot');
        const text = el.querySelector('.text');
        dot.className = 'dot ' + status;
        text.textContent = status === 'connected' ? 'Connected' :
                           status === 'error' ? 'Disconnected' : 'Connecting...';
    }}

    function updateWinProb(prob) {{
        const fill = document.getElementById('gauge-fill');
        const text = document.getElementById('gauge-text');
        const trend = document.getElementById('win-trend');
        const pct = Math.round(prob * 100);
        fill.style.width = pct + '%';
        text.textContent = pct + '%';
        if (prob >= 0.55) {{
            fill.style.background = '{_Colors.WIN_HIGH}';
            trend.textContent = 'Favorable position';
        }} else if (prob >= 0.45) {{
            fill.style.background = '{_Colors.WIN_MID}';
            trend.textContent = 'Even game';
        }} else {{
            fill.style.background = '{_Colors.WIN_LOW}';
            trend.textContent = 'Behind — play safe';
        }}
    }}

    function updateComponents(components) {{
        const grid = document.getElementById('components-grid');
        let html = '';
        for (const [name, info] of Object.entries(components || {{}})) {{
            const status = info.error_count > 5 ? 'err' : info.error_count > 0 ? 'warn' : 'ok';
            const label = status === 'ok' ? 'OK' : status === 'warn' ? 'WARN' : 'ERR';
            html += '<div class="component-item">' +
                '<span class="component-name">' + name + '</span>' +
                '<span class="component-status ' + status + '">' + label + '</span></div>';
        }}
        grid.innerHTML = html || '<div style="color:#8b949e">No components</div>';
    }}

    function addLog(line) {{
        logs.push(line);
        if (logs.length > MAX_LOGS) logs.shift();
        const container = document.getElementById('log-container');
        const cls = line.includes('ERROR') ? 'error' : line.includes('WARN') ? 'warning' : '';
        container.innerHTML = logs.map(l => '<div class="log-line ' + cls + '">' + l + '</div>').join('');
        container.scrollTop = container.scrollHeight;
    }}

    async function poll() {{
        try {{
            const res = await fetch(API + '/state');
            if (!res.ok) throw new Error(res.status);
            const data = await res.json();
            setConnectionStatus('connected');

            if (data.win_probability !== undefined) updateWinProb(data.win_probability);
            if (data.components) updateComponents(data.components);
            if (data.tick_count !== undefined) {{
                document.getElementById('tick-counter').textContent = 'Tick: ' + data.tick_count;
            }}
            if (data.uptime_sec !== undefined) {{
                document.getElementById('uptime').textContent = 'Uptime: ' + Math.round(data.uptime_sec) + 's';
            }}
            if (data.strategy) {{
                const el = document.getElementById('strategy-content');
                const urgent = data.strategy.urgency === 'HIGH' || data.strategy.urgency === 'CRITICAL';
                el.innerHTML = '<div class="advice-item' + (urgent ? ' urgent' : '') + '">' +
                    data.strategy.text + '</div>';
            }}
            if (data.macro) {{
                document.getElementById('macro-action').textContent = data.macro.action || 'IDLE';
                document.getElementById('macro-rationale').textContent = data.macro.rationale || '--';
            }}
        }} catch (e) {{
            setConnectionStatus('error');
        }}
    }}

    function connectSSE() {{
        const es = new EventSource(API + '/logs/stream');
        es.onopen = () => setConnectionStatus('connected');
        es.onmessage = (e) => addLog(e.data);
        es.onerror = () => {{
            setConnectionStatus('error');
            es.close();
            setTimeout(connectSSE, {_SSE_RECONNECT_MS});
        }};
    }}

    // Start
    poll();
    pollTimer = setInterval(poll, POLL_MS);
    try {{ connectSSE(); }} catch(e) {{ console.warn('SSE not available'); }}
}})();
"""

    def stats(self) -> Dict[str, Any]:
        return {"generation_count": self._generation_count}


# ─── Convenience ─────────────────────────────────────────────────────────────

def generate_dashboard_html(port: int = 8080) -> str:
    """Generate the dashboard HTML page.

    Args:
        port: Dreamview API port.

    Returns:
        Complete HTML string.
    """
    return DashboardHTMLGenerator().generate(port=port)
