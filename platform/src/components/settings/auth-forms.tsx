"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input, Label, FieldError } from "@/components/ui/input";

async function postJson(url: string, body: unknown) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const json = await res.json().catch(() => null);
  if (!res.ok || !json?.ok) {
    throw new Error(json?.error?.message ?? "Something went wrong");
  }
  return json.data as unknown;
}

/** Sign-in form. `next` is a same-origin path validated server-side. */
export function LoginForm({ next }: { next: string | null }) {
  const router = useRouter();
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  const registerHref = next
    ? `/register?next=${encodeURIComponent(next)}`
    : "/register";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await postJson("/api/auth/login", { email, password });
      router.push(next ?? "/app");
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      <div>
        <Label htmlFor="login-email">Email</Label>
        <Input
          id="login-email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@company.com"
        />
      </div>
      <div>
        <Label htmlFor="login-password">Password</Label>
        <Input
          id="login-password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="••••••••"
        />
        <FieldError>{error}</FieldError>
      </div>
      <Button type="submit" loading={busy} className="w-full">
        Sign in
      </Button>
      <p className="text-center text-xs text-ink-muted">
        No account yet?{" "}
        <Link href={registerHref} className="text-accent-soft hover:underline">
          Create one
        </Link>
      </p>
    </form>
  );
}

/** Registration form. Redirects to `next` (e.g. an invite) or onboarding. */
export function RegisterForm({ next }: { next: string | null }) {
  const router = useRouter();
  const [name, setName] = React.useState("");
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  const loginHref = next ? `/login?next=${encodeURIComponent(next)}` : "/login";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await postJson("/api/auth/register", { name, email, password });
      router.push(next ?? "/onboarding");
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      <div>
        <Label htmlFor="reg-name">Name</Label>
        <Input
          id="reg-name"
          autoComplete="name"
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Ada Lovelace"
        />
      </div>
      <div>
        <Label htmlFor="reg-email">Email</Label>
        <Input
          id="reg-email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@company.com"
        />
      </div>
      <div>
        <Label htmlFor="reg-password">Password</Label>
        <Input
          id="reg-password"
          type="password"
          autoComplete="new-password"
          required
          minLength={8}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="At least 8 characters"
        />
        <FieldError>{error}</FieldError>
      </div>
      <Button type="submit" loading={busy} className="w-full">
        Create account
      </Button>
      <p className="text-center text-xs text-ink-muted">
        Already have an account?{" "}
        <Link href={loginHref} className="text-accent-soft hover:underline">
          Sign in
        </Link>
      </p>
    </form>
  );
}
