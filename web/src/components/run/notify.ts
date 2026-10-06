// Browser notifications when a run ends (CR02). Permission is asked once, on the first Run
// click, and never waited on: a refusal (or a browser without notifications) changes nothing.

let asked = false;

function api(): typeof Notification | null {
  return typeof Notification === 'undefined' ? null : Notification;
}

export function askNotifyOnce(): void {
  if (asked) return;
  asked = true;
  const N = api();
  if (!N || N.permission !== 'default') return;
  try {
    const p = N.requestPermission() as Promise<NotificationPermission> | undefined;
    p?.catch(() => {});
  } catch {
    /* an old browser's callback form, or blocked: ignore */
  }
}

export function notify(title: string, body?: string): void {
  const N = api();
  if (!N || N.permission !== 'granted') return;
  try {
    new N(title, { body, tag: 'control-room-run' });
  } catch {
    /* some browsers only allow notifications from a service worker */
  }
}

/** Tests only: forget that permission was asked. */
export function resetNotifyForTests(): void {
  asked = false;
}
