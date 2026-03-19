import { createServer } from 'http';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RequestDispatcher } from '../../network/dispatcher';
import { GatekeeperWebSocket } from '../gatekeeper-ws';
import { Guardian } from '../guardian';
import type { DomainInfo, PendingDecision } from '../types';

function buildDomainInfo(overrides: Partial<DomainInfo>): DomainInfo {
  return {
    domain: 'example.com',
    firstSeen: Date.now(),
    lastSeen: Date.now(),
    visitCount: 1,
    trustLevel: 30,
    guardianMode: 'balanced',
    category: 'general',
    notes: null,
    ...overrides,
  };
}

function createGuardianHarness(domainInfo: DomainInfo | null) {
  const infoRef = { current: domainInfo };
  const events: Array<{ eventType: string; details: string }> = [];
  const db = {
    getDomainInfo: vi.fn((domain: string) => {
      if (!infoRef.current || infoRef.current.domain !== domain) return null;
      return { ...infoRef.current };
    }),
    upsertDomain: vi.fn((domain: string, update: Partial<DomainInfo>) => {
      const existing = infoRef.current?.domain === domain ? infoRef.current : buildDomainInfo({ domain });
      infoRef.current = { ...existing, ...update };
    }),
    logEvent: vi.fn((event: { eventType: string; details: string }) => {
      events.push(event);
    }),
    isWhitelistedPair: vi.fn(() => false),
  };
  const shield = {
    checkUrl: vi.fn(() => ({ blocked: false })),
  };
  const outboundGuard = {
    analyzeWebSocket: vi.fn(() => ({
      action: 'allow',
      reason: 'same-origin-ws',
      severity: 'info',
      explanation: 'Allowed because the WebSocket stays on the same origin.',
    })),
    analyzeOutbound: vi.fn(() => ({
      action: 'allow',
      reason: 'no-threat-detected',
      severity: 'info',
      explanation: 'Allowed because outbound analysis found no containment signal.',
    })),
  };

  return {
    guardian: new Guardian(db as never, shield as never, outboundGuard as never),
    db,
    outboundGuard,
    events,
  };
}

describe('RequestDispatcher async gatekeeper support', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('waits for async onBeforeRequest handlers before resolving the Electron callback', async () => {
    let beforeRequestHandler:
      | ((details: Electron.OnBeforeRequestListenerDetails, callback: (result: { cancel: boolean }) => void) => void)
      | undefined;

    const session = {
      webRequest: {
        onBeforeRequest: vi.fn((handler) => {
          beforeRequestHandler = handler;
        }),
        onBeforeSendHeaders: vi.fn(),
        onHeadersReceived: vi.fn(),
        onBeforeRedirect: vi.fn(),
        onCompleted: vi.fn(),
        onErrorOccurred: vi.fn(),
      },
    };

    const dispatcher = new RequestDispatcher(session as never);
    dispatcher.registerBeforeRequest({
      name: 'AsyncGatekeeper',
      priority: 1,
      handler: async () => {
        await new Promise(resolve => setTimeout(resolve, 25));
        return { cancel: true };
      },
    });

    dispatcher.attach();
    expect(beforeRequestHandler).toBeTypeOf('function');

    const callback = vi.fn();
    beforeRequestHandler?.({ url: 'https://example.com' } as never, callback);

    expect(callback).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(25);
    expect(callback).toHaveBeenCalledWith({ cancel: true });
  });
});

