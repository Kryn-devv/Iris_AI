import { db } from "@/lib/db";
import { requireOrgPage, roleAtLeast } from "@/lib/auth/guards";
import { brand } from "@/config/brand";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/misc";
import { WebhooksPanel } from "@/components/integrations/webhooks-panel";
import { ApiKeysPanel } from "@/components/integrations/apikeys-panel";
import { CsvImport } from "@/components/integrations/csv-import";
import { CopyButton } from "@/components/integrations/copy-button";

export const dynamic = "force-dynamic";

export default async function IntegrationsPage({
  params,
}: {
  params: Promise<{ orgSlug: string }>;
}) {
  const { orgSlug } = await params;
  const ctx = await requireOrgPage(orgSlug);
  const canManage = roleAtLeast(ctx.role, "ADMIN");
  const appUrl = process.env.NEXT_PUBLIC_APP_URL || "http://localhost:3000";

  const [webhooks, apiKeys] = await Promise.all([
    db.webhook.findMany({
      where: { orgId: ctx.org.id },
      orderBy: { createdAt: "desc" },
      select: { id: true, url: true, events: true, active: true },
    }),
    db.apiKey.findMany({
      where: { orgId: ctx.org.id },
      orderBy: { createdAt: "desc" },
      select: { id: true, name: true, prefix: true, lastUsedAt: true, createdAt: true },
    }),
  ]);

  const widgetUrl = `${appUrl}/w/${ctx.org.widgetKey}`;
  const embedSnippet = `<iframe
  src="${widgetUrl}"
  title="${ctx.org.name} feedback"
  style="width:380px;height:560px;border:0;border-radius:12px"
  loading="lazy"
></iframe>`;
  const curlList = `curl ${appUrl}/api/v1/posts \\
  -H "Authorization: Bearer nvk_your_key"`;
  const curlCreate = `curl -X POST ${appUrl}/api/v1/posts \\
  -H "Authorization: Bearer nvk_your_key" \\
  -H "Content-Type: application/json" \\
  -d '{"title": "Add dark mode", "body": "Please!", "type": "FEATURE_REQUEST"}'`;

  return (
    <div>
      <PageHeader
        title="Integrations"
        description={`Connect ${brand.name} to your stack: webhooks out, REST + widget + imports in.`}
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Webhooks</CardTitle>
            <CardDescription>
              Signed JSON callbacks (HMAC-SHA256 in <code>X-Signature</code>) on feedback and release
              events.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <WebhooksPanel orgSlug={orgSlug} webhooks={webhooks} canManage={canManage} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>API keys</CardTitle>
            <CardDescription>Authenticate the public REST API. Keys are stored hashed.</CardDescription>
          </CardHeader>
          <CardContent>
            <ApiKeysPanel
              orgSlug={orgSlug}
              apiKeys={apiKeys.map((k) => ({
                ...k,
                lastUsedAt: k.lastUsedAt?.toISOString() ?? null,
                createdAt: k.createdAt.toISOString(),
              }))}
              canManage={canManage}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>REST API</CardTitle>
            <CardDescription>
              <code>GET /api/v1/posts</code> · <code>POST /api/v1/posts</code> ·{" "}
              <code>GET /api/v1/roadmap</code>
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div>
              <div className="mb-1 flex items-center justify-between">
                <span className="text-[11px] uppercase tracking-wide text-ink-faint">List posts</span>
                <CopyButton text={curlList} />
              </div>
              <pre className="overflow-x-auto rounded-lg border border-line bg-void/60 p-3 font-mono text-[11px] leading-relaxed text-ink-muted">{curlList}</pre>
            </div>
            <div>
              <div className="mb-1 flex items-center justify-between">
                <span className="text-[11px] uppercase tracking-wide text-ink-faint">Create feedback</span>
                <CopyButton text={curlCreate} />
              </div>
              <pre className="overflow-x-auto rounded-lg border border-line bg-void/60 p-3 font-mono text-[11px] leading-relaxed text-ink-muted">{curlCreate}</pre>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Embeddable widget</CardTitle>
            <CardDescription>
              Drop the {brand.name} widget into your product — visitors browse top ideas, vote, and
              submit without leaving your app.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="flex items-center justify-between">
              <a
                href={widgetUrl}
                target="_blank"
                rel="noreferrer"
                className="text-xs text-accent-soft hover:underline"
              >
                Preview widget ↗
              </a>
              <CopyButton text={embedSnippet} label="Copy embed code" />
            </div>
            <pre className="overflow-x-auto rounded-lg border border-line bg-void/60 p-3 font-mono text-[11px] leading-relaxed text-ink-muted">{embedSnippet}</pre>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Import feedback</CardTitle>
            <CardDescription>
              Bring existing feedback in from a CSV with <code>title</code> and <code>body</code>{" "}
              columns (optional <code>category</code>, <code>name</code>). Rows are AI-enriched on
              import.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {canManage ? (
              <CsvImport orgSlug={orgSlug} />
            ) : (
              <p className="text-xs text-ink-faint">Admins can import CSV files.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
