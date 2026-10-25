import { describe, expect, it } from "vitest";
import { leadFormSchema, toLeadPayload } from "./schemas";

describe("lead form", () => {
  it("rejects an empty name", () => {
    const parsed = leadFormSchema.safeParse({
      name: "  ",
      currency: "AED",
      status: "new",
      priority: "unset",
    });
    expect(parsed.success).toBe(false);
  });

  it("converts blank budget fields to null", () => {
    const values = leadFormSchema.parse({
      name: "Aisha",
      email: "",
      currency: "AED",
      status: "new",
      priority: "hot",
      budget_min: "",
      budget_max: "8000",
    });
    const payload = toLeadPayload(values);
    expect(payload.email).toBeNull();
    expect(payload.budget_min).toBeNull();
    expect(payload.budget_max).toBe(8000);
  });
});
