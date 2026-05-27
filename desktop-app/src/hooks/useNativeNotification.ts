import { sendNotification, isPermissionGranted, requestPermission } from '@tauri-apps/plugin-notification'

export async function notify(title: string, body: string) {
  let granted = await isPermissionGranted()
  if (!granted) {
    const perm = await requestPermission()
    granted = perm === 'granted'
  }
  if (granted) sendNotification({ title, body })
}

export function useNativeNotification() {
  return { notify }
}
