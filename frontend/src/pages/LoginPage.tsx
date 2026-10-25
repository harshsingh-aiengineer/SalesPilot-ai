import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, errorText, setToken } from "../lib/api";
import { Button, TextInput } from "../components/ui";

export function LoginPage() {
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError("");
    try {
      const result = await api<{ token: string }>("/auth/login", { method: "POST", body: JSON.stringify({ password }) });
      setToken(result.token);
      navigate("/");
    } catch (err) {
      setError(errorText(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="grid min-h-screen place-items-center bg-sidebar px-4">
      <form onSubmit={submit} className="w-full max-w-md rounded-[28px] bg-paper p-8 shadow-card">
        <p className="text-xs uppercase tracking-[0.2em] text-brass">SalesPilot AI</p>
        <h1 className="mt-2 font-display text-4xl">Sign in to the local desk</h1>
        <p className="mt-3 text-sm text-ink/70">This password stays on your machine. It is the SALESPILOT_PASSWORD from your environment file.</p>
        <label className="mt-6 block text-sm font-medium">
          Password
          <TextInput className="mt-2" type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoFocus required />
        </label>
        {error ? <p className="mt-3 text-sm text-red-700" role="alert">{error}</p> : null}
        <Button className="mt-6 w-full" disabled={pending}>{pending ? "Checking…" : "Enter"}</Button>
      </form>
    </div>
  );
}
