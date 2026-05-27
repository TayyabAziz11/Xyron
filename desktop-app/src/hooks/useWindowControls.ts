import { getCurrentWindow } from '@tauri-apps/api/window'

export function useWindowControls() {
  const win = getCurrentWindow()
  return {
    minimize: () => win.minimize(),
    maximize: async () => {
      if (await win.isMaximized()) win.unmaximize()
      else win.maximize()
    },
    close:    () => win.close(),
    hide:     () => win.hide(),
    show:     () => win.show().then(() => win.setFocus()),
  }
}
