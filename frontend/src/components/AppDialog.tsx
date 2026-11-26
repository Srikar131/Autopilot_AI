import { useEffect, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';

interface AppDialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  titleId: string;
  maxWidthClass?: string;
  children: ReactNode;
}

export function AppDialog({
  open,
  onClose,
  title,
  titleId,
  maxWidthClass = 'max-w-3xl',
  children,
}: AppDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    if (open && !dialog.open) {
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    const handleClose = () => {
      dialog.returnValue = '';
      onClose();
    };

    dialog.addEventListener('close', handleClose);

    // Fallback light-dismiss for browsers without native closedby="any" (e.g. Safari)
    let cleanupFallback: (() => void) | undefined;
    if (!('closedBy' in HTMLDialogElement.prototype)) {
      let pressedOutside = false;
      const isOutside = (e: PointerEvent) => {
        const rect = dialog.getBoundingClientRect();
        return (
          e.clientX < rect.left ||
          e.clientX > rect.right ||
          e.clientY < rect.top ||
          e.clientY > rect.bottom
        );
      };
      const onPointerDown = (e: PointerEvent) => {
        pressedOutside = isOutside(e);
      };
      const onPointerUp = (e: PointerEvent) => {
        if (pressedOutside && isOutside(e)) {
          dialog.close();
        }
        pressedOutside = false;
      };
      dialog.addEventListener('pointerdown', onPointerDown);
      dialog.addEventListener('pointerup', onPointerUp);
      cleanupFallback = () => {
        dialog.removeEventListener('pointerdown', onPointerDown);
        dialog.removeEventListener('pointerup', onPointerUp);
      };
    }

    return () => {
      dialog.removeEventListener('close', handleClose);
      cleanupFallback?.();
    };
  }, [onClose]);

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      className={`app-dialog ${maxWidthClass}`}
      {...({ closedby: 'any' } as Record<string, unknown>)}
    >
      <div className="flex items-center justify-between gap-4 border-b border-[var(--border-subtle)] px-6 py-4">
        <h1 id={titleId} className="text-lg font-semibold tracking-tight">
          {title}
        </h1>
        <form method="dialog">
          <button
            type="submit"
            value="close"
            aria-label={`Close ${title}`}
            className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-[var(--border-subtle)] hover:bg-[var(--surface-muted)]"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </form>
      </div>
      <div className="app-scroll overflow-y-auto p-6">{children}</div>
    </dialog>
  );
}
