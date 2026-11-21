import * as Popover from "@radix-ui/react-popover";
import { useState } from "react";
import { UploadPanel, type UploadHandlers } from "./UploadPanel";

const POPOVER_OFFSET_PX = 8;

export function UploadPopover({ onSession }: UploadHandlers) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  return (
    <Popover.Root
      open={open}
      onOpenChange={(next) => {
        // Closing mid-upload would unmount the panel and drop the upload's error.
        if (!next && busy) return;
        setOpen(next);
      }}
      modal
    >
      <Popover.Trigger asChild>
        <button type="button" className="btn btn-ghost">
          上传轨迹
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          className="upload-popover"
          aria-label="上传轨迹"
          align="end"
          sideOffset={POPOVER_OFFSET_PX}
        >
          <UploadPanel
            onBusyChange={setBusy}
            onSession={(session) => {
              setOpen(false);
              onSession(session);
            }}
          />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
