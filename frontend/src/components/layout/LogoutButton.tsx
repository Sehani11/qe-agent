"use client";

import { useState, useTransition } from "react";
import { LogOut } from "lucide-react";

import { signOut } from "@/app/actions/auth";
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";

export default function LogoutButton() {
  const [isConfirming, setIsConfirming] = useState(false);
  // The action is called directly rather than through a <form>, so the modal's
  // confirm button is what submits it. useTransition gives us the same pending
  // state useFormStatus did.
  const [isPending, startTransition] = useTransition();

  const handleConfirm = () => {
    startTransition(async () => {
      await signOut();
    });
  };

  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        loading={isPending}
        onClick={() => setIsConfirming(true)}
        aria-label="Log out"
      >
        {!isPending && <LogOut className="h-3.5 w-3.5" aria-hidden="true" />}
        <span className="hidden sm:inline">{isPending ? "Logging out" : "Log out"}</span>
      </Button>

      <ConfirmModal
        open={isConfirming}
        title="Log out?"
        description="Saved sessions stay on your account, but anything you have not saved in the editor will be lost."
        confirmLabel="Log out"
        isConfirming={isPending}
        onConfirm={handleConfirm}
        onCancel={() => setIsConfirming(false)}
      />
    </>
  );
}
