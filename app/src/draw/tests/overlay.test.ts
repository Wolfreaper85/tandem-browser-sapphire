import fs from 'fs';
import path from 'path';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { clipboardWriteImage, nativeImageCreateFromBuffer } = vi.hoisted(() => ({
  clipboardWriteImage: vi.fn(),
  nativeImageCreateFromBuffer: vi.fn().mockReturnValue({ isEmpty: () => false }),
}));

vi.mock('electron', () => ({
  app: {
    getPath: vi.fn().mockReturnValue('/tmp/tandem-user-data'),
  },
  clipboard: {
    writeImage: clipboardWriteImage,
  },
  nativeImage: {
    createFromBuffer: nativeImageCreateFromBuffer,
  },
  webContents: {
    fromId: vi.fn(),
  },
}));

vi.mock('fs', async () => {
  const actual = await vi.importActual<typeof fs>('fs');
  return {
    ...actual,
    default: {
      ...actual,
      existsSync: vi.fn().mockReturnValue(false),
      mkdirSync: vi.fn(),
      writeFileSync: vi.fn(),
      readFileSync: vi.fn(),
      readdirSync: vi.fn().mockReturnValue([]),
    },
    existsSync: vi.fn().mockReturnValue(false),
    mkdirSync: vi.fn(),
    writeFileSync: vi.fn(),
    readFileSync: vi.fn(),
    readdirSync: vi.fn().mockReturnValue([]),
  };
});

import { DrawOverlayManager } from '../overlay';

describe('DrawOverlayManager screenshot modes', () => {
  const pngBuffer = Buffer.from('png-data');

  const win = {
    webContents: {
      send: vi.fn(),
    },
    capturePage: vi.fn().mockResolvedValue({
      toPNG: () => pngBuffer,
    }),
    getContentSize: vi.fn().mockReturnValue([1200, 800]),
  } as any;

  const configManager = {
    getConfig: vi.fn().mockReturnValue({
      screenshots: {
        applePhotos: false,
        googlePhotos: false,
      },
    }),
  } as any;

  const googlePhotosManager = {
    uploadScreenshot: vi.fn().mockResolvedValue(null),
  } as any;

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fs.existsSync).mockReturnValue(false);
  });

  it('captures the full application window and emits a screenshot event', async () => {
    const manager = new DrawOverlayManager(win, configManager, googlePhotosManager);

    const result = await manager.captureApplicationScreenshot('https://example.com/demo');

    expect(result.ok).toBe(true);
    expect(win.capturePage).toHaveBeenCalledWith();
    expect(clipboardWriteImage).toHaveBeenCalled();
    expect(fs.writeFileSync).toHaveBeenCalledTimes(2);
    expect(win.webContents.send).toHaveBeenCalledWith(
      'screenshot-taken',
      expect.objectContaining({
        path: expect.stringContaining(path.join('Pictures', 'Tandem')),
        appPath: expect.stringContaining(path.join('/tmp/tandem-user-data', 'screenshots')),
        base64: pngBuffer.toString('base64'),
      }),
    );
  });

  it('captures a clamped region inside the application window', async () => {
    const manager = new DrawOverlayManager(win, configManager, googlePhotosManager);

    const result = await manager.captureRegionScreenshot(
      { x: -10, y: 20, width: 1400, height: 900 },
      'https://example.com/demo',
    );

    expect(result.ok).toBe(true);
    expect(win.capturePage).toHaveBeenCalledWith({
      x: 0,
      y: 20,
      width: 1200,
      height: 780,
    });
  });
});
