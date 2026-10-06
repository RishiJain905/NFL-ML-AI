// The confirm dialog (mockup: "Run 2026 week 5?"). Built on Radix Dialog for focus handling
// and Escape. CR02 uses it for the Run, Resume and injury-update buttons.

import * as Dialog from '@radix-ui/react-dialog';
import type { ReactNode } from 'react';

export function ConfirmDialog({
  open,
  onOpenChange,
  eyebrow,
  title,
  children,
  confirmLabel,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  eyebrow?: string;
  title: string;
  children?: ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="scrim" />
        <Dialog.Content
          className="modal"
          style={{ position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%, -50%)', zIndex: 41 }}
          aria-describedby={undefined}
        >
          <header>
            {eyebrow ? <span className="eyebrow">{eyebrow}</span> : null}
            <Dialog.Title asChild>
              <h2>{title}</h2>
            </Dialog.Title>
          </header>
          <div className="mb">{children}</div>
          <footer>
            <Dialog.Close asChild>
              <button type="button" className="btn">
                Cancel
              </button>
            </Dialog.Close>
            <button
              type="button"
              className="btn primary"
              autoFocus
              onClick={() => {
                onOpenChange(false);
                onConfirm();
              }}
            >
              {confirmLabel}
            </button>
          </footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
