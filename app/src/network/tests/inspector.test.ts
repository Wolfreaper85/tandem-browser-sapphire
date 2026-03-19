import { describe, expect, it } from 'vitest';
import { NetworkInspector } from '../inspector';

describe('NetworkInspector', () => {
  it('exports recent requests as HAR', () => {
    const inspector = new NetworkInspector();
    const mutableInspector = inspector as unknown as {
      requests: Array<{
        id: number;
        url: string;
        method: string;
        status: number;
        contentType: string;
        size: number;
        timestamp: number;
        initiator: string;
        domain: string;
        durationMs: number;
        requestHeaders: Record<string, string>;
        responseHeaders: Record<string, string[]>;
      }>;
    };

    mutableInspector.requests = [
      {
        id: 1,
        url: 'https://api.example.com/v1/users?id=42',
        method: 'GET',
        status: 200,
        contentType: 'application/json',
        size: 128,
        timestamp: Date.parse('2026-03-08T12:00:00.000Z'),
        initiator: 'https://example.com/app',
        domain: 'api.example.com',
        durationMs: 84,
        requestHeaders: {
          Accept: 'application/json',
        },
        responseHeaders: {
          'content-type': ['application/json'],
          'cache-control': ['no-cache'],
        },
      },
    ];

    const har = inspector.toHar();

    expect(har.log.version).toBe('1.2');
    expect(har.log.entries).toHaveLength(1);
    expect(har.log.entries[0]).toMatchObject({
      startedDateTime: '2026-03-08T12:00:00.000Z',
      time: 84,
      request: {
        method: 'GET',
        url: 'https://api.example.com/v1/users?id=42',
        queryString: [{ name: 'id', value: '42' }],
      },
      response: {
        status: 200,
        statusText: 'OK',
        content: {
          size: 128,
          mimeType: 'application/json',
        },
      },
    });
    expect(har.log.entries[0].request.headers).toContainEqual({
      name: 'Accept',
      value: 'application/json',
    });
    expect(har.log.entries[0].response.headers).toContainEqual({
      name: 'content-type',
      value: 'application/json',
    });
  });
});
