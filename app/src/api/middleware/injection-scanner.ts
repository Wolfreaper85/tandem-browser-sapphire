// Injection Scanner Middleware — scans API responses for prompt injection patterns
// Wires PromptInjectionGuard into /page-content, /page-html, /execute-js routes

import type { Request, Response, NextFunction } from 'express';
import { PromptInjectionGuard, type PromptInjectionFinding } from '../../security/prompt-injection-guard';
import { createLogger } from '../../utils/logger';

const log = createLogger('InjectionScanner');
const guard = new PromptInjectionGuard();

// Per-domain override TTL (5 minutes)
const OVERRIDE_TTL_MS = 5 * 60 * 1000;
const overrides = new Map<string, number>();

/** Escape a string for safe JS embedding in modal HTML */
function escapeForJs(str: string): string {
  return str
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/"/g, '\\"')
    .replace(/\n/g, '\\n')
    .replace(/\r/g, '\\r')
    .replace(/</g, '\\x3c')
    .replace(/>/g, '\\x3e');
}

/** Check if a domain has an active override */
function hasActiveOverride(domain: string): boolean {
  const expires = overrides.get(domain);
  if (!expires) return false;
  if (Date.now() > expires) {
    overrides.delete(domain);
    return false;
  }
  return true;
}

/**
 * Express middleware that intercepts JSON responses from content endpoints
 * and scans them for prompt injection patterns.
 *
 * - riskScore >= 70: BLOCKS the response (replaces with error)
 * - riskScore 30-69: Adds injectionWarnings field to response
 * - riskScore < 30:  Passes through unchanged
 */
export function injectionScannerMiddleware(req: Request, res: Response, next: NextFunction): void {
  // Capture the original json method
  const originalJson = res.json.bind(res);

  res.json = function(body: unknown): Response {
    try {
      if (!body || typeof body !== 'object') {
        return originalJson(body);
      }

      const data = body as Record<string, unknown>;

      // Extract text content based on route shape
      const text = (data.text as string) || (data.result as string) || '';
      const html = (data.html as string) || '';
      const url = (data.url as string) || req.query.url as string || '';

      if (!text && !html) {
        return originalJson(body);
      }

      // Get domain for override checking
      let domain = '';
      try {
        if (url) domain = new URL(url).hostname;
      } catch { /* ignore invalid URLs */ }

      // Skip search engines — they naturally contain zero-width chars, aria-labels, etc.
      const SEARCH_ENGINE_HOSTS = ['duckduckgo.com', 'google.com', 'bing.com', 'search.yahoo.com'];
      if (domain && SEARCH_ENGINE_HOSTS.some(h => domain === h || domain.endsWith(`.${h}`))) {
        return originalJson(body);
      }

      // Skip if domain has an active override
      if (domain && hasActiveOverride(domain)) {
        return originalJson(body);
      }

      // Run the scan
      const report = guard.scan(text, html || undefined);

      if (report.clean) {
        return originalJson(body);
      }

      log.warn(`Injection scan for ${url || 'unknown'}: ${report.summary}`);

      if (report.riskScore >= 70) {
        // BLOCK — don't return the content
        log.warn(`BLOCKED content from ${url} (risk: ${report.riskScore})`);

        // Emit SSE event so browser shell can show a modal
        emitInjectionAlert('blocked', domain, report.riskScore, report.findings, report.summary);

        return originalJson({
          error: 'Content blocked by Prompt Injection Guard',
          injectionReport: {
            riskScore: report.riskScore,
            findings: report.findings,
            summary: report.summary,
            blocked: true,
            domain,
          },
          url,
        });
      }

      // WARN — return content with warnings attached
      log.info(`Injection warning for ${url} (risk: ${report.riskScore})`);
      emitInjectionAlert('warning', domain, report.riskScore, report.findings, report.summary);

      data.injectionWarnings = {
        riskScore: report.riskScore,
        findings: report.findings,
        summary: report.summary,
        domain,
      };

      return originalJson(data);
    } catch (err) {
      // Scanner failure should never break the API
      log.error('Injection scanner error (passing through):', err);
      return originalJson(body);
    }
  };

  next();
}

// ═══ Injection alert event system ═══
// Allows the browser shell to subscribe via SSE and show modals

interface InjectionAlert {
  type: 'blocked' | 'warning';
  domain: string;
  riskScore: number;
  findings: PromptInjectionFinding[];
  summary: string;
  timestamp: number;
}

let latestAlert: InjectionAlert | null = null;
const alertListeners: Array<(alert: InjectionAlert) => void> = [];

function emitInjectionAlert(type: 'blocked' | 'warning', domain: string, riskScore: number, findings: PromptInjectionFinding[], summary: string): void {
  const alert: InjectionAlert = { type, domain, riskScore, findings, summary, timestamp: Date.now() };
  latestAlert = alert;
  for (const listener of alertListeners) {
    try { listener(alert); } catch { /* ignore */ }
  }
}

/** Register SSE endpoint for injection alerts + override route */
export function registerInjectionRoutes(router: { get: Function; post: Function }): void {
  // SSE stream — browser shell subscribes to this for real-time injection alerts
  router.get('/security/injection-alerts', (_req: Request, res: Response) => {
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    });
    res.write('data: {"connected":true}\n\n');

    const listener = (alert: InjectionAlert) => {
      res.write(`data: ${JSON.stringify(alert)}\n\n`);
    };
    alertListeners.push(listener);

    res.on('close', () => {
      const idx = alertListeners.indexOf(listener);
      if (idx >= 0) alertListeners.splice(idx, 1);
    });
  });

  // GET latest alert (for polling fallback)
  router.get('/security/injection-latest', (_req: Request, res: Response) => {
    res.json(latestAlert || { type: 'none' });
  });

  registerOverrideRoute(router);
}

/**
 * POST /security/injection-override
 * Allows user to temporarily bypass injection blocking for a domain.
 * Requires double confirmation (handled by frontend modal).
 */
export function registerOverrideRoute(router: { post: Function }): void {
  router.post('/security/injection-override', (req: Request, res: Response) => {
    const { domain, confirmed } = req.body || {};

    if (!domain || typeof domain !== 'string') {
      res.status(400).json({ error: 'domain is required' });
      return;
    }

    if (!confirmed) {
      res.status(400).json({ error: 'Double confirmation required' });
      return;
    }

    const expires = Date.now() + OVERRIDE_TTL_MS;
    overrides.set(domain, expires);
    log.warn(`Override granted for ${domain} — expires in 5 minutes`);

    res.json({
      ok: true,
      domain,
      expiresAt: new Date(expires).toISOString(),
      ttlMs: OVERRIDE_TTL_MS,
    });
  });
}
