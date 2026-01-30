/**
 * Vercel Serverless Function Entry Point
 */
const express = require('express');
const app = express();

app.use(express.json());

// CORS
app.use((req, res, next) => {
    res.header('Access-Control-Allow-Origin', '*');
    res.header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    res.header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-GitHub-Token');
    if (req.method === 'OPTIONS') return res.sendStatus(200);
    next();
});

// Health check
app.get('/health', (req, res) => {
    res.json({ status: 'healthy', version: '1.0.0', service: 'agentos-copilot' });
});

// Root
app.get('/', (req, res) => {
    res.json({
        name: 'AgentOS Copilot Extension',
        version: '1.0.0',
        description: 'Build safe AI agents with natural language',
        endpoints: { health: '/health', copilot: '/api/copilot', setup: '/setup' }
    });
});

// Setup page
app.get('/setup', (req, res) => {
    res.send(`
        <html><head><title>AgentOS Setup</title></head>
        <body style="font-family:system-ui;max-width:600px;margin:2rem auto;padding:1rem;">
        <h1 style="color:#10b981;">🤖 AgentOS Setup</h1>
        <p>Welcome! Use <code>@agentos help</code> in GitHub Copilot Chat.</p>
        <p><a href="https://imran-siddique.github.io/agent-os-docs/tutorials/copilot-extension/">📚 Documentation</a></p>
        </body></html>
    `);
});

// Main Copilot endpoint
app.post('/api/copilot', async (req, res) => {
    try {
        const { messages } = req.body;
        const userMsg = messages?.filter(m => m.role === 'user').pop();
        const content = userMsg?.content || '';
        
        let response = "👋 Welcome to AgentOS! Try:\n• `@agentos create an API monitor agent`\n• `@agentos templates`\n• `@agentos help`";
        
        if (content.toLowerCase().includes('help')) {
            response = `# 🤖 AgentOS Help

**Commands:**
• \`create <description>\` - Create agent from natural language
• \`templates [category]\` - Browse 50+ templates
• \`compliance <framework>\` - Check GDPR, HIPAA, SOC2, PCI-DSS
• \`test\` - Test your agent
• \`deploy\` - Deploy to GitHub Actions
• \`review\` - CMVK multi-model review

**Example:** \`@agentos create an agent that monitors my API and alerts on errors\``;
        } else if (content.toLowerCase().includes('template')) {
            response = `# 📚 Agent Templates

**Categories:**
• **devops** - CI/CD, monitoring, deployments
• **data-processing** - ETL, analytics, reports
• **security** - Audits, scanning, compliance
• **customer-support** - Tickets, chatbots, feedback
• **automation** - Workflows, scheduling, notifications

**Try:** \`@agentos templates devops\``;
        } else if (content.toLowerCase().includes('create')) {
            response = `# ✅ Agent Created!

\`\`\`python
# Generated Agent: API Monitor
import requests
import schedule

def check_api():
    response = requests.get("https://api.example.com/health")
    if response.status_code != 200:
        send_alert(f"API unhealthy: {response.status_code}")

schedule.every(5).minutes.do(check_api)
\`\`\`

**Next steps:**
• \`@agentos test\` - Run test scenarios
• \`@agentos compliance gdpr\` - Check compliance
• \`@agentos deploy\` - Deploy to GitHub Actions`;
        }
        
        res.json({ choices: [{ message: { role: 'assistant', content: response } }] });
    } catch (error) {
        res.json({ choices: [{ message: { role: 'assistant', content: '❌ Error processing request. Try again.' } }] });
    }
});

// Webhook
app.post('/api/webhook', (req, res) => {
    console.log('Webhook received:', req.headers['x-github-event']);
    res.json({ received: true });
});

// OAuth callback
app.get('/auth/callback', (req, res) => {
    res.send('<html><body><h1>✅ AgentOS Installed!</h1><p>Return to GitHub Copilot Chat and try @agentos help</p></body></html>');
});

module.exports = app;
