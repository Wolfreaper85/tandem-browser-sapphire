export type SidebarState = 'hidden' | 'narrow' | 'wide';
export type SidebarItemType = 'panel' | 'webview';

export interface SidebarItem {
  id: string;
  label: string;
  icon: string;         // SVG string (Heroicons outline)
  type: SidebarItemType;
  enabled: boolean;
  order: number;
}

export interface SidebarConfig {
  state: SidebarState;
  activeItemId: string | null;
  panelPinned: boolean;
  panelWidths: Record<string, number>;
  items: SidebarItem[];
}
