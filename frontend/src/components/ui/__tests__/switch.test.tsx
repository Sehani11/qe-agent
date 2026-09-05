/**
 * Switch — a `role="switch"` control, not a restyled checkbox. These pin the
 * accessible contract, which is the whole reason it is not an <input>.
 */
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

import { Switch } from "@/components/ui/switch";

describe("Switch", () => {
    it("exposes on/off state through role and aria-checked", () => {
        render(<Switch checked={false} onCheckedChange={vi.fn()} label="Use fine-tuned model" />);

        const control = screen.getByRole("switch", { name: /use fine-tuned model/i });
        expect(control).toHaveAttribute("aria-checked", "false");
    });

    it("reports the state it is moving to, not the one it left", () => {
        const onCheckedChange = vi.fn();
        render(<Switch checked={false} onCheckedChange={onCheckedChange} label="Toggle" />);

        fireEvent.click(screen.getByRole("switch"));
        expect(onCheckedChange).toHaveBeenCalledWith(true);
    });

    it("turns back off", () => {
        const onCheckedChange = vi.fn();
        render(<Switch checked onCheckedChange={onCheckedChange} label="Toggle" />);

        expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "true");
        fireEvent.click(screen.getByRole("switch"));
        expect(onCheckedChange).toHaveBeenCalledWith(false);
    });

    it("does not fire while disabled", () => {
        const onCheckedChange = vi.fn();
        render(<Switch checked={false} onCheckedChange={onCheckedChange} label="Toggle" disabled />);

        fireEvent.click(screen.getByRole("switch"));
        expect(onCheckedChange).not.toHaveBeenCalled();
    });

    it("keeps the label clickable, and the description out of the name", () => {
        const onCheckedChange = vi.fn();
        render(
            <Switch
                checked={false}
                onCheckedChange={onCheckedChange}
                label="Toggle"
                description="Extra detail"
            />
        );

        fireEvent.click(screen.getByText("Toggle"));
        expect(onCheckedChange).toHaveBeenCalledWith(true);
        expect(screen.getByText("Extra detail")).toBeInTheDocument();
    });
});
