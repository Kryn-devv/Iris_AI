"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Check, Copy, Mail, Trash2, UserPlus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Select, Label, FieldError } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Dialog } from "@/components/ui/dialog";
import { Avatar, EmptyState } from "@/components/ui/misc";
import { format } from "date-fns";
import { timeAgo } from "@/lib/utils";

export type MemberRow = {
  id: string;
  role: "OWNER" | "ADMIN" | "MEMBER" | "VIEWER";
  joinedAt: string;
  user: { id: string; name: string; email: string; avatarUrl: string | null };
};

export type InviteRow = {
  id: string;
  email: string;
  role: string;
  expiresAt: string;
  createdAt: string;
  url: string;
  path: string;
};

const ROLE_OPTIONS = ["VIEWER", "MEMBER", "ADMIN", "OWNER"] as const;

function roleTone(role: string) {
  if (role === "OWNER") return "accent" as const;
  if (role === "ADMIN") return "aurora" as const;
  return "neutral" as const;
}

async function jsonFetch(url: string, init?: RequestInit) {
  const res = await fetch(url, init);
  const json = await res.json().catch(() => null);
  if (!res.ok || !json?.ok) {
    throw new Error(json?.error?.message ?? "Something went wrong");
  }
  return json.data as Record<string, unknown>;
}

/** Build an absolute invite link, falling back to the current origin. */
function inviteHref(invite: InviteRow): string {
  if (invite.url.startsWith("http")) return invite.url;
  if (typeof window !== "undefined") {
    return `${window.location.origin}${invite.path}`;
  }
  return invite.path;
}

function CopyLinkButton({ href }: { href: string }) {
  const [copied, setCopied] = React.useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(href);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard unavailable — leave the link selectable instead.
      window.prompt("Copy this invite link:", href);
    }
  }
  return (
    <Button
      type="button"
      variant="secondary"
      size="sm"
      onClick={copy}
      aria-label="Copy invite link"
    >
      {copied ? <Check size={13} /> : <Copy size={13} />}
      {copied ? "Copied" : "Copy link"}
    </Button>
  );
}

