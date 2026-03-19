import { describe, expect, it } from 'vitest';
import { OutboundGuard } from '../outbound-guard';
import type { DomainInfo } from '../types';

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

function createGuard(...domains: DomainInfo[]) {
  const byDomain = new Map(domains.map((domain) => [domain.domain, domain]));
  const db = {
    getDomainInfo: (domain: string) => byDomain.get(domain) ?? null,
    isWhitelistedPair: () => false,
  };

  return new OutboundGuard(db as never);
}

describe('OutboundGuard mutating request containment', () => {
  it('escalates strict first-visit cross-origin mutations to fail-closed gatekeeper review', () => {
    const guard = createGuard(
      buildDomainInfo({
        domain: 'fresh.example',
        trustLevel: 18,
        visitCount: 0,
      }),
    );

    const decision = guard.analyzeOutbound({
      url: 'https://fresh.example/api/submit',
      method: 'POST',
      referrer: 'https://origin.example/form',
      uploadData: [{ bytes: Buffer.from('hello=world') }],
    } as never, 'strict');

    expect(decision).toMatchObject({
      action: 'flag',
      reason: 'first-visit-mutating-destination',
      severity: 'high',
      gatekeeperDecisionClass: 'deny_on_timeout',
    });
    expect(decision.explanation).toContain('fresh.example');
  });

  it('holds balanced trusted-to-untrusted transitions for gatekeeper review', () => {
    const guard = createGuard(
      buildDomainInfo({
        domain: 'dashboard.example',
        trustLevel: 82,
        visitCount: 12,
      }),
      buildDomainInfo({
        domain: 'collector.example',
        trustLevel: 20,
        visitCount: 2,
      }),
    );

    const decision = guard.analyzeOutbound({
      url: 'https://collector.example/ingest',
      method: 'PATCH',
      referrer: 'https://dashboard.example/app',
      uploadData: [{ bytes: Buffer.from('status=ok') }],
    } as never, 'balanced');

    expect(decision).toMatchObject({
      action: 'flag',
      reason: 'cross-origin-trusted-to-untrusted',
      severity: 'medium',
      gatekeeperDecisionClass: 'hold_for_decision',
    });
    expect(decision.context).toMatchObject({
      originDomain: 'dashboard.example',
      destinationDomain: 'collector.example',
      originTrust: 82,
      destinationTrust: 20,
    });
  });

  it('allows same-site cross-subdomain mutations to avoid noisy balanced-mode false positives', () => {
    const guard = createGuard(
      buildDomainInfo({
        domain: 'github.com',
        trustLevel: 90,
        visitCount: 50,
      }),
      buildDomainInfo({
        domain: 'api.github.com',
        trustLevel: 30,
        visitCount: 2,
      }),
    );

    const decision = guard.analyzeOutbound({
      url: 'https://api.github.com/_private/browser/stats',
      method: 'POST',
      referrer: 'https://github.com/',
      uploadData: [{ bytes: Buffer.from('ping=1') }],
    } as never, 'balanced');

    expect(decision).toMatchObject({
      action: 'allow',
      reason: 'same-site-cross-origin',
      severity: 'info',
    });
  });
});

describe('OutboundGuard WebSocket containment', () => {
  it('holds balanced unknown websocket endpoints without a referrer', () => {
    const guard = createGuard(
      buildDomainInfo({
        domain: 'socket.example',
        trustLevel: 22,
        visitCount: 1,
      }),
    );

    const decision = guard.analyzeWebSocket('wss://socket.example/live', undefined, 'balanced');

    expect(decision).toMatchObject({
      action: 'flag',
      reason: 'unknown-ws-no-referrer',
      severity: 'medium',
      gatekeeperDecisionClass: 'hold_for_decision',
    });
  });

  it('keeps permissive unknown websocket endpoints usable while still flagging them', () => {
    const guard = createGuard(
      buildDomainInfo({
        domain: 'socket.example',
        trustLevel: 22,
        visitCount: 1,
      }),
    );

    const decision = guard.analyzeWebSocket('wss://socket.example/live', 'https://app.example', 'permissive');

    expect(decision).toMatchObject({
      action: 'flag',
      reason: 'unknown-ws-endpoint',
      severity: 'medium',
      gatekeeperDecisionClass: undefined,
    });
  });
});