describe('Guardian gatekeeper enforcement', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('holds risky first-visit navigations until Gatekeeper responds', async () => {
    const { guardian, db, events } = createGuardianHarness(null);

    const gatekeeperWs = {
      getStatus: vi.fn(() => ({
        connected: true,
        pendingDecisions: 0,
        totalDecisions: 0,
        lastAgentSeen: Date.now(),
      })),
      sendDecisionRequest: vi.fn((item: PendingDecision) => {
        setTimeout(() => {
          guardian.submitDecision(item.id, {
            action: 'allow',
            reason: 'agent-reviewed',
            confidence: 88,
          });
        }, 20);
      }),
    };

    guardian.setGatekeeper(gatekeeperWs as never);

    const resultPromise = (guardian as any).checkRequest({
      url: 'http://fresh.example/welcome',
      method: 'GET',
      referrer: '',
      resourceType: 'mainFrame',
    });

    await vi.advanceTimersByTimeAsync(20);

    await expect(resultPromise).resolves.toBeNull();
    expect(gatekeeperWs.sendDecisionRequest).toHaveBeenCalledWith(
      expect.objectContaining({
        domain: 'fresh.example',
        decisionClass: 'hold_for_decision',
        defaultAction: 'allow',
      }),
    );
    expect(db.logEvent).toHaveBeenCalled();
    expect(events.map(event => event.eventType)).toContain('gatekeeper_held');
    expect(events.map(event => event.eventType)).toContain('gatekeeper_allowed');
  });

  it('fails closed for strict low-trust scripts when Gatekeeper is unavailable', async () => {
    const { guardian, events } = createGuardianHarness(buildDomainInfo({
      domain: 'scripts.example',
      trustLevel: 12,
      guardianMode: 'strict',
    }));

    const result = await (guardian as any).checkRequest({
      url: 'https://scripts.example/app.js',
      method: 'GET',
      referrer: 'https://bank.example/dashboard',
      resourceType: 'script',
    });

    expect(result).toEqual({ cancel: true });
    expect(events.map(event => event.eventType)).toContain('gatekeeper_blocked');
  });

  it('holds flagged mutating requests when outbound containment requests review', async () => {
    const { guardian, outboundGuard, events } = createGuardianHarness(buildDomainInfo({
      domain: 'collector.example',
      trustLevel: 20,
      guardianMode: 'balanced',
      visitCount: 1,
    }));

    outboundGuard.analyzeOutbound.mockReturnValue({
      action: 'flag',
      reason: 'cross-origin-trusted-to-untrusted',
      severity: 'medium',
      explanation: 'Flagged because a trusted page is mutating a lower-trust destination.',
      gatekeeperDecisionClass: 'hold_for_decision',
      context: {
        originDomain: 'dashboard.example',
        destinationDomain: 'collector.example',
      },
    });

    const gatekeeperWs = {
      getStatus: vi.fn(() => ({
        connected: true,
        pendingDecisions: 0,
        totalDecisions: 0,
        lastAgentSeen: Date.now(),
      })),
      sendDecisionRequest: vi.fn((item: PendingDecision) => {
        setTimeout(() => {
          guardian.submitDecision(item.id, {
            action: 'allow',
            reason: 'agent-reviewed',
            confidence: 91,
          });
        }, 20);
      }),
    };

    guardian.setGatekeeper(gatekeeperWs as never);

    const resultPromise = (guardian as any).checkRequest({
      url: 'https://collector.example/ingest',
      method: 'POST',
      referrer: 'https://dashboard.example/app',
      resourceType: 'xhr',
      uploadData: [{ bytes: Buffer.from('status=ok') }],
    });

    await vi.advanceTimersByTimeAsync(20);

    await expect(resultPromise).resolves.toBeNull();
    expect(gatekeeperWs.sendDecisionRequest).toHaveBeenCalledWith(
      expect.objectContaining({
        domain: 'collector.example',
        decisionClass: 'hold_for_decision',
      }),
    );
    expect(events.map(event => event.eventType)).toContain('gatekeeper_held');
    expect(events.map(event => event.eventType)).toContain('gatekeeper_allowed');
  });

  it('fails closed for strict unknown websocket containment when Gatekeeper is unavailable', async () => {
    const { guardian, outboundGuard, events } = createGuardianHarness(buildDomainInfo({
      domain: 'socket.example',
      trustLevel: 15,
      guardianMode: 'strict',
      visitCount: 1,
    }));

    outboundGuard.analyzeWebSocket.mockReturnValue({
      action: 'flag',
      reason: 'unknown-ws-endpoint',
      severity: 'high',
      explanation: 'Flagged because the WebSocket destination is still unknown.',
      gatekeeperDecisionClass: 'deny_on_timeout',
      context: {
        destinationDomain: 'socket.example',
      },
    });

    const result = await (guardian as any).checkRequest({
      url: 'wss://socket.example/live',
      method: 'GET',
      referrer: 'https://app.example',
      resourceType: 'webSocket',
    });

    expect(result).toEqual({ cancel: true });
    expect(events.map(event => event.eventType)).toContain('gatekeeper_blocked');
  });
});

describe('GatekeeperWebSocket timeout policy', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(GatekeeperWebSocket.prototype as never, 'getOrCreateSecret' as never).mockReturnValue('test-secret');
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('uses the pending decision default action on timeout', async () => {
    const guardian = {
      submitDecision: vi.fn(),
      setMode: vi.fn(),
    };
    const db = {
      logEvent: vi.fn(),
      upsertDomain: vi.fn(),
    };
    const server = createServer();
    const gatekeeper = new GatekeeperWebSocket(server, guardian as never, db as never);

    gatekeeper.sendDecisionRequest({
      id: 'deny-1',
      category: 'request',
      domain: 'danger.example',
      decisionClass: 'deny_on_timeout',
      context: {
        trust: 10,
        mode: 'strict',
      },
      defaultAction: 'block',
      timeout: 1000,
      createdAt: Date.now(),
    });

    gatekeeper.sendDecisionRequest({
      id: 'hold-1',
      category: 'request',
      domain: 'new.example',
      decisionClass: 'hold_for_decision',
      context: {
        trust: 30,
        mode: 'balanced',
      },
      defaultAction: 'allow',
      timeout: 1000,
      createdAt: Date.now(),
    });

    await vi.advanceTimersByTimeAsync(1000);

    expect(guardian.submitDecision).toHaveBeenNthCalledWith(1, 'deny-1', expect.objectContaining({ action: 'block' }));
    expect(guardian.submitDecision).toHaveBeenNthCalledWith(2, 'hold-1', expect.objectContaining({ action: 'allow' }));
    expect(gatekeeper.getHistory(2)).toEqual([
      expect.objectContaining({
        id: 'deny-1',
        decisionClass: 'deny_on_timeout',
        action: 'block',
        source: 'timeout',
      }),
      expect.objectContaining({
        id: 'hold-1',
        decisionClass: 'hold_for_decision',
        action: 'allow',
        source: 'timeout',
      }),
    ]);

    gatekeeper.destroy();
    server.close();
  });
});
