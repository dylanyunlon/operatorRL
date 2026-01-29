/**
 * Agent OS Copilot Extension
 * 
 * Main entry point for the GitHub Copilot Extension.
 * Provides safety verification for Copilot suggestions.
 * 
 * Features:
 * - Agent creation from natural language
 * - 50+ agent templates
 * - Policy-aware code suggestions
 * - CMVK multi-model verification
 * - Compliance checking (GDPR, HIPAA, SOC2, PCI DSS)
 * - GitHub Actions deployment
 */

import express, { Request, Response } from 'express';
import { CopilotExtension } from './copilotExtension';
import { PolicyEngine } from './policyEngine';
import { CMVKClient } from './cmvkClient';
import { AuditLogger } from './auditLogger';
import { TemplateGallery } from './templateGallery';
import { PolicyLibrary } from './policyLibrary';
import { logger } from './logger';
import dotenv from 'dotenv';

dotenv.config();

const app = express();
app.use(express.json());

// Initialize components
const policyEngine = new PolicyEngine();
const cmvkClient = new CMVKClient();
const auditLogger = new AuditLogger();
const extension = new CopilotExtension(policyEngine, cmvkClient, auditLogger);
const templateGallery = new TemplateGallery();
const policyLibrary = new PolicyLibrary();

// Health check endpoint
app.get('/health', (req: Request, res: Response) => {
    res.json({
        status: 'healthy',
        version: '1.0.0',
        service: 'agent-os-copilot-extension'
    });
});

// GitHub Copilot Extension webhook endpoints

/**
 * Filter endpoint - Called by Copilot to filter suggestions
 * POST /api/filter
 */
app.post('/api/filter', async (req: Request, res: Response) => {
    try {
        const { suggestions, context } = req.body;
        
        logger.info('Filtering suggestions', { 
            count: suggestions?.length,
            file: context?.file?.path 
        });

        const result = await extension.filterSuggestions(suggestions, context);
        
        res.json(result);
    } catch (error) {
        logger.error('Filter error', { error });
        res.status(500).json({ error: 'Internal server error' });
    }
});

/**
 * Chat endpoint - Called by Copilot Chat for @agent-os commands
 * POST /api/chat
 */
app.post('/api/chat', async (req: Request, res: Response) => {
    try {
        const { message, context, command } = req.body;
        
        logger.info('Chat request', { command, messageLength: message?.length });

        const response = await extension.handleChatMessage(message, context, command);
        
        res.json(response);
    } catch (error) {
        logger.error('Chat error', { error });
        res.status(500).json({ error: 'Internal server error' });
    }
});

/**
 * Annotation endpoint - Called to get safety annotations for code
 * POST /api/annotate
 */
app.post('/api/annotate', async (req: Request, res: Response) => {
    try {
        const { code, language, context } = req.body;
        
        const annotations = await extension.annotateCode(code, language, context);
        
        res.json(annotations);
    } catch (error) {
        logger.error('Annotation error', { error });
        res.status(500).json({ error: 'Internal server error' });
    }
});

/**
 * Audit endpoint - Get audit log
 * GET /api/audit
 */
app.get('/api/audit', (req: Request, res: Response) => {
    const limit = parseInt(req.query.limit as string) || 20;
    const logs = auditLogger.getRecent(limit);
    res.json({ logs });
});

/**
 * Policy endpoint - Get or update policies
 * GET/POST /api/policy
 */
app.get('/api/policy', (req: Request, res: Response) => {
    const policies = policyEngine.getActivePolicies();
    res.json({ policies });
});

app.post('/api/policy', async (req: Request, res: Response) => {
    try {
        const { policy, enabled } = req.body;
        policyEngine.setPolicy(policy, enabled);
        res.json({ success: true, policies: policyEngine.getActivePolicies() });
    } catch (error) {
        res.status(400).json({ error: 'Invalid policy configuration' });
    }
});

/**
 * Templates endpoint - List and search templates
 * GET /api/templates
 */
app.get('/api/templates', (req: Request, res: Response) => {
    const query = req.query.q as string;
    const category = req.query.category as string;
    const limit = parseInt(req.query.limit as string) || 20;
    
    const results = templateGallery.search(query, category as any, undefined, limit);
    res.json(results);
});

/**
 * Template by ID
 * GET /api/templates/:id
 */
app.get('/api/templates/:id', (req: Request, res: Response) => {
    const template = templateGallery.getById(req.params.id);
    if (template) {
        res.json(template);
    } else {
        res.status(404).json({ error: 'Template not found' });
    }
});

/**
 * Compliance frameworks
 * GET /api/compliance
 */
app.get('/api/compliance', (req: Request, res: Response) => {
    const frameworks = policyLibrary.getFrameworks();
    res.json({ frameworks });
});

/**
 * Validate code against compliance framework
 * POST /api/compliance/validate
 */
app.post('/api/compliance/validate', (req: Request, res: Response) => {
    try {
        const { code, language, framework } = req.body;
        const policyId = `${framework}-standard`;
        const result = policyLibrary.validateAgainstPolicy(code, language, policyId);
        res.json(result);
    } catch (error) {
        res.status(400).json({ error: 'Validation failed' });
    }
});

/**
 * Health check with detailed status
 * GET /api/status
 */
app.get('/api/status', (req: Request, res: Response) => {
    const stats = auditLogger.getStats();
    res.json({
        status: 'healthy',
        version: '1.0.0',
        service: 'agent-os-copilot-extension',
        stats: {
            blockedToday: stats.blockedToday,
            reviewsToday: stats.cmvkReviewsToday,
            templatesAvailable: templateGallery.search().totalCount,
            activePolicies: policyEngine.getActivePolicies().filter(p => p.enabled).length
        }
    });
});

// Start server
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
    logger.info(`Agent OS Copilot Extension running on port ${PORT}`);
    logger.info('Endpoints:');
    logger.info('  POST /api/filter   - Filter Copilot suggestions');
    logger.info('  POST /api/chat     - Handle @agent-os chat commands');
    logger.info('  POST /api/annotate - Get safety annotations');
    logger.info('  GET  /api/audit    - Get audit log');
    logger.info('  GET  /api/policy   - Get active policies');
});

export { app };
