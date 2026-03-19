import type { Router, Request, Response } from 'express';
import type { RouteContext } from '../context';
import { handleRouteError } from '../../utils/errors';

export function registerSidebarRoutes(router: Router, ctx: RouteContext): void {
  // GET /sidebar/config
  router.get('/sidebar/config', (_req: Request, res: Response) => {
    try {
      res.json({ ok: true, config: ctx.sidebarManager.getConfig() });
    } catch (e) { handleRouteError(res, e); }
  });

  // POST /sidebar/config — update state, activeItemId, item order
  router.post('/sidebar/config', (req: Request, res: Response) => {
    try {
      const config = ctx.sidebarManager.updateConfig(req.body);
      res.json({ ok: true, config });
    } catch (e) { handleRouteError(res, e); }
  });

  // POST /sidebar/items/:id/toggle — enable/disable an item
  router.post('/sidebar/items/:id/toggle', (req: Request, res: Response) => {
    try {
      const item = ctx.sidebarManager.toggleItem(req.params.id as string);
      if (!item) { res.status(404).json({ error: 'Item not found' }); return; }
      res.json({ ok: true, item });
    } catch (e) { handleRouteError(res, e); }
  });

  // POST /sidebar/items/:id/activate — open panel (or close it if already active)
  router.post('/sidebar/items/:id/activate', (req: Request, res: Response) => {
    try {
      const cfg = ctx.sidebarManager.getConfig();
      const id = req.params.id as string;
      const newActive = cfg.activeItemId === id ? null : id;
      ctx.sidebarManager.setActiveItem(newActive);
      res.json({ ok: true, activeItemId: newActive });
    } catch (e) { handleRouteError(res, e); }
  });

  // POST /sidebar/reorder — drag-to-reorder
  router.post('/sidebar/reorder', (req: Request, res: Response) => {
    try {
      const { orderedIds } = req.body; // string[]
      if (!Array.isArray(orderedIds)) { res.status(400).json({ error: 'orderedIds must be array' }); return; }
      ctx.sidebarManager.reorderItems(orderedIds);
      res.json({ ok: true, config: ctx.sidebarManager.getConfig() });
    } catch (e) { handleRouteError(res, e); }
  });

  // POST /sidebar/state — toggle hidden/narrow/wide
  router.post('/sidebar/state', (req: Request, res: Response) => {
    try {
      const { state } = req.body;
      if (!['hidden', 'narrow', 'wide'].includes(state)) {
        res.status(400).json({ error: 'state must be hidden|narrow|wide' }); return;
      }
      ctx.sidebarManager.setState(state);
      res.json({ ok: true, state });
    } catch (e) { handleRouteError(res, e); }
  });
}
