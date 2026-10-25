import { z } from "zod";

export const leadFormSchema = z.object({
  name: z.string().trim().min(1, "Name is required").max(200),
  email: z
    .string()
    .optional()
    .refine((value) => !value || value.trim() === "" || z.string().email().safeParse(value.trim()).success, "Enter a valid email"),
  phone: z.string().max(50).optional(),
  company: z.string().max(200).optional(),
  location: z.string().max(200).optional(),
  requirements: z.string().optional(),
  budget_min: z.string().optional(),
  budget_max: z.string().optional(),
  currency: z.string().min(3).max(8),
  source: z.string().max(100).optional(),
  status: z.enum(["new", "contacted", "qualified", "quotation", "negotiation", "won", "lost", "archived"]),
  priority: z.enum(["unset", "hot", "warm", "cold"]),
  next_action: z.string().optional(),
  next_follow_up_at: z.string().optional(),
  notes: z.string().optional(),
});

export type LeadFormValues = z.infer<typeof leadFormSchema>;

export function toLeadPayload(values: LeadFormValues) {
  const num = (value?: string) => (value && value.trim() !== "" ? Number(value) : null);
  return {
    ...values,
    email: values.email?.trim() ? values.email.trim() : null,
    phone: values.phone?.trim() || null,
    company: values.company?.trim() || null,
    location: values.location?.trim() || null,
    requirements: values.requirements?.trim() || null,
    source: values.source?.trim() || null,
    next_action: values.next_action?.trim() || null,
    notes: values.notes?.trim() || null,
    budget_min: num(values.budget_min),
    budget_max: num(values.budget_max),
    next_follow_up_at: values.next_follow_up_at ? new Date(values.next_follow_up_at).toISOString() : null,
  };
}