/** Team members table with role management and removal. */
export function MembersTable({
  orgSlug,
  members,
  currentUserId,
  currentRole,
}: {
  orgSlug: string;
  members: MemberRow[];
  currentUserId: string;
  currentRole: "OWNER" | "ADMIN";
}) {
  const router = useRouter();
  const [busyId, setBusyId] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const isOwner = currentRole === "OWNER";
  const ownerCount = members.filter((m) => m.role === "OWNER").length;

  async function changeRole(member: MemberRow, role: string) {
    setBusyId(member.id);
    setError(null);
    try {
      await jsonFetch(`/api/orgs/${orgSlug}/members/${member.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role }),
      });
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change role");
    } finally {
      setBusyId(null);
    }
  }

  async function remove(member: MemberRow) {
    const self = member.user.id === currentUserId;
    const label = self
      ? "Leave this workspace? You will lose access."
      : `Remove ${member.user.name} from the workspace?`;
    if (!window.confirm(label)) return;
    setBusyId(member.id);
    setError(null);
    try {
      const data = await jsonFetch(`/api/orgs/${orgSlug}/members/${member.id}`, {
        method: "DELETE",
      });
      if (data.self) {
        router.push("/app");
      }
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove member");
    } finally {
      setBusyId(null);
    }
  }

  /** Which roles the current user may assign to this member. */
  function assignableRoles(member: MemberRow): readonly string[] {
    if (isOwner) return ROLE_OPTIONS;
    // ADMIN: may set VIEWER/MEMBER/ADMIN, never touch OWNER either way.
    if (member.role === "OWNER") return [];
    return ["VIEWER", "MEMBER", "ADMIN"];
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Members</CardTitle>
        <CardDescription>
          People with access to this workspace and their roles.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <FieldError>{error}</FieldError>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-line text-xs text-ink-faint">
                <th className="py-2 pr-3 font-medium">Member</th>
                <th className="py-2 pr-3 font-medium">Role</th>
                <th className="py-2 pr-3 font-medium">Joined</th>
                <th className="py-2 text-right font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {members.map((member) => {
                const roles = assignableRoles(member);
                const lastOwner = member.role === "OWNER" && ownerCount <= 1;
                const canEditRole = roles.length > 0 && !lastOwner;
                const canRemove =
                  !lastOwner && (isOwner || member.role !== "OWNER");
                return (
                  <tr key={member.id} className="border-b border-line/60">
                    <td className="py-3 pr-3">
                      <div className="flex items-center gap-2.5">
                        <Avatar
                          name={member.user.name}
                          src={member.user.avatarUrl}
                          size={30}
                        />
                        <div className="min-w-0">
                          <p className="truncate font-medium text-ink">
                            {member.user.name}
                            {member.user.id === currentUserId && (
                              <span className="ml-1.5 text-xs text-ink-faint">
                                (you)
                              </span>
                            )}
                          </p>
                          <p className="truncate text-xs text-ink-muted">
                            {member.user.email}
                          </p>
                        </div>
                      </div>
                    </td>
                    <td className="py-3 pr-3">
                      {canEditRole ? (
                        <Select
                          aria-label={`Role for ${member.user.name}`}
                          value={member.role}
                          disabled={busyId === member.id}
                          onChange={(e) => changeRole(member, e.target.value)}
                          className="h-8 w-28 text-xs"
                        >
                          {roles.map((r) => (
                            <option key={r} value={r}>
                              {r.charAt(0) + r.slice(1).toLowerCase()}
                            </option>
                          ))}
                        </Select>
                      ) : (
                        <Badge tone={roleTone(member.role)}>
                          {member.role.charAt(0) +
                            member.role.slice(1).toLowerCase()}
                        </Badge>
                      )}
                    </td>
                    <td className="py-3 pr-3 text-xs text-ink-muted">
                      {timeAgo(member.joinedAt)}
                    </td>
                    <td className="py-3 text-right">
                      {canRemove && (
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={busyId === member.id}
                          onClick={() => remove(member)}
                          aria-label={
                            member.user.id === currentUserId
                              ? "Leave workspace"
                              : `Remove ${member.user.name}`
                          }
                          className="text-ink-faint hover:text-danger"
                        >
                          <Trash2 size={14} />
                          {member.user.id === currentUserId ? "Leave" : "Remove"}
                        </Button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

/** Pending invites list + "Invite teammate" dialog. */
export function InvitesPanel({
  orgSlug,
  invites,
  currentRole,
}: {
  orgSlug: string;
  invites: InviteRow[];
  currentRole: "OWNER" | "ADMIN";
}) {
  const router = useRouter();
  const [open, setOpen] = React.useState(false);
  const [email, setEmail] = React.useState("");
  const [role, setRole] = React.useState("MEMBER");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [created, setCreated] = React.useState<InviteRow | null>(null);
  const [revokingId, setRevokingId] = React.useState<string | null>(null);
  const [listError, setListError] = React.useState<string | null>(null);

  const inviteRoles =
    currentRole === "OWNER"
      ? ROLE_OPTIONS
      : (["VIEWER", "MEMBER", "ADMIN"] as const);

  function closeDialog() {
    setOpen(false);
    setError(null);
    setCreated(null);
    setEmail("");
    setRole("MEMBER");
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const data = await jsonFetch(`/api/orgs/${orgSlug}/invites`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, role }),
      });
      setCreated(data.invite as InviteRow);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create invite");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(invite: InviteRow) {
    if (!window.confirm(`Revoke the invite for ${invite.email}?`)) return;
    setRevokingId(invite.id);
    setListError(null);
    try {
      await jsonFetch(`/api/orgs/${orgSlug}/invites/${invite.id}`, {
        method: "DELETE",
      });
      router.refresh();
    } catch (err) {
      setListError(err instanceof Error ? err.message : "Could not revoke invite");
    } finally {
      setRevokingId(null);
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <CardTitle>Pending invites</CardTitle>
          <CardDescription>
            Share an invite link — no email is sent automatically.
          </CardDescription>
        </div>
        <Button size="sm" onClick={() => setOpen(true)}>
          <UserPlus size={14} />
          Invite teammate
        </Button>
      </CardHeader>
      <CardContent>
        <FieldError>{listError}</FieldError>
        {invites.length === 0 ? (
          <EmptyState
            icon={<Mail size={20} />}
            title="No pending invites"
            description="Invite a teammate and share the link with them directly."
            className="py-8"
          />
        ) : (
          <ul className="divide-y divide-line/60">
            {invites.map((invite) => {
              const expired = new Date(invite.expiresAt) < new Date();
              return (
                <li
                  key={invite.id}
                  className="flex flex-wrap items-center justify-between gap-2 py-3"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-ink">
                      {invite.email}
                    </p>
                    <p className="text-xs text-ink-muted">
                      {invite.role.charAt(0) +
                        invite.role.slice(1).toLowerCase()}{" "}
                      ·{" "}
                      {expired ? (
                        <span className="text-danger">expired</span>
                      ) : (
                        <>expires {format(new Date(invite.expiresAt), "MMM d, yyyy")}</>
                      )}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    {expired ? (
                      <Badge tone="danger">Expired</Badge>
                    ) : (
                      <CopyLinkButton href={inviteHref(invite)} />
                    )}
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={revokingId === invite.id}
                      onClick={() => revoke(invite)}
                      aria-label={`Revoke invite for ${invite.email}`}
                      className="text-ink-faint hover:text-danger"
                    >
                      <Trash2 size={14} />
                      Revoke
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>

      <Dialog
        open={open}
        onClose={closeDialog}
        title="Invite a teammate"
        description="Create an invite link and share it with them. Links expire after 7 days."
      >
        {created ? (
          <div className="space-y-4">
            <p className="text-sm text-ink-muted">
              Invite for <span className="font-medium text-ink">{created.email}</span>{" "}
              is ready. Copy the link below and send it to them.
            </p>
            <div className="flex items-center gap-2">
              <Input
                readOnly
                value={inviteHref(created)}
                aria-label="Invite link"
                onFocus={(e) => e.currentTarget.select()}
                className="font-mono text-xs"
              />
              <CopyLinkButton href={inviteHref(created)} />
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={closeDialog}>
                Done
              </Button>
            </div>
          </div>
        ) : (
          <form onSubmit={submit} noValidate className="space-y-4">
            <div>
              <Label htmlFor="invite-email">Email</Label>
              <Input
                id="invite-email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="teammate@company.com"
              />
            </div>
            <div>
              <Label htmlFor="invite-role">Role</Label>
              <Select
                id="invite-role"
                value={role}
                onChange={(e) => setRole(e.target.value)}
              >
                {inviteRoles.map((r) => (
                  <option key={r} value={r}>
                    {r.charAt(0) + r.slice(1).toLowerCase()}
                  </option>
                ))}
              </Select>
              <FieldError>{error}</FieldError>
            </div>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="secondary" onClick={closeDialog}>
                Cancel
              </Button>
              <Button type="submit" loading={busy}>
                Create invite link
              </Button>
            </div>
          </form>
        )}
      </Dialog>
    </Card>
  );
}
