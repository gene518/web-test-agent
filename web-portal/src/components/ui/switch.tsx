import * as React from "react";
import * as SwitchPrimitive from "@radix-ui/react-switch";

import { cn } from "@/lib/utils";

function Switch({
  className,
  ...props
}: React.ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      className={cn(
        "peer inline-flex h-5 w-9 shrink-0 items-center rounded-full border border-[#94a3b8] bg-[#cbd5e1] shadow-xs transition-colors outline-none focus-visible:border-[#2563eb] focus-visible:ring-[3px] focus-visible:ring-[#93c5fd] disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:border-[#111827] data-[state=checked]:bg-[#111827]",
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb
        data-slot="switch-thumb"
        className={cn(
          "pointer-events-none block size-4 rounded-full border border-[#94a3b8] bg-[#ffffff] shadow-md ring-0 transition-transform data-[state=checked]:translate-x-[15px] data-[state=checked]:border-[#ffffff] data-[state=unchecked]:translate-x-0",
        )}
      />
    </SwitchPrimitive.Root>
  );
}

export { Switch };
